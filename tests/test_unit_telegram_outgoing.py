"""Telegram outbound-file guard — every refusal reason, both directions.

`open_outgoing` is the only thing standing between "an app can send a file to my
phone" and "an app can send ANY file off this machine". Each test below pins one
refusal; the happy-path tests pin that the guard does not simply refuse
everything, which is the failure mode a security check most easily degrades into.

Four groups are load-bearing beyond their own lines:

- **the denylist is parametrized one row per member.** A hostile review found the
  first version pinned only the four members it happened to name, so trimming
  `DENY_SUFFIXES` to those four — losing `.db-wal`, `.env`, `.pfx` — kept the
  suite green (`.claude/rules/audits.md`, the `check_skill_refs` lesson). The
  expected members are written out here rather than imported from the module,
  because a test that reads its expectation from the code under test only ever
  proves self-consistency.
- **case-insensitivity** — `Path.resolve()` returns the true on-disk casing, so a
  directory named `Secrets` yields the part `Secrets`; without `.lower()` the
  denylist misses it on the two case-insensitive filesystems this runs on.
- **content sniffing, not extension trust** — `notes.md.mp4` is a text file. The
  scan must read it; an extension-keyed scan would skip it, which is a one-line
  rename away from defeating the whole content check.
- **the scan covers the WHOLE file.** The first version read 512 KB, so a key at
  byte 600,000 of a long note shipped. That is a working exfiltration path, not a
  rounding error.

Pure — no daemon, no network. Run:
    python -m pytest tests/test_unit_telegram_outgoing.py -v
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def og():
    spec = importlib.util.spec_from_file_location(
        "telegram_outgoing_under_test", REPO / "plugins" / "telegram" / "outgoing.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def stub_scan(text: str):
    """Stand-in for scan_outbound: (name, preview) tuples, like Finding."""
    return [("OpenAI API key", "sk-*")] if "sk-live-" in text else []


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "out"
    (v / "clips").mkdir(parents=True)
    (v / "clips" / "take.mp4").write_bytes(b"\x00\x00fake mp4 payload")
    (v / "notes.md").write_text("plain notes, nothing secret", encoding="utf-8")
    return v


def call(og, path, roots, kind="video", **kw):
    """Run the guard and close any handle — tests assert on (path, reason)."""
    fh, p, reason = og.open_outgoing(str(path), roots=roots, kind=kind, scan=stub_scan, **kw)
    if fh is not None:
        fh.close()
    return p, reason


# --- the happy paths: the guard must still let real sends through -----------

def test_video_under_a_root_is_allowed(og, vault):
    p, reason = call(og, vault / "clips" / "take.mp4", [vault])
    assert reason == ""
    assert p == (vault / "clips" / "take.mp4").resolve()


def test_clean_text_document_is_allowed(og, vault):
    p, reason = call(og, vault / "notes.md", [vault], kind="document")
    assert reason == "" and p is not None


def test_handle_returned_reads_the_file_from_the_start(og, vault):
    """The caller uploads from this handle, so it must be positioned at 0."""
    fh, _, reason = og.open_outgoing(
        str(vault / "clips" / "take.mp4"), roots=[vault], kind="video", scan=stub_scan)
    assert reason == ""
    try:
        assert fh.read() == (vault / "clips" / "take.mp4").read_bytes()
    finally:
        fh.close()


def test_a_file_swapped_during_the_checks_is_refused(og, vault, monkeypatch):
    """The TOCTOU guard: the path was stat'd, then something replaced the file
    before the handle was opened. Simulated by making fstat disagree, because
    winning the real race in a test would be flaky rather than informative.
    """
    real_fstat = og.os.fstat

    def lying_fstat(fd):
        st = real_fstat(fd)
        return type("S", (), {
            "st_dev": st.st_dev, "st_ino": st.st_ino + 1, "st_size": st.st_size})()

    monkeypatch.setattr(og.os, "fstat", lying_fstat)
    p, reason = call(og, vault / "clips" / "take.mp4", [vault])
    assert p is None and "changed while it was being checked" in reason


def test_second_root_is_also_allowed(og, vault, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "report.pdf").write_bytes(b"%PDF-1.4 \x00binary")
    p, reason = call(og, data / "report.pdf", [vault, data], kind="document")
    assert reason == "" and p is not None


# --- containment -----------------------------------------------------------

def test_file_outside_roots_is_refused(og, vault, tmp_path):
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"\x00x")
    p, reason = call(og, outside, [vault])
    assert p is None and "outside the allowed roots" in reason


def test_traversal_out_of_the_root_is_refused(og, vault, tmp_path):
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"\x00x")
    p, reason = call(og, vault / "clips" / ".." / ".." / "elsewhere.mp4", [vault])
    assert p is None and "outside the allowed roots" in reason


def test_symlink_escaping_the_root_is_refused(og, vault, tmp_path):
    outside = tmp_path / "secret-elsewhere.mp4"
    outside.write_bytes(b"\x00x")
    link = vault / "clips" / "innocent.mp4"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    p, reason = call(og, link, [vault])
    assert p is None and "outside the allowed roots" in reason


def test_junction_escaping_the_root_is_refused(og, vault, tmp_path):
    """Windows fallback for the symlink case — a file symlink needs a privilege
    this machine does not grant, but a directory junction does not, so without
    this the containment check ships unproven on the OS it runs on."""
    import subprocess

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.mp4").write_bytes(b"\x00x")
    link_dir = vault / "clips" / "link"
    try:
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_dir), str(outside_dir)],
            capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("mklink unavailable (not Windows)")
    via = link_dir / "secret.mp4"
    if r.returncode != 0 or not via.exists():
        pytest.skip("directory junctions not permitted on this machine")
    p, reason = call(og, via, [vault])
    assert p is None and "outside the allowed roots" in reason


def test_missing_file_is_refused(og, vault):
    p, reason = call(og, vault / "clips" / "nope.mp4", [vault])
    assert p is None and reason == "not a file"


def test_directory_is_refused(og, vault):
    p, reason = call(og, vault / "clips", [vault])
    assert p is None and reason == "not a file"


def test_empty_path_is_refused(og, vault):
    p, reason = call(og, "  ", [vault])
    assert p is None and reason == "no path given"


def test_no_roots_refuses_rather_than_allowing_everything(og, vault):
    p, reason = call(og, vault / "clips" / "take.mp4", [])
    assert p is None and "no allowed roots" in reason


def test_a_nonexistent_root_never_matches(og, vault, tmp_path):
    p, reason = call(og, vault / "clips" / "take.mp4", [tmp_path / "ghost"])
    assert p is None and "outside the allowed roots" in reason


# --- denylist: one row per member, expectations written out here ------------

DENIED_DIRS = ["secrets", "node_modules", "__pycache__"]
DENIED_DOT_DIRS = [".git", ".obsidian", ".ssh", ".claude", ".venv"]
DENIED_NAMES = ["emptyos.toml", "emptyos.example.toml", "id_rsa", "id_ed25519"]
DENIED_SUFFIXES = [
    ".env", ".key", ".pem", ".p12", ".pfx", ".crt", ".cer", ".keystore",
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pyc",
]


@pytest.mark.parametrize("dirname", DENIED_DIRS)
def test_each_denied_directory_is_refused(og, vault, dirname):
    d = vault / dirname
    d.mkdir()
    f = d / "payload.md"
    f.write_text("x", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="document")
    assert p is None and "denied directory" in reason


@pytest.mark.parametrize("dirname", DENIED_DOT_DIRS)
def test_each_dot_directory_is_refused(og, vault, dirname):
    d = vault / dirname
    d.mkdir()
    f = d / "payload.md"
    f.write_text("x", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="document")
    assert p is None and "dot-directory" in reason


@pytest.mark.parametrize("name", DENIED_NAMES)
def test_each_denied_name_is_refused(og, vault, name):
    f = vault / name
    f.write_text("x", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="document")
    assert p is None and "denied file name" in reason, (
        "DENY_NAMES must refuse by NAME — if this reads 'not allowed for', the "
        "extension check caught it first and the name list is doing no work")


@pytest.mark.parametrize("suffix", DENIED_SUFFIXES)
def test_each_denied_suffix_is_refused(og, vault, suffix):
    f = vault / f"payload{suffix}"
    f.write_bytes(b"x")
    p, reason = call(og, f, [vault], kind="document")
    assert p is None and "denied file type" in reason


def test_denied_directory_is_matched_case_insensitively(og, vault):
    d = vault / "Secrets"
    d.mkdir()
    f = d / "token.json"
    f.write_text("{}", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="document")
    assert p is None and "denied directory" in reason


def test_denied_name_is_matched_case_insensitively(og, vault):
    f = vault / "EmptyOS.TOML"
    f.write_text("x", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="document")
    assert p is None and "denied file name" in reason


def test_dotfile_is_refused_by_its_own_branch(og, vault):
    f = vault / ".hidden.md"
    f.write_text("x", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="document")
    assert p is None and "denied dotfile" in reason


# --- kind / size -----------------------------------------------------------

def test_extension_must_match_the_kind(og, vault):
    p, reason = call(og, vault / "clips" / "take.mp4", [vault], kind="photo")
    assert p is None and "not allowed for photo" in reason


def test_zip_is_not_sendable(og, vault):
    """An archive is an arbitrary-content envelope the scan cannot read."""
    f = vault / "bundle.zip"
    f.write_bytes(b"PK\x03\x04\x00fake")
    p, reason = call(og, f, [vault], kind="document")
    assert p is None and "not allowed for document" in reason


def test_unknown_kind_is_refused(og, vault):
    p, reason = call(og, vault / "clips" / "take.mp4", [vault], kind="anything")
    assert p is None and "unknown send kind" in reason


def test_oversize_file_is_refused(og, vault):
    big = vault / "clips" / "big.mp4"
    big.write_bytes(b"\x00" * 2048)
    p, reason = call(og, big, [vault], max_bytes=1024)
    assert p is None and "over the" in reason


def test_a_file_exactly_at_the_cap_is_allowed(og, vault):
    """Pins `>` rather than `>=` — an off-by-one here silently refuses a file
    the operator's own cap says is fine."""
    f = vault / "clips" / "exact.mp4"
    f.write_bytes(b"\x00" * 1024)
    p, reason = call(og, f, [vault], max_bytes=1024)
    assert reason == "" and p is not None


def test_photo_cap_is_lower_than_the_video_cap(og):
    """Telegram takes 10 MB photos and 50 MB other files; one flat cap would
    pass a 20 MB jpg that the API then rejects."""
    assert og.effective_cap("photo") == 10 * og.MB
    assert og.effective_cap("video") == 45 * og.MB
    assert og.effective_cap("document") == 45 * og.MB


def test_an_operator_cap_cannot_exceed_the_api_limit(og):
    assert og.effective_cap("photo", 200 * og.MB) == 10 * og.MB
    assert og.effective_cap("video", 200 * og.MB) == 50 * og.MB
    assert og.effective_cap("video", 5 * og.MB) == 5 * og.MB


def test_empty_file_is_refused(og, vault):
    f = vault / "clips" / "zero.mp4"
    f.write_bytes(b"")
    p, reason = call(og, f, [vault])
    assert p is None and reason == "file is empty"


# --- content scan ----------------------------------------------------------

def test_text_with_a_secret_is_refused(og, vault):
    f = vault / "leaky.md"
    f.write_text("token: sk-live-abc123", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="document")
    assert p is None and "outbound scan flagged" in reason


def test_a_secret_far_past_the_first_chunk_is_still_caught(og, vault):
    """The first version scanned 512 KB. A long journal note with a key near the
    end shipped clean — a working exfiltration path, found by hostile review."""
    f = vault / "long.md"
    f.write_text("x" * 3_000_000 + "\ntoken: sk-live-abc123\n", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="document", max_bytes=10 * og.MB)
    assert p is None and "outbound scan flagged" in reason


def test_a_secret_spanning_a_chunk_boundary_is_caught(og, vault):
    """The overlap carried between reads is what makes this possible."""
    secret = "sk-live-" + "b" * 40
    pad = og.SCAN_CHUNK - 4
    f = vault / "boundary.md"
    f.write_text("y" * pad + secret + "\n", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="document", max_bytes=10 * og.MB)
    assert p is None and "outbound scan flagged" in reason


def test_text_renamed_as_video_is_still_scanned(og, vault):
    """`notes.md.mp4` is a text file. An extension-keyed scan would skip it."""
    f = vault / "clips" / "notes.md.mp4"
    f.write_text("token: sk-live-abc123", encoding="utf-8")
    p, reason = call(og, f, [vault], kind="video")
    assert p is None and "outbound scan flagged" in reason


def test_real_binary_is_not_scanned(og, vault):
    """Binary media carries no useful signal for a text scanner — which is why
    the extension allowlist has to stay narrow and .zip is excluded."""
    f = vault / "clips" / "payload.mp4"
    f.write_bytes(b"\x00\x01\x02sk-live-abc123\x00\xff")
    p, reason = call(og, f, [vault])
    assert reason == "" and p is not None


def test_a_missing_scanner_refuses_rather_than_skipping_the_check(og, vault):
    fh, p, reason = og.open_outgoing(
        str(vault / "notes.md"), roots=[vault], kind="document", scan=None)
    assert fh is None and p is None and "no outbound scanner" in reason


def test_real_scan_outbound_signature_matches(og, vault):
    """Drive the guard with the real scanner, not a stub — shape drift fails here."""
    from emptyos.capabilities.outbound_scan import scan_outbound

    leaky = vault / "leaky-real.md"
    leaky.write_text(
        "key sk-ant-" + "a" * 40 + " in a note", encoding="utf-8")  # check-secrets: ignore
    fh, p, reason = og.open_outgoing(
        str(leaky), roots=[vault], kind="document", scan=scan_outbound)
    assert fh is None and p is None and "Anthropic API key" in reason

    clean = vault / "clean-real.md"
    clean.write_text("nothing to see here", encoding="utf-8")
    fh2, p2, reason2 = og.open_outgoing(
        str(clean), roots=[vault], kind="document", scan=scan_outbound)
    if fh2:
        fh2.close()
    assert reason2 == "" and p2 is not None


# --- caption ---------------------------------------------------------------

def test_caption_with_a_secret_is_refused(og):
    assert "caption flagged" in og.scan_caption("here: sk-live-abc123", stub_scan)


def test_clean_caption_passes(og):
    assert og.scan_caption("the lip-sync test clip", stub_scan) == ""


def test_a_caption_without_a_scanner_is_refused(og):
    """Fail closed: the earlier version returned "" here, which made a caller
    that forgot `scan=` silently lose the caption check."""
    assert "no outbound scanner" in og.scan_caption("anything at all", None)


def test_an_empty_caption_needs_no_scanner(og):
    assert og.scan_caption("", None) == ""
