#!/usr/bin/env python3
"""doc_extract.py — extract plain text from legacy office/archive documents.

Companion to fileindex_scan.py: the mining loop needs to *read* what the
ledger queues. Handles the formats that actually appear in the archive,
auto-detecting by magic bytes (extensions lie — .doc files in this corpus
are variously RTF, Word97 OLE, or even docx).

    python scripts/doc_extract.py <file> [--out FILE] [--max-chars N] [--grep PAT]

Formats: .docx/.xlsx/.pptx (zip+xml), RTF (brace state machine, \\'xx→gbk,
\\uN→unicode), Word97-family OLE incl. .wps (FIB → Clx piece table via
olefile), legacy .ppt (PowerPoint 97 record tree via olefile), .pdf (pypdf),
plain text. Legacy .xls is refused loudly instead of dumping binary noise --
it is covered by the markitdown plugin (needs its `[xls]` extra).

Coverage boundary vs the read-capability plugins: markitdown handles the
zip-based formats (.docx/.xlsx/.pptx/...) plus legacy .xls via xlrd, the ocr
plugin handles SCANNED PDFs; this script covers the LEGACY slice
(.doc/.rtf/.wps/.ppt) neither does -- markitdown refuses a binary .ppt too.

That graduation has since happened: `plugins/legacy-doc/` is the read enhancer
this docstring used to predict, serving the same slice over the `read`
capability. The two coexist deliberately and do not compete -- a script is not
a capability provider, so no priority-0 shadowing applies, and on `.ppt` they
fail differently (this walker pulls more text where the record tree parses;
anydoc opens decks it cannot). `_ppt97_text` falls back to anydoc rather than
exiting, which is what makes the pair complete.

Stdlib + olefile (Word97/.ppt paths) + pypdf (PDF path) + anydoc (optional .ppt
fallback, shared with plugins/legacy-doc/); all three degrade gracefully.
"""
from __future__ import annotations

import argparse
import codecs
import re
import struct
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
    # CNKI 知网 proprietary reader format (.caj). Common signatures: "CAJ",
    # "KDH"; some files also begin with "HN", but that 2-byte prefix is too
    # short to sniff safely on arbitrary text inputs, so the suffix gate below
    # handles that variant.
    if raw[:3] in (b"CAJ", b"KDH"):
        return "caj"
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


def _rtf_codec(s: str) -> str:
    """Codec for \\'xx bytes: the document's declared \\ansicpgN, else gbk.

    gbk stays the DEFAULT rather than the RTF-spec cp1252 because this corpus
    is Chinese-dominant and declares nothing (0 of 943 files carry \\ansicpg or
    \\fcharset). A declaration, when present, always wins.
    """
    m = re.search(r"\\ansicpg(\d+)", s[:4096])
    if not m:
        return "gbk"
    cp = int(m.group(1))
    known = {936: "gbk", 950: "big5", 932: "shift_jis", 949: "euc_kr", 65001: "utf-8"}
    if cp in known:
        return known[cp]
    try:
        codecs.lookup(f"cp{cp}")
    except LookupError:
        return "gbk"
    return f"cp{cp}"


def from_rtf(raw: bytes) -> str:
    """Minimal RTF reader: walk groups, skip metadata/binary groups entirely."""
    s = raw.decode("latin-1")  # byte-transparent; \'xx resolved against the codec below
    codec = _rtf_codec(s)
    out: list[str] = []
    buf = bytearray()          # pending \'xx bytes, flushed as `codec`
    depth = 0
    skip_at: int | None = None  # depth at which we entered a skipped group
    # \ucN: fallback chars trailing each \uN (spec default 1). The spec scopes
    # \uc to the enclosing group and restores it on exit; this reader keeps one
    # global value, matching how it already ignores group state elsewhere (no
    # font/charset stack). Harmless here -- across 943 corpus files \uc is never
    # written at all, so the default of 1 holds throughout. Revisit only if a
    # document turns up that sets \uc inside a group and relies on the restore.
    uc = 1
    i, n = 0, len(s)

    def flush() -> None:
        if buf:
            out.append(buf.decode(codec, "ignore"))
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
            if word == "uc" and param:
                uc = max(0, int(param))
            elif word == "u" and param:
                if skip_at is None:
                    flush()
                    out.append(chr(int(param) % 65536))
                # \uN is trailed by `uc` ANSI fallback units, which must be
                # DISCARDED. A unit is a \'xx escape, a \-escaped literal, or one
                # plain char. Missing the \'xx form is what fed Western quote
                # bytes to the gbk decoder and produced CJK mojibake.
                skipped = 0
                while skipped < uc and m < n:
                    if s[m] == "\\" and m + 1 < n:
                        if s[m + 1] == "'" and m + 3 < n:
                            m += 4
                        elif not s[m + 1].isalpha():
                            m += 2
                        else:
                            break  # a control word is content, not a fallback
                    elif s[m] in "{}":
                        break
                    else:
                        m += 1
                    skipped += 1
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


def _anydoc_text(path: Path) -> str:
    """Optional fallback through anydoc -- the same library `plugins/legacy-doc/` uses.

    Returns "" when anydoc is absent or cannot read the file, so every caller keeps
    its own error path and a fresh clone without it behaves exactly as before
    (matching the stdlib-plus-optional-deps posture in the module docstring).

    NEVER passes `ocr=`. That argument ships document pages to a third-party host,
    bypassing the cloud-consent gate (CLAUDE.md rules 18/19). Without it anydoc makes
    no network call at all and simply raises on a scanned page.
    """
    try:
        import anydoc
    except Exception:
        return ""
    try:
        return anydoc.to_markdown(str(path)) or ""
    except Exception:
        return ""


def _ppt97_text(path: Path) -> str:
    """Text from a legacy PowerPoint 97-2003 (.ppt) OLE container.

    Walks the record tree in the ``PowerPoint Document`` stream and collects
    ``TextBytesAtom`` (0x0FA0) and ``TextCharsAtom`` (0x0FA8) payloads.  A
    record whose version nibble is 0xF is a container, so we descend into it
    rather than skipping its length.

    The spec says TextBytesAtom holds the *low bytes* of UTF-16 (i.e. Latin-1
    only) and CJK text belongs in TextCharsAtom.  Real decks written by
    Chinese PowerPoint put UTF-16LE in TextBytesAtom anyway, and the file
    carries no flag to distinguish them, so each payload is decoded both ways.
    An all-ASCII single-byte reading wins outright (see below); otherwise the
    two candidates are scored and the better one kept.  Guessing wrong is the
    difference between a readable slide and a page of mojibake.
    """
    import olefile
    with olefile.OleFileIO(str(path)) as ole:
        data = ole.openstream("PowerPoint Document").read()

    def looks_like_text(s: str) -> float:
        if not s:
            return -1.0
        printable = sum(1 for c in s if c.isprintable() or c in "\r\n\t\x0b")
        cjk = sum(1 for c in s if "一" <= c <= "鿿")
        return printable / len(s) + cjk / len(s)

    def all_ascii(s: str) -> bool:
        return bool(s) and all(c.isascii() and (c.isprintable() or c in "\r\n\t\x0b")
                               for c in s)

    out: list[str] = []
    i = 0
    while i + 8 <= len(data):
        ver_inst, rtype, rlen = struct.unpack_from("<HHI", data, i)
        body = i + 8
        if rlen > len(data) - body:
            break
        if rtype in (0x0FA0, 0x0FA8):
            payload = data[body:body + rlen]
            cands = []
            if len(payload) % 2 == 0:
                cands.append(payload.decode("utf-16-le", "replace"))
            single = payload.decode("cp1252", "replace")
            # A clean all-ASCII single-byte reading settles it, because UTF-16LE
            # can never produce one -- for two different reasons, and both are
            # load-bearing: CJK carries high bytes (55 53 fb 51), and ASCII
            # carries NUL bytes (4f 00 4b 00), which all_ascii rejects since
            # '\x00'.isprintable() is False.  Without this shortcut the CJK
            # reward in looks_like_text wins on short numeric cells ("12.5kN")
            # and turns table values into mojibake while body text stays fine.
            if all_ascii(single):
                out.append(single)
            else:
                out.append(max(cands + [single], key=looks_like_text))
        i = body if (ver_inst & 0x0F) == 0x0F else body + rlen

    if not out:
        # The record walk found nothing -- an image-only deck, or a container
        # shape this reader does not descend. anydoc opens some of those (it
        # read 8,936 chars from a deck this path returns zero for), so try it
        # before giving up. Measured 2026-08-30; see plugins/legacy-doc/.
        fallback = _anydoc_text(path)
        if fallback:
            return fallback
        sys.exit("legacy .ppt carried no text atoms (image-only deck?)")
    return "\n".join(out)


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
            sys.exit(f"legacy .xls (BIFF) not supported here — use the markitdown "
                     f"plugin (its venv needs the [xls] extra -> xlrd), or open in "
                     f"Excel and save as .xlsx. Streams: {sorted(streams)[:5]}")
        if "PowerPoint Document" in streams:
            return _ppt97_text(path)
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
    if kind == "caj" or (kind == "text" and path.suffix.lower() == ".caj"):
        sys.exit("CNKI .caj not supported — open in CAJViewer and 'export/print to "
                 "PDF', then re-run on the PDF (or OCR the PDF via the ocr plugin)")
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
