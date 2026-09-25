"""Legacy-doc plugin — local legacy-document → markdown behind `read`.

Fills the slice `plugins/markitdown/` and `plugins/ocr/` both leave open. Those
two cover the zip-based office formats (.docx/.xlsx/.pptx/...) and scanned PDFs;
neither reads the *pre-XML* Microsoft formats or OpenDocument. Measured against
this machine's file index (69,531 files) the uncovered legacy slice is real:

    doc  6,201    rtf  943    ppt  360    wps  110

At build time (2026-08-28) `.ppt` was unreadable by any path — `scripts/doc_extract.py`
refused it loudly. This plugin is the `legacy-doc` read enhancer that script's own
docstring names as its graduation target (CLAUDE.md rule 9).

`.ppt` now has TWO readers, and they are complementary rather than competing
(measured 2026-08-30 on the same three decks):

    ppt1   script: fails, "no text atoms"   anydoc: 8,936 ch / 7,170 CJK
    ppt2   script: 2,690 ch / 1,775 CJK     anydoc: 2,287 / 1,355
    ppt3   script: 1,207 ch /    36 CJK     anydoc:   945 /     0

`doc_extract._ppt97_text` (olefile record-tree walk) pulls more text where it parses;
anydoc reads the deck it cannot open at all. They do not shadow each other — the script
is not a capability provider, so no priority-0 ordering applies — and the script falls
back to this plugin's library when it finds no text atoms.

Backed by `anydoc` (Firecrawl, MIT), a Rust library with Python bindings.

Why in-process instead of a user-home venv:
    Unlike markitdown (magika → onnxruntime pin), ocr (torch/CUDA) and cadquery
    (VTK), anydoc ships a **zero-dependency abi3 wheel** — one 3.4 MB binary,
    nothing to conflict with the daemon env. There is no dependency to isolate,
    so the `emptyos/sdk/userhome_venv.py` subprocess pattern would buy nothing
    and cost a process spawn per conversion. Install it into the daemon env:

        pip install firecrawl-anydoc

    Until then `available()` is False and the read chain is byte-for-byte
    unchanged (graceful enhancement, same contract as the other three).

100% local — CLAUDE.md rules 18/19:
    anydoc's Rust core never makes a network call. Its only cloud path is the
    opt-in `ocr="hosted"` argument, which routes scanned pages to Firecrawl
    Parse. **This plugin never passes `ocr`**, so a scanned document raises
    `NeedsOcrError` and falls through to the `ocr` plugin (local GPU marker),
    which is exactly where it belongs. Do not add an `ocr=` argument here — a
    cloud document path must go through the consent gate, not a plugin flag.

Public service (apps may reach via `self.require("legacy-doc")`):
    async def convert(self, path: str) -> str
        Convert one legacy document to markdown. Raises RuntimeError on failure.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.basepath import resolve_under_base
from emptyos.capabilities import Provider
from emptyos.sdk import BasePlugin

# The legacy slice ONLY. Every extension here is one no other read provider
# claims — deliberately NOT .docx/.xlsx/.pptx/.epub/.html/.csv/.json/.xml/.zip
# (markitdown owns those), NOT .xls (markitdown owns it via its [xls] extra),
# and NOT .pdf (the KB page-marked extractor and the ocr plugin own it).
# Adding an extension another provider already handles would shadow a working,
# proven path with an unproven one.
SUPPORTED_EXTENSIONS = {
    ".doc",   # Word 97-2003
    ".docm",  # Word macro-enabled
    ".wps",   # Works / Kingsoft Writer (Word97-family container)
    ".rtf",   # Rich Text Format
    ".ppt",   # PowerPoint 97-2003 -- the 360-file hole; nothing else reads it
    ".pps",   # PowerPoint 97-2003 slideshow
    ".pot",   # PowerPoint 97-2003 template
    ".pptm",  # PowerPoint macro-enabled
    ".ppsx",  # PowerPoint slideshow (XML)
    ".ppsm",  # PowerPoint macro-enabled slideshow
    ".odt",   # OpenDocument text
    ".ods",   # OpenDocument spreadsheet
    ".odp",   # OpenDocument presentation
    ".xlsb",  # Excel binary workbook
    ".xlsm",  # Excel macro-enabled
}


class _UnsupportedFormat(Exception):
    """Raised so `Capability.execute` moves on to the next read provider."""


class LegacyDocReadProvider(Provider):
    """`read` provider for the legacy document slice.

    Registered at priority 0 so it sees every read, but it only *handles*
    `SUPPORTED_EXTENSIONS`; anything else raises `_UnsupportedFormat`, which the
    capability treats as a provider failure and continues past. The suffix check
    is a cheap string compare and runs before anydoc is even imported, so the
    vault's hot path of `.md` reads pays nothing.
    """

    name = "legacy-doc"

    def __init__(self, plugin: "LegacyDocPlugin", base_path: str = ""):
        self._plugin = plugin
        self.base_path = Path(base_path) if base_path else None

    async def available(self) -> bool:
        return await self._plugin.available()

    async def execute(self, *, path: str, **kwargs) -> str:
        target = self._resolve(path)
        suffix = target.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise _UnsupportedFormat(suffix)
        return await self._plugin.convert(str(target))

    def _resolve(self, path: str) -> Path:
        return resolve_under_base(path, self.base_path)


class LegacyDocPlugin(BasePlugin):
    name = "legacy-doc"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        # None = not yet probed; True/False = anydoc did (or did not) import.
        self._import_ok: bool | None = None
        self._import_err: str = ""
        self._version: str = "?"

    async def connect(self) -> None:
        self._probe_import()
        if self._import_ok:
            print(f"[legacy-doc] ready — anydoc {self._version}")
        else:
            print(
                "[legacy-doc] anydoc not installed — legacy .doc/.rtf/.ppt/.wps "
                "reads are unchanged.\n"
                "             Run: pip install firecrawl-anydoc\n"
                f"             ({self._import_err})"
            )

        # Register regardless — `available()` gates it, so a fresh clone without
        # anydoc leaves the read chain exactly as it was.
        try:
            read = self.kernel.capabilities.get("read")
        except Exception:
            return
        base = ""
        try:
            np = getattr(self.kernel.config, "notes_path", None)
            base = str(np) if np else ""
        except Exception:
            base = ""
        read.add_provider(LegacyDocReadProvider(self, base_path=base), priority=0)

    def _probe_import(self) -> None:
        """Cache whether anydoc imports, and its version. Never raises.

        The import is the probe: a wheel built for another platform fails here,
        not at `find_spec`, so importing is the only honest check.
        """
        try:
            import anydoc  # noqa: F401  (the import IS the probe)
        except Exception as e:  # ImportError, or a bad wheel for this platform
            self._import_ok, self._import_err = False, f"{type(e).__name__}: {e}"
            return
        self._import_ok, self._import_err = True, ""
        # Version is cosmetic and probed SEPARATELY on purpose: `anydoc.version`
        # is importlib.metadata's function leaked into their namespace, so it
        # raises TypeError when called bare. Inside the try above that failure
        # would flip a perfectly working install to "unavailable".
        try:
            from importlib.metadata import version

            self._version = version("firecrawl-anydoc")
        except Exception:
            self._version = "?"

    async def available(self) -> bool:
        if self._import_ok is None:
            self._probe_import()
        return bool(self._import_ok)

    async def convert(self, path: str) -> str:
        """Convert one legacy document to markdown. Raises RuntimeError on failure."""
        if not await self.available():
            raise RuntimeError("anydoc is not installed (pip install firecrawl-anydoc)")

        import anydoc

        def _run() -> str:
            # NO ocr= argument, deliberately. See the module docstring: passing
            # it would send document pages to a third-party host, bypassing the
            # cloud-consent gate. A scanned file must fall through to `ocr`.
            return anydoc.to_markdown(path)

        try:
            # anydoc is synchronous Rust; a multi-hundred-ms call on the event
            # loop stalls every other task (.claude/rules/debugging.md).
            return await asyncio.to_thread(_run)
        except Exception as e:
            raise RuntimeError(f"anydoc failed on {Path(path).name}: {e}") from e
