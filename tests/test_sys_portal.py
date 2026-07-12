"""System app tests: Portal — folders + pins + UI smoke."""
import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok


def _make_folder_name(suffix: str = "") -> str:
    return f"{TEST_PREFIX}room-{suffix}" if suffix else f"{TEST_PREFIX}room"


def _cleanup_test_folders(client) -> None:
    """Delete any folder whose name starts with TEST_PREFIX. Called as a
    fail-safe; individual tests should also delete what they create."""
    try:
        resp = client.get("/portal/api/folders")
        if resp.status_code != 200:
            return
        for f in resp.json().get("folders", []):
            name = str(f.get("name", ""))
            if TEST_PREFIX in name:
                fid = f.get("id")
                if fid:
                    client.delete(f"/portal/api/folders/{fid}")
    except Exception:
        pass


def _cleanup_test_pins(client) -> None:
    """Remove any pinned thread id that starts with TEST_PREFIX."""
    try:
        resp = client.get("/portal/api/pins")
        if resp.status_code != 200:
            return
        for tid in resp.json().get("threads", []):
            if str(tid).startswith(TEST_PREFIX):
                client.delete(f"/portal/api/pins/{tid}")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _portal_test_cleanup(http_client):
    """Run cleanup before and after each test in this module."""
    _cleanup_test_folders(http_client)
    _cleanup_test_pins(http_client)
    yield
    _cleanup_test_folders(http_client)
    _cleanup_test_pins(http_client)


@pytest.mark.api
class TestPortalFoldersAPI:
    def test_folders_list_endpoint(self, http_client):
        data = assert_dict_response(http_client.get("/portal/api/folders"))
        assert "folders" in data
        assert isinstance(data["folders"], list)

    def test_create_folder(self, http_client):
        payload = {"name": _make_folder_name("create"), "default_mode": "think"}
        f = assert_ok(http_client.post("/portal/api/folders", json=payload))
        assert f.get("id", "").startswith("fld-")
        assert f.get("name") == payload["name"]
        assert f.get("default_mode") == "think"
        assert f.get("thread_ids") == []
        assert f.get("system_prompt") == ""
        assert f.get("model") == ""

    def test_create_folder_requires_name(self, http_client):
        resp = http_client.post("/portal/api/folders", json={"name": ""})
        data = resp.json()
        assert "error" in data

    def test_create_folder_normalizes_invalid_mode(self, http_client):
        payload = {"name": _make_folder_name("mode"), "default_mode": "BOGUS"}
        f = assert_ok(http_client.post("/portal/api/folders", json=payload))
        assert f.get("default_mode") == "think"  # invalid → think fallback

    def test_update_folder_fields(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("upd")}).json()
        fid = f["id"]
        patch = {
            "name": _make_folder_name("upd-renamed"),
            "default_mode": "code",
            "model": "claude-cli",
            "system_prompt": "be careful",
        }
        updated = assert_ok(http_client.patch(f"/portal/api/folders/{fid}", json=patch))
        assert updated["name"] == patch["name"]
        assert updated["default_mode"] == "code"
        assert updated["model"] == "claude-cli"
        assert updated["system_prompt"] == "be careful"

    def test_update_folder_clears_model_with_empty_string(self, http_client):
        # Empty string clears (per the api_update_folder docstring); only
        # `None` is treated as "field absent". Important: claude.ai-style
        # "no model override" semantics.
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("clr")}).json()
        fid = f["id"]
        http_client.patch(f"/portal/api/folders/{fid}", json={"model": "ollama"})
        cleared = assert_ok(http_client.patch(f"/portal/api/folders/{fid}", json={"model": ""}))
        assert cleared["model"] == ""

    def test_delete_folder_releases_threads(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("del")}).json()
        fid = f["id"]
        http_client.post(
            f"/portal/api/folders/{fid}/threads",
            json={"thread_id": f"{TEST_PREFIX}t1"},
        )
        result = assert_ok(http_client.delete(f"/portal/api/folders/{fid}"))
        assert result["deleted"] == fid
        assert f"{TEST_PREFIX}t1" in result["freed_threads"]
        # Folder no longer in list.
        listing = http_client.get("/portal/api/folders").json()
        assert all(x.get("id") != fid for x in listing.get("folders", []))

    def test_attach_thread_appears_in_folder(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("att")}).json()
        fid = f["id"]
        tid = f"{TEST_PREFIX}thread-a"
        updated = assert_ok(http_client.post(
            f"/portal/api/folders/{fid}/threads", json={"thread_id": tid},
        ))
        assert tid in updated["thread_ids"]

    def test_attach_moves_thread_from_prior_folder(self, http_client):
        # A thread can only belong to one folder. Attaching to folder B
        # must pull it out of folder A.
        a = http_client.post("/portal/api/folders", json={"name": _make_folder_name("a")}).json()
        b = http_client.post("/portal/api/folders", json={"name": _make_folder_name("b")}).json()
        tid = f"{TEST_PREFIX}thread-move"
        http_client.post(f"/portal/api/folders/{a['id']}/threads", json={"thread_id": tid})
        http_client.post(f"/portal/api/folders/{b['id']}/threads", json={"thread_id": tid})
        a_after = next(x for x in http_client.get("/portal/api/folders").json()["folders"] if x["id"] == a["id"])
        b_after = next(x for x in http_client.get("/portal/api/folders").json()["folders"] if x["id"] == b["id"])
        assert tid not in a_after["thread_ids"]
        assert tid in b_after["thread_ids"]

    def test_detach_thread(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("det")}).json()
        fid = f["id"]
        tid = f"{TEST_PREFIX}thread-det"
        http_client.post(f"/portal/api/folders/{fid}/threads", json={"thread_id": tid})
        result = assert_ok(http_client.delete(f"/portal/api/folders/{fid}/threads/{tid}"))
        assert tid not in result["thread_ids"]

    def test_folder_of_thread_reverse_lookup(self, http_client):
        f = http_client.post("/portal/api/folders", json={"name": _make_folder_name("rev")}).json()
        fid = f["id"]
        tid = f"{TEST_PREFIX}thread-rev"
        http_client.post(f"/portal/api/folders/{fid}/threads", json={"thread_id": tid})
        data = assert_dict_response(http_client.get(f"/portal/api/threads/{tid}/folder"))
        assert data["thread_id"] == tid
        assert data["folder_id"] == fid
        # Unknown thread → folder_id is null.
        unknown = assert_dict_response(http_client.get(f"/portal/api/threads/{TEST_PREFIX}nope/folder"))
        assert unknown["folder_id"] is None


@pytest.mark.api
class TestPortalPinsAPI:
    def test_pins_list_endpoint(self, http_client):
        data = assert_dict_response(http_client.get("/portal/api/pins"))
        assert "threads" in data
        assert isinstance(data["threads"], list)

    def test_pin_thread_inserts_at_top(self, http_client):
        # Newest pin floats to position 0.
        first = f"{TEST_PREFIX}pin-1"
        second = f"{TEST_PREFIX}pin-2"
        http_client.post(f"/portal/api/pins/{first}")
        result = assert_dict_response(http_client.post(f"/portal/api/pins/{second}"))
        assert result["threads"][0] == second
        assert first in result["threads"]

    def test_pin_idempotent(self, http_client):
        tid = f"{TEST_PREFIX}pin-idem"
        http_client.post(f"/portal/api/pins/{tid}")
        # Pinning again should not duplicate.
        result = assert_dict_response(http_client.post(f"/portal/api/pins/{tid}"))
        assert result["threads"].count(tid) == 1

    def test_unpin_thread(self, http_client):
        tid = f"{TEST_PREFIX}pin-unp"
        http_client.post(f"/portal/api/pins/{tid}")
        result = assert_dict_response(http_client.delete(f"/portal/api/pins/{tid}"))
        assert tid not in result["threads"]

    def test_unpin_unknown_is_idempotent(self, http_client):
        # Unpinning a thread that isn't pinned should not error.
        resp = http_client.delete(f"/portal/api/pins/{TEST_PREFIX}never-pinned")
        assert resp.status_code == 200


@pytest.mark.interactive
class TestPortalUI:
    def test_page_loads_with_verb_chips(self, app_page, page_errors):
        page = app_page("portal")
        # All five capability chips render — three real verbs + Think (default) + Adaptive (stub).
        for verb in ("think", "capture", "find", "learn", "adaptive"):
            page.locator(f'.portal-chip[data-verb="{verb}"]').wait_for(state="visible", timeout=4000)

    def test_sidebar_has_rooms_and_shortcuts(self, app_page, page_errors):
        page = app_page("portal")
        # Sidebar sections: Shortcuts (with browse-all link), Rooms (with + button).
        page.locator("text=Shortcuts").wait_for(state="visible", timeout=4000)
        page.locator("text=Rooms").wait_for(state="visible", timeout=4000)
        page.locator(".portal-section-add").wait_for(state="visible", timeout=4000)
