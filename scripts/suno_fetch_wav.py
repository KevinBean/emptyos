"""Poll Suno's CDN for converted WAVs and download them into a vault album folder.

Prereq: WAV conversion already triggered in-browser (POST /api/gen/<id>/convert_wav/).
Once converted, https://cdn1.suno.ai/<id>.wav is public (no auth). This polls each
track's WAV URL until ready (or timeout), downloads NN-<title>.wav, verifies size,
and merges results into the album's download-report.json.

    python scripts/suno_fetch_wav.py --folder "70_Media/Music/不识" --clips .cache/suno/buishi-clips.json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from suno_common import download, safe, vault_root, wav_ready


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--clips", required=True)
    ap.add_argument("--timeout", type=int, default=300, help="max seconds to wait for all WAVs")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    vault = vault_root()
    folder = vault / args.folder
    clips = json.loads(Path(args.clips).read_text(encoding="utf-8"))

    pending = {}
    for c in clips:
        sid = c.get("id")
        base = f"{c.get('n',0):02d}-{safe(c.get('title'))}"
        dest = folder / f"{base}.wav"
        if dest.exists() and not args.overwrite:
            print(f"  [exists] {base}.wav")
            continue
        pending[sid] = {"url": f"https://cdn1.suno.ai/{sid}.wav", "dest": dest, "base": base}

    deadline = time.time() + args.timeout
    done = {}
    while pending and time.time() < deadline:
        for sid in list(pending):
            size = wav_ready(pending[sid]["url"])
            if size > 0:
                p = pending.pop(sid)
                res = download(p["url"], p["dest"])
                flag = "OK " if res["ok"] else "FAIL"
                print(f"  [{flag}] {p['base']}.wav  {res['bytes']:,}  {res['note']}")
                done[sid] = res
        if pending:
            time.sleep(8)

    for sid in pending:
        print(f"  [TIMEOUT] {pending[sid]['base']}.wav still not ready")

    # merge into download-report.json
    rp = folder / "download-report.json"
    if rp.exists():
        report = json.loads(rp.read_text(encoding="utf-8"))
        for row in report:
            if row["id"] in done:
                row.setdefault("files", {})["wav"] = done[row["id"]]
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nWAV downloaded: {sum(1 for r in done.values() if r['ok'])}/{len(clips)}  |  still pending: {len(pending)}")


if __name__ == "__main__":
    main()
