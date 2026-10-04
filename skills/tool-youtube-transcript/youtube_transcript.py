"""Fetch YouTube transcripts via youtube-transcript-api.

Usage:
    python youtube_transcript.py <url-or-id> [--lang en] [--out path] [--with-timestamps]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


VIDEO_ID_RE = re.compile(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})")


def extract_video_id(s: str) -> str:
    s = s.strip()
    if len(s) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = VIDEO_ID_RE.search(s)
    if not m:
        raise SystemExit(f"Could not extract a YouTube video id from: {s!r}")
    return m.group(1)


def fetch(video_id: str, lang: str | None) -> list:
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    languages = [lang] if lang else ["en"]
    try:
        return list(api.fetch(video_id, languages=languages))
    except Exception:
        # Fall back to whatever the video offers
        return list(api.fetch(video_id))


def format_plain(snippets) -> str:
    return "\n".join(s.text for s in snippets)


def format_timestamped(snippets) -> str:
    lines = []
    for s in snippets:
        m, sec = divmod(int(s.start), 60)
        h, m = divmod(m, 60)
        ts = f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:d}:{sec:02d}"
        lines.append(f"[{ts}] {s.text}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch a YouTube transcript.")
    ap.add_argument("video", help="Video URL or 11-char id")
    ap.add_argument("--lang", default=None, help="Preferred language code (default: en, then auto)")
    ap.add_argument("--out", type=Path, default=None, help="Write transcript to file instead of stdout")
    ap.add_argument("--with-timestamps", action="store_true", help="Prefix each line with [hh:mm:ss]")
    args = ap.parse_args()

    vid = extract_video_id(args.video)
    snippets = fetch(vid, args.lang)
    text = format_timestamped(snippets) if args.with_timestamps else format_plain(snippets)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {len(snippets)} snippets ({len(text):,} chars) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
