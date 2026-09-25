"""OCR plugin — local GPU OCR for scanned / image-only PDFs behind `read`.

Fills the one ingestion gap markitdown leaves open. `plugins/markitdown/`
deliberately EXCLUDES PDF (the KB page-marked extractor owns born-digital PDFs),
and that extractor's `digest_pdf.py` exits 3 when a PDF has no extractable text
layer — a scanned document. This plugin is what that branch can route to instead
of failing: it injects a provider into the `read` capability that runs datalab
**marker-pdf** on the GPU to turn an image-only PDF (or an image) into
page-marked markdown, fully local, $0.

The provider only fires when it can confirm there is no text layer. Every read of
a `.pdf` runs a CHEAP in-process `pypdf` probe first (no subprocess, no torch):

  - text layer present  → born-digital → raise `_NeedsNoOcr`, fall through (the
    page-marked extractor / filesystem provider keeps handling it — never shadowed)
  - text layer absent   → scanned     → invoke marker
  - undetermined        → fall through (safe default; never auto-OCR on a guess
    unless `[plugins.ocr] force = true`)

Images (.png/.jpg/.tiff/...) have no text-layer concept, so they always OCR.

Output contract: page-marked markdown using the KB `_fulltext/` convention —
`<!-- Page N of TOTAL -->` (1-INDEXED) — so it drops straight into
`vault-source-digest`. (marker emits 0-indexed `{N}----` separators; the runner
post-processes them.)

Why a user-home venv instead of the daemon interpreter:
    `marker-pdf` pulls a large surya-ocr / torch stack, and installing it
    in-process downgrades the daemon env's CUDA torch to CPU (see the install
    gotcha below). So OCR runs in an isolated user-home venv reached via
    subprocess — the same pattern as `plugins/markitdown/` and
    `plugins/cadquery/`. This is the THIRD consumer of
    `emptyos/sdk/userhome_venv.py`. See `.claude/rules/environment.md`
    § "User-home Python envs for heavy deps".

    [plugins.ocr]
    python_exe = "C:/Users/<you>/AppData/Local/eos/envs/marker-ocr-3.12/Scripts/python.exe"
    cache_dir  = ""      # empty (default) = standard HF cache (~/.cache/huggingface);
                         # set a path only to relocate the ~2GB surya weights
    force      = false   # OCR every PDF, skip the text-layer probe
    dpi        = 0       # 0 = marker default; else highres OCR DPI
    max_pages  = 1200    # refuse to OCR a PDF larger than this (runaway guard)

If `python_exe` is unset, the canonical default
`%LOCALAPPDATA%/eos/envs/marker-ocr-3.12/Scripts/python.exe` (Windows) or
`~/.local/eos/envs/marker-ocr-3.12/bin/python` (POSIX) is used. Set up with:

    uv venv --python 3.12 "%LOCALAPPDATA%/eos/envs/marker-ocr-3.12"
    uv pip install --python "<that>/Scripts/python.exe" marker-pdf
    # INSTALL GOTCHA — marker-pdf downgrades torch to CPU. Reinstall CUDA torch
    # (cu124 matches the daemon env + is proven on this machine's GPU):
    uv pip install --python "<that>/Scripts/python.exe" --index-url \
        https://download.pytorch.org/whl/cu124 "torch>=2.6,<3.0" "torchvision"
    # verify: <that> -c "import torch; print(torch.cuda.is_available())"  -> True
    # CPU still works (no GPU = slower, not broken).

Until the venv exists, the provider's `available()` is False, so the `read`
chain is byte-identical to today (graceful enhancement — never crash boot).

Public service (apps may reach via `self.require("ocr")`):
    async def ocr(self, path: str, *, page_count: int = 1, timeout: float | None = None) -> str
        OCR one scanned PDF / image to page-marked markdown. Raises RuntimeError
        on failure (the read chain treats that as a miss and falls through).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from emptyos.basepath import resolve_under_base
from emptyos.capabilities import Provider
from emptyos.sdk import BasePlugin
from emptyos.sdk.userhome_venv import default_venv_python, probe_launch, run_venv

# Raster formats the provider OCRs unconditionally (no text-layer concept). The
# runner converts these to a 1-page PDF before handing them to marker.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def _default_python_exe() -> str:
    """Canonical user-home path for the marker-ocr venv.
    See .claude/rules/environment.md § User-home Python envs."""
    return default_venv_python("marker-ocr", "3.12")


def _pdf_stats(path: str, *, sample_pages: int, min_chars_per_page: int) -> tuple[int | None, bool | None]:
    """Cheap in-process probe: (page_count, has_text_layer).

    Delegates to the shared SDK helper (reader's importer is the other caller).
    Never a marker subprocess. Runs in a worker thread (see `_NeedsNoOcr` path)."""
    from emptyos.sdk.pdf import pdf_text_stats

    return pdf_text_stats(path, sample_pages=sample_pages, min_chars_per_page=min_chars_per_page)


class _NeedsNoOcr(Exception):
    """Raised by the provider so the read-capability chain falls through to the
    next provider. NOT an error — a routing signal (mirrors markitdown's
    `_UnsupportedFormat`)."""


class OcrReadProvider(Provider):
    """`read` provider that OCRs scanned PDFs / images to page-marked markdown.

    Registered at priority 0 so it gets first crack at every read, but it only
    *handles* a `.pdf` with no text layer (or an image). For born-digital PDFs,
    plain-text reads, and every other suffix it raises `_NeedsNoOcr`, which
    `Capability.execute` treats like any provider miss and continues down the
    chain. 100% local → never cloud, so the consent gate never fires here.
    """

    name = "ocr"

    def __init__(self, plugin: OcrPlugin, base_path: str = ""):
        self._plugin = plugin
        self.base_path = Path(base_path) if base_path else None

    async def available(self) -> bool:
        return await self._plugin.available()

    async def execute(self, *, path: str, **kwargs) -> str:
        target = self._resolve(path)
        suffix = target.suffix.lower()
        is_pdf = suffix == ".pdf"
        is_img = suffix in IMAGE_EXTENSIONS
        if not (is_pdf or is_img):
            # .md/.txt/.docx/etc. — not ours; let the next provider handle it.
            raise _NeedsNoOcr(suffix)
        if not target.exists():
            raise _NeedsNoOcr("missing file")

        page_count = 1
        if is_pdf:
            page_count, has_text = await asyncio.to_thread(
                _pdf_stats,
                str(target),
                sample_pages=self._plugin.probe_pages,
                min_chars_per_page=self._plugin.min_chars_per_page,
            )
            if not self._plugin.force and has_text is not False:
                # born-digital (True) or undetermined (None) → don't shadow.
                raise _NeedsNoOcr("born-digital" if has_text else "text-layer undetermined")
            if self._plugin.max_pages and page_count and page_count > self._plugin.max_pages:
                raise _NeedsNoOcr(f"exceeds max_pages ({page_count}>{self._plugin.max_pages})")

        return await self._plugin.ocr(str(target), page_count=page_count or 1)

    def _resolve(self, path: str) -> Path:
        return resolve_under_base(path, self.base_path)


class OcrPlugin(BasePlugin):
    name = "ocr"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._python_exe: str = ""
        self._cache_dir: str = ""
        self._runner: str = ""
        self.force: bool = False
        self.dpi: int = 0
        self.max_pages: int = 1200
        self.probe_pages: int = 5
        self.min_chars_per_page: int = 50
        self.base_timeout: float = 180.0
        self.per_page_timeout: float = 12.0
        # Launch-probe result: None = not yet probed, True/False = the venv
        # interpreter actually imported marker (or didn't) at connect().
        self._launch_ok: bool | None = None
        self._launch_err: str = ""

    async def connect(self) -> None:
        self._python_exe = self.config("python_exe", "") or _default_python_exe()
        # Empty = standard HF cache (where the weights already live); set a path to relocate.
        self._cache_dir = self.config("cache_dir", "")
        self._runner = str(Path(__file__).parent / "runner.py")
        self.force = bool(self.config("force", False))
        self.dpi = int(self.config("dpi", 0) or 0)
        self.max_pages = int(self.config("max_pages", 1200) or 0)
        self.probe_pages = int(self.config("probe_pages", 5) or 0)
        self.min_chars_per_page = int(self.config("min_chars_per_page", 50) or 0)

        if not Path(self._python_exe).exists():
            print(
                f"[ocr] python_exe not found: {self._python_exe}\n"
                f"      Run: uv venv --python 3.12 "
                f'"{Path(self._python_exe).parent.parent}" && '
                f'uv pip install --python "{self._python_exe}" marker-pdf\n'
                f"      Then reinstall CUDA torch (marker downgrades it to CPU) — see plugin.py.\n"
                f"      Or set [plugins.ocr] python_exe in emptyos.toml.\n"
                f"      (read capability is unchanged until the venv exists)"
            )
        elif not Path(self._runner).exists():
            print(f"[ocr] runner.py missing at {self._runner} (corrupted install?)")
        else:
            await self._probe_launch()
            if self._launch_ok:
                print(f"[ocr] ready — python_exe={self._python_exe}")
            else:
                print(
                    f"[ocr] python_exe exists but marker won't import: "
                    f"{self._python_exe}\n      {self._launch_err}"
                )

        # Register the read provider regardless — `available()` gates it, so a
        # fresh clone with no venv just leaves the read chain unchanged.
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
        read.add_provider(OcrReadProvider(self, base_path=base), priority=0)

    async def _probe_launch(self) -> None:
        """Confirm the venv can import marker (loads a chunk of torch — allow a
        slow cold start). Caches the result; never raises."""
        self._launch_ok, self._launch_err = await probe_launch(
            self._python_exe, ["-c", "import marker"], timeout=60.0
        )

    async def available(self) -> bool:
        return (
            bool(self._python_exe)
            and Path(self._python_exe).exists()
            and Path(self._runner).exists()
            and self._launch_ok is not False
        )

    async def health_check(self) -> bool:
        return await self.available()

    async def ocr(self, path: str, *, page_count: int = 1, timeout: float | None = None) -> str:
        """OCR one scanned PDF / image to page-marked markdown via the venv runner.

        Returns the markdown string. Raises RuntimeError on any failure (the
        read-capability chain treats that as a provider miss and falls through)."""
        if not await self.available():
            raise RuntimeError(f"ocr venv unavailable at {self._python_exe}")
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"no such file: {path}")

        if timeout is None:
            timeout = self.base_timeout + max(1, page_count) * self.per_page_timeout

        with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
            out_path = Path(tmp) / "out.md"
            args = [self._runner, str(src), str(out_path)]
            if self._cache_dir:
                args += ["--cache-dir", self._cache_dir]
            if self.dpi > 0:
                args += ["--dpi", str(self.dpi)]
            if self.max_pages > 0:
                args += ["--max-pages", str(self.max_pages)]
            res = await run_venv(self._python_exe, args, timeout=timeout)
            if res.launch_failed:
                self._launch_ok = False
                self._launch_err = res.launch_exc
                raise RuntimeError(f"failed to launch ocr interpreter: {res.launch_exc}")
            if res.timed_out:
                raise RuntimeError(f"ocr timed out after {timeout:.0f}s ({page_count} pages)")

            status: dict = {}
            if res.stdout:
                try:
                    status = json.loads(res.stdout.splitlines()[-1])
                except json.JSONDecodeError:
                    status = {}
            if res.returncode != 0 or not status.get("ok"):
                err = status.get("error") or res.stderr or f"exit code {res.returncode}"
                raise RuntimeError(f"ocr failed: {err}")
            return out_path.read_text(encoding="utf-8")
