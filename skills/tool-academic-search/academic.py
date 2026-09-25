#!/usr/bin/env python
"""Academic resource fetcher — search + download papers from the terminal.

Wraps `paper-search-mcp` (arXiv, PubMed, bioRxiv, medRxiv, Google Scholar) and
`arxiv-dl` (fast arXiv PDF downloads), both installed in an isolated venv at
%LOCALAPPDATA%/eos/envs/academic/ so they never touch the EmptyOS daemon env.

The script re-execs itself under the venv python if those packages aren't
importable, so it can be invoked with a plain `python`.

Subcommands:
  search  <query> [--source arxiv|pubmed|biorxiv|medrxiv|scholar|all] [-n N] [--json]
  download <id|url> [--dir DIR] [--source arxiv|pubmed|biorxiv|medrxiv]
  read    <id>     [--source arxiv|pubmed|biorxiv|medrxiv]   (prints extracted text)
"""
from __future__ import annotations

import os
import sys

# ── venv re-exec shim ───────────────────────────────────────────────────────
def _venv_scripts_dir() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "eos", "envs", "academic", "Scripts")


def _ensure_venv():
    try:
        import paper_search_mcp  # noqa: F401
        return
    except ImportError:
        pass
    venv_py = os.path.join(_venv_scripts_dir(), "python.exe")
    if not os.path.exists(venv_py):
        sys.stderr.write(
            "academic venv missing. Create it with:\n"
            '  uv venv --python 3.13 "%LOCALAPPDATA%/eos/envs/academic"\n'
            '  uv pip install --python "%LOCALAPPDATA%/eos/envs/academic/Scripts/python.exe" '
            "arxiv-dl paper-search-mcp\n"
        )
        sys.exit(2)
    if os.path.abspath(venv_py) != os.path.abspath(sys.executable):
        # NB: os.execv mangles space-containing args on Windows — use subprocess.
        import subprocess
        sys.exit(subprocess.call([venv_py, os.path.abspath(__file__), *sys.argv[1:]]))

_ensure_venv()

import argparse
import json
import subprocess

# ── source registry ─────────────────────────────────────────────────────────
def _searchers():
    from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher
    from paper_search_mcp.academic_platforms.pubmed import PubMedSearcher
    from paper_search_mcp.academic_platforms.biorxiv import BioRxivSearcher
    from paper_search_mcp.academic_platforms.medrxiv import MedRxivSearcher
    from paper_search_mcp.academic_platforms.google_scholar import GoogleScholarSearcher
    return {
        "arxiv": ArxivSearcher,
        "pubmed": PubMedSearcher,
        "biorxiv": BioRxivSearcher,
        "medrxiv": MedRxivSearcher,
        "scholar": GoogleScholarSearcher,
    }

_ALIASES = {"google_scholar": "scholar", "gs": "scholar", "arXiv": "arxiv"}


def _norm_source(s: str) -> str:
    return _ALIASES.get(s, s)


def _paper_brief(p) -> dict:
    authors = getattr(p, "authors", None) or []
    if isinstance(authors, str):
        authors = [authors]
    return {
        "id": getattr(p, "paper_id", "") or "",
        "title": (getattr(p, "title", "") or "").strip(),
        "authors": authors[:4],
        "date": getattr(p, "published_date", "") or "",
        "source": getattr(p, "source", "") or "",
        "url": getattr(p, "url", "") or getattr(p, "pdf_url", "") or "",
        "doi": getattr(p, "doi", "") or "",
    }


# ── commands ─────────────────────────────────────────────────────────────────
def cmd_search(args):
    reg = _searchers()
    src = _norm_source(args.source)
    sources = list(reg) if src == "all" else [src]
    if src != "all" and src not in reg:
        sys.stderr.write(f"unknown source '{args.source}'. choices: {', '.join(reg)}, all\n")
        sys.exit(2)

    results = []
    for name in sources:
        try:
            papers = reg[name]().search(args.query, max_results=args.n)
            for p in papers:
                results.append(_paper_brief(p))
        except Exception as e:  # noqa: BLE001 — per-source soft fail
            sys.stderr.write(f"[{name}] search failed: {e}\n")

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    if not results:
        print("No results.")
        return
    for i, r in enumerate(results, 1):
        auth = ", ".join(r["authors"])
        if len(r["authors"]) == 4:
            auth += ", et al."
        print(f"[{i}] {r['title']}")
        meta = f"    {r['source']} · {r['id']}"
        if r["date"]:
            meta += f" · {r['date']}"
        print(meta)
        if auth:
            print(f"    {auth}")
        if r["url"]:
            print(f"    {r['url']}")
        print()


def cmd_download(args):
    src = _norm_source(args.source)
    out = os.path.abspath(args.dir)
    os.makedirs(out, exist_ok=True)

    # arXiv → use arxiv-dl (faster, better filenames, parallel)
    if src == "arxiv":
        exe = os.path.join(_venv_scripts_dir(), "arxiv-dl.exe")
        cmd = [exe, "-d", out, args.target]
        if args.pdf_only:
            cmd.insert(1, "-p")
        rc = subprocess.call(cmd)
        sys.exit(rc)

    # others → paper-search-mcp download_* server tools (async)
    import asyncio
    from paper_search_mcp import server as srv
    fn = {
        "pubmed": srv.download_pubmed,
        "biorxiv": srv.download_biorxiv,
        "medrxiv": srv.download_medrxiv,
    }.get(src)
    if not fn:
        sys.stderr.write(f"download not supported for source '{args.source}'\n")
        sys.exit(2)
    path = asyncio.run(fn(args.target, save_path=out))
    print(path)


def cmd_read(args):
    import asyncio
    from paper_search_mcp import server as srv
    src = _norm_source(args.source)
    fn = {
        "arxiv": srv.read_arxiv_paper,
        "pubmed": srv.read_pubmed_paper,
        "biorxiv": srv.read_biorxiv_paper,
        "medrxiv": srv.read_medrxiv_paper,
    }.get(src)
    if not fn:
        sys.stderr.write(f"read not supported for source '{args.source}'\n")
        sys.exit(2)
    text = asyncio.run(fn(args.target, save_path=os.path.abspath(args.dir)))
    print(text)


def main():
    ap = argparse.ArgumentParser(description="Search & download academic papers from the terminal.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search one or all sources")
    s.add_argument("query")
    s.add_argument("--source", default="arxiv",
                   help="arxiv|pubmed|biorxiv|medrxiv|scholar|all (default: arxiv)")
    s.add_argument("-n", type=int, default=8, help="max results per source (default 8)")
    s.add_argument("--json", action="store_true", help="emit JSON")
    s.set_defaults(func=cmd_search)

    d = sub.add_parser("download", help="download a paper PDF by id/url")
    d.add_argument("target", help="arXiv id/URL, PMID, or bioRxiv/medRxiv DOI")
    d.add_argument("--source", default="arxiv", help="arxiv|pubmed|biorxiv|medrxiv (default: arxiv)")
    d.add_argument("--dir", default="./downloads", help="output directory")
    d.add_argument("--pdf-only", action="store_true", help="arXiv: skip the notes file")
    d.set_defaults(func=cmd_download)

    r = sub.add_parser("read", help="download + extract a paper's text to stdout")
    r.add_argument("target", help="paper id")
    r.add_argument("--source", default="arxiv", help="arxiv|pubmed|biorxiv|medrxiv (default: arxiv)")
    r.add_argument("--dir", default="./downloads", help="scratch directory for the PDF")
    r.set_defaults(func=cmd_read)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
