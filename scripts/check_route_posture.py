#!/usr/bin/env python3
"""Every core daemon route is classified operator-only or user-ok — and every
shipped hosted/demo config resolves to `user` posture.

The operator/user trust posture (docs/AUTH.md § Operator vs user) is enforced at
one middleware for core `@server.*` routes, reading `emptyos.posture.OPERATOR_ROUTES`.
That list is only as good as its completeness: a NEW core route that exposes the
host, config, network, plugins, code install, or generic dispatch is a hole the
moment it is added and forgotten. This check closes that — a core route that is
neither in `OPERATOR_ROUTES` nor in the reviewed `USER_ROUTES` set below **fails
preflight until someone classifies it**.

Second half: a hosted or demo config committed to the repo (demo, englishos-cloud,
any `profiles/*`) must resolve to `user` posture, or the edition ships open. We
parse the `[trust]`, `[demo]`, `[cloud]`, `[network]` sections and run the same
resolution `Config.trust_web` uses.

Both halves gate. On a healthy tree: 0 findings. Pinned both directions in
`tests/test_unit_check_route_posture.py`.

Adding a route:
- operator-shaped (host/config/network/plugins/install/dispatch/outbound) →
  add it to `OPERATOR_ROUTES` in `emptyos/posture.py`;
- user-ok (the user's own vault/app data, status reads) → add it to
  `USER_ROUTES` here with a one-word reason to yourself in review.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emptyos.posture import is_operator_route  # noqa: E402

_ROUTE_RE = re.compile(
    r'@server\.(get|post|put|delete|patch|api_route|websocket)\(\s*(["\'])(.*?)\2'
)
_PARAM_RE = re.compile(r"\{[^}]+\}")


def _concrete(path: str) -> str:
    """A route pattern with FastAPI params → a concrete path for matching."""
    return _PARAM_RE.sub("x", path)


# Core routes a signed-in web USER may reach even when they are not the operator.
# Reviewed set — the checker fails on any core route absent from BOTH this and
# OPERATOR_ROUTES, so a new route forces a classification decision.
USER_ROUTES: set[tuple[str, str]] = {
    ("GET", "/"),
    ("GET", "/api/app-icons/sprite"),
    ("GET", "/api/appdoc"),
    ("GET", "/api/apps"),
    ("GET", "/api/apps/clusters"),
    ("GET", "/api/apps/load-timings"),
    ("GET", "/api/apps/sections"),
    ("GET", "/api/apps/{app_id}"),
    ("GET", "/api/capabilities"),
    ("GET", "/api/capabilities/think/effective"),
    ("GET", "/api/cloud/pending"),
    ("GET", "/api/cloud/status"),
    ("GET", "/api/demo/status"),
    ("GET", "/api/events"),
    ("GET", "/api/health"),
    ("GET", "/api/health/gpu"),
    ("GET", "/api/i18n/lang"),
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/{job_id}"),
    ("GET", "/api/presentation/state"),
    ("GET", "/api/realtime/status"),
    ("GET", "/api/scheduler/jobs"),
    ("GET", "/api/sdk/suggest-apps"),
    ("GET", "/api/sdk/timeline"),
    ("GET", "/api/sdk/timeline-apps"),
    ("GET", "/api/shortcuts"),
    ("GET", "/api/think-status"),
    ("GET", "/api/think/usage"),
    ("GET", "/api/topology"),
    ("GET", "/api/topology/improvements"),
    ("GET", "/api/topology/layers"),
    ("GET", "/api/topology/node/{node_id:path}"),
    ("GET", "/api/topology/releases"),
    ("GET", "/api/topology/timeline"),
    ("GET", "/api/topology/tree"),
    ("GET", "/api/vault/file"),
    ("GET", "/api/vault/query"),
    ("GET", "/api/vault/read"),
    ("GET", "/api/vault/reconcile"),
    ("GET", "/appdoc"),
    ("GET", "/auth/shell-exchange"),
    ("GET", "/console"),
    ("GET", "/favicon.ico"),
    ("GET", "/login"),
    ("GET", "/manifest.webmanifest"),
    ("GET", "/net-worth/{path:path}"),
    ("GET", "/notes"),
    ("GET", "/offline.html"),
    ("GET", "/retirement/{path:path}"),
    ("GET", "/sw.js"),
    ("GET", "/system"),
    ("GET", "/topology"),
    ("POST", "/api/auth/shell-exchange"),
    ("POST", "/api/cloud/consent"),
    ("POST", "/api/i18n/batch"),
    ("POST", "/api/i18n/lang"),
    ("POST", "/api/presentation/set"),
    ("POST", "/api/presentation/toggle"),
    ("POST", "/api/sdk/ai-form-fill"),
    ("POST", "/api/sdk/suggest-field"),
    ("POST", "/api/shortcuts"),
    ("POST", "/api/vault/enrich"),
    ("POST", "/api/vault/write"),
    ("POST", "/api/vault/write-bytes"),
    ("POST", "/login"),
    ("POST", "/logout"),
    ("WEBSOCKET", "/ws"),
}


def scan_routes(root: Path = ROOT) -> list[str]:
    """Findings: core routes classified as neither operator nor reviewed-user."""
    findings: list[str] = []
    # NOTE: this sees only `@server.*` decorators in emptyos/web/*.py. A core
    # route added via `server.add_api_route(...)`, an APIRouter, or a `.mount()`
    # sub-app is invisible here (none exist in emptyos/web today, 2026-09-30).
    # If one is added, extend this scan — the middleware (C1) only covers the
    # OPERATOR_ROUTES table, so an unscanned operator-shaped route is a hole.
    web = root / "emptyos" / "web"
    for f in sorted(web.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        for m in _ROUTE_RE.finditer(text):
            # Skip a commented-out decorator: look at the start of the line the
            # match begins on. (finditer over full text also catches a decorator
            # split across lines, which a per-line scan would miss.)
            line_start = text.rfind("\n", 0, m.start()) + 1
            if text[line_start:m.start()].lstrip().startswith("#"):
                continue
            method = m.group(1).upper()
            path = m.group(3)
            if is_operator_route(_concrete(path)):
                continue
            if (method, path) in USER_ROUTES:
                continue
            findings.append(
                f"{f.relative_to(root)}: {method} {path} — unclassified. Add it "
                f"to OPERATOR_ROUTES (emptyos/posture.py) if it exposes host/"
                f"config/network/plugins/install/dispatch, else to USER_ROUTES "
                f"(scripts/check_route_posture.py)."
            )
    return findings


# ── Config posture (mirror of Config.trust_web, without importing the daemon) ──

def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return False


def _as_bool_default_true(v) -> bool:
    """Config._as_bool(v, default=True): an unrecognised value reads as True.
    Only used for cloud.locked, which fails closed (a typo must not unlock)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off", ""):
            return False
    return True


def resolve_trust_web(data: dict) -> str:
    """Same resolution as emptyos.kernel.config.Config.trust_web, over a parsed
    TOML dict. Kept in sync by tests/test_unit_check_route_posture.py."""
    raw = (data.get("trust") or {}).get("web")
    if raw is not None:
        v = str(raw).strip().lower()
        return v if v in ("operator", "user") else "user"
    demo = _as_bool((data.get("demo") or {}).get("enabled", False))
    # Mirror Config.cloud_locked EXACTLY: an ABSENT cloud.locked reads as
    # not-locked (get() returns the False default), but a PRESENT unrecognised
    # value reads as locked — `_as_bool(x, default=True)`. Getting only the
    # absent case right (and defaulting a bad string to False) drifts the two
    # apart on e.g. `locked = "maybe"`: daemon → user, checker → operator.
    raw_locked = (data.get("cloud") or {}).get("locked", None)
    if raw_locked is None:
        locked = False
    else:
        locked = _as_bool_default_true(raw_locked)
    mode = str((data.get("network") or {}).get("mode", "local")).lower().strip()
    if demo or locked:
        return "user"
    if mode in ("local", "private"):
        return "operator"
    return ""  # public, unset


# Configs that MUST resolve to user posture (they serve non-operators).
HOSTED_CONFIGS = [
    "demo/emptyos.toml",
    "englishos-cloud/emptyos.toml.example",
]


def scan_configs(root: Path = ROOT) -> list[str]:
    findings: list[str] = []
    globbed = [root / p for p in HOSTED_CONFIGS]
    globbed += sorted(root.glob("profiles/*/emptyos.toml"))
    for cfg in globbed:
        if not cfg.exists():
            continue
        try:
            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as e:
            findings.append(f"{cfg.relative_to(root)}: cannot parse ({e})")
            continue
        posture = resolve_trust_web(data)
        if posture != "user":
            findings.append(
                f"{cfg.relative_to(root)}: resolves to trust posture "
                f"'{posture or 'UNRESOLVED'}', must be 'user' — a hosted/demo "
                f"config serves non-operators. Set [trust] web = \"user\"."
            )
    return findings


def main() -> int:
    findings = scan_routes() + scan_configs()
    if findings:
        print(f"check_route_posture: {len(findings)} finding(s)")
        for x in findings:
            print(f"  - {x}")
        return 1
    print("check_route_posture: OK (all core routes classified; hosted configs are user posture)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
