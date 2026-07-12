"""Reader — import EPUB / PDF / DOCX / HTML / text into a readable vault book.

The conversion muscle already exists as plugins: `markitdown` handles the
office/ebook formats through the ``read`` capability, and `ocr` handles scanned
PDFs. This module is the plumbing that was missing — stage the upload, route it
to the right converter, split the markdown into chapters, and write a book in
the shape ``_list_books`` already understands.

Owns: the extension→converter routing table, the chapter splitter, title
sanitisation/collision handling, and the ``POST /api/import`` endpoint.

**Born-digital PDFs must never go through ``self.read()``.** markitdown excludes
PDF and the ocr plugin falls through when a text layer is present, so a
born-digital PDF lands on the filesystem read provider and comes back as
mojibake. The pdf branch below routes explicitly instead.

Pure helpers are module-level (unit-tested in ``tests/test_unit_reader_import.py``);
the mixin holds everything that touches the app.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from ._helpers import _slugify

if TYPE_CHECKING:
    from .app import ReaderApp  # noqa: F401 — for type hints only


IMPORT_EXTENSIONS = {".epub", ".pdf", ".docx", ".html", ".htm", ".txt", ".md", ".markdown"}
MARKITDOWN_EXTENSIONS = {".epub", ".docx", ".html", ".htm"}
PASSTHROUGH_EXTENSIONS = {".txt", ".md", ".markdown"}

MAX_IMPORT_BYTES = 100 * 1024 * 1024  # 100 MB
MAX_CHAPTERS = 300  # beyond this the "headings" clearly aren't chapters
MIN_PREAMBLE_CHARS = 200  # shorter than this and the preamble joins chapter 1
MIN_USEFUL_CHARS = 50  # a conversion yielding less than this produced nothing

# PDF probe tuning. The probe samples the *first* pages, and a book's first
# pages are often a sparse title/copyright page — so a born-digital book can
# easily read as "scanned". We therefore sample deeper, accept a thinner page,
# and (below LARGE_PDF_PAGES) try extraction anyway before reaching for OCR.
PDF_PROBE_PAGES = 10
PDF_PROBE_MIN_CHARS = 100
LARGE_PDF_PAGES = 80  # above this, trust the probe rather than scan every page

_ILLEGAL_TITLE_CHARS = r'<>:"/\\|?*'

# User-facing wording stays generic (Rule 14 — no plugin/vendor names, no repo
# paths in a toast). The operator detail goes to syslog, where it belongs.
_NO_CONVERTER = "document conversion isn't available on this machine"
_NO_OCR = "this file needs text recognition, which isn't available on this machine"


# ─── Pure helpers ───────────────────────────────────────────────────


def route_for_suffix(suffix: str) -> str:
    """Which converter handles this extension: passthrough | markitdown | pdf | unsupported."""
    suffix = (suffix or "").lower()
    if suffix in PASSTHROUGH_EXTENSIONS:
        return "passthrough"
    if suffix in MARKITDOWN_EXTENSIONS:
        return "markitdown"
    if suffix == ".pdf":
        return "pdf"
    return "unsupported"


def sanitize_title(name: str) -> str:
    """A vault- and Windows-safe book title. Unicode (incl. CJK) survives."""
    title = "".join(c for c in (name or "") if c not in _ILLEGAL_TITLE_CHARS and ord(c) >= 32)
    title = re.sub(r"\s+", " ", title).strip()
    title = title.rstrip(". ")
    return title[:80].strip() or "Imported Book"


def unique_title(title: str, existing: set[str]) -> str:
    """Append ' (2)', ' (3)'… until the title (and its slug) is free.

    ``existing`` should carry both names and slugs, case-folded: two pure-CJK
    titles slugify to the same ascii fallback, so name-uniqueness alone leaks.
    """
    taken = {e.casefold() for e in existing}

    def clashes(candidate: str) -> bool:
        return candidate.casefold() in taken or _slugify(candidate).casefold() in taken

    if not clashes(title):
        return title
    n = 2
    while clashes(f"{title} ({n})"):
        n += 1
    return f"{title} ({n})"


def _heading_title(line: str, index: int) -> str:
    text = re.sub(r"^#{1,6}\s*", "", line).strip()
    text = re.sub(r"[*_`\[\]]", "", text).strip()
    return text or f"Chapter {index}"


def _heading_lines(lines: list[str], level: int) -> list[int]:
    """Indices of level-N ATX headings, ignoring anything inside a code fence."""
    marker = "#" * level + " "
    out: list[int] = []
    fenced = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
            continue
        if not fenced and line.startswith(marker):
            out.append(i)
    return out


def split_chapters(md: str, fallback_title: str) -> list[tuple[str, str]]:
    """Split converted markdown into ``[(chapter_title, chapter_body)]``.

    Bodies keep their own heading line. Splits on H1 when there are 2+, else on
    H2 when there are 2+, else returns the whole document as a single chapter —
    which the reader renders as a single-file book.
    """
    text = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)  # defensive frontmatter strip
    lines = text.split("\n")

    marks = _heading_lines(lines, 1)
    if len(marks) < 2:
        marks = _heading_lines(lines, 2)
    if len(marks) < 2 or len(marks) > MAX_CHAPTERS:
        return [(fallback_title, text.strip())]

    chapters: list[tuple[str, str]] = []
    for n, start in enumerate(marks, start=1):
        end = marks[n] if n < len(marks) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        chapters.append((_heading_title(lines[start], n), body))

    preamble = "\n".join(lines[: marks[0]]).strip()
    if preamble:
        if len(re.sub(r"\s", "", preamble)) > MIN_PREAMBLE_CHARS:
            chapters.insert(0, ("Front Matter", preamble))
        else:
            first_title, first_body = chapters[0]
            chapters[0] = (first_title, f"{preamble}\n\n{first_body}")
    return chapters


def chapter_filename(index: int, total: int, title: str) -> str:
    """``NN-<slug>.md`` — zero-padded so lexical sort is reading order."""
    width = max(2, len(str(total)))
    return f"{str(index).zfill(width)}-{_slugify(title)[:60]}.md"


# ─── The mixin ──────────────────────────────────────────────────────


class ImportingMixin:
    """Import flow. Mixed into ``ReaderApp`` — see app.py."""

    async def _convert_to_markdown(self, staged: Path, suffix: str) -> tuple[str, list[str]]:
        """Staged upload → (markdown, warnings). Raises ValueError with a
        message meant for the user when no converter can handle the file."""
        route = route_for_suffix(suffix)

        if route == "passthrough":
            return staged.read_text(encoding="utf-8", errors="replace"), []

        if route == "markitdown":
            plugin = self.service("markitdown")
            if plugin is None or not await plugin.available():
                self.log_warn(
                    f"import: no markitdown provider for {suffix} — install the markitdown "
                    "venv (see .claude/rules/environment.md § user-home Python envs)"
                )
                raise ValueError(f"{_NO_CONVERTER} — can't read {suffix} files")
            try:
                return await plugin.convert(str(staged), timeout=180.0), []
            except Exception as e:  # noqa: BLE001 — the reason is operator detail
                self.log_error(f"import: markitdown failed on {suffix}: {e}")
                raise ValueError(f"couldn't read this {suffix} file") from e

        if route == "pdf":
            return await self._convert_pdf(staged)

        raise ValueError(f"unsupported file type '{suffix}'")

    async def _convert_pdf(self, staged: Path) -> tuple[str, list[str]]:
        """Born-digital → pypdf; scanned → the ocr plugin. Never ``self.read()``.

        The probe decides whether extraction is worth attempting, not whether the
        PDF is born-digital: a real extraction that yields text always wins, so a
        book with a sparse title page can't be mis-routed to OCR.
        """
        from emptyos.sdk.pdf import extract_pdf_text, pdf_text_stats

        pages, has_text = await asyncio.to_thread(
            pdf_text_stats, str(staged),
            sample_pages=PDF_PROBE_PAGES, min_chars_per_page=PDF_PROBE_MIN_CHARS,
        )
        warnings: list[str] = []

        if has_text is None:
            self.log_warn("import: pdf probe returned nothing — pypdf missing or file unreadable")
            raise ValueError("couldn't read this PDF — it may be corrupt")

        # Extracting a big scan means calling extract_text() on every page for
        # nothing, so above LARGE_PDF_PAGES we take the probe at its word.
        if has_text or (pages or 0) <= LARGE_PDF_PAGES:
            try:
                text = await asyncio.to_thread(extract_pdf_text, str(staged))
            except Exception as e:  # noqa: BLE001 — a malformed PDF is not a crash
                self.log_warn(f"import: pdf text extraction failed, trying OCR: {e}")
                text = ""
            if len(text.strip()) >= MIN_USEFUL_CHARS:
                lines = text.split("\n")
                if not _heading_lines(lines, 1) and not _heading_lines(lines, 2):
                    warnings.append("PDF text has no headings — imported as a single file")
                return text, warnings
            if has_text:
                warnings.append("PDF reported a text layer but yielded nothing — falling back to OCR")

        ocr = self.service("ocr")
        if ocr is None or not await ocr.available():
            self.log_warn(
                "import: scanned PDF but no OCR provider — install the marker-ocr venv "
                "(see .claude/rules/environment.md § user-home Python envs)"
            )
            raise ValueError(f"this PDF is a scan, and {_NO_OCR}")
        warnings.append("scanned PDF — the text may contain recognition errors")
        return await ocr.ocr(str(staged), page_count=pages or 1), warnings

    def _existing_book_names(self) -> set[str]:
        names: set[str] = set()
        for b in self._list_books():
            names.add(b["title"])
            names.add(b["slug"])
        return names

    @web_route("POST", "/api/import")
    async def api_import(self, request):
        """Convert an uploaded book file into a readable vault book.

        Multipart: ``file`` (required), ``title`` (optional — defaults to the
        filename stem). Conversion is synchronous; a scanned PDF can take
        minutes.
        """
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return {"error": "no file uploaded"}

        filename = getattr(upload, "filename", "") or "book"
        suffix = Path(filename).suffix.lower()
        if suffix not in IMPORT_EXTENSIONS:
            return {
                "error": f"unsupported file type '{suffix or filename}'",
                "hint": "supported: " + " ".join(sorted(IMPORT_EXTENSIONS)),
            }

        data = await upload.read()
        if not data:
            return {"error": "uploaded file is empty"}
        if len(data) > MAX_IMPORT_BYTES:
            return {"error": f"file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB"}

        books_dir = self._books_dir()
        if not books_dir:
            return {"error": "no vault configured — set notes.path in emptyos.toml"}
        books_dir.mkdir(parents=True, exist_ok=True)

        title = sanitize_title((form.get("title") or "").strip() or Path(filename).stem)

        # Binary never touches the vault — only the converted markdown does.
        with tempfile.TemporaryDirectory(prefix="reader-import-") as tmp:
            staged = Path(tmp) / f"src{suffix}"
            staged.write_bytes(data)
            try:
                md, warnings = await self._convert_to_markdown(staged, suffix)
            except ValueError as e:
                return {"error": str(e)}

        if len(md.strip()) < MIN_USEFUL_CHARS:
            return {"error": "conversion produced no readable text"}

        chapters = split_chapters(md, fallback_title=title)
        title = unique_title(title, self._existing_book_names())
        slug = _slugify(title)
        imported = datetime.now(timezone.utc).isoformat()
        base_fm = {"book": title, "source": filename, "imported": imported, "imported_by": "reader"}

        if len(chapters) == 1:
            # Single-file book. Frontmatter is stripped on read, so it's invisible.
            self.vault_create_note(f"{self._books_dir_rel()}/{title}.md", base_fm, chapters[0][1])
            kind = "file"
        else:
            total = len(chapters)
            for i, (chapter_title, body) in enumerate(chapters, start=1):
                # No `_meta.md` — `_`-prefixed files are invisible to _list_books.
                rel = f"{self._books_dir_rel()}/{title}/{chapter_filename(i, total, chapter_title)}"
                self.vault_create_note(rel, {**base_fm, "chapter": i, "chapter_title": chapter_title}, body)
            kind = "dir"

        await self.emit(
            "reader:book_imported",
            {"slug": slug, "title": title, "chapters": len(chapters), "source_ext": suffix},
        )
        return {
            "ok": True,
            "slug": slug,
            "title": title,
            "kind": kind,
            "chapters": len(chapters),
            "warnings": warnings,
            "path": f"{self._books_dir_rel()}/{title}",
        }
