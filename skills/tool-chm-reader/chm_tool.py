#!/usr/bin/env python3
"""CHM (compiled HTML Help) reader tool — decompile, TOC, extract, search, dump.

Zero pip dependencies. Decompiles via 7-Zip (`7z x`) when available, falling
back to Windows' built-in `hh.exe -decompile`. Extracted files are cached under
%TEMP%/chm_tool/<name>-<hash>/ keyed by path+size+mtime, so repeated commands
against the same .chm don't re-decompile.

Commands:
    info    <file.chm>                       counts, title, extractor, cache dir
    toc     <file.chm>                       numbered table of contents (.hhc)
    list    <file.chm>                       every HTML page (incl. non-TOC ones)
    extract <file.chm> --start N --end M     TOC entries N..M as markdown, stdout
    page    <file.chm> <internal-path>       one page as markdown, stdout
    search  <file.chm> <keyword>             pages + line snippets containing keyword
    dump    <file.chm> <out.md>              whole CHM to one markdown file, TOC order

Sibling of skills/tool-pdf-reader/pdf_tool.py — same info/toc/extract/search/dump
verb set, CHM-shaped (topics instead of pages).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------- decompile


def _find_7z() -> str | None:
    p = shutil.which("7z")
    if p:
        return p
    for cand in (
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ):
        if Path(cand).exists():
            return cand
    return None


def _find_hh() -> str | None:
    p = shutil.which("hh")
    if p:
        return p
    cand = Path(os.environ.get("WINDIR", r"C:\Windows")) / "hh.exe"
    return str(cand) if cand.exists() else None


def cache_dir_for(chm: Path) -> Path:
    st = chm.stat()
    key = hashlib.md5(
        f"{chm.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()
    ).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "chm_tool" / f"{chm.stem}-{key}"


def decompile(chm: Path) -> Path:
    """Extract the CHM once; return the cached extraction dir."""
    out = cache_dir_for(chm)
    marker = out / ".chm_tool_done"
    if marker.exists():
        return out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    sevenzip = _find_7z()
    if sevenzip:
        r = subprocess.run(
            [sevenzip, "x", "-y", f"-o{out}", str(chm)],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0 and not list(out.rglob("*.htm*")):
            sys.exit(f"7z extraction failed (exit {r.returncode}): {r.stderr.strip()[:300]}")
    else:
        hh = _find_hh()
        if not hh:
            sys.exit(
                "No CHM extractor found. Install 7-Zip (7z on PATH or "
                r"C:\Program Files\7-Zip) or run on Windows where hh.exe exists."
            )
        subprocess.run([hh, "-decompile", str(out), str(chm)], timeout=600)
        # hh.exe is a GUI-subsystem app and may return before extraction
        # settles — wait until the file count stops changing.
        last = -1
        for _ in range(120):
            n = sum(1 for _ in out.rglob("*"))
            if n == last and n > 0:
                break
            last = n
            time.sleep(0.5)
        if not list(out.rglob("*.htm*")):
            sys.exit("hh.exe -decompile produced no HTML files")

    marker.write_text("ok", encoding="utf-8")
    return out


# ---------------------------------------------------------------- helpers


def read_html(p: Path) -> str:
    """Read an HTML file honouring its meta charset; CHM pages are commonly
    windows-1252, not UTF-8."""
    raw = p.read_bytes()
    m = re.search(rb"charset=[\"']?([A-Za-z0-9_\-]+)", raw[:2048], re.I)
    encodings = ([m.group(1).decode("ascii", "ignore")] if m else []) + ["utf-8", "cp1252"]
    for enc in encodings:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def html_pages(root: Path) -> list[Path]:
    """All content pages, skipping CHM system files (#SYSTEM, $WWKeywordLinks…)."""
    pages = []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in (".htm", ".html") and not p.name.startswith(("#", "$")):
            pages.append(p)
    return pages


# ---------------------------------------------------------------- TOC (.hhc)


class _HHCParser(HTMLParser):
    """Parse a .hhc sitemap: <UL> nesting = depth, <OBJECT type=text/sitemap>
    with <param name=Name|Local> = one entry."""

    def __init__(self):
        super().__init__()
        self.entries: list[dict] = []  # {depth, name, local}
        self._depth = 0
        self._cur: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "ul":
            self._depth += 1
        elif tag == "object" and a.get("type", "").lower() == "text/sitemap":
            self._cur = {"depth": self._depth, "name": "", "local": ""}
        elif tag == "param" and self._cur is not None:
            n = a.get("name", "").lower()
            if n == "name" and not self._cur["name"]:
                self._cur["name"] = a.get("value", "")
            elif n == "local" and not self._cur["local"]:
                self._cur["local"] = a.get("value", "").replace("\\", "/")

    def handle_endtag(self, tag):
        if tag == "ul":
            self._depth = max(0, self._depth - 1)
        elif tag == "object" and self._cur is not None:
            if self._cur["name"] or self._cur["local"]:
                self.entries.append(self._cur)
            self._cur = None


def parse_toc(root: Path) -> list[dict]:
    hhcs = [p for p in root.rglob("*.hhc") if not p.name.startswith(("#", "$"))]
    if not hhcs:
        return []
    # Largest .hhc is the real TOC when several exist.
    hhc = max(hhcs, key=lambda p: p.stat().st_size)
    parser = _HHCParser()
    parser.feed(read_html(hhc))
    return parser.entries


def resolve_local(root: Path, local: str) -> Path | None:
    """Map a TOC 'Local' value to an extracted file (case-insensitive)."""
    if not local:
        return None
    local = local.split("#")[0].lstrip("/")
    cand = root / local
    if cand.exists():
        return cand
    lower = local.lower()
    for p in root.rglob("*"):
        if p.is_file() and str(p.relative_to(root)).replace("\\", "/").lower() == lower:
            return p
    return None


# ---------------------------------------------------------------- HTML → MD


class _H2M(HTMLParser):
    """Compact HTML → markdown converter for help-file content: headings,
    paragraphs, lists, tables, pre/code, bold/italic, image alt text."""

    _SKIP = {"script", "style", "head", "title", "meta", "link"}
    _BLOCK_END = {"p", "div", "section", "article", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0
        self._pre = False
        self._lists: list[dict] = []  # {"ordered": bool, "n": int}
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._table_rows = 0

    # -- emit helpers
    def _out(self, text: str):
        if self._cell is not None:
            self._cell.append(text)
        else:
            self.parts.append(text)

    def _newline(self, n=1):
        self._out("\n" * n)

    # -- tags
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self._SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._newline(2)
            self._out("#" * int(tag[1]) + " ")
        elif tag == "p":
            self._newline(2)
        elif tag == "br":
            self._newline()
        elif tag in ("ul", "ol"):
            self._lists.append({"ordered": tag == "ol", "n": 0})
        elif tag == "li":
            self._newline()
            indent = "  " * max(0, len(self._lists) - 1)
            if self._lists and self._lists[-1]["ordered"]:
                self._lists[-1]["n"] += 1
                self._out(f"{indent}{self._lists[-1]['n']}. ")
            else:
                self._out(f"{indent}- ")
        elif tag == "pre":
            self._pre = True
            self._newline(2)
            self._out("```\n")
        elif tag in ("b", "strong"):
            self._out("**")
        elif tag in ("i", "em"):
            self._out("*")
        elif tag == "table":
            self._newline(2)
            self._table_rows = 0
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "img":
            alt = (a.get("alt") or "").strip()
            if alt:
                self._out(f"![{alt}]")
        elif tag == "hr":
            self._newline(2)
            self._out("---")
            self._newline()

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in ("b", "strong"):
            self._out("**")
        elif tag in ("i", "em"):
            self._out("*")
        elif tag == "pre":
            self._pre = False
            self._out("\n```")
            self._newline()
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            self._newline()
        elif tag in ("td", "th"):
            if self._row is not None and self._cell is not None:
                cell = re.sub(r"\s+", " ", "".join(self._cell)).strip()
                self._row.append(cell.replace("|", "\\|"))
            self._cell = None
        elif tag == "tr":
            if self._row is not None and any(c for c in self._row):
                self.parts.append("\n| " + " | ".join(self._row) + " |")
                self._table_rows += 1
                if self._table_rows == 1:
                    self.parts.append(
                        "\n|" + "|".join([" --- "] * len(self._row)) + "|"
                    )
            self._row = None
        elif tag == "table":
            self._table_rows = 0
            self._newline()
        elif tag in self._BLOCK_END:
            self._newline()

    def handle_data(self, data):
        if self._skip:
            return
        if self._pre:
            self._out(data)
        else:
            text = re.sub(r"\s+", " ", data)
            if text.strip() or (self.parts and not self.parts[-1].endswith("\n")):
                self._out(text)


def html_to_md(html_text: str) -> str:
    conv = _H2M()
    try:
        conv.feed(html_text)
        conv.close()
    except Exception:
        # Malformed page — fall back to a crude tag strip.
        return re.sub(r"<[^>]+>", " ", unescape(html_text))
    md = "".join(conv.parts)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def page_md(p: Path) -> str:
    return html_to_md(read_html(p))


# ---------------------------------------------------------------- commands


def _toc_or_pages(root: Path) -> tuple[list[dict], bool]:
    """TOC entries with resolvable pages; falls back to a flat page list when
    the CHM ships no .hhc. Second value = True when a real TOC was used."""
    toc = [e for e in parse_toc(root) if e.get("local")]
    if toc:
        return toc, True
    pages = html_pages(root)
    return [
        {"depth": 1, "name": p.stem, "local": str(p.relative_to(root)).replace("\\", "/")}
        for p in pages
    ], False


def cmd_info(chm: Path):
    root = decompile(chm)
    toc = parse_toc(root)
    pages = html_pages(root)
    title = next((e["name"] for e in toc if e.get("name")), chm.stem)
    print(f"File:      {chm}")
    print(f"Size:      {chm.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"Title:     {title}")
    print(f"HTML pages:{len(pages):>6}")
    print(f"TOC items: {len([e for e in toc if e.get('local')]):>6}"
          + ("" if toc else "   (no .hhc — commands fall back to flat page order)"))
    print(f"Extracted: {root}")


def cmd_toc(chm: Path):
    root = decompile(chm)
    entries, real = _toc_or_pages(root)
    if not real:
        print("(no .hhc TOC in this CHM — flat page list, same numbering as `list`)")
    for i, e in enumerate(entries, 1):
        indent = "  " * max(0, e["depth"] - 1)
        print(f"{i:>4}. {indent}{e['name'] or '(unnamed)'}  —  {e['local']}")


def cmd_list(chm: Path):
    root = decompile(chm)
    for p in html_pages(root):
        print(str(p.relative_to(root)).replace("\\", "/"))


def cmd_extract(chm: Path, start: int, end: int):
    root = decompile(chm)
    entries, _ = _toc_or_pages(root)
    if not entries:
        sys.exit("no pages found")
    lo, hi = max(1, start), min(len(entries), end)
    for i in range(lo, hi + 1):
        e = entries[i - 1]
        p = resolve_local(root, e["local"])
        print(f"\n\n{'=' * 70}\n## [{i}] {e['name']}  ({e['local']})\n{'=' * 70}\n")
        print(page_md(p) if p else "(page not found in archive)")


def cmd_page(chm: Path, local: str):
    root = decompile(chm)
    p = resolve_local(root, local)
    if not p:
        sys.exit(f"page not found: {local}")
    print(page_md(p))


def cmd_search(chm: Path, keyword: str):
    root = decompile(chm)
    entries, _ = _toc_or_pages(root)
    by_local = {e["local"].lower(): (i, e) for i, e in enumerate(entries, 1)}
    kw = keyword.lower()
    hits = 0
    for p in html_pages(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        text = page_md(p)
        lines = [ln.strip() for ln in text.splitlines() if kw in ln.lower()]
        if not lines:
            continue
        hits += 1
        idx, entry = by_local.get(rel.lower(), (None, None))
        label = f"[{idx}] {entry['name']}" if entry else rel
        print(f"\n--- {label}  ({rel})")
        for ln in lines[:5]:
            print(f"    {ln[:200]}")
        if len(lines) > 5:
            print(f"    … {len(lines) - 5} more matching lines")
    print(f"\n{hits} page(s) matched '{keyword}'")


def cmd_dump(chm: Path, out: Path):
    root = decompile(chm)
    entries, real = _toc_or_pages(root)
    seen: set[str] = set()
    parts: list[str] = [f"# {chm.name} — full dump\n"]
    parts.append(f"> Extracted {time.strftime('%Y-%m-%d')} via chm_tool.py; "
                 f"{'TOC order' if real else 'flat page order (no .hhc)'}.\n")
    for i, e in enumerate(entries, 1):
        p = resolve_local(root, e["local"])
        key = str(p.relative_to(root)).replace("\\", "/").lower() if p else e["local"].lower()
        heading = "#" * min(6, e["depth"] + 1)
        parts.append(f"\n\n{heading} [{i}] {e['name'] or e['local']}\n")
        parts.append(f"<!-- source: {e['local']} -->\n")
        if p is None:
            parts.append("\n*(page not found in archive)*\n")
            continue
        if key in seen:
            parts.append("\n*(duplicate of an earlier TOC entry — content omitted)*\n")
            continue
        seen.add(key)
        parts.append("\n" + page_md(p) + "\n")
    # Pages that exist in the archive but aren't in the TOC.
    unlisted = [p for p in html_pages(root)
                if str(p.relative_to(root)).replace("\\", "/").lower() not in seen]
    if real and unlisted:
        parts.append(f"\n\n## Unlisted pages ({len(unlisted)} not in TOC)\n")
        for p in unlisted:
            rel = str(p.relative_to(root)).replace("\\", "/")
            parts.append(f"\n\n### {p.stem}\n<!-- source: {rel} -->\n\n{page_md(p)}\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB, "
          f"{len(entries)} TOC entries, {len(unlisted) if real else 0} unlisted pages)")


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description="CHM reader: info/toc/list/extract/page/search/dump")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, *extra):
        s = sub.add_parser(name)
        s.add_argument("chm", type=Path)
        for e in extra:
            e(s)
        return s

    add("info")
    add("toc")
    add("list")
    add("extract",
        lambda s: s.add_argument("--start", type=int, default=1),
        lambda s: s.add_argument("--end", type=int, default=10))
    add("page", lambda s: s.add_argument("local"))
    add("search", lambda s: s.add_argument("keyword"))
    add("dump", lambda s: s.add_argument("out", type=Path))

    args = ap.parse_args()
    chm: Path = args.chm
    if not chm.exists():
        sys.exit(f"no such file: {chm}")

    if args.cmd == "info":
        cmd_info(chm)
    elif args.cmd == "toc":
        cmd_toc(chm)
    elif args.cmd == "list":
        cmd_list(chm)
    elif args.cmd == "extract":
        cmd_extract(chm, args.start, args.end)
    elif args.cmd == "page":
        cmd_page(chm, args.local)
    elif args.cmd == "search":
        cmd_search(chm, args.keyword)
    elif args.cmd == "dump":
        cmd_dump(chm, args.out)


if __name__ == "__main__":
    main()
