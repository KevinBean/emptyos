"""yt-digest — ingest a YouTube video into a vault-ready bundle.

Pulls metadata + transcript + (optionally) slide-change screenshots into a
temp bundle directory. Claude then reads the bundle and writes the actual
digest note in the user's voice — this script does no LLM work.

Output bundle (under --out, defaults to %TEMP%/yt-digest-<id>/):
    meta.json           # title, channel, duration, upload_date, url, thumbnail
    transcript.txt      # plain transcript (one snippet per line)
    transcript_ts.txt   # transcript with [hh:mm:ss] timestamps
    thumbnail.jpg       # poster frame (always)
    shots/NNNN_HHMMSS.jpg  # slide-change keyframes (only if --shots)
    shots/index.txt     # one line per shot: timestamp + filename

Usage:
    python yt_digest.py <url-or-id> [--shots] [--shot-threshold 0.4]
                                    [--max-shots 30] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
TRANSCRIPT_SCRIPT = SKILL_DIR.parent / "tool-youtube-transcript" / "youtube_transcript.py"


def extract_video_id(s: str) -> str:
    s = s.strip()
    if len(s) == 11 and "/" not in s and "?" not in s:
        return s
    import re
    m = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})", s)
    if not m:
        sys.exit(f"could not extract video id from: {s}")
    return m.group(1)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kw)


def fetch_meta(video_id: str, out_dir: Path) -> dict:
    cp = run(
        ["yt-dlp", "--skip-download", "--print-json", f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True,
    )
    info = json.loads(cp.stdout)
    meta = {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration_s": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "url": info.get("webpage_url"),
        "thumbnail": info.get("thumbnail"),
        "description": (info.get("description") or "")[:2000],
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def fetch_thumbnail(meta: dict, out_dir: Path) -> None:
    url = meta.get("thumbnail")
    if not url:
        return
    try:
        run(
            ["yt-dlp", "--skip-download", "--write-thumbnail", "--convert-thumbnails", "jpg",
             "-o", str(out_dir / "thumbnail.%(ext)s"),
             f"https://www.youtube.com/watch?v={meta['id']}"],
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"thumbnail fetch failed (non-fatal): {e}", file=sys.stderr)


def fetch_transcript(video_id: str, out_dir: Path) -> None:
    if not TRANSCRIPT_SCRIPT.exists():
        sys.exit(f"missing dependency: {TRANSCRIPT_SCRIPT}")
    run(
        [sys.executable, str(TRANSCRIPT_SCRIPT), video_id, "--out", str(out_dir / "transcript.txt")],
        capture_output=True,
    )
    run(
        [sys.executable, str(TRANSCRIPT_SCRIPT), video_id, "--with-timestamps",
         "--out", str(out_dir / "transcript_ts.txt")],
        capture_output=True,
    )


def extract_shots(video_id: str, out_dir: Path, threshold: float, max_shots: int) -> None:
    """Download video at low res then ffmpeg scene-detect into shots/."""
    shots_dir = out_dir / "shots"
    shots_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        video_path = Path(tmp) / "video.mp4"
        try:
            run(
                ["yt-dlp", "-f", "bv*[height<=480]+ba/b[height<=480]/wv*+ba/w",
                 "--merge-output-format", "mp4",
                 "-o", str(video_path),
                 f"https://www.youtube.com/watch?v={video_id}"],
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"video download failed: {e.stderr or e}", file=sys.stderr)
            return

        run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(video_path),
             "-vf", f"select='gt(scene,{threshold})',showinfo",
             "-vsync", "vfr",
             "-frame_pts", "1",
             str(shots_dir / "raw_%05d.jpg")],
            capture_output=True,
        )

        raw = sorted(shots_dir.glob("raw_*.jpg"))
        # Drop near-duplicate consecutive keyframes by file size delta as a cheap filter.
        kept: list[Path] = []
        last_size = -1
        for p in raw:
            sz = p.stat().st_size
            if last_size > 0 and abs(sz - last_size) / max(last_size, 1) < 0.02:
                p.unlink(missing_ok=True)
                continue
            kept.append(p)
            last_size = sz

        # Cap to max_shots evenly distributed.
        if len(kept) > max_shots:
            step = len(kept) / max_shots
            picks = {int(i * step) for i in range(max_shots)}
            for i, p in enumerate(list(kept)):
                if i not in picks:
                    p.unlink(missing_ok=True)
                    kept[i] = None  # type: ignore
            kept = [p for p in kept if p is not None]

        # Use ffprobe to read each frame's PTS — but raw_*.jpg has no embedded ts.
        # Re-extract with select+showinfo and parse stderr would be heavier. Instead,
        # we re-name by the frame index proportionally to duration as a best-effort.
        try:
            cp = run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                capture_output=True,
            )
            duration = float(cp.stdout.strip())
        except Exception:
            duration = 0.0

        index_lines = []
        n = len(kept)
        for i, p in enumerate(kept):
            ts = duration * (i + 0.5) / n if n and duration else 0.0
            hh = int(ts // 3600)
            mm = int((ts % 3600) // 60)
            ss = int(ts % 60)
            new_name = f"{i + 1:04d}_{hh:02d}{mm:02d}{ss:02d}.jpg"
            new_path = shots_dir / new_name
            p.rename(new_path)
            index_lines.append(f"{hh:02d}:{mm:02d}:{ss:02d}\t{new_name}")
        (shots_dir / "index.txt").write_text("\n".join(index_lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest a YouTube video into a vault-ready bundle.")
    ap.add_argument("video", help="YouTube URL or 11-char id")
    ap.add_argument("--shots", action="store_true", help="extract slide-change screenshots")
    ap.add_argument("--shot-threshold", type=float, default=0.4,
                    help="ffmpeg scene-detect threshold 0..1 (lower = more shots)")
    ap.add_argument("--max-shots", type=int, default=30, help="cap number of shots")
    ap.add_argument("--out", help="output bundle dir (default: %%TEMP%%/yt-digest-<id>/)")
    args = ap.parse_args()

    if not shutil.which("yt-dlp"):
        sys.exit("yt-dlp not found in PATH")

    video_id = extract_video_id(args.video)
    out_dir = Path(args.out) if args.out else Path(tempfile.gettempdir()) / f"yt-digest-{video_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"bundle: {out_dir}", file=sys.stderr)
    meta = fetch_meta(video_id, out_dir)
    fetch_thumbnail(meta, out_dir)
    fetch_transcript(video_id, out_dir)
    if args.shots:
        if not shutil.which("ffmpeg"):
            sys.exit("--shots requires ffmpeg in PATH")
        extract_shots(video_id, out_dir, args.shot_threshold, args.max_shots)

    summary = {
        "bundle": str(out_dir),
        "title": meta.get("title"),
        "channel": meta.get("channel"),
        "duration_s": meta.get("duration_s"),
        "shots": sorted(p.name for p in (out_dir / "shots").glob("*.jpg")) if args.shots else [],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
