"""Unit tests for the legacy-doc plugin (plugins/legacy-doc/) — no daemon, no anydoc.

Covers the three contracts that matter and would fail silently otherwise:

  - the extension set must NOT overlap markitdown's, or an unproven provider
    shadows a proven one at the same priority (both register at 0);
  - `.pdf` must never be claimed — the KB page-marked extractor and the `ocr`
    plugin own PDFs, and OCR-needing files must fall through to local GPU
    marker rather than anydoc's hosted path;
  - `convert()` must never pass `ocr=` to anydoc. That argument ships document
    pages to a third-party host, bypassing the cloud-consent gate (CLAUDE.md
    rules 18/19). A stub anydoc records the kwargs so a future edit that adds
    it fails here.

Modules are loaded by path, so no `plugins` package and no anydoc install.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    legacy = _load("legacy_doc_under_test", ROOT / "plugins" / "legacy-doc" / "plugin.py")
except Exception as exc:  # pragma: no cover - surfaces as a skip, not a silent pass
    legacy = None
    _import_err = exc

pytestmark = pytest.mark.skipif(
    legacy is None, reason=f"legacy-doc plugin did not import: {globals().get('_import_err')}"
)


# ── extension-scope contract ────────────────────────────────────────────────

def test_does_not_shadow_markitdown_extensions():
    """Both providers register at priority 0; overlapping suffixes is a bug."""
    markit = _load("markitdown_under_test", ROOT / "plugins" / "markitdown" / "plugin.py")
    overlap = legacy.SUPPORTED_EXTENSIONS & markit.SUPPORTED_EXTENSIONS
    assert not overlap, f"legacy-doc would shadow markitdown for: {sorted(overlap)}"


def test_pdf_is_never_claimed():
    """PDFs belong to the page-marked extractor and the local-GPU ocr plugin."""
    assert ".pdf" not in legacy.SUPPORTED_EXTENSIONS


def test_claims_the_legacy_slice_it_exists_for():
    """.ppt is the 360-file hole no other read path covers."""
    for ext in (".ppt", ".doc", ".rtf", ".wps"):
        assert ext in legacy.SUPPORTED_EXTENSIONS, ext


# ── provider fall-through ───────────────────────────────────────────────────

class _StubPlugin:
    def __init__(self, ok=True):
        self.ok = ok
        self.converted: list[str] = []

    async def available(self):
        return self.ok

    async def convert(self, path):
        self.converted.append(path)
        return "# converted"


def test_unclaimed_suffix_falls_through_without_converting():
    stub = _StubPlugin()
    prov = legacy.LegacyDocReadProvider(stub)
    with pytest.raises(legacy._UnsupportedFormat):
        asyncio.run(prov.execute(path="notes.md"))
    assert stub.converted == [], "must not convert a file it does not claim"


def test_claimed_suffix_is_converted():
    stub = _StubPlugin()
    prov = legacy.LegacyDocReadProvider(stub)
    out = asyncio.run(prov.execute(path="deck.ppt"))
    assert out == "# converted"
    assert len(stub.converted) == 1


def test_relative_path_resolves_against_base_like_filesystem_provider():
    stub = _StubPlugin()
    prov = legacy.LegacyDocReadProvider(stub, base_path="D:/vault")
    asyncio.run(prov.execute(path="sub/deck.ppt"))
    assert stub.converted[0].replace("\\", "/").endswith("D:/vault/sub/deck.ppt")


# ── the cloud-consent guard ─────────────────────────────────────────────────

class _StubAnydoc:
    """Stands in for the anydoc module and records how it was called."""

    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    def to_markdown(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "# stub"

    def version(self):
        return "stub"


def test_convert_never_requests_hosted_ocr(monkeypatch):
    """`ocr=` would send pages to a third-party host, bypassing the consent gate."""
    stub = _StubAnydoc()
    monkeypatch.setitem(sys.modules, "anydoc", stub)
    plug = legacy.LegacyDocPlugin.__new__(legacy.LegacyDocPlugin)
    plug._import_ok, plug._import_err = True, ""

    out = asyncio.run(plug.convert("C:/tmp/legacy.doc"))

    assert out == "# stub"
    assert len(stub.calls) == 1
    args, kwargs = stub.calls[0]
    assert "ocr" not in kwargs, f"hosted OCR requested: {kwargs}"
    assert args == ("C:/tmp/legacy.doc",)


def test_convert_raises_cleanly_when_anydoc_missing():
    plug = legacy.LegacyDocPlugin.__new__(legacy.LegacyDocPlugin)
    plug._import_ok, plug._import_err = False, "ImportError: no module"
    with pytest.raises(RuntimeError, match="firecrawl-anydoc"):
        asyncio.run(plug.convert("x.doc"))


def test_convert_wraps_anydoc_failure_with_filename(monkeypatch):
    class _Boom(_StubAnydoc):
        def to_markdown(self, *a, **k):
            raise ValueError("NeedsOcr")

    monkeypatch.setitem(sys.modules, "anydoc", _Boom())
    plug = legacy.LegacyDocPlugin.__new__(legacy.LegacyDocPlugin)
    plug._import_ok, plug._import_err = True, ""
    with pytest.raises(RuntimeError, match="scan.ppt"):
        asyncio.run(plug.convert("C:/tmp/scan.ppt"))


def test_version_probe_failure_does_not_disable_the_plugin(monkeypatch):
    """`anydoc.version` is importlib.metadata's fn leaked into their namespace and
    raises when called bare. A cosmetic probe must never flip a working install
    to unavailable (it did, once)."""
    class _BadVersion:
        def version(self, *a, **k):
            raise TypeError("missing 1 required positional argument")

        def to_markdown(self, *a, **k):
            return "# ok"

    monkeypatch.setitem(sys.modules, "anydoc", _BadVersion())
    plug = legacy.LegacyDocPlugin.__new__(legacy.LegacyDocPlugin)
    plug._import_ok, plug._import_err, plug._version = None, "", "?"
    plug._probe_import()
    assert plug._import_ok is True, f"version probe disabled the plugin: {plug._import_err}"
    assert asyncio.run(plug.available()) is True


# ── connect(): registration contract ────────────────────────────────────────
# connect() runs only at daemon boot, so a mistake here is a silent no-op --
# the plugin loads, prints nothing wrong, and simply never handles a read.

class _RecordingCap:
    def __init__(self):
        self.registered: list[tuple[str, int]] = []

    def add_provider(self, prov, priority=None):
        self.registered.append((prov.name, priority))


class _StubKernel:
    def __init__(self, notes_path="C:/vault", read_cap=None, raise_on_get=False):
        self._cap = read_cap or _RecordingCap()
        self._raise = raise_on_get
        cap_holder = self

        class _Caps:
            def get(self, name):
                assert name == "read", f"registered on the wrong capability: {name}"
                if cap_holder._raise:
                    raise RuntimeError("read capability missing")
                return cap_holder._cap

        class _Cfg:
            pass

        _Cfg.notes_path = notes_path
        self.capabilities = _Caps()
        self.config = _Cfg()


def _fresh_plugin(kernel):
    plug = legacy.LegacyDocPlugin.__new__(legacy.LegacyDocPlugin)
    plug.kernel = kernel
    plug._import_ok, plug._import_err, plug._version = None, "", "?"
    return plug


def test_connect_registers_one_read_provider_at_priority_zero():
    kernel = _StubKernel()
    plug = _fresh_plugin(kernel)
    asyncio.run(plug.connect())
    assert kernel._cap.registered == [("legacy-doc", 0)], kernel._cap.registered


def test_connect_registers_even_when_anydoc_is_absent(monkeypatch):
    """Graceful enhancement: the provider always registers; `available()` gates it.

    Registering only on success would mean installing anydoc later has no effect
    until the NEXT restart, and the read chain would differ by install order.
    """
    kernel = _StubKernel()
    plug = _fresh_plugin(kernel)
    def _probe_fails():
        plug._import_ok, plug._import_err = False, "stub: anydoc not installed"

    monkeypatch.setattr(plug, "_probe_import", _probe_fails)
    asyncio.run(plug.connect())
    assert kernel._cap.registered == [("legacy-doc", 0)]
    assert asyncio.run(plug.available()) is False


def test_connect_threads_notes_path_into_provider_base():
    """Vault-relative reads must resolve exactly like the filesystem provider."""
    kernel = _StubKernel(notes_path="C:/vault")
    captured = {}

    class _Cap(_RecordingCap):
        def add_provider(self, prov, priority=None):
            super().add_provider(prov, priority)
            captured["base"] = prov.base_path

    kernel._cap = _Cap()
    asyncio.run(_fresh_plugin(kernel).connect())
    assert str(captured["base"]).replace("\\", "/") == "C:/vault"


def test_connect_survives_a_missing_read_capability():
    """A kernel without `read` must not take the whole plugin load down."""
    kernel = _StubKernel(raise_on_get=True)
    asyncio.run(_fresh_plugin(kernel).connect())  # must not raise


def test_anydoc_is_a_declared_runtime_dependency():
    """The plugin is dark without its wheel, and Docker installs only the main
    `dependencies` (`pip install -e .`). Undeclared, it shipped off on every
    Docker and fresh install from 2026-08-28 to v0.8.0 while the startup log
    printed `No module named 'anydoc'` and nothing else noticed."""
    import re
    import tomllib

    with open(ROOT / "pyproject.toml", "rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    names = {re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0].lower() for d in deps}
    assert "firecrawl-anydoc" in names
