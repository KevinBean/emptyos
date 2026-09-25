"""emptyos/sdk/attachments.py — confinement, extraction, native content, hydrate.

The confinement cases are the reason for this file's strictness: the assistant
helpers these replace joined ``vault_root / rel_path`` unchecked.
"""

from __future__ import annotations

import base64

from emptyos.sdk import attachments as A

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "pics").mkdir(parents=True)
    (root / "pics" / "dot.png").write_bytes(PNG)
    (root / "notes.md").write_text("hello from the vault", encoding="utf-8")
    (tmp_path / "secret.md").write_text("outside", encoding="utf-8")
    return root


def test_confine_refuses_every_way_out(tmp_path):
    root = vault(tmp_path)
    assert A.confine(root, "notes.md") == (root / "notes.md").resolve()
    for bad in ("../secret.md", "pics/../../secret.md", "/etc/passwd", "C:/Windows/win.ini", "C:\\x", ""):
        assert A.confine(root, bad) is None, bad


def test_images_resolve_only_inside_the_vault(tmp_path):
    root = vault(tmp_path)
    url = A.path_to_data_url(root, "pics/dot.png")
    assert url.startswith("data:image/png;base64,")
    assert A.path_to_data_url(root, "../secret.md") is None
    assert A.path_to_data_url(root, "notes.md") is None  # not an image
    assert A.path_to_data_url(root, "https://x/y.png") == "https://x/y.png"
    assert A.resolve_images(root, ["pics/dot.png", "missing.png", "../x.png"]) == [url]


def test_oversized_image_is_dropped(tmp_path, monkeypatch):
    root = vault(tmp_path)
    monkeypatch.setattr(A, "MAX_IMAGE_BYTES", 10)
    assert A.path_to_data_url(root, "pics/dot.png") is None


def test_extract_is_confined_and_truncates(tmp_path):
    root = vault(tmp_path)
    ok = A.extract_file(root, "notes.md", max_chars=5)
    assert ok.text == "hello" and ok.truncated and not ok.error
    assert A.extract_file(root, "../secret.md").error == "outside the vault"
    assert A.extract_file(root, "nope.md").error == "file not found"
    assert "failed to read" in A.format_block(A.extract_file(root, "nope.md"))
    assert A.format_block(ok).startswith("[Attached file: notes.md (truncated)]")


def test_store_upload_sanitises_and_stays_inside(tmp_path):
    root = vault(tmp_path)
    r = A.store_upload(root, "00_Inbox/_attachments", "../../evil name!.png", PNG, max_bytes=1024)
    assert r["path"].startswith("00_Inbox/_attachments/") and ".." not in r["path"]
    assert (root / r["path"]).read_bytes() == PNG
    assert "error" in A.store_upload(root, "../outside", "a.png", PNG, max_bytes=1024)
    assert "error" in A.store_upload(root, "in", "a.png", PNG, max_bytes=10)
    assert "error" in A.store_upload(root, "in", "a.png", b"", max_bytes=10)


def test_native_content_per_provider_family():
    url = "data:image/png;base64,QUJD"
    assert A.build_user_content("openai", "hi", []) == "hi"  # text-only turns are unchanged
    assert A.build_user_content("openai", "hi", [url]) == [
        {"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": url}},
    ]
    assert A.build_user_content("anthropic", "hi", [url, "https://x/p.png"]) == [
        {"type": "text", "text": "hi"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
        {"type": "image", "source": {"type": "url", "url": "https://x/p.png"}},
    ]


def test_dehydrate_then_hydrate_round_trips_without_eos_blocks(tmp_path):
    root = vault(tmp_path)
    url = A.path_to_data_url(root, "pics/dot.png")
    sent = A.build_user_content("openai", "what is this", [url])
    stored = A.dehydrate_content(sent, ["pics/dot.png"])
    assert stored[1] == {"type": "eos_image", "path": "pics/dot.png"}
    assert "base64" not in str(stored)  # history keeps a reference, not the bytes
    (back,) = A.hydrate_messages([{"role": "user", "content": stored, "eos_note": "x"}], root, "openai")
    assert back == {"role": "user", "content": sent}
    (anth,) = A.hydrate_messages([{"role": "user", "content": stored}], root, "anthropic")
    assert anth["content"][1]["type"] == "image"


def test_prepare_turn_sorts_and_reports_every_attachment(tmp_path):
    root = vault(tmp_path)
    # An escaping IMAGE is the case that separates the confinement check from
    # the extractor's own: both refuse a .md, only this one names the reason.
    t = A.prepare_turn(root, ["pics/dot.png", "notes.md", "../secret.md", "../away.png", "gone.png", "x.zip", ""])
    assert t.image_paths == ["pics/dot.png"] and t.has_images
    assert t.image_urls[0].startswith("data:image/png;base64,")
    assert len(t.file_blocks) == 1 and "hello from the vault" in t.file_blocks[0]
    # Nothing silently dropped: each unusable path is named with its reason.
    assert t.problems == [
        "secret.md: outside the vault",
        "away.png: outside the vault",
        "gone.png: image missing, empty or larger than 12 MB",
        "x.zip: unsupported file type",
    ]
    assert not A.prepare_turn(root, None).has_images


def test_a_url_can_never_be_attached(tmp_path):
    """Review B3 #2: path_to_data_url passes a URL through, so a URL in the
    attachments list would have the PROVIDER fetch an arbitrary address."""
    root = vault(tmp_path)
    t = A.prepare_turn(root, ["https://evil.example/x.png", "http://169.254.169.254/latest/meta-data",
                              "data:image/png;base64,QUJD", "pics/dot.png"])
    assert t.image_paths == ["pics/dot.png"]
    assert all("only files in your vault" in p for p in t.problems), t.problems
    assert len(t.problems) == 3


def test_the_cap_names_what_it_refused(tmp_path):
    root = vault(tmp_path)
    t = A.prepare_turn(root, ["notes.md"] * 2 + ["pics/dot.png"], max_items=2)
    assert t.names == ["notes.md", "notes.md"]
    assert t.problems == ["dot.png: not sent — at most 2 attachments per message"]


def test_a_replay_withholds_until_approved_for_this_provider(tmp_path):
    root = vault(tmp_path)
    said = A.mark_vault_derived({"role": "user", "content": "secret budget 42"}, typed="what is it?")
    approved = A.mark_vault_derived({"role": "user", "content": "secret budget 42"},
                                    typed="what is it?", approved_for="openai-mini")
    (out,) = A.hydrate_messages([said], root, "openai", withhold=True, provider_name="openai-mini")
    assert out["content"].startswith("what is it?") and "withheld" in out["content"]
    (ok,) = A.hydrate_messages([approved], root, "openai", withhold=True, provider_name="openai-mini")
    assert ok["content"] == "secret budget 42"
    # Approved for one model is not approval for another.
    (other,) = A.hydrate_messages([approved], root, "openai", withhold=True, provider_name="openai")
    assert "withheld" in other["content"]


def test_replayed_images_respect_vision_and_a_byte_budget(tmp_path):
    root = vault(tmp_path)
    msg = {"role": "user", "content": [{"type": "text", "text": "look"},
                                       {"type": A.EOS_IMAGE, "path": "pics/dot.png"}]}
    (blind,) = A.hydrate_messages([msg], root, "openai", images=False)
    assert blind["content"][1] == {"type": "text", "text": A.NO_VISION_NOTE}
    (capped,) = A.hydrate_messages([msg], root, "openai", max_image_bytes=10)
    assert capped["content"][1] == {"type": "text", "text": A.TOO_MUCH_NOTE}
    (ok,) = A.hydrate_messages([msg], root, "openai")
    assert ok["content"][1]["type"] == "image_url"


def test_vision_by_declaration_family_and_model_name():
    from types import SimpleNamespace as NS

    assert A.provider_reads_images(NS(kind="anthropic", model="claude-x")) is True
    assert A.provider_reads_images(NS(kind="openai", model="gpt-5.4-mini")) is True
    assert A.provider_reads_images(NS(kind="openai", model="qwen3.5-32k:latest")) is False
    assert A.provider_reads_images(NS(kind="openai", model="qwen3.5-32k", supports_vision=True)) is True
    assert A.provider_reads_images(NS(kind="native", model="")) is False


def test_a_vanished_image_becomes_a_note_not_a_hole(tmp_path):
    root = vault(tmp_path)
    (msg,) = A.hydrate_messages([{"role": "user", "content": [{"type": "eos_image", "path": "gone.png"}]}], root, "openai")
    assert msg["content"] == [{"type": "text", "text": "[image 'gone.png' is no longer available]"}]
