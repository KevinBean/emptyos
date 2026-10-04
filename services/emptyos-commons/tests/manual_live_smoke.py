"""Live ASGI + SQLite smoke proof for EmptyOS Commons.

Starts the real uvicorn app factory on a free loopback port, backed by a
throwaway SQLite file, and drives the trusted-team collaboration flow over
HTTP. This is intentionally not a pytest.

Run from the repository root:
    python services/emptyos-commons/tests/manual_live_smoke.py

Exits non-zero on any failed check. The uvicorn child and temporary directory
are cleaned up even when the flow raises unexpectedly.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

SERVICE_DIR = Path(__file__).resolve().parents[1]
REGISTRATION_SECRET = "live-smoke-invite"
PASSWORD = "live-smoke-password"

results: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    results.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label, flush=True)


def free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in {9000, 9001}:
            return port


def wait_until_ready(origin: str, process: subprocess.Popen, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            response = httpx.get(f"{origin}/health", timeout=0.5)
            if response.status_code == 200:
                return response.json().get("service") == "emptyos-commons"
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.1)
    return False


def register(client: httpx.Client, origin: str, email: str) -> httpx.Response:
    return client.post(
        "/auth/register",
        headers={"Origin": origin},
        json={
            "email": email,
            "password": PASSWORD,
            "secret": REGISTRATION_SECRET,
        },
    )


def sign_in(client: httpx.Client, origin: str, email: str) -> httpx.Response:
    return client.post(
        "/auth/session",
        headers={"Origin": origin},
        json={"email": email, "password": PASSWORD},
    )


def run_flow(origin: str, database_path: Path) -> None:
    mutation_headers = {"Origin": origin}
    with (
        httpx.Client(base_url=origin, timeout=5.0) as user_a,
        httpx.Client(base_url=origin, timeout=5.0) as user_b,
    ):
        response = register(user_a, origin, "a@live-smoke.invalid")
        check(response.status_code == 200, "register user A through live server")

        response = sign_in(user_a, origin, "a@live-smoke.invalid")
        check(
            response.status_code == 200 and bool(user_a.cookies.get("commons_session")),
            "create user A session cookie",
        )

        response = user_a.post(
            "/api/notes",
            headers=mutation_headers,
            json={
                "slug": "live-smoke-note",
                "title": "Live smoke",
                "body": "private v1",
                "visibility": "private",
            },
        )
        body = response.json() if response.status_code == 200 else {}
        note = body.get("note", {})
        note_id = note.get("id", "")
        check(
            bool(note_id) and note.get("visibility") == "private",
            "publish a private note",
        )
        check(database_path.is_file(), "persist flow state to throwaway SQLite file")

        response = user_a.patch(
            f"/api/notes/{note_id}/visibility",
            headers=mutation_headers,
            json={"visibility": "public"},
        )
        visible = user_a.get(f"/api/notes/{note_id}") if response.status_code == 200 else response
        visible_body = visible.json() if visible.status_code == 200 else {}
        check(
            response.status_code == 200
            and visible_body.get("note", {}).get("visibility") == "public",
            "flip note visibility private -> public",
        )

        response = register(user_b, origin, "b@live-smoke.invalid")
        check(response.status_code == 200, "register user B through live server")

        response = sign_in(user_b, origin, "b@live-smoke.invalid")
        check(response.status_code == 200, "create user B session")

        # A read-DENIAL is the load-bearing visibility assertion: without it a
        # smoke that only ever sees 200s cannot prove can_read() gates anything.
        response = user_a.post(
            "/api/notes",
            headers=mutation_headers,
            json={
                "slug": "live-smoke-secret",
                "title": "Live smoke secret",
                "body": "never visible to B",
                "visibility": "private",
            },
        )
        secret_id = (response.json().get("note", {}) if response.status_code == 200 else {}).get(
            "id", ""
        )
        check(bool(secret_id), "user A publishes a second private note")

        response = user_b.get(f"/api/notes/{secret_id}")
        check(
            response.status_code in (403, 404)
            and "never visible to B" not in response.text,
            "user B is DENIED read on user A's private note (body never leaks)",
        )

        response = user_b.get(f"/api/notes/{note_id}")
        response_body = response.json() if response.status_code == 200 else {}
        check(
            response.status_code == 200
            and response_body.get("note", {}).get("body") == "private v1",
            "user B can read the public note",
        )

        response = user_b.patch(
            f"/api/notes/{note_id}",
            headers=mutation_headers,
            json={"body": "unauthorized edit"},
        )
        check(response.status_code == 403, "public visibility does not grant user B write access")

        response = user_a.post(
            f"/api/notes/{note_id}/share",
            headers=mutation_headers,
            json={"principal": "b@live-smoke.invalid", "level": "write"},
        )
        check(
            response.status_code == 200 and response.json().get("level") == "write",
            "user A grants user B write access",
        )

        response = user_b.patch(
            f"/api/notes/{note_id}",
            headers=mutation_headers,
            json={"body": "edited by user B"},
        )
        updated = user_a.get(f"/api/notes/{note_id}") if response.status_code == 200 else response
        updated_body = updated.json() if updated.status_code == 200 else {}
        check(
            response.status_code == 200
            and updated_body.get("note", {}).get("body") == "edited by user B",
            "write grant lets user B edit and user A observe the edit",
        )

        response = user_a.post(
            "/api/tokens",
            headers=mutation_headers,
            json={"label": "live-smoke-machine"},
        )
        token_body = response.json() if response.status_code == 200 else {}
        machine_token = token_body.get("token", "")
        check(
            response.status_code == 200 and machine_token.startswith("ct_"),
            "user A mints a machine token",
        )

        with httpx.Client(base_url=origin, timeout=5.0) as machine:
            response = machine.get(
                "/api/me",
                headers={"Authorization": f"Bearer {machine_token}"},
            )
        response_body = response.json() if response.status_code == 200 else {}
        check(
            response.status_code == 200
            and response_body.get("email") == "a@live-smoke.invalid",
            "cookie-less Bearer machine token authenticates as user A",
        )

        old_session = user_a.cookies.get("commons_session", "")
        response = user_a.post("/auth/logout", headers=mutation_headers)
        check(response.status_code == 200, "logout user A")

        with httpx.Client(base_url=origin, timeout=5.0) as replay:
            response = replay.get(
                "/api/me",
                headers={"Cookie": f"commons_session={old_session}"},
            )
        check(response.status_code == 401, "logged-out user A session cannot be replayed")


def summary() -> int:
    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} checks passed ===")
    return 0 if passed == total else 1


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="emptyos-commons-live-smoke-") as temp:
            temp_dir = Path(temp)
            database_path = temp_dir / "commons-smoke.db"
            log_path = temp_dir / "uvicorn.log"
            port = free_port()
            origin = f"http://127.0.0.1:{port}"
            env = os.environ.copy()
            env.update(
                {
                    "COMMONS_DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
                    "COMMONS_SESSION_HASH_SECRET": "live-smoke-session-secret-32-characters-minimum",
                    "COMMONS_PUBLIC_ORIGIN": origin,
                    "COMMONS_AUTH_PROVIDER": "password",
                    "COMMONS_REGISTRATION_SECRET": REGISTRATION_SECRET,
                    "COMMONS_SESSION_SECURE": "false",
                    "PYTHONPATH": str(SERVICE_DIR)
                    + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
                }
            )
            process: subprocess.Popen | None = None
            log_handle = None
            try:
                log_handle = log_path.open("w", encoding="utf-8")
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "emptyos_commons.app:create_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--log-level",
                        "warning",
                    ],
                    cwd=SERVICE_DIR,
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
                ready = wait_until_ready(origin, process)
                check(ready, f"real uvicorn app is healthy on 127.0.0.1:{port}")
                if ready:
                    run_flow(origin, database_path)
                elif log_path.is_file():
                    log_handle.flush()
                    print(log_path.read_text(encoding="utf-8"), file=sys.stderr)
            finally:
                # Stop the exact child we started before TemporaryDirectory tries
                # to remove the SQLite file (important on Windows open handles).
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if log_handle is not None:
                    log_handle.close()
    except Exception as exc:
        check(False, f"unexpected smoke exception: {type(exc).__name__}: {exc}")
    return summary()


if __name__ == "__main__":
    sys.exit(main())
