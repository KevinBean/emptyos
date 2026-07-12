#!/usr/bin/env python3
"""doc_extract.py — extract plain text from legacy office/archive documents.

Companion to fileindex_scan.py: the mining loop needs to *read* what the
ledger queues. Handles the formats that actually appear in the archive,
auto-detecting by magic bytes (extensions lie — .doc files in this corpus
are variously RTF, Word97 OLE, or even docx).

    python scripts/doc_extract.py <file> [--out FILE] [--max-chars N] [--grep PAT]

Formats: .docx/.xlsx/.pptx (zip+xml), RTF (brace state machine, \\'xx→gbk,
\\uN→unicode), Word97-family OLE incl. .wps (FIB → Clx piece table via
olefile), .pdf (pypdf), plain text. Legacy .xls/.ppt are refused loudly
(open+resave, or markitdown) instead of dumping binary noise.

Coverage boundary vs the read-capability plugins: markitdown handles the
MODERN formats (.docx/.xlsx/.pptx/...), the ocr plugin handles SCANNED
PDFs; this script covers the LEGACY slice (.doc/.rtf/.wps) neither does.
Graduation target if a daemon-side consumer appears: a `legacy-doc` read
enhancer plugin beside them (not an app), per CLAUDE.md rule 9.

Stdlib + olefile (Word97 path) + pypdf (PDF path); both degrade gracefully.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def sniff(raw: bytes) -> str:
    if raw[:4] == b"PK\x03\x04":
        return "zip"
    if raw[:5] == b"{\\rtf":
        return "rtf"
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"
    if raw[:5] == b"%PDF-":
        return "pdf"
    return "text"


def from_zip(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "word/document.xml" in names:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>|</w:tr>", "\n", xml)
            xml = re.sub(r"</w:tc>", " | ", xml)
            return re.sub(r"<[^>]+>", "", xml)
        if any(n.startswith("ppt/slides/slide") for n in names):
            out = []
            for n in sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)):
                x = z.read(n).decode("utf-8", "ignore")
                x = re.sub(r"</a:p>", "\n", x)
                out.append(f"\n--- {n} ---\n" + re.sub(r"<[^>]+>", "", x))
            return "\n".join(out)
        if "xl/workbook.xml" in names:
            out = []
            for n in names:
                if n.startswith("xl/") and n.endswith(".xml"):
                    out.append(re.sub(r"<[^>]+>", " ",
                                      z.read(n).decode("utf-8", "ignore")))
            return "\n".join(out)
    return ""


# RTF groups whose *entire contents* are metadata/binary, never body text.
# Regex can't do this — the groups nest — so from_rtf is a brace state machine.
_RTF_SKIP = {
    "fonttbl", "colortbl", "stylesheet", "listtable", "listoverridetable",
    "info", "pict", "object", "objdata", "themedata", "colorschememapping",
    "latentstyles", "datastore", "generator", "xmlnstbl", "rsidtbl",
    "mmathPr", "wgrffmtfilter", "filetbl", "revtbl", "upr", "bkmkstart",
    "bkmkend", "header", "footer", "headerl", "headerr", "footerl", "footerr",
    "footnote", "shppict", "nonshppict", "field", "fldinst", "template",
}
_RTF_BREAK = {"par", "line", "sect", "row", "pard"}


def from_rtf(raw: bytes) -> str:
    """Minimal RTF reader: walk groups, skip metadata/binary groups entirely."""
    s = raw.decode("latin-1")  # byte-transparent; \'xx resolved against gbk below
    out: list[str] = []
    buf = bytearray()          # pending \'xx bytes, flushed as gbk
    depth = 0
    skip_at: int | None = None  # depth at which we entered a skipped group
    i, n = 0, len(s)

    def flush() -> None:
        if buf:
            out.append(buf.decode("gbk", "ignore"))
            buf.clear()

    while i < n:
        c = s[i]
        if c == "{":
            depth += 1
            i += 1
            continue
        if c == "}":
            flush()
            if skip_at is not None and depth <= skip_at:
                skip_at = None
            depth -= 1
            i += 1
            continue
        if c == "\\":
            j = i + 1
            if j < n and not s[j].isalpha():
                # escaped literal, \'xx hex byte, or \uNNNN
                if s[j] == "'" and j + 2 < n:
                    if skip_at is None:
                        buf.append(int(s[j + 1:j + 3], 16))
                    i = j + 3
                    continue
                if skip_at is None:
                    flush()
                    out.append(s[j])
                i = j + 1
                continue
            k = j
            while k < n and s[k].isalpha():
                k += 1
            word = s[j:k]
            m = k
            if m < n and (s[m] == "-" or s[m].isdigit()):
                m += 1
                while m < n and s[m].isdigit():
                    m += 1
            param = s[k:m]
            if m < n and s[m] == " ":
                m += 1
            if word == "u" and param:
                if skip_at is None:
                    flush()
                    out.append(chr(int(param) % 65536))
                # \uN is followed by a fallback char to discard
                if m < n and s[m] not in "\\{}":
                    m += 1
            elif word in _RTF_SKIP:
                if skip_at is None:
                    skip_at = depth
            elif word in _RTF_BREAK and skip_at is None:
                flush()
                out.append("\n")
            elif word == "cell" and skip_at is None:
                flush()
                out.append(" | ")
            elif word == "tab" and skip_at is None:
                flush()
                out.append("\t")
            i = m
            continue
        if skip_at is None:
            if c in "\r\n":
                i += 1
                continue
            buf.extend(c.encode("latin-1"))
        i += 1
    flush()
    return "".join(out)


def _word97_pieces(path: Path) -> str:
    """Proper Word97 text extraction: FIB → Clx piece table → text pieces.

    Each piece descriptor says whether its run is utf-16le or 8-bit ANSI
    (cp936 for these Chinese docs). This skips all embedded binary.
    """
    import olefile
    with olefile.OleFileIO(str(path)) as ole:
        word = ole.openstream("WordDocument").read()
        flags = int.from_bytes(word[0x0A:0x0C], "little")
        table_name = "1Table" if flags & 0x0200 else "0Table"
        table = ole.openstream(table_name).read()
    fc_clx = int.from_bytes(word[0x01A2:0x01A6], "little")
    lcb_clx = int.from_bytes(word[0x01A6:0x01AA], "little")
    clx = table[fc_clx:fc_clx + lcb_clx]
    # walk Prc blocks until the Pcdt (0x02)
    i = 0
    while i < len(clx) and clx[i] == 0x01:
        i += 3 + int.from_bytes(clx[i + 1:i + 3], "little")
    if i >= len(clx) or clx[i] != 0x02:
        raise ValueError("no Pcdt in Clx")
    lcb = int.from_bytes(clx[i + 1:i + 5], "little")
    plc = clx[i + 5:i + 5 + lcb]
    n = (lcb - 4) // 12
    cps = [int.from_bytes(plc[4 * k:4 * k + 4], "little") for k in range(n + 1)]
    out = []
    for k in range(n):
        pcd = plc[4 * (n + 1) + 8 * k:4 * (n + 1) + 8 * k + 8]
        fc = int.from_bytes(pcd[2:6], "little")
        nchars = cps[k + 1] - cps[k]
        if fc & 0x40000000:  # compressed: 8-bit ANSI at fc/2
            start = (fc & 0x3FFFFFFF) // 2
            out.append(word[start:start + nchars].decode("cp936", "ignore"))
        else:
            out.append(word[fc:fc + 2 * nchars].decode("utf-16-le", "ignore"))
    txt = "".join(out)
    # Word control chars → readable structure
    return (txt.replace("\r", "\n").replace("\x07", " | ")   # cell/row ends
               .replace("\x0b", "\n").replace("\x0c", "\n")
               .replace("\x13", "").replace("\x14", "").replace("\x15", "")
               .replace("\x01", "").replace("\x08", ""))


def from_ole(path: Path, raw: bytes) -> str:
    try:
        return _word97_pieces(path)
    except Exception:
        pass
    # Not a Word97-family stream. Identify what it actually is and fail
    # loudly rather than dumping a whole-file decode of binary noise.
    try:
        import olefile
        with olefile.OleFileIO(str(path)) as ole:
            streams = {"/".join(e) for e in ole.listdir()}
        if "Workbook" in streams or "Book" in streams:
            sys.exit(f"legacy .xls (BIFF) not supported — open in Excel and "
                     f"save as .xlsx, or use markitdown. Streams: {sorted(streams)[:5]}")
        if "PowerPoint Document" in streams:
            sys.exit("legacy .ppt not supported — save as .pptx, or use markitdown")
        sys.exit(f"unrecognised OLE document (streams: {sorted(streams)[:8]})")
    except SystemExit:
        raise
    except Exception:
        # olefile missing/corrupt container: last-resort dense-text heuristic
        best, best_score = "", -1
        for enc in ("utf-16-le", "gbk"):
            t = raw.decode(enc, "ignore")
            t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]+", " ", t)
            score = sum(1 for c in t if "一" <= c <= "鿿" or c.isalnum())
            if score > best_score:
                best, best_score = t, score
        return best


def from_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("pypdf not installed: pip install pypdf")
    r = PdfReader(str(path))
    return "\n".join(
        f"\n<!-- Page {i+1} of {len(r.pages)} -->\n" + (p.extract_text() or "")
        for i, p in enumerate(r.pages))


def clean(t: str) -> str:
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]+", " ", t)
    t = re.sub(r"[ \t　]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def extract(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    kind = sniff(raw)
    if kind == "zip":
        return kind, clean(from_zip(path))
    if kind == "rtf":
        return kind, clean(from_rtf(raw))
    if kind == "ole":
        return kind, clean(from_ole(path, raw))
    if kind == "pdf":
        return kind, clean(from_pdf(path))
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return "text", clean(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return "text", clean(raw.decode("utf-8", "ignore"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--out", type=Path, help="write to file instead of stdout")
    ap.add_argument("--max-chars", type=int, default=0, help="0 = no limit")
    ap.add_argument("--grep", help="print only lines matching this regex (+context)")
    ap.add_argument("--context", type=int, default=0)
    args = ap.parse_args()

    if not args.file.exists():
        sys.exit(f"no such file: {args.file}")
    kind, txt = extract(args.file)

    if args.grep:
        pat = re.compile(args.grep)
        lines = txt.split("\n")
        keep: set[int] = set()
        for i, ln in enumerate(lines):
            if pat.search(ln):
                keep |= set(range(max(0, i - args.context),
                                  min(len(lines), i + args.context + 1)))
        txt = "\n".join(lines[i] for i in sorted(keep))

    if args.max_chars and len(txt) > args.max_chars:
        txt = txt[:args.max_chars] + f"\n\n[... truncated, {len(txt):,} chars total]"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(txt, encoding="utf-8")
        print(f"[{kind}] {len(txt):,} chars → {args.out}")
    else:
        print(f"<!-- format: {kind} | {len(txt):,} chars -->\n")
        print(txt)


if __name__ == "__main__":
    main()
