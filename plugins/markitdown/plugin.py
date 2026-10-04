"""MarkItDown plugin — local document → markdown conversion behind `read`.

Wraps Microsoft MarkItDown (`convert_local`) as a provider on the `read`
capability for office / structured document formats the plain filesystem reader
can't turn into useful text: **PPTX, XLSX, DOCX, EPub, HTML, CSV, JSON, XML, and
ZIP traversal**.

What it deliberately does NOT handle:
  - **PDF** — KB-bound PDFs keep the existing page-marked extractor
    (the vault-source-digest flow needs structure fidelity MarkItDown discards).
    `.pdf` is absent from `SUPPORTED_EXTENSIONS`, so a PDF read falls straight
    through to the next provider, untouched.
  - **.md / .txt** and anything else — plain text reads belong to the filesystem
    provider; MarkItDown is never invoked for them (the provider raises
    `_UnsupportedFormat` before importing anything, so the vault's hot path of
    `.md` reads pays nothing).

100% local: MarkItDown runs with `enable_plugins=False`, no `docintel_endpoint`,
no Azure Content Understanding, no cloud `llm_client` (CLAUDE.md rules 18/19).
If image-OCR-via-vision is wanted later, route it through the consent-gated
cloud `think` provider — never a hardcoded OpenAI/Azure client here.

Why a user-home venv instead of the daemon interpreter:
    MarkItDown's core `magika` dependency pins `onnxruntime<=1.20.1` on Windows,
    which conflicts with the daemon env's `onnxruntime-gpu 1.23.2`. Installing
    markitdown in-process would downgrade (and break) the GPU runtime. So
    conversion runs in an isolated user-home venv, reached via subprocess — the
    same pattern as `plugins/cadquery/`. See
    `.claude/rules/environment.md` § "User-home Python envs for heavy deps".

    [plugins.markitdown]
    python_exe = "C:/Users/<you>/AppData/Local/eos/envs/markitdown-3.13/Scripts/python.exe"

If `python_exe` is unset, the canonical default
`%LOCALAPPDATA%/eos/envs/markitdown-3.13/Scripts/python.exe` (Windows) or
`~/.local/eos/envs/markitdown-3.13/bin/python` (POSIX) is used. Set up with:

    uv venv --python 3.13 "%LOCALAPPDATA%/eos/envs/markitdown-3.13"
    uv pip install --python "<that>/Scripts/python.exe" "markitdown[pptx,xlsx,xls,docx]"

Public service (apps may reach via `self.require("markitdown")`):
    async def convert(self, path: str, *, timeout: float = 120.0) -> str
        Convert one document to markdown. Raises RuntimeError on failure.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from emptyos.basepath import resolve_under_base
from emptyos.capabilities import Provider
from emptyos.sdk import BasePlugin
from emptyos.sdk.userhome_venv import default_venv_python, probe_launch, run_venv

# Extensions MarkItDown owns on the `read` chain. PDF is intentionally absent —
# see the module docstring. .md/.txt/.* not listed here fall through to the
# filesystem read provider.
SUPPORTED_EXTENSIONS = {
    ".pptx",  # PowerPoint
    ".xlsx",  # Excel
    ".xls",   # Excel 97-2003 (needs the [xls] extra -> xlrd; doc_extract.py
              #                refuses legacy .xls, so nothing else covers it)
    ".docx",  # Word
    ".epub",  # EPub (core handles via zip + html, no extra dep)
    ".html",  # HTML
    ".htm",
    ".csv",  # CSV
    ".json",  # JSON
    ".xml",  # XML
    ".zip",  # ZIP archive traversal
}


def _default_python_exe() -> str:
    """Canonical user-home path for the markitdown venv.
    See .claude/rules/environment.md § User-home Python envs."""
    return default_venv_python("markitdown", "3.13")


class _UnsupportedFormat(Exception):
    """Raised by the provider so the read-capability chain falls through to the
    next provider (filesystem). NOT an error — a routing signal."""


class MarkItDownReadProvider(Provider):
    """`read` provider that converts office/structured docs to markdown.

    Added at priority 0 so it gets first crack at every read, but it only
    *handles* `SUPPORTED_EXTENSIONS`; for anything else it raises
    `_UnsupportedFormat`, which `Capability.execute` treats like any other
    provider failure and continues to the filesystem provider. The suffix check
    runs before any subprocess, so non-document reads cost a cheap string check.
    """

    name = "markitdown"

    def __init__(self, plugin: MarkItDownPlugin, base_path: str = ""):
        self._plugin = plugin
        self.base_path = Path(base_path) if base_path else None

    async def available(self) -> bool:
        return await self._plugin.available()

    async def execute(self, *, path: str, **kwargs) -> str:
        target = self._resolve(path)
        suffix = target.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            # .md/.txt/.pdf/etc. — let the filesystem provider handle it.
            raise _UnsupportedFormat(suffix)
        return await self._plugin.convert(str(target))

    def _resolve(self, path: str) -> Path:
        return resolve_under_base(path, self.base_path)


class MarkItDownPlugin(BasePlugin):
    name = "markitdown"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._python_exe: str = ""
        self._runner: str = ""
        # Launch-probe result: None = not yet probed, True/False = the venv
        # interpreter actually imported markitdown (or didn't) at connect().
        self._launch_ok: bool | None = None
        self._launch_err: str = ""

    async def connect(self) -> None:
        self._python_exe = self.config("python_exe", "") or _default_python_exe()
        self._runner = str(Path(__file__).parent / "runner.py")

        if not Path(self._python_exe).exists():
            print(
                f"[markitdown] python_exe not found: {self._python_exe}\n"
                f"             Run: uv venv --python 3.13 "
                f'"{Path(self._python_exe).parent.parent}" && '
                f'uv pip install --python "{self._python_exe}" "markitdown[pptx,xlsx,xls,docx]"\n'
                f"             Or set [plugins.markitdown] python_exe in emptyos.toml.\n"
                f"             (read capability is unchanged until the venv exists)"
            )
        elif not Path(self._runner).exists():
            print(f"[markitdown] runner.py missing at {self._runner} (corrupted install?)")
        else:
            await self._probe_launch()
            if self._launch_ok:
                print(f"[markitdown] ready — python_exe={self._python_exe}")
            else:
                print(
                    f"[markitdown] python_exe exists but markitdown won't import: "
                    f"{self._python_exe}\n             {self._launch_err}"
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
        read.add_provider(MarkItDownReadProvider(self, base_path=base), priority=0)

    async def _probe_launch(self) -> None:
        """Confirm the venv can actually convert by importing markitdown (which
        loads magika/onnxruntime — allow a slow cold start). Caches the result;
        never raises."""
        self._launch_ok, self._launch_err = await probe_launch(
            self._python_exe, ["-c", "import markitdown"], timeout=30.0
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

    async def convert(self, path: str, *, timeout: float = 120.0) -> str:
        """Convert one document to markdown via the venv runner.

        Returns the markdown string. Raises RuntimeError on any failure (the
        read-capability chain treats that as a provider miss and falls through
        to the filesystem provider)."""
        if not await self.available():
            raise RuntimeError(f"markitdown venv unavailable at {self._python_exe}")
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"no such file: {path}")

        with tempfile.TemporaryDirectory(prefix="markitdown-") as tmp:
            out_path = Path(tmp) / "out.md"
            res = await run_venv(
                self._python_exe, [self._runner, str(src), str(out_path)], timeout=timeout
            )
            if res.launch_failed:
                self._launch_ok = False
                self._launch_err = res.launch_exc
                raise RuntimeError(f"failed to launch markitdown interpreter: {res.launch_exc}")
            if res.timed_out:
                raise RuntimeError(f"markitdown conversion timed out after {timeout}s")

            status: dict = {}
            if res.stdout:
                try:
                    status = json.loads(res.stdout.splitlines()[-1])
                except json.JSONDecodeError:
                    status = {}
            if res.returncode != 0 or not status.get("ok"):
                err = status.get("error") or res.stderr or f"exit code {res.returncode}"
                raise RuntimeError(f"markitdown conversion failed: {err}")
            return out_path.read_text(encoding="utf-8")
