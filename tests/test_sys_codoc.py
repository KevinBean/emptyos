"""System tests for the codoc snapshot write-side bridge.

The codoc app persists a live co-doc snapshot (the author:both markdown the
co-doc service produces) into the vault. Test notes carry TEST_PREFIX in their
frontmatter title so the vault leak guard auto-cleans them.
"""

from helpers import TEST_PREFIX


def _snapshot_md(title: str) -> str:
    return (
        f"---\ntitle: {title}\nauthor: both\nsource: codoc\n"
        f"codoc_id: test-id\n---\n\nThe merged live doc body.\n"
    )


def test_codoc_save_persists_snapshot(http_client):
    title = TEST_PREFIX + "codoc snapshot"
    r = http_client.post(
        "/codoc/api/save", json={"markdown": _snapshot_md(title), "slug": title}
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") is True
    assert d["path"].endswith(".md") and "codoc" in d["path"]


def test_codoc_save_rejects_empty_markdown(http_client):
    r = http_client.post("/codoc/api/save", json={"markdown": "   ", "slug": "x"})
    assert r.json().get("error")


def test_codoc_save_rejects_missing_slug(http_client):
    r = http_client.post("/codoc/api/save", json={"markdown": "hi", "slug": ""})
    # empty slug slugifies to the "co-doc" fallback — still a valid save, not an error.
    assert r.json().get("ok") is True


def test_codoc_saved_lists_snapshots(http_client):
    title = TEST_PREFIX + "codoc listed"
    http_client.post("/codoc/api/save", json={"markdown": _snapshot_md(title), "slug": title})
    saved = http_client.get("/codoc/api/saved").json()["saved"]
    assert isinstance(saved, list)
    assert all("slug" in s and "path" in s for s in saved)


def test_codoc_save_slug_cannot_traverse(http_client):
    # A malicious slug is neutralized by slugify + the path-containment check —
    # the note still lands inside the app's vault dir, no parent escape.
    r = http_client.post(
        "/codoc/api/save",
        json={"markdown": _snapshot_md(TEST_PREFIX + "trav"),
              "slug": "../../" + TEST_PREFIX + "escape"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") is True
    assert ".." not in d["path"]
    assert d["path"].startswith("30_Resources/EmptyOS/codoc/")


def test_codoc_agent_edit_rejects_non_agent_principal(http_client):
    for p in ("human:victim", "alice", "agent"):
        r = http_client.post(
            "/codoc/api/agent-edit", json={"doc_id": "d", "principal": p, "text": "x"}
        )
        assert r.json().get("error"), p


def test_codoc_agent_edit_validates_input(http_client):
    r = http_client.post(
        "/codoc/api/agent-edit", json={"doc_id": "", "principal": "", "text": ""}
    )
    assert r.json().get("error")


def test_codoc_agent_edit_degrades_when_service_unreachable(http_client):
    # The co-doc service isn't running in CI — the agent co-editor must degrade
    # to a clean error, never a 500. (Live convergence is verified out-of-band.)
    r = http_client.post(
        "/codoc/api/agent-edit",
        json={"doc_id": "no-such", "principal": "agent:ci", "text": "hi"},
    )
    assert r.status_code == 200
    assert "error" in r.json()
