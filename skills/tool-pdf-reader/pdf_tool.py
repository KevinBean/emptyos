#!/usr/bin/env python3
"""
PDF Tool - Extract and analyze PDF files for Claude Code.
Usage: python pdf_tool.py <command> <pdf_path> [options]
"""

import argparse
import sys
import os

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Run: pip install pymupdf")
    sys.exit(1)


def get_info(pdf_path: str) -> None:
    """Get PDF metadata and basic info."""
    doc = fitz.open(pdf_path)
    metadata = doc.metadata

    file_size = os.path.getsize(pdf_path)
    size_mb = file_size / (1024 * 1024)

    print(f"=== PDF Info ===")
    print(f"File: {os.path.basename(pdf_path)}")
    print(f"Size: {size_mb:.2f} MB")
    print(f"Total Pages: {len(doc)}")
    print(f"Title: {metadata.get('title', 'N/A')}")
    print(f"Author: {metadata.get('author', 'N/A')}")
    print(f"Subject: {metadata.get('subject', 'N/A')}")
    print(f"Creator: {metadata.get('creator', 'N/A')}")

    doc.close()


def get_toc(pdf_path: str) -> None:
    """Extract table of contents."""
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()

    if not toc:
        print("No table of contents found in this PDF.")
        doc.close()
        return

    print(f"=== Table of Contents ({len(toc)} entries) ===\n")
    for item in toc:
        level, title, page = item
        indent = "  " * (level - 1)
        print(f"{indent}- {title} (p.{page})")

    doc.close()


def extract_text(pdf_path: str, start: int = 1, end: int = 10) -> None:
    """Extract text from page range."""
    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    # Validate page range
    start = max(1, start)
    end = min(total_pages, end)

    if start > total_pages:
        print(f"ERROR: Start page {start} exceeds total pages {total_pages}")
        doc.close()
        return

    print(f"=== Extracting Pages {start}-{end} of {total_pages} ===\n")

    for page_num in range(start - 1, end):  # fitz uses 0-indexed
        page = doc[page_num]
        text = page.get_text()

        print(f"\n--- Page {page_num + 1} ---\n")
        print(text.strip())

    print(f"\n=== End of Pages {start}-{end} ===")

    # Suggest next chunk
    if end < total_pages:
        next_start = end + 1
        next_end = min(end + 10, total_pages)
        print(f"\nNext chunk: --start {next_start} --end {next_end}")

    doc.close()


def search_text(pdf_path: str, keyword: str) -> None:
    """Search for keyword in PDF."""
    doc = fitz.open(pdf_path)

    print(f"=== Searching for '{keyword}' ===\n")

    found_pages = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()

        if keyword.lower() in text.lower():
            # Count occurrences
            count = text.lower().count(keyword.lower())
            found_pages.append((page_num + 1, count))

    if found_pages:
        print(f"Found in {len(found_pages)} pages:\n")
        for page, count in found_pages:
            print(f"  - Page {page}: {count} occurrence(s)")

        # Suggest extraction
        if len(found_pages) <= 5:
            pages = [str(p[0]) for p in found_pages]
            print(f"\nTo read these pages, extract around them:")
            for page, _ in found_pages[:3]:
                start = max(1, page - 1)
                end = min(len(doc), page + 1)
                print(f"  --start {start} --end {end}")
    else:
        print(f"'{keyword}' not found in this PDF.")

    doc.close()


def main():
    parser = argparse.ArgumentParser(description="PDF Tool for Claude Code")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Info command
    info_parser = subparsers.add_parser("info", help="Get PDF info")
    info_parser.add_argument("pdf_path", help="Path to PDF file")

    # TOC command
    toc_parser = subparsers.add_parser("toc", help="Get table of contents")
    toc_parser.add_argument("pdf_path", help="Path to PDF file")

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract text from pages")
    extract_parser.add_argument("pdf_path", help="Path to PDF file")
    extract_parser.add_argument("--start", type=int, default=1, help="Start page (1-indexed)")
    extract_parser.add_argument("--end", type=int, default=10, help="End page (inclusive)")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search for keyword")
    search_parser.add_argument("pdf_path", help="Path to PDF file")
    search_parser.add_argument("keyword", help="Keyword to search")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Validate file exists
    if not os.path.exists(args.pdf_path):
        print(f"ERROR: File not found: {args.pdf_path}")
        sys.exit(1)

    # Run command
    if args.command == "info":
        get_info(args.pdf_path)
    elif args.command == "toc":
        get_toc(args.pdf_path)
    elif args.command == "extract":
        extract_text(args.pdf_path, args.start, args.end)
    elif args.command == "search":
        search_text(args.pdf_path, args.keyword)


if __name__ == "__main__":
    main()
