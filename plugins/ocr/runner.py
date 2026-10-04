"""OCR runner — executes INSIDE the marker-ocr user-home venv.

Reads an input path (scanned PDF or image) + an output path from argv, runs
datalab **marker-pdf** with `force_ocr` + `paginate_output`, post-processes
marker's 0-indexed `{N}----` page separators into the KB `_fulltext/` convention
`<!-- Page N of TOTAL -->` (1-INDEXED), writes the markdown (utf-8) to the output
path, and prints a one-line JSON status to stdout. The markdown travels via a
file (not stdout) so Windows cp1252 stdout can't mangle non-ASCII content.

Images are converted to a single-page PDF (Pillow) before marker sees them.

100% local — marker runs on the local GPU (or CPU, slower). Model weights load
from the HF cache under `--cache-dir`; the first run downloads them once. See
`plugins/ocr/plugin.py` for why OCR runs in a separate interpreter (marker's
torch stack downgrades the daemon env's CUDA torch).

Top-level imports are stdlib only so `repaginate` is unit-testable from the
daemon env (which has no marker); everything heavy is imported inside `main`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# marker (markdown renderer, paginate_output=True) separates pages with a
# 0-indexed page id wrapped in braces followed by a run of dashes:
#   "\n\n{0}------------------------------------------------\n\n"
_PAGE_SEP = re.compile(r"\{(\d+)\}-{6,}")

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def repaginate(text: str, total: int) -> str:
    """Convert marker's 0-indexed `{N}----` separators to 1-indexed
    `<!-- Page N of TOTAL -->`, guaranteeing a page-1 marker leads the document.

    Pure (stdlib only) so it can be unit-tested without marker installed."""

    def _repl(m: re.Match) -> str:
        return f"\n\n<!-- Page {int(m.group(1)) + 1} of {total} -->\n\n"

    out = _PAGE_SEP.sub(_repl, text)
    if "<!-- Page 1 of" not in out:
        # marker emitted separators only *between* pages (none for page 0).
        out = f"<!-- Page 1 of {total} -->\n\n" + out.lstrip("\n")
    return out.strip() + "\n"


def _page_count(pdf_path: str) -> int:
    """Total pages via pypdfium2 (a marker dependency). 0 if it can't be read."""
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(pdf_path)
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return 0


def _image_to_pdf(img_path: str, out_pdf: str, dpi: int) -> None:
    from PIL import Image

    img = Image.open(img_path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(out_pdf, "PDF", resolution=float(dpi or 200))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--cache-dir", default="")
    ap.add_argument("--dpi", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=0)
    args = ap.parse_args()

    # Point HF / torch caches at the configured dir BEFORE importing marker, so
    # model weights live under the user-home cache. Force-assign (not setdefault)
    # so an explicit --cache-dir is actually honored even when HF_HOME is already
    # set in the environment. When no --cache-dir is given, marker uses the
    # standard HF cache (~/.cache/huggingface) — a fine, repo-external location.
    if args.cache_dir:
        os.makedirs(args.cache_dir, exist_ok=True)
        os.environ["HF_HOME"] = args.cache_dir
        os.environ["HF_HUB_CACHE"] = str(Path(args.cache_dir) / "hub")
        os.environ["TORCH_HOME"] = str(Path(args.cache_dir) / "torch")

    tmpdir = tempfile.TemporaryDirectory(prefix="ocr-runner-")
    try:
        in_path = args.input
        suffix = Path(in_path).suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            pdf_path = str(Path(tmpdir.name) / "image.pdf")
            _image_to_pdf(in_path, pdf_path, args.dpi)
        elif suffix == ".pdf":
            pdf_path = in_path
        else:
            print(json.dumps({"ok": False, "error": f"unsupported input suffix '{suffix}'"}))
            return 1

        total = _page_count(pdf_path)
        if args.max_pages and total and total > args.max_pages:
            print(json.dumps({"ok": False, "error": f"{total} pages exceeds max_pages {args.max_pages}"}))
            return 1

        # ── marker (heavy import — kept out of module top level) ──
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.config.parser import ConfigParser
        from marker.output import text_from_rendered

        cfg: dict = {"output_format": "markdown", "force_ocr": True, "paginate_output": True}
        if args.dpi > 0:
            cfg["highres_image_dpi"] = args.dpi
        parser = ConfigParser(cfg)
        converter = PdfConverter(
            config=parser.generate_config_dict(),
            artifact_dict=create_model_dict(),
            processor_list=parser.get_processors(),
            renderer=parser.get_renderer(),
        )
        rendered = converter(pdf_path)
        text, _ext, _images = text_from_rendered(rendered)

        if not total:
            # pypdfium2 failed earlier — recover total from the markers.
            ids = [int(m) for m in _PAGE_SEP.findall(text)]
            total = (max(ids) + 1) if ids else 1

        marked = repaginate(text, total)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(marked)
        print(json.dumps({"ok": True, "needs_ocr": True, "pages": total, "chars": len(marked)}))
        return 0
    except Exception as exc:  # noqa: BLE001 — surface any failure as JSON for the parent
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    finally:
        tmpdir.cleanup()


if __name__ == "__main__":
    sys.exit(main())
