"""System tests: Mark It Up — the review page's triage surface.

The review data is served from a fixture through Playwright route mocks, so
these exercise the page's own behaviour (keys, counts, export link, delete)
without capturing a site or spending a model call, and never touch a real
review. Writes the page makes (PATCH / POST / DELETE) are intercepted and
recorded with their bodies, never forwarded.
"""

import copy
import json
import re

import pytest

RID = "rv-ui-test"
PAUSED = "rv-ui-paused"

REVIEW = {
    "id": RID, "title": "Fixture review", "source": "https://fixture.test/",
    "rubric": "site", "created": "2026-10-03T10:00:00", "summary": "",
    "anchoring": {"comments": 4, "anchored": 4},
    "shots": [
        {"id": "s1", "title": "Home", "url": "https://fixture.test/", "image": "s1.png",
         "viewport": {"w": 1440, "h": 900}, "origin": {"x": 0, "y": 0, "w": 1440, "h": 1800}},
        {"id": "s2", "title": "Contact", "url": "https://fixture.test/c", "image": "s2.png",
         "viewport": {"w": 1440, "h": 900}, "origin": {"x": 0, "y": 0, "w": 1440, "h": 1800}},
    ],
    # Rail order: 1, 2 on s1 · 3, 4 on s2. Comment 2 starts resolved.
    "comments": [
        {"n": 1, "shot_id": "s1", "tag": "fix", "title": "First", "body": "a", "status": "open",
         "x": 0.2, "y": 0.2, "anchor": "dom"},
        {"n": 2, "shot_id": "s1", "tag": "keep", "title": "Second", "body": "b", "status": "dismissed",
         "x": 0.4, "y": 0.4, "anchor": "dom"},
        {"n": 3, "shot_id": "s2", "tag": "fix", "title": "Third", "body": "c", "status": "open",
         "x": 0.3, "y": 0.3, "anchor": "dom"},
        {"n": 4, "shot_id": "s2", "tag": "fix", "title": "Fourth", "body": "d", "status": "open",
         "x": 0.6, "y": 0.6, "anchor": "dom"},
    ],
}


def _mock(page):
    """Serve the fixture; record every write the page attempts, with its body."""
    state = {"review": copy.deepcopy(REVIEW), "calls": []}

    def handle(route):
        req = route.request
        path = re.sub(r"^https?://[^/]+", "", req.url).split("?")[0]
        body = None
        if req.method in ("POST", "PATCH"):
            try:
                body = req.post_data_json
            except Exception:  # noqa: BLE001 — a non-JSON body is recorded as None
                body = None
        state["calls"].append((req.method, path, body))
        ok = lambda b: route.fulfill(status=200, content_type="application/json",  # noqa: E731
                                     body=json.dumps(b))
        if req.method == "GET" and path == "/markitup/api/reviews":
            cs = state["review"]["comments"]
            return ok({"reviews": [{
                "id": RID, "title": "Fixture review", "source": "https://fixture.test/",
                "rubric": "site", "shots": 2, "comments": len(cs),
                "open": sum(1 for c in cs if c["status"] == "open"),
                "status": "ready", "created": "2026-10-03T10:00:00"}]})
        if req.method == "GET" and path == f"/markitup/api/reviews/{RID}":
            return ok({"review": state["review"], "has_proposal": False})
        if req.method == "GET" and path == f"/markitup/api/run/{RID}/status":
            return ok({"run": None})
        # A run paused at the approval gate: no review.json yet.
        if req.method == "GET" and path == f"/markitup/api/reviews/{PAUSED}":
            return ok({"error": f"no review with id {PAUSED!r}"})
        if req.method == "GET" and path == f"/markitup/api/run/{PAUSED}/status":
            return ok({"run": {"status": "paused", "stage": "discover", "completed": ["discover"],
                               "inputs": {"url": "https://paused.test/", "title": "Paused run"},
                               "results": {"discover": {"views": []}}}})
        m = re.match(rf"/markitup/api/reviews/{RID}/comments/(\d+)$", path)
        if req.method == "PATCH" and m:
            n = int(m.group(1))
            for c in state["review"]["comments"]:
                if c["n"] == n:
                    c.update(body or {})
                    return ok({"comment": c})
        if req.method == "POST" and path == f"/markitup/api/reviews/{RID}/comments":
            new = dict(body or {}, n=99, status="open", author="human",
                       shot_id=(body or {}).get("shot_id", "s1"), tag="fix", title="Added")
            return ok({"comment": new})
        if req.method == "POST" and path == f"/markitup/api/reviews/{RID}/export":
            fmt = (body or {}).get("format", "markdown")
            ext = "pdf" if fmt == "pdf" else "md"
            return ok({"ok": True, "format": fmt, "path": f"D:/vault/x/{RID}.{ext}",
                       "vault_path": f"30_Resources/EmptyOS/markitup/outputs/{RID}.{ext}"})
        if req.method == "POST" and path == f"/markitup/api/reviews/{RID}/tasks":
            b = body or {}
            ready = [c for c in state["review"]["comments"]
                     if c["status"] == "accepted" and not c.get("task_sent")
                     and (not b.get("comments") or c["n"] in b["comments"])]
            plan = [{"n": c["n"], "title": c["title"], "text": f"TASK-LINE {c['title']} / comment {c['n']}"}
                    for c in ready]
            if b.get("dry_run", True):
                return ok({"ok": True, "dry_run": True, "tasks": plan, "skipped": []})
            for c in ready:
                c["task_sent"] = {"project": b.get("project") or "inbox", "at": "now"}
            return ok({"ok": True, "dry_run": False, "sent": plan, "skipped": []})
        if req.method == "DELETE" and path.startswith("/markitup/api/reviews/"):
            return ok({"ok": True})
        if req.method == "POST" and path.endswith("/rereview"):
            return ok({"error": "the test never re-reviews"})
        if "/shots/" in path:
            return route.fulfill(status=404, body="")
        return route.continue_()

    page.route("**/markitup/api/**", handle)
    return state


def _patches(state):
    """[(n, body)] for every comment PATCH the page sent, in order."""
    out = []
    for m, p, b in state["calls"]:
        hit = re.search(r"/comments/(\d+)$", p)
        if m == "PATCH" and hit:
            out.append((int(hit.group(1)), b))
    return out


def _writes(state, method):
    return [p for (m, p, _b) in state["calls"] if m == method]


def _open(page, base_url, rid=RID, ready=".mk-note"):
    page.goto(f"{base_url}/markitup/#{rid}", wait_until="domcontentloaded")
    page.wait_for_selector(ready, timeout=10000)
    # The keys arrive with eos-keys.js, which eos.js injects asynchronously.
    page.wait_for_function("!!(window.EOS && EOS.keys && EOS.keys.register)", timeout=10000)
    page.wait_for_timeout(200)


def _selected(page, n):
    page.wait_for_selector(f'.mk-note.on[data-note="{n}"]', timeout=3000)


def _meta_says(page, text):
    page.wait_for_function(
        "t => document.getElementById('mk-d-meta').textContent.indexOf(t) !== -1", arg=text, timeout=5000)


@pytest.mark.interactive
class TestMarkitupTriageUI:
    def test_list_card_says_how_far_triage_got(self, page, base_url, page_errors):
        from page_helpers import assert_no_js_errors
        _mock(page)
        page.goto(f"{base_url}/markitup/", wait_until="domcontentloaded")
        card = page.locator(f'.mk-card[data-open="{RID}"]')
        card.wait_for(state="visible", timeout=10000)
        assert "1 of 4 resolved" in card.inner_text()
        assert_no_js_errors(page_errors)

    def test_a_accepts_d_dismisses_and_a_again_reopens(self, page, base_url, page_errors):
        from page_helpers import assert_no_js_errors
        state = _mock(page)
        _open(page, base_url)
        _meta_says(page, "1 of 4 resolved")
        page.keyboard.press("j"); _selected(page, 1)
        page.keyboard.press("a"); _meta_says(page, "2 of 4 resolved")
        page.keyboard.press("a"); _meta_says(page, "1 of 4 resolved")     # toggled back
        page.keyboard.press("d"); _meta_says(page, "2 of 4 resolved")
        assert _patches(state) == [(1, {"status": "accepted"}), (1, {"status": "open"}),
                                   (1, {"status": "dismissed"})]
        assert_no_js_errors(page_errors)

    def test_j_and_k_walk_across_views_in_rail_order(self, page, base_url):
        _mock(page)
        _open(page, base_url)
        for n in (1, 2, 3, 4):
            page.keyboard.press("j"); _selected(page, n)
        page.keyboard.press("j"); _selected(page, 4)      # stays on the last
        page.keyboard.press("k"); _selected(page, 3)       # back across the view boundary
        page.keyboard.press("k"); _selected(page, 2)

    def test_j_steps_to_the_next_comment_after_one_the_filter_just_hid(self, page, base_url):
        """With "Hide resolved" on, accepting the selected comment hides it;
        j must go to its neighbour, not back to the first comment."""
        _mock(page)
        _open(page, base_url)
        page.click('[data-toggle="resolved"]')             # hides comment 2
        page.keyboard.press("j"); _selected(page, 1)
        page.keyboard.press("j"); _selected(page, 3)
        page.keyboard.press("a")                            # 3 accepted → hidden
        page.wait_for_selector('.mk-note[data-note="3"]', state="detached", timeout=5000)
        page.keyboard.press("j"); _selected(page, 4)

    def test_modifier_chords_never_act(self, page, base_url):
        """Ctrl+A is select-all and Ctrl+D a bookmark — never accept/dismiss."""
        state = _mock(page)
        _open(page, base_url)
        page.keyboard.press("j"); _selected(page, 1)
        for chord in ("Control+a", "Control+d", "Alt+d", "Meta+a", "Control+e", "Control+j"):
            page.keyboard.press(chord)
        page.wait_for_timeout(300)
        assert _patches(state) == []
        assert page.locator("#eos-form-title").count() == 0
        _selected(page, 1)

    def test_e_opens_the_editor_and_keys_stay_out_of_it(self, page, base_url):
        state = _mock(page)
        _open(page, base_url)
        page.keyboard.press("j"); _selected(page, 1)
        page.keyboard.press("e")
        page.wait_for_selector("#eos-form-title", timeout=3000)
        page.locator("#eos-form-title").press("End")
        page.keyboard.type("ad")                      # typing, not accept/dismiss
        assert _patches(state) == []
        assert page.input_value("#eos-form-title").endswith("ad")

    def test_keys_do_nothing_behind_a_dialog(self, page, base_url):
        """The delete confirm takes focus on a button, not an input, so the
        global input guard does not apply."""
        state = _mock(page)
        _open(page, base_url)
        page.keyboard.press("j"); _selected(page, 1)
        page.click('[data-act="delete-review"]')
        page.wait_for_selector("#eos-confirm-no", timeout=3000)
        page.locator("#eos-confirm-no").focus()
        for k in "adj":
            page.keyboard.press(k)
        page.wait_for_timeout(300)
        assert _patches(state) == []

    def test_keys_do_nothing_behind_the_note_reader(self, page, base_url):
        """The export link opens the note reader — not a modal — and a key
        there must not act on the comment hidden behind it."""
        state = _mock(page)
        _open(page, base_url)
        page.keyboard.press("j"); _selected(page, 1)
        page.click('[data-export="markdown"]')
        page.click("#mk-exported [data-view-note]")
        page.wait_for_selector("#eos-note-overlay.open", timeout=5000)
        page.locator("body").press("a")
        page.keyboard.press("d")
        page.wait_for_timeout(300)
        assert _patches(state) == []

    def test_keys_do_nothing_behind_the_help(self, page, base_url):
        state = _mock(page)
        _open(page, base_url)
        page.keyboard.press("j"); _selected(page, 1)
        page.keyboard.press("?")
        page.wait_for_selector("#eos-help-overlay.show", timeout=3000)
        page.keyboard.press("a")
        page.wait_for_timeout(300)
        assert _patches(state) == []

    def test_keys_do_nothing_on_the_list(self, page, base_url):
        state = _mock(page)
        page.goto(f"{base_url}/markitup/", wait_until="domcontentloaded")
        page.wait_for_selector(".mk-card", timeout=10000)
        page.wait_for_function("!!(window.EOS && EOS.keys && EOS.keys.register)", timeout=10000)
        for k in "jada":
            page.keyboard.press(k)
        page.wait_for_timeout(300)
        assert _patches(state) == []

    def test_the_count_includes_a_comment_just_added(self, page, base_url):
        _mock(page)
        _open(page, base_url)
        _meta_says(page, "4 comments")
        page.click('[data-act="add-view"]')
        page.wait_for_selector("#eos-form-title", timeout=3000)
        page.fill("#eos-form-title", "Added by hand")
        page.locator("#eos-modal-overlay button", has_text="Add comment").click()
        _meta_says(page, "5 comments")

    def test_export_shows_the_file_as_a_vault_link(self, page, base_url):
        _mock(page)
        _open(page, base_url)
        page.click('[data-export="markdown"]')
        link = page.locator("#mk-exported [data-view-note]")
        link.wait_for(state="visible", timeout=5000)
        assert link.get_attribute("data-view-note") == f"30_Resources/EmptyOS/markitup/outputs/{RID}.md"
        link.click()                                   # opens the reader, not an external app
        page.wait_for_selector("#eos-note-overlay.open", timeout=5000)

    def test_delete_asks_then_deletes_and_never_rereviews(self, page, base_url):
        state = _mock(page)
        _open(page, base_url)
        page.click('[data-act="delete-review"]')
        page.wait_for_selector("#eos-confirm-no", timeout=3000)
        page.click("#eos-confirm-no")                 # cancel: nothing happens
        page.wait_for_timeout(300)
        assert _writes(state, "DELETE") == []
        page.click('[data-act="delete-review"]')
        page.click("#eos-confirm-yes")
        page.wait_for_selector("#mk-list-view:not([hidden])", timeout=5000)
        assert _writes(state, "DELETE") == [f"/markitup/api/reviews/{RID}"]
        assert not [p for p in _writes(state, "POST") if p.endswith("/rereview")], \
            "the Delete button fell through to re-review"

    def test_a_paused_run_can_be_deleted(self, page, base_url):
        """A run waiting at the approval gate has no review yet — the run most
        worth discarding. Delete must still work on it."""
        state = _mock(page)
        _open(page, base_url, rid=PAUSED, ready="#mk-approve:not([hidden])")
        page.click('[data-act="delete-review"]')
        page.click("#eos-confirm-yes")
        page.wait_for_selector("#mk-list-view:not([hidden])", timeout=5000)
        assert _writes(state, "DELETE") == [f"/markitup/api/reviews/{PAUSED}"]


def _mock_projects(page):
    page.route("**/projects/api/projects*", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps([{"id": "site-refresh"}, {"id": "inbox"}])))   # the real shape: a bare array


@pytest.mark.interactive
class TestMarkitupSendToTasks:
    def test_preview_then_create_sends_the_accepted_comment_once(self, page, base_url, page_errors):
        from page_helpers import assert_no_js_errors
        state = _mock(page)
        _mock_projects(page)
        _open(page, base_url)
        page.keyboard.press("j"); _selected(page, 1)
        page.keyboard.press("a"); _meta_says(page, "2 of 4 resolved")
        page.click('[data-act="send-tasks"]')
        plan = page.locator(".mk-task-plan li")
        plan.first.wait_for(state="visible", timeout=5000)
        # The exact task line the server built — not the comment title.
        assert plan.count() == 1 and plan.first.inner_text() == "TASK-LINE First / comment 1"
        assert [b for m, p, b in state["calls"] if p.endswith("/tasks")] == [{"dry_run": True}]
        page.fill("#mk-task-project", "site-refresh")
        page.locator("#eos-modal-overlay button", has_text="Create 1 task").click()
        page.wait_for_selector('.mk-note[data-note="1"] .mk-state:has-text("→ task")', timeout=5000)
        sends = [b for m, p, b in state["calls"] if p.endswith("/tasks") and b and not b.get("dry_run", True)]
        assert sends == [{"dry_run": False, "project": "site-refresh", "comments": [1]}]
        assert not [p for m, p, b in state["calls"] if p.endswith("/rereview")]
        assert_no_js_errors(page_errors)

    def test_nothing_accepted_opens_no_dialog(self, page, base_url):
        state = _mock(page)
        _mock_projects(page)
        _open(page, base_url)
        page.click('[data-act="send-tasks"]')
        page.wait_for_timeout(800)
        assert page.locator(".mk-task-plan").count() == 0
        assert not [b for m, p, b in state["calls"] if p.endswith("/tasks") and b and not b.get("dry_run", True)]
        assert not [p for m, p, b in state["calls"] if p.endswith("/rereview")]

    def test_an_unknown_project_is_announced_before_it_is_created(self, page, base_url):
        _mock(page)
        _mock_projects(page)
        _open(page, base_url)
        page.keyboard.press("j"); _selected(page, 1)
        page.keyboard.press("a"); _meta_says(page, "2 of 4 resolved")
        page.click('[data-act="send-tasks"]')
        page.wait_for_selector("#mk-task-project", timeout=5000)
        page.fill("#mk-task-project", "site-refresh")
        assert page.locator("#mk-task-newproj").is_hidden()
        page.fill("#mk-task-project", "Site-Refresh")           # projects matches case-insensitively
        assert page.locator("#mk-task-newproj").is_hidden()
        page.fill("#mk-task-project", "site-refrsh")
        assert page.locator("#mk-task-newproj").is_visible()

    def test_a_refused_project_keeps_the_dialog_open(self, page, base_url):
        """A refusal before anything was sent must not throw away the typed name."""
        _mock(page)
        _mock_projects(page)
        page.route(f"**/markitup/api/reviews/{RID}/tasks", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"ok": True, "dry_run": True, "skipped": [],
                             "tasks": [{"n": 1, "title": "First", "text": "TASK-LINE"}]})
            if (r.request.post_data_json or {}).get("dry_run", True)
            else json.dumps({"error": "project: not allowed", "sent": []})))
        _open(page, base_url)
        page.click('[data-act="send-tasks"]')
        page.wait_for_selector("#mk-task-project", timeout=5000)
        page.fill("#mk-task-project", "bad name.")
        page.locator("#eos-modal-overlay button", has_text="Create 1 task").click()
        page.wait_for_timeout(600)
        assert page.locator("#mk-task-project").input_value() == "bad name."
