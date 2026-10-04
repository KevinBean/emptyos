#!/usr/bin/env python
"""Render PDF pages to PNG so a session can READ the print, not the extraction.

    python scripts/pdf_pages.py "<pdf>" --pages 11,17-19          # printed→PDF offset is yours
    python scripts/pdf_pages.py "<pdf>" --pages 13 --out "$SCRATCH/iec60949"
    python scripts/pdf_pages.py "<pdf>"                            # every page

Prints one path per line; open them with the Read tool. Pages are 1-based PDF
page numbers (the same index the fulltext's ``<!-- Page i of n -->`` markers
use), not the printed folio — note the offset when you cite.

Why this exists: text extraction garbles stacked equations (radicals and
exponents vanish) and streams tables column-wise, and no detector can see
through that garble (IEC 60949, 2026-09-30: the pages that went wrong scored
zero on every "equation page" signal). Verifying a transcribed figure means
looking at the page image. ``eos-citation-verify`` Q3 and the digest skill
both route through here.

Default output is a per-PDF folder under the system temp dir so a one-off
verification leaves nothing in the repo or the vault; pass ``--out`` to keep
the images (the digest keeps them under ``data/kb/pages/<SLUG>/``).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from emptyos.sdk.pdf import render_pdf_pages  # noqa: E402


def parse_pages(spec: str) -> list[int]:
    """``"11,17-19"`` → ``[11, 17, 18, 19]``; empty → all pages (None)."""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            if hi < lo:
                raise ValueError(f"bad range {part!r}")
            pages.extend(range(lo, hi + 1))
        else:
            pages.append(int(part))
    return pages


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pdf", help="path to the PDF")
    ap.add_argument("--pages", default="", help="1-based PDF pages, e.g. 11,17-19 (default: all)")
    ap.add_argument("--out", default="", help="output folder (default: <temp>/eos-pdf-pages/<stem>)")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--force", action="store_true", help="re-render pages already on disk")
    args = ap.parse_args(argv)

    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"error: PDF not found: {pdf}", file=sys.stderr)
        return 2
    try:
        pages = parse_pages(args.pages) or None
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "eos-pdf-pages" / pdf.stem
    try:
        paths = render_pdf_pages(pdf, out, dpi=args.dpi, pages=pages, skip_existing=not args.force)
    except (ValueError, ImportError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
