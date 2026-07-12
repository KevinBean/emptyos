"""Download a Suno album's audio into a vault folder, verified.

Input: a clips JSON (list of {n, title, id, created, mp3, mp4, wav}) produced from
the Suno playlist API (in-browser), plus a target vault-relative folder. Downloads
NN-<title>.mp3 / .mp4 / .wav (whichever URLs are present), verifies each file is
non-zero and matches the server Content-Length, and writes a download-report.json.

MP3/MP4 come from the public cdn1.suno.ai (no auth). WAV, when present, is a
pre-resolved signed URL captured in-browser (also no auth at download time).

    python scripts/suno_download_album.py --folder "70_Media/Music/不识" --clips path/to/clips.json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from suno_common import download, safe, vault_root


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True, help="vault-relative target folder")
    ap.add_argument("--clips", required=True, help="path to clips JSON")
    ap.add_argument("--overwrite", action="store_true", help="re-download even if file exists")
    ap.add_argument("--delay", type=float, default=4.0, help="seconds to wait between tracks (politeness)")
    args = ap.parse_args()

    vault = vault_root()
    folder = vault / args.folder
    folder.mkdir(parents=True, exist_ok=True)
    clips = json.loads(Path(args.clips).read_text(encoding="utf-8"))

    report = []
    for ti, c in enumerate(clips):
        n = c.get("n") or 0
        base = f"{n:02d}-{safe(c.get('title'))}"
        row = {"n": n, "title": c.get("title"), "id": c.get("id"),
               "created": c.get("created"), "files": {}}
        for ext, key in (("mp3", "mp3"), ("mp4", "mp4"), ("wav", "wav")):
            url = (c.get(key) or "").strip()
            if not url:
                continue
            dest = folder / f"{base}.{ext}"
            if dest.exists() and not args.overwrite:
                row["files"][ext] = {"ok": True, "bytes": dest.stat().st_size, "note": "exists, skipped"}
                continue
            res = download(url, dest)
            row["files"][ext] = res
            flag = "OK " if res["ok"] else "FAIL"
            print(f"  [{flag}] {base}.{ext}  {res['bytes']:,}  {res['note']}")
            time.sleep(max(0.0, args.delay * 0.5))  # brief pause between files
        report.append(row)
        if ti < len(clips) - 1 and args.delay > 0:
            time.sleep(args.delay)  # polite gap between tracks

    (folder / "download-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # summary
    n_ok = sum(1 for r in report for f in r["files"].values() if f.get("ok"))
    n_bad = sum(1 for r in report for f in r["files"].values() if not f.get("ok"))
    print(f"\nAlbum: {args.folder}")
    print(f"Tracks: {len(report)}  |  files OK: {n_ok}  |  files FAILED: {n_bad}")
    miss_mp4 = [r["title"] for r in report if "mp4" not in r["files"]]
    if miss_mp4:
        print(f"No MP4 on Suno for: {', '.join(miss_mp4)}")
    miss_wav = [r["title"] for r in report if "wav" not in r["files"]]
    if miss_wav:
        print(f"No WAV downloaded for: {len(miss_wav)} tracks")


if __name__ == "__main__":
    main()
