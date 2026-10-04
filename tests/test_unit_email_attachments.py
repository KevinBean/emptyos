"""email-smtp attachment validation — refuse loudly, never send a silent miss.

This is an outbound path, so the failure that matters is not a crash: it is a
mail that *arrives* without the file it was sent for. Every case here asserts the
provider raises instead of skipping, and that consent_summary shows the name AND
size, since the gate is the last place a wrong attachment can be caught.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "plugins/email-smtp/plugin.py"


def _provider_cls():
    """Load the provider class without booting the kernel or the plugin loader."""
    spec = importlib.util.spec_from_file_location("eos_email_smtp_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and hasattr(obj, "_resolve_attachments"):
            return obj
    raise AssertionError("no provider class exposing _resolve_attachments")


CLS = _provider_cls()


def _provider():
    """A provider instance with only the fields these paths touch."""
    p = CLS.__new__(CLS)
    p.host, p.port, p.username, p.password_env = "smtp.example.com", 587, "", ""
    p.from_addr, p.from_name, p.security, p.timeout = "me@example.com", "Me", "starttls", 30
    p.name = "email-smtp"
    return p


# ── the empty / absent cases must be clean no-ops ──────────────────────────

@pytest.mark.parametrize("raw", [None, "", [], ()])
def test_no_attachments_is_an_empty_list(raw):
    assert _provider()._resolve_attachments(raw) == []


# ── the green path ─────────────────────────────────────────────────────────

def test_single_path_as_a_bare_string(tmp_path):
    f = tmp_path / "bundle.zip"
    f.write_bytes(b"x" * 32)
    assert _provider()._resolve_attachments(str(f)) == [f]


def test_several_paths_preserve_order(tmp_path):
    a, b = tmp_path / "a.zip", tmp_path / "b.html"
    a.write_bytes(b"a"); b.write_bytes(b"b")
    assert _provider()._resolve_attachments([str(a), str(b)]) == [a, b]


def test_pathlib_objects_are_accepted(tmp_path):
    f = tmp_path / "x.zip"
    f.write_bytes(b"x")
    assert _provider()._resolve_attachments([f]) == [f]


# ── the red path: refuse, do not skip ──────────────────────────────────────

def test_missing_file_raises_rather_than_being_dropped(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        _provider()._resolve_attachments([str(tmp_path / "nope.zip")])


def test_a_directory_is_not_a_file(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        _provider()._resolve_attachments([str(tmp_path)])


def test_relative_path_is_refused(tmp_path, monkeypatch):
    """A relative path resolves against the daemon's cwd, which is not the
    caller's — so it either misses or attaches the wrong file. Refuse it."""
    f = tmp_path / "rel.zip"
    f.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute path"):
        _provider()._resolve_attachments(["rel.zip"])


def test_one_good_file_does_not_rescue_a_bad_sibling(tmp_path):
    good = tmp_path / "good.zip"
    good.write_bytes(b"x")
    with pytest.raises(ValueError, match="not found"):
        _provider()._resolve_attachments([str(good), str(tmp_path / "bad.zip")])


def test_total_size_cap_is_across_files_not_per_file(tmp_path):
    p = _provider()
    half = p.MAX_ATTACH_BYTES // 2 + 1024
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"\0" * half); b.write_bytes(b"\0" * half)
    assert p._resolve_attachments([str(a)]) == [a], "one file under the cap is fine"
    with pytest.raises(ValueError, match="exceed"):
        p._resolve_attachments([str(a), str(b)])


def test_cap_message_names_the_offending_file(tmp_path):
    p = _provider()
    big = tmp_path / "enormous.zip"
    big.write_bytes(b"\0" * (p.MAX_ATTACH_BYTES + 1))
    with pytest.raises(ValueError, match="enormous.zip"):
        p._resolve_attachments([str(big)])


# ── the consent gate must show what is leaving ─────────────────────────────

def test_consent_summary_lists_name_and_size(tmp_path):
    f = tmp_path / "worklog-standalone-1.0.0.zip"
    f.write_bytes(b"\0" * (213 * 1024))
    s = _provider().consent_summary(
        to="me@example.com", subject="bundle", body="see attached",
        attachments=[str(f)])
    assert "worklog-standalone-1.0.0.zip" in s
    assert "213 KB" in s, "a bare filename hides how big the outbound payload is"
    assert "me@example.com" in s and "see attached" in s


def test_consent_summary_reports_an_invalid_attachment_instead_of_raising(tmp_path):
    """The gate renders BEFORE execute, so it must degrade to a visible warning —
    a summary that raises would blank the approval card instead of explaining."""
    s = _provider().consent_summary(
        to="me@example.com", subject="x", body="y",
        attachments=[str(tmp_path / "ghost.zip")])
    assert "INVALID" in s and "ghost.zip" in s


def test_consent_summary_unchanged_when_there_are_no_attachments():
    s = _provider().consent_summary(to="a@b.c", subject="s", body="b")
    assert "Attachment" not in s
    assert s.startswith("To: a@b.c")
