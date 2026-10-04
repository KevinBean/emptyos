"""System app tests: Notebook — note-first vault workspace.

The module seeds its own notes under ``PLAYWRIGHT-TEST-notebook/`` (via the
platform's /api/vault/write), so every expected value below comes from what
the test wrote — never from the endpoint under test. Seeded files are removed
at teardown when the daemon's vault is the one in emptyos.toml; on a sandbox
member the vault is throwaway. Prefer a sandbox member: each seeded write is a
`vault:changed`, which invalidates the `link` index, and on a large vault its
rebuild can outlast the 120 s the backlinks tests wait (they then skip).

Acceptance criteria → tests (from 30_Resources/EmptyOS/grill/new-app-notebook-2026-09-28.md):
  AC1 Tree lists notes + folders with counts           → test_tree_lists_seeded_folder_exactly, test_tree_root_hides_dot_folders
  AC2 Tree refuses escapes in-band (never 500)         → test_tree_refuses_escape, test_tree_accepts_colon_in_folder_name
  AC3 /notebook/#<path> renders + reveals the folder   → test_deep_link_renders_note_and_reveals
  AC4 Links panel: outgoing + backlinks                → test_outgoing_resolves_seeded_links, test_backlinks_list_the_linking_note,
                                                         test_links_panel_lists_outgoing, test_backlinks_panel_lists_linking_note
  AC5 Wikilink click opens the target in place         → test_wikilink_click_navigates_and_back_returns,
                                                         test_ambiguous_wikilink_offers_a_choice, test_bare_md_mention_opens_in_place
  AC6 resolve: path or in-band error with candidates   → test_resolve_*
  AC7 Timeline button on the open note                 → test_timeline_source_registered, test_timeline_button_mounts
Edge/regression (no AC): test_page_loads_clean, test_tree_click_opens_note, test_folder_click_expands,
  test_missing_note_shows_error, test_unencoded_percent_hash_does_not_crash, test_params_required,
  test_missing_note_is_in_band
Unit coverage: tests/test_unit_notebook_listing.py (pure listing/resolution),
  tests/test_unit_notebook_backlinks.py (the backlinks wait/cache logic).
"""

from __future__ import annotations

import time
import tomllib
from pathlib import Path
from urllib.parse import quote

import pytest

from helpers import TEST_PREFIX, assert_ok
from page_helpers import assert_no_js_errors

ROOT = TEST_PREFIX + "notebook"
SRC = f"{ROOT}/deep/{TEST_PREFIX}nb-src.md"
DST = f"{ROOT}/deep/{TEST_PREFIX}nb-dst.md"
AMBIG_X = f"{ROOT}/x/{TEST_PREFIX}nb-ambig.md"
AMBIG_Y = f"{ROOT}/y/{TEST_PREFIX}nb-ambig.md"
BODY_MARK = TEST_PREFIX + "body-marker"
SEED = {
    SRC: (
        f"# Source note\n\n{BODY_MARK}\n\n"
        f"Go to [[{TEST_PREFIX}nb-dst]], then [[{TEST_PREFIX}nb-ambig]], "
        f"then [[{TEST_PREFIX}nb-missing]], and [[{TEST_PREFIX}nb-src|myself]].\n\n"
        f"Also mentioned: {TEST_PREFIX}nb-dst.md\n\n"
        f"```\n[[{TEST_PREFIX}nb-incode]]\n```\n"
    ),
    DST: "# Destination note\n\nNothing here links anywhere.\n",
    AMBIG_X: "# Ambiguous X\n",
    AMBIG_Y: "# Ambiguous Y\n",
}


def _installed(http_client) -> bool:
    r = http_client.get("/api/apps")
    return r.status_code == 200 and any(a.get("id") == "notebook" for a in r.json())


def _toml_vault() -> Path | None:
    try:
        with open(Path(__file__).resolve().parents[1] / "emptyos.toml", "rb") as f:
            p = tomllib.load(f).get("notes", {}).get("path", "")
        return Path(p).resolve() if p else None
    except Exception:
        return None


@pytest.fixture(autouse=True, scope="module")
def seeded(http_client):
    if not _installed(http_client):
        pytest.skip("notebook app not installed")
    try:
        for path, content in SEED.items():
            body = assert_ok(http_client.post("/api/vault/write", json={"path": path, "content": content}))
            assert body.get("ok"), body
        # The index learns of new files from the vault watcher; wait for it.
        want = {"deep": 2, "x": 1, "y": 1}
        deadline = time.time() + 60
        got = None
        while time.time() < deadline:
            got = http_client.get("/notebook/api/tree", params={"dir": ROOT}).json()
            if {f["name"]: f["count"] for f in got.get("folders", [])} == want:
                break
            time.sleep(1)
        else:
            pytest.fail(f"seeded notes never reached the index: {got}")
        yield
    finally:
        _remove_seed(http_client)


def _remove_seed(http_client):
    """Remove only what this module wrote, and only from the vault the daemon uses.

    Runs even when seeding failed. A sandbox member's vault is not the one in
    emptyos.toml, so nothing is removed there — that vault is throwaway.
    """
    info = http_client.get("/api/vault/read", params={"path": SRC}).json()
    vault = _toml_vault()
    abs_src = Path(info.get("path", "")).resolve() if info.get("path") else None
    if not (vault and abs_src and vault in abs_src.parents):
        return
    for rel in SEED:
        (vault / rel).unlink(missing_ok=True)
    for d in ("deep", "x", "y", ""):
        try:
            (vault / ROOT / d).rmdir()
        except OSError:
            pass


def _enc(p: str) -> str:
    """encodeURIComponent, which the page uses — differs from quote() on !'()*."""
    return quote(p, safe="!'()*-_.~")


@pytest.mark.api
class TestNotebookAPI:
    def test_tree_lists_seeded_folder_exactly(self, http_client):
        d = assert_ok(http_client.get("/notebook/api/tree", params={"dir": ROOT}))
        assert d == {
            "dir": ROOT,
            "folders": [
                {"name": "deep", "path": f"{ROOT}/deep", "count": 2},
                {"name": "x", "path": f"{ROOT}/x", "count": 1},
                {"name": "y", "path": f"{ROOT}/y", "count": 1},
            ],
            "files": [],
        }
        deep = http_client.get("/notebook/api/tree", params={"dir": f"{ROOT}/deep"}).json()
        assert [f["path"] for f in deep["files"]] == [DST, SRC]

    def test_tree_root_hides_dot_folders(self, http_client):
        d = assert_ok(http_client.get("/notebook/api/tree"))
        assert d["dir"] == ""
        assert any(f["name"] == ROOT for f in d["folders"])
        for f in d["folders"]:
            assert not f["name"].startswith("."), f
        for f in d["files"]:
            assert f["path"].lower().endswith(".md")

    @pytest.mark.parametrize("bad", ["../", "..", "a/../../b", "/etc", "C:/Windows", ".git"])
    def test_tree_refuses_escape(self, http_client, bad):
        r = http_client.get("/notebook/api/tree", params={"dir": bad})
        assert r.status_code == 200
        body = r.json()
        assert "error" in body and "files" not in body

    def test_tree_accepts_colon_in_folder_name(self, http_client):
        d = assert_ok(http_client.get("/notebook/api/tree", params={"dir": "Meeting: notes"}))
        assert "error" not in d and d["dir"] == "Meeting: notes"

    def test_resolve_path_form(self, http_client):
        assert http_client.get("/notebook/api/resolve", params={"title": DST[:-3]}).json() == {"path": DST}

    def test_resolve_stem(self, http_client):
        assert http_client.get("/notebook/api/resolve", params={"title": TEST_PREFIX + "nb-dst"}).json() == {"path": DST}

    def test_resolve_ambiguous_lists_candidates(self, http_client):
        d = http_client.get("/notebook/api/resolve", params={"title": TEST_PREFIX + "nb-ambig"}).json()
        assert "error" in d and d["candidates"] == [AMBIG_X, AMBIG_Y]

    def test_resolve_unknown(self, http_client):
        d = http_client.get("/notebook/api/resolve", params={"title": TEST_PREFIX + "nb-missing"}).json()
        assert "error" in d and d["candidates"] == []

    def test_outgoing_resolves_seeded_links(self, http_client):
        d = assert_ok(http_client.get("/notebook/api/outgoing", params={"path": SRC}))
        assert d["path"] == SRC
        assert d["links"] == [
            {"target": TEST_PREFIX + "nb-dst", "paths": [DST]},
            {"target": TEST_PREFIX + "nb-ambig", "paths": [AMBIG_X, AMBIG_Y]},
            {"target": TEST_PREFIX + "nb-missing", "paths": []},
        ]  # the self-link and the fenced [[…]] are not links

    def test_backlinks_list_the_linking_note(self, http_client):
        deadline = time.time() + 120
        while True:
            d = assert_ok(http_client.get("/notebook/api/backlinks", params={"path": DST}, timeout=30))
            if not d.get("pending") or time.time() > deadline:
                break
            time.sleep(3)
        if d.get("pending"):
            pytest.skip("link index still building after 120 s")
        assert "unavailable" not in d and "error" not in d, d
        assert d == {"path": DST, "backlinks": [SRC]}

    def test_timeline_source_registered(self, http_client):
        d = assert_ok(http_client.get("/api/sdk/timeline-apps"))
        row = next((a for a in d["apps"] if a["app_id"] == "notebook"), None)
        assert row and row["entity_source"] == "note_path" and row["route_prefix"] == "/notebook"

    def test_params_required(self, http_client):
        for ep in ("resolve", "outgoing", "backlinks"):
            assert "error" in http_client.get(f"/notebook/api/{ep}").json(), ep

    def test_missing_note_is_in_band(self, http_client):
        for ep in ("outgoing", "backlinks"):
            r = http_client.get(f"/notebook/api/{ep}", params={"path": TEST_PREFIX + "nb-missing.md"})
            assert r.status_code == 200 and "error" in r.json(), ep


def _open(page, base_url, path):
    page.goto(base_url + "/notebook/#" + _enc(path))
    page.wait_for_selector(f'#nb-head[data-entity-path="{path}"]', timeout=10000)


@pytest.mark.interactive
class TestNotebookUI:
    def test_page_loads_clean(self, page, base_url, page_errors):
        page.goto(base_url + "/notebook/")
        # The tree's first real row, which only notebook.js can render.
        page.wait_for_selector(f'#nb-tree .nb-folder[data-dir="{ROOT}"]', timeout=10000)
        assert_no_js_errors(page_errors)

    def test_deep_link_renders_note_and_reveals(self, page, base_url, page_errors):
        _open(page, base_url, SRC)
        assert BODY_MARK in page.inner_text("#nb-note")
        assert page.inner_text("#nb-crumb") == SRC
        page.wait_for_selector(f'.nb-file.active[data-path="{SRC}"]', timeout=10000)
        assert page.get_attribute(f'.nb-folder[data-dir="{ROOT}/deep"]', "aria-expanded") == "true"
        assert_no_js_errors(page_errors)

    def test_tree_click_opens_note(self, page, base_url):
        _open(page, base_url, SRC)
        page.click(f'.nb-file[data-path="{DST}"]')
        page.wait_for_selector(f'#nb-head[data-entity-path="{DST}"]', timeout=10000)
        assert "Destination note" in page.inner_text("#nb-note")

    def test_folder_click_expands(self, page, base_url):
        page.goto(base_url + "/notebook/")
        btn = page.wait_for_selector(f'.nb-folder[data-dir="{ROOT}"]', timeout=10000)
        btn.click()
        page.wait_for_selector(f'.nb-folder[data-dir="{ROOT}/x"]', timeout=10000)
        assert btn.get_attribute("aria-expanded") == "true"

    def test_links_panel_lists_outgoing(self, page, base_url):
        _open(page, base_url, SRC)
        page.wait_for_selector(f'#nb-outgoing a[href="#{_enc(DST)}"]', timeout=10000)
        text = page.inner_text("#nb-outgoing")
        assert "2 notes" in text and "no such note" in text

    def test_backlinks_panel_lists_linking_note(self, page, base_url):
        _open(page, base_url, DST)
        try:
            page.wait_for_selector(f'#nb-backlinks a[href="#{_enc(SRC)}"]', timeout=120000)
        except Exception:
            if "Building the link index" in page.inner_text("#nb-backlinks"):
                pytest.skip("link index still building after 120 s")
            raise

    def test_wikilink_click_navigates_and_back_returns(self, page, base_url, page_errors):
        _open(page, base_url, SRC)
        page.click(f'#nb-note a.nb-wl[data-target="{TEST_PREFIX}nb-dst"]')
        page.wait_for_selector(f'#nb-head[data-entity-path="{DST}"]', timeout=10000)
        page.go_back()
        page.wait_for_selector(f'#nb-head[data-entity-path="{SRC}"]', timeout=10000)
        assert_no_js_errors(page_errors)

    def test_ambiguous_wikilink_offers_a_choice(self, page, base_url):
        _open(page, base_url, SRC)
        page.click(f'#nb-note a.nb-wl[data-target="{TEST_PREFIX}nb-ambig"]')
        page.wait_for_selector(f'.nb-pick a[href="#{_enc(AMBIG_Y)}"]', timeout=10000)
        page.click(f'.nb-pick a[href="#{_enc(AMBIG_Y)}"]')
        page.wait_for_selector(f'#nb-head[data-entity-path="{AMBIG_Y}"]', timeout=10000)

    def test_bare_md_mention_opens_in_place(self, page, base_url):
        _open(page, base_url, SRC)
        page.click("#nb-note a.note-ref")
        page.wait_for_selector(f'#nb-head[data-entity-path="{DST}"]', timeout=10000)
        assert page.locator("#eos-note-overlay.open").count() == 0

    def test_timeline_button_mounts(self, page, base_url):
        _open(page, base_url, SRC)
        page.wait_for_selector("#nb-head > .eos-timeline-btn", timeout=10000)

    def test_missing_note_shows_error(self, page, base_url):
        page.goto(base_url + "/notebook/#" + _enc(TEST_PREFIX + "nb-missing.md"))
        page.wait_for_selector("#nb-note .eos-error-state", timeout=10000)
        assert page.get_attribute("#nb-head", "data-entity-path") is None

    def test_unencoded_percent_hash_does_not_crash(self, page, base_url, page_errors):
        page.goto(base_url + "/notebook/#50%")
        page.wait_for_selector("#nb-note .eos-error-state", timeout=10000)
        assert_no_js_errors(page_errors)
