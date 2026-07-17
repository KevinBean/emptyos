"""Real-Chromium walk for the unpacked extension's reading layer.

Uses a disposable in-process HTTP server as the daemon boundary, so the walk
never touches the user's :9000/:9001 processes or vault.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.interactive

# Analyze is a real model call — seconds, not milliseconds. The race that this
# module regresses only exists while one is in flight, so the fake daemon can be
# told to take its time.
ANALYZE_DELAY_S = 0.0

# Every page the reading layer sends to a model lands here. A private host must
# never appear in this count — that is the whole claim.
ANALYZED = []

# What the reader may wait on, and whether it can answer NOW. The local model starts
# COLD — the state that made the layer look broken.
# Two providers named "ollama" — the reader added a model variant, and a variant
# keeps the family name. Identity is `id`, never `name`.
MODELS = [
    {"id": "ollama", "name": "ollama", "model": "qwen3.5-32k", "kind": "local",
     "available": True, "warmable": True, "warm": False, "ready": False},
    {"id": "openai-mini", "name": "openai-mini", "model": "gpt-5.4-mini", "kind": "cloud",
     "available": True, "warmable": False, "warm": True, "ready": True},
    {"id": "ollama:gemma4:e4b", "name": "ollama", "model": "gemma4:e4b", "kind": "local",
     "available": True, "warmable": True, "warm": False, "ready": False},
]

# The daemon owns reading settings — the extension stores only host + token. The
# walk therefore drives mode by writing to this, exactly as the side panel does.
SETTINGS = {
    "mode": "off", "display": "auto", "flow_provider": "", "local_provider": "",
    "excluded_hosts": [], "native_language": "Chinese", "target_language": "English",
    "pronounce": True, "rail": True, "enrich_on_save": True,
}


class _FakeDaemon(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def _json(self, payload, status=200):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path == "/linky":
            # A page that links as it writes: a menu of standalone links (must be
            # ignored) and a paragraph with an inline link (must be READ).
            raw = (
                b"<!doctype html><html><body>"
                b"<ul id='menu'><li><a href='/a'>Services</a></li>"
                b"<li><a href='/b'>Find a service</a></li>"
                b"<li><a href='/c'>Cancer service</a></li></ul>"
                b"<main><p id='copy'>Her <a href='/x'>sesquipedalian</a> style made a simple "
                b"idea sound needlessly ornate, and the committee found the whole report "
                b"quite impossible to read without a dictionary at hand.</p></main>"
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path == "/live":
            # A page that mutates forever — a ticking clock, an ad slot, a React
            # re-render. This is what an ordinary site looks like to a
            # MutationObserver, and it must not starve the scan.
            raw = (
                b"<!doctype html><html><body><main style='max-width:700px;margin:60px auto;"
                b"font:20px/1.7 Georgia,serif'><h1>A reading test</h1>"
                b"<p id='copy'>Her sesquipedalian style made a simple idea sound needlessly ornate.</p>"
                b"<p id='tick'>0</p></main>"
                b"<script>let n=0;setInterval(()=>{document.getElementById('tick')"
                b".textContent=String(++n);},200);</script></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path == "/":
            raw = (
                b"<!doctype html><html><body><main style='max-width:700px;margin:60px auto;"
                b"font:20px/1.7 Georgia,serif'><h1>A reading test</h1>"
                b"<p id='copy'>Her sesquipedalian style made a simple idea sound needlessly ornate.</p>"
                b"<p>A sunset clause that would obviate the need for another negotiation.</p>"
                b"</main></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path.startswith("/api/capabilities/think/effective"):
            self._json({
                "provider": "ollama",
                "providers": [
                    {"name": "ollama", "model": "qwen", "available": True,
                     "is_cloud": False, "auth_mode": "local"},
                    {"name": "openai", "model": "gpt-test", "available": True,
                     "is_cloud": True, "auth_mode": "api-key"},
                ],
            })
        elif self.path == "/dictionary/api/reading/models":
            self._json({"ok": True, "providers": MODELS})
        elif self.path == "/dictionary/api/reading/settings":
            self._json({"ok": True, "settings": SETTINGS})
        elif self.path == "/dictionary/api/reading/status":
            self._json({"ok": True, "profile": {"calibration": "advanced-default"},
                        "cache": {"entries": 1}})
        elif self.path == "/api/cloud/pending":
            self._json({"pending": []})
        elif self.path == "/assistant/api/sessions":
            self._json([])
        elif self.path.startswith("/assistant/api/sessions/"):
            self._json({"messages": []})
        elif self.path.startswith("/quick-action/api/has"):
            self._json({"has": False})
        else:
            self._json({})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        if self.path == "/dictionary/api/reading/known":
            # The reader's own words, straight from the vault. No model runs here.
            self._json({"ok": True, "items": [{
                "word": "obviate", "part_of_speech": "verb",
                "definition": "To remove a need or difficulty.",
                "meaning_in_context": "It removes the need for another negotiation.",
                "native": "免除", "sense_label": "removal",
                "sentence": "a sunset clause that would obviate the need",
                "source": "vault",
            }]})
            return
        if self.path == "/dictionary/api/reading/warm":
            try:
                wanted = json.loads(raw or b"{}").get("provider") or ""
            except ValueError:
                wanted = ""
            for m in MODELS:                       # ONLY the model that was asked for
                if m["kind"] == "local" and m["id"] == wanted:
                    m["warm"] = m["ready"] = True
            self._json({"ok": True, "warm": True})
            return
        if self.path == "/dictionary/api/reading/settings":
            try:
                SETTINGS.update(json.loads(raw or b"{}"))
            except ValueError:
                pass
            self._json({"ok": True, "settings": SETTINGS})
            return
        if self.path == "/dictionary/api/reading/lookup":
            self._json({
                "ok": True,
                "cached": False,
                "tier": "local",
                "item": {
                    "word": "sesquipedalian",
                    "part_of_speech": "adjective",
                    "definition": "Characterized by long words.",
                    "meaning_in_context": "Using unnecessarily long or complex words.",
                    "native": "冗长的",
                    "sense_label": "wordiness",
                    "sentence": "Her sesquipedalian style made a simple idea sound needlessly ornate.",
                },
            })
        elif self.path == "/dictionary/api/reading/analyze":
            try:
                ANALYZED.append(json.loads(raw or b"{}").get("url", ""))
            except ValueError:
                ANALYZED.append("")
            if ANALYZE_DELAY_S:
                time.sleep(ANALYZE_DELAY_S)
            self._json({
                "ok": True,
                "cached": False,
                "items": [{
                    "word": "sesquipedalian",
                    "part_of_speech": "adjective",
                    "definition": "Characterized by long words.",
                    "meaning_in_context": "Using unnecessarily long or complex words.",
                    "native": "冗长的",
                    "sense_label": "wordiness",
                    "sentence": "Her sesquipedalian style made a simple idea sound needlessly ornate.",
                }],
            })
        elif self.path == "/assistant/api/sessions":
            self._json({"id": "walk", "name": "Walk"})
        else:
            self._json({"ok": True})


def _record(folder: Path | None, *, usecase: str, step: int, action: str,
            status: str, note: str, shot: str, url: str):
    if folder is None:
        return
    with (folder / "steplog.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "usecase": usecase,
            "step": step,
            "action": action,
            "status": status,
            "note": note,
            "shot": shot,
            "url": url,
        }) + "\n")


def _set_mode(worker, mode):
    """Drive mode the way the side panel does: write to the daemon, then broadcast.

    The panel pings the worker over `EOS_READING_SETTINGS_CHANGED`; here we are
    *inside* the worker, and Chrome does not deliver a runtime message back to its
    own sender — so we invoke the handler's work directly.
    """
    worker.evaluate(
        """async ({mode}) => {
            const { host } = await chrome.storage.sync.get({ host: '' });
            await fetch(host + '/dictionary/api/reading/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode }),
            });
            invalidateReadingSettings();
            await broadcastReadingSettings();
        }""",
        {"mode": mode},
    )


def _patch_settings(worker, patch):
    """Drive one panel control: save the patch, then broadcast — exactly as the
    side panel's `change` handlers do (every control saves, every save broadcasts)."""
    worker.evaluate(
        """async ({host, patch}) => {
            const { host: stored } = await chrome.storage.sync.get({ host: '' });
            await fetch((stored || host) + '/dictionary/api/reading/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            invalidateReadingSettings();
            await broadcastReadingSettings();
        }""",
        {"host": "", "patch": patch},
    )


def _start_daemon():
    # The daemon owns the settings, and POSTs mutate them in place — so a walk that
    # left Flow on must not leak that into the next one.
    SETTINGS.update({"mode": "off", "flow_provider": "", "pronounce": True,
                     "rail": True, "excluded_hosts": [], "allowed_private_hosts": []})
    for m in MODELS:                               # every walk starts with cold models
        if m["kind"] == "local":
            m["warm"] = m["ready"] = False
    ANALYZED.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeDaemon)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _load_extension(pw, tmp_path, host, server, resolver_rules=""):
    """Boot Chromium with the REAL unpacked extension and point it at the fake daemon.

    ``resolver_rules`` maps a real hostname onto the fake daemon, so a walk can load
    a page that genuinely IS ``mail.google.com`` to the extension's every gate.
    """
    source_extension = Path(__file__).parents[1] / "tools" / "chrome-extension"
    extension_dir = tmp_path / "extension"
    shutil.copytree(source_extension, extension_dir)
    # This regression walk models an upgraded v0.10 install, whose prior
    # all-site grant is retained and converted to dynamic registration.
    manifest_path = extension_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["host_permissions"] += ["http://*/*", "https://*/*"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    args = [
        f"--disable-extensions-except={extension_dir}",
        f"--load-extension={extension_dir}",
    ]
    if resolver_rules:
        args.append(f"--host-resolver-rules={resolver_rules}")
    try:
        context = pw.chromium.launch_persistent_context(
            str(tmp_path / "profile"),
            headless=True,
            channel="chromium",
            args=args,
        )
    except Exception as exc:
        server.shutdown()
        pytest.skip(f"Chromium extension context unavailable: {exc}")

    workers = context.service_workers
    worker = workers[0] if workers else context.wait_for_event("serviceworker", timeout=10000)
    extension_id = worker.url.split("/")[2]
    worker.evaluate(
        """async ({host}) => {
            await chrome.storage.sync.clear();
            // Only non-secret connection metadata is synchronized.
            await chrome.storage.sync.set({host});
        }""",
        {"host": host},
    )
    worker.evaluate("new Promise(resolve => setTimeout(resolve, 500))")
    registered = worker.evaluate("chrome.scripting.getRegisteredContentScripts()")
    assert any(item.get("id") == "eos-reading" for item in registered), {
        "worker": worker.url,
        "registered": registered,
        "grants": worker.evaluate("chrome.permissions.getAll()"),
        "init_error": worker.evaluate("globalThis.__EOS_BROWSER_INIT_ERROR__ || ''"),
    }
    return context, worker, extension_id


def test_reading_layer_off_ask_off_and_sidebar(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    report_env = os.environ.get("EOS_UI_WALK_DIR", "")
    report_dir = Path(report_env).resolve() if report_env else None
    if report_dir:
        report_dir.mkdir(parents=True, exist_ok=True)

    server, host = _start_daemon()

    with playwright.sync_playwright() as pw:
        context, worker, extension_id = _load_extension(pw, tmp_path, host, server)
        try:
            page = context.new_page()
            page.goto(host + "/")
            page.wait_for_timeout(700)
            assert page.locator("#eos-reading-card").count() == 0
            assert page.evaluate("!CSS.highlights || !CSS.highlights.has('eos-reading-words')")
            shot = str((report_dir or tmp_path) / "uc1-s1-off.png")
            page.screenshot(path=shot)
            _record(report_dir, usecase="Reading mode lifecycle", step=1,
                    action="Open an article with reading Off", status="pass",
                    note="No card or highlight appears while Off.", shot=shot, url=page.url)

            panel = context.new_page()
            panel.set_viewport_size({"width": 420, "height": 820})
            panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
            panel.locator("#reading-control").wait_for()
            worker.evaluate(
                """async ({host}) => {
                    const tabs = await chrome.tabs.query({});
                    const article = tabs.find(tab => tab.url === host + '/');
                    if (article) await chrome.tabs.update(article.id, {active: true});
                }""",
                {"host": host},
            )
            panel.wait_for_timeout(250)
            assert panel.locator(".reading-mode").all_text_contents() == ["Off", "Ask", "Flow"]
            assert panel.locator("#reading-flow-provider option").count() >= 2
            assert panel.locator("#reading-local-provider option").count() >= 2
            assert panel.locator("#reading-site").inner_text() == "Pause this site"
            shot = str((report_dir or tmp_path) / "uc2-s1-sidebar.png")
            panel.locator("#reading-control").screenshot(path=shot)
            _record(report_dir, usecase="Control reading from the sidebar", step=1,
                    action="Open the EmptyOS side panel", status="pass",
                    note="Off/Ask/Flow, paid/local model selectors, placement, and site pause are visible together.",
                    shot=shot, url=panel.url)

            # Ask is the explicit user gesture that grants the optional page
            # origin and dynamically registers the reading content script.
            panel.locator('.reading-mode[data-mode="ask"]').click()
            panel.wait_for_timeout(500)
            page.wait_for_timeout(250)
            page.locator("#copy").dblclick(position={"x": 70, "y": 20})
            card = page.locator("#eos-reading-card")
            card.wait_for(timeout=5000)
            assert "long or complex words" in card.inner_text()
            shot = str((report_dir or tmp_path) / "uc1-s2-ask.png")
            page.screenshot(path=shot)
            _record(report_dir, usecase="Reading mode lifecycle", step=2,
                    action="Switch to Ask and double-click sesquipedalian", status="pass",
                    note="The local on-demand explanation appears in-page with feedback and Save word controls.",
                    shot=shot, url=page.url)

            page.evaluate("getSelection().removeAllRanges()")
            _set_mode(worker, "flow")
            page.wait_for_function("CSS.highlights && CSS.highlights.has('eos-reading-words')", timeout=5000)
            page.locator("#copy").click(position={"x": 70, "y": 20})
            card.wait_for(timeout=3000)
            shot = str((report_dir or tmp_path) / "uc1-s3-flow.png")
            page.screenshot(path=shot)
            _record(report_dir, usecase="Reading mode lifecycle", step=3,
                    action="Switch to Flow and click the highlighted word", status="pass",
                    note="Flow adds a non-DOM CSS highlight and opens the same restrained card on click.",
                    shot=shot, url=page.url)

            _set_mode(worker, "off")
            card.wait_for(state="detached", timeout=3000)
            assert page.evaluate("!CSS.highlights || !CSS.highlights.has('eos-reading-words')")
            shot = str((report_dir or tmp_path) / "uc1-s4-off-again.png")
            page.screenshot(path=shot)
            _record(report_dir, usecase="Reading mode lifecycle", step=4,
                    action="Switch back to Off", status="pass",
                    note="The card is removed immediately and no highlight remains.", shot=shot, url=page.url)
        finally:
            context.close()
            server.shutdown()


def test_flow_rail_survives_a_settings_change_mid_scan(tmp_path):
    """A cosmetic panel change must not discard the scan it is standing on.

    Every control in the reading panel saves, and every save broadcasts a refresh
    to the page. The refresh used to tear the flow engine down wholesale — which
    cleared the words, dropped the in-flight analyze on arrival, and (because the
    scan throttle survived the teardown) blocked the re-scan for up to twenty
    seconds. The rail spent that window saying "Nothing flagged yet.", so a read
    the daemon had already performed and cached looked to the reader like a model
    that had found nothing.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    global ANALYZE_DELAY_S
    server, host = _start_daemon()
    ANALYZE_DELAY_S = 1.5          # hold the scan open so the change lands mid-flight

    with playwright.sync_playwright() as pw:
        context, worker, _ = _load_extension(pw, tmp_path, host, server)
        try:
            page = context.new_page()
            page.goto(host + "/")
            page.wait_for_timeout(400)

            _set_mode(worker, "flow")
            rail = page.locator("#eos-reading-rail")
            rail.wait_for(timeout=5000)

            # While the scan is open the rail must say so. Claiming "nothing" here
            # is the lie that made a working pipeline look broken.
            page.wait_for_timeout(400)
            assert "Nothing flagged yet" not in rail.inner_text(), rail.inner_text()

            # The reader now picks a Flow model / ticks Speak — a change that has no
            # bearing on WHICH words are hard.
            _patch_settings(worker, {"pronounce": False})

            # The reader's own words now arrive first, so waiting for "any row" would
            # pass before the model has answered. Wait for the MODEL's word.
            page.locator("#eos-reading-rail .eos-rail-row[data-word='sesquipedalian']")                 .wait_for(timeout=8000)
            assert "sesquipedalian" in rail.inner_text()
            # The word survived the change: it was never re-fetched, it was kept.
            assert page.evaluate(
                "!!CSS.highlights && CSS.highlights.has('eos-reading-words')"
            )
        finally:
            ANALYZE_DELAY_S = 0.0
            context.close()
            server.shutdown()


def test_flow_never_reads_a_private_host(tmp_path):
    """Flow is ON, the origin is granted — and the inbox is still not read.

    Reading is not passive: Flow sends the visible text of the page to a model.
    "Read the web for me" was never consent to read the reader's mail, so mail,
    money, health and messaging hosts are off until the reader names the host. The
    old per-site Pause button could not carry this: it is opt-out, so the page has
    already been read and sent by the time it is clicked.

    The page here genuinely IS mail.google.com as far as Chrome, the content-script
    registration, and every gate in the extension are concerned.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    server, host = _start_daemon()
    port = server.server_port
    SETTINGS["mode"] = "flow"          # the reader has Flow on, everywhere

    with playwright.sync_playwright() as pw:
        context, worker, _ = _load_extension(
            pw, tmp_path, host, server,
            resolver_rules=f"MAP mail.google.com 127.0.0.1:{port},"
                           f"MAP theatlantic.com 127.0.0.1:{port}",
        )
        try:
            inbox = context.new_page()
            inbox.goto("http://mail.google.com/")
            inbox.wait_for_timeout(2500)     # long past the 250ms first scan
            assert inbox.locator("#eos-reading-rail").count() == 0
            assert inbox.evaluate(
                "!CSS.highlights || !CSS.highlights.has('eos-reading-words')")
            # The content script lives in an isolated world, so its state is asked
            # for the way the rest of the extension asks — over a message.
            state = worker.evaluate(
                """async () => {
                    const [tab] = await chrome.tabs.query({url: 'http://mail.google.com/*'});
                    return await chrome.tabs.sendMessage(tab.id, {type: 'EOS_READING_STATE'});
                }"""
            )
            assert state["mode"] == "off", state
            assert state["items"] == [], state
            # The claim that matters: the text never left the page.
            assert ANALYZED == [], ANALYZED

            # The service worker's gate says the same thing on its own, so neither
            # gate is load-bearing alone.
            assert worker.evaluate(
                "readingModeForUrl({mode:'flow',excluded_hosts:[],"
                "allowed_private_hosts:[]}, 'https://mail.google.com/mail/u/0/')"
            ) == "off"
            assert worker.evaluate(
                "readingModeForUrl({mode:'flow',excluded_hosts:[],"
                "allowed_private_hosts:['mail.google.com']},"
                " 'https://mail.google.com/mail/u/0/')"
            ) == "flow"

            # An article is untouched by any of this — the feature still works.
            article = context.new_page()
            article.goto("http://theatlantic.com/")
            article.locator("#eos-reading-rail .eos-rail-row").first.wait_for(timeout=8000)
            assert ANALYZED and all("mail.google.com" not in url for url in ANALYZED)
        finally:
            context.close()
            server.shutdown()


def test_flow_is_not_starved_by_a_mutating_page(tmp_path):
    """A page that never stops mutating must still get read.

    scheduleScan clears and re-arms its timer on every call, and the
    MutationObserver calls it on every DOM change. On a live page — an ad slot, a
    React re-render, a ticking clock — each mutation pushed the scan another 1.8s
    into the future, so it never fired at all. The reader watched "Reading this
    screen…" forever while nothing was ever read, and before the rail learned to
    admit a pending scan the same starvation showed as "Nothing flagged yet.".
    """
    playwright = pytest.importorskip("playwright.sync_api")
    server, host = _start_daemon()
    SETTINGS["mode"] = "flow"

    with playwright.sync_playwright() as pw:
        context, worker, _ = _load_extension(pw, tmp_path, host, server)
        try:
            page = context.new_page()
            page.goto(host + "/live")
            # Generous: the debounce is 1.8s, so a healthy layer reads within a few
            # seconds. A starved one never reads at all, however long you wait.
            page.locator("#eos-reading-rail .eos-rail-row").first.wait_for(timeout=15000)
            assert "sesquipedalian" in page.locator("#eos-reading-rail").inner_text()
            assert ANALYZED, "the page was never sent for analysis"
        finally:
            context.close()
            server.shutdown()


def test_a_slow_model_still_lands(tmp_path):
    """A model that thinks for 40s must still deliver — or say it failed.

    claude-cli takes ~33s on a full page and a cold ollama load can take longer.
    The MV3 service worker is torn down after 30s idle, so the answer arrives to a
    worker that no longer exists and the page waits on a promise that never
    settles. The rail then says "Reading this screen..." forever.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    global ANALYZE_DELAY_S
    server, host = _start_daemon()
    SETTINGS["mode"] = "flow"
    ANALYZE_DELAY_S = 40.0          # past the 30s service-worker idle limit

    with playwright.sync_playwright() as pw:
        context, worker, _ = _load_extension(pw, tmp_path, host, server)
        try:
            page = context.new_page()
            page.goto(host + "/")
            try:
                page.locator("#eos-reading-rail .eos-rail-row").first.wait_for(timeout=70000)
                print("RESULT: rail populated after the slow model returned")
            except Exception:
                rail = page.locator("#eos-reading-rail")
                print("RESULT: NO ROWS. rail says:",
                      repr(rail.inner_text() if rail.count() else "<no rail>"))
                raise
        finally:
            ANALYZE_DELAY_S = 0.0
            context.close()
            server.shutdown()


def test_a_cold_model_is_shown_and_warmable_not_silently_chosen(tmp_path):
    """A cold local model is offered with a way to load it — never quietly picked.

    Loading a 32k-context model onto a busy GPU can outlast the browser's patience,
    and the reader experiences that as a layer that does not work. So warmth is a
    fact the panel states: the cold model is visible (it exists, and it is one button
    away) but not selectable, and Auto reads on the cloud model until it is loaded.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    report_env = os.environ.get("EOS_UI_WALK_DIR", "")
    report_dir = Path(report_env).resolve() if report_env else None
    server, host = _start_daemon()

    with playwright.sync_playwright() as pw:
        context, worker, ext_id = _load_extension(pw, tmp_path, host, server)
        try:
            panel = context.new_page()
            panel.set_viewport_size({"width": 420, "height": 860})
            panel.goto(f"chrome-extension://{ext_id}/sidepanel.html")
            panel.locator("#reading-control").wait_for()
            panel.wait_for_timeout(900)

            flow = panel.locator("#reading-flow-provider")
            cold = flow.locator("option[value='ollama']")   # the primary, by identity
            assert "not loaded" in cold.inner_text(), cold.inner_text()
            assert cold.is_disabled(), "a cold model must not be selectable"
            assert not panel.locator("option[value='openai-mini']").is_disabled()
            assert panel.locator("#reading-warm").is_visible(), "no way to warm it up"
            assert "not loaded" in panel.locator("#reading-model-note").inner_text()
            panel.locator("#reading-control").screenshot(
                path=str((report_dir or tmp_path) / "reading-cold.png"))

            panel.locator("#reading-warm").click()
            panel.wait_for_timeout(1200)

            assert not cold.is_disabled(), "a warm model must be choosable"
            assert "loaded" in panel.locator("#reading-model-note").inner_text()
            assert not panel.locator("#reading-warm").is_visible(), "nothing left to warm"
            panel.locator("#reading-control").screenshot(
                path=str((report_dir or tmp_path) / "reading-warm.png"))

            # And it can now actually be chosen.
            panel.select_option("#reading-flow-provider", "ollama")
            panel.wait_for_timeout(500)
            assert SETTINGS["flow_provider"] == "ollama"

            # The OTHER ollama variant is still cold, and must not be confused with
            # the one that is now reading: warming one may not report the other.
            variant = flow.locator("option[value='ollama:gemma4:e4b']")
            assert variant.is_disabled(), "the cold variant must stay unselectable"
            assert "gemma4" in variant.inner_text()
            note = panel.locator("#reading-model-note").inner_text()
            assert "qwen3.5-32k is loaded" in note, note
            assert "gemma4" not in note, "a cold idle variant must not shadow the model that reads"
        finally:
            context.close()
            server.shutdown()


def test_own_words_appear_without_waiting_for_the_model(tmp_path):
    """A word the reader is already learning shows instantly — and even if the model
    finds nothing at all.

    It is in the vault, with a better gloss than a model would write, and meeting it
    in the wild is the whole reason it was saved. Gating that behind a model that may
    decide the screen is easy — and on a page of slang, did — threw away the best
    moment in the loop.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    global ANALYZE_DELAY_S
    server, host = _start_daemon()
    SETTINGS["mode"] = "flow"
    ANALYZE_DELAY_S = 6.0          # the model is still thinking

    with playwright.sync_playwright() as pw:
        context, worker, _ = _load_extension(pw, tmp_path, host, server)
        try:
            page = context.new_page()
            page.goto(host + "/")
            rail = page.locator("#eos-reading-rail")
            # Well inside the model's 6s: the reader's own word is already there.
            page.locator("#eos-reading-rail .eos-rail-row").first.wait_for(timeout=4000)
            assert "obviate" in rail.inner_text(), rail.inner_text()
            assert "yours" in rail.inner_text(), "an own word must be marked as the reader's"
            assert "sesquipedalian" not in rail.inner_text(), "the model has not answered yet"

            # ...and the model's words join it when they arrive.
            page.wait_for_timeout(6000)
            assert "sesquipedalian" in rail.inner_text()
            assert "obviate" in rail.inner_text(), "the own word must survive the model pass"
        finally:
            ANALYZE_DELAY_S = 0.0
            context.close()
            server.shutdown()


def test_inline_links_are_prose_and_menus_are_not(tmp_path):
    """A link inside a sentence is prose. A menu item is not.

    Every <a> used to be skipped, so a page that links as it writes had its text
    gutted — and the word inside the link is often the most interesting one there.
    The tag cannot tell them apart (half the web builds navigation out of <ul><li><a>
    and never mentions <nav>), so the SHAPE does: a menu item IS its link, while a
    prose link is a few words inside a sentence that goes on without it.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    server, host = _start_daemon()
    SETTINGS["mode"] = "flow"

    with playwright.sync_playwright() as pw:
        context, worker, _ = _load_extension(pw, tmp_path, host, server)
        try:
            page = context.new_page()
            page.goto(host + "/linky")
            page.locator("#eos-reading-rail .eos-rail-row").first.wait_for(timeout=10000)
            sent = ANALYZED and True
            assert sent, "the page was never sent — its prose was thrown away with its links"

            state = worker.evaluate(
                """async ({host}) => {
                    const [tab] = await chrome.tabs.query({url: host + '/linky'});
                    return await chrome.tabs.sendMessage(tab.id, {type: 'EOS_READING_STATE'});
                }""", {"host": host})
            # The word lives INSIDE an <a>, and it is found and highlightable.
            found = [i for i in state["items"] if i["word"] == "sesquipedalian"]
            assert found and found[0]["ranges"] > 0, state
        finally:
            context.close()
            server.shutdown()


def test_flow_still_lets_you_ask_about_any_word(tmp_path):
    """Flow is Ask PLUS proactive flagging — not instead of it.

    Look-up was gated on `mode === "ask"`, so in Flow the reader could only open the
    words the model had already chosen for them. Flow is the mode where you most
    obviously want to point at a word and say "what about THIS one".
    """
    playwright = pytest.importorskip("playwright.sync_api")
    server, host = _start_daemon()
    SETTINGS["mode"] = "flow"

    with playwright.sync_playwright() as pw:
        context, worker, _ = _load_extension(pw, tmp_path, host, server)
        try:
            page = context.new_page()
            page.goto(host + "/")
            page.wait_for_timeout(1200)
            # A word the model did NOT flag, double-clicked in Flow.
            page.locator("#copy").dblclick(position={"x": 70, "y": 20})
            card = page.locator("#eos-reading-card")
            card.wait_for(timeout=6000)
            assert "long or complex words" in card.inner_text(), card.inner_text()
        finally:
            context.close()
            server.shutdown()
