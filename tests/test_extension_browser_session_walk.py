"""Real-Chromium Browser Session round trip against a disposable fake daemon."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.interactive
fastapi = pytest.importorskip("fastapi")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _BridgeWalk:
    def __init__(self):
        self.done = threading.Event()
        self.disarmed = threading.Event()
        self.error = ""
        self.results: dict[str, dict] = {}

    async def run(self, websocket) -> None:
        await websocket.accept()

        async def receive(kind: str) -> dict:
            while True:
                message = json.loads(await asyncio.wait_for(websocket.receive_text(), 10))
                if message.get("type") == kind:
                    return message

        async def command(request_id: str, action: str, tab_id: int, args: dict | None = None) -> dict:
            await websocket.send_json({
                "type": "browser.command", "protocol": 1, "request_id": request_id,
                "session_id": "walk-session", "action": action, "tab_id": tab_id,
                "args": args or {}, "deadline": time.time() + 30,
            })
            result = await receive("browser.result")
            self.results[request_id] = result
            return result

        try:
            hello = await receive("browser.hello")
            assert hello["protocol"] == 1
            assert "snapshot" in hello["commands"]
            await websocket.send_json({"type": "browser.ready", "protocol": 1, "enabled": True})
            arm = await receive("browser.arm")
            tab_id = arm["tabs"][0]["tab_id"]
            await websocket.send_json({
                "type": "browser.armed", "protocol": 1, "session_id": "walk-session",
                "expires_at": time.time() + 3600, "tabs": arm["tabs"],
            })

            snapshot = await command("snapshot", "snapshot", tab_id)
            snap = snapshot["result"]
            name_input = next(item for item in snap["elements"] if item["name"] == "Name")
            fill = await command("fill_initial", "fill", tab_id, {
                "ref": name_input["ref"], "document_version": snap["document_version"],
                "value": "EmptyOS",
            })
            if not fill.get("ok") and fill.get("error") == "stale_ref":
                snap = (await command("snapshot_retry", "snapshot", tab_id))["result"]
                name_input = next(item for item in snap["elements"] if item["name"] == "Name")
                fill = await command("fill", "fill", tab_id, {
                    "ref": name_input["ref"], "document_version": snap["document_version"],
                    "value": "EmptyOS",
                })
            else:
                self.results["fill"] = fill
            await command("scroll", "scroll", tab_id, {"delta_y": 180})
            await command("screenshot", "screenshot", tab_id)
            await command("password", "fill", tab_id, {
                "ref": next(item["ref"] for item in snap["elements"] if item["name"] == "Password"),
                "document_version": snap["document_version"], "value": "never-send-this",
            })
            self.done.set()
            await receive("browser.disarm")
            self.disarmed.set()
        except Exception as exc:  # surfaced with the assertion below
            self.error = repr(exc)
            self.done.set()


def test_browser_session_arm_snapshot_act_screenshot_and_disarm(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    uvicorn = pytest.importorskip("uvicorn")
    from fastapi.responses import HTMLResponse

    report_path = os.environ.get("EOS_UI_WALK_DIR", "")
    report_dir = Path(report_path).resolve() if report_path else None
    if report_dir:
        report_dir.mkdir(parents=True, exist_ok=True)

    walk = _BridgeWalk()
    app = fastapi.FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/page", response_class=HTMLResponse)
    async def page():
        return """<!doctype html><html><head><title>Bridge test</title></head><body>
        <main style="height:1400px"><h1>Browser bridge</h1>
        <label>Name <input aria-label="Name"></label>
        <label>Password <input type="password" aria-label="Password"></label>
        <button id="plain">Preview</button></main></body></html>"""

    @app.websocket("/ws")
    async def websocket_route(websocket: fastapi.WebSocket):
        await walk.run(websocket)

    port = _free_port()
    host = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        threading.Event().wait(0.05)
    assert server.started

    source = Path(__file__).parents[1] / "tools" / "chrome-extension"
    extension = tmp_path / "extension"
    shutil.copytree(source, extension)
    manifest_path = extension / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Test-only grant to exercise captureVisibleTab in headless Chromium. The
    # public manifest instead relies on activeTab from invoking the extension
    # on that tab and returns screenshot_permission_required without it.
    manifest["host_permissions"] += [host + "/*", "<all_urls>"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        with playwright.sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                str(tmp_path / "profile"), headless=True, channel="chromium",
                args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"],
            )
            try:
                workers = context.service_workers
                worker = workers[0] if workers else context.wait_for_event("serviceworker", timeout=10_000)
                extension_id = worker.url.split("/")[2]
                worker.evaluate("async host => chrome.storage.sync.set({host})", host)

                target = context.new_page()
                target.goto(host + "/page")
                panel = context.new_page()
                panel.set_viewport_size({"width": 420, "height": 820})
                panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
                worker.evaluate("""async url => {
                    const tab = (await chrome.tabs.query({})).find(item => item.url === url);
                    await chrome.tabs.update(tab.id, {active:true});
                }""", host + "/page")
                panel.locator("#share-current").click()
                playwright.expect(panel.locator("#tab-count")).to_contain_text("Sharing 1 tab")
                panel.locator("#browser-arm").click()
                try:
                    playwright.expect(panel.locator("#browser-session-state")).to_contain_text("Connected", timeout=10_000)
                except AssertionError as exc:
                    raise AssertionError({
                        "panel_status": panel.locator("#status").text_content(),
                        "session": worker.evaluate("chrome.storage.session.get()"),
                        "init_error": worker.evaluate("globalThis.__EOS_BROWSER_INIT_ERROR__ || ''"),
                        "walk_error": walk.error,
                    }) from exc
                if report_dir:
                    panel.screenshot(path=str(report_dir / "01-armed-panel.png"))

                assert walk.done.wait(15), "fake daemon did not finish command sequence"
                assert not walk.error, walk.results
                assert walk.results["snapshot"]["ok"] is True
                assert walk.results["fill"]["ok"] is True, walk.results["fill"]
                assert walk.results["scroll"]["ok"] is True
                assert walk.results["screenshot"]["ok"] is True, walk.results["screenshot"]
                assert walk.results["screenshot"]["result"]["data_url"].startswith("data:image/png;base64,")
                assert walk.results["password"]["ok"] is False
                assert walk.results["password"]["error"] == "blocked_sensitive_field"
                assert target.locator("input[aria-label=Name]").input_value() == "EmptyOS"
                assert target.locator("input[aria-label=Password]").input_value() == ""
                if report_dir:
                    target.screenshot(path=str(report_dir / "02-directed-actions.png"))

                panel.locator("#browser-disarm").click()
                assert walk.disarmed.wait(5)
                playwright.expect(panel.locator("#browser-session-state")).to_have_text("Disarmed")
                if report_dir:
                    panel.screenshot(path=str(report_dir / "03-disarmed-panel.png"))
                    (report_dir / "report.md").write_text(
                        "# Browser Session UI walk\n\n"
                        "- PASS — selected tab armed and showed Connected.\n"
                        "- PASS — daemon-directed snapshot, fill, scroll, and screenshot returned structured results.\n"
                        "- PASS — password filling was rejected and the value stayed empty.\n"
                        "- PASS — explicit Disarm revoked the session.\n",
                        encoding="utf-8",
                    )
            finally:
                context.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
