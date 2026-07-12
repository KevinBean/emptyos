"""Rename numbered screenshot files into an album folder, matching track basenames.

Captured screenshots land in the repo root as shot-<prefix>-NN.png. This maps each
to <album folder>/NN-<safe(title)>-suno-screenshot.png using the album's clips JSON,
so titles with / : etc. are handled by the same safe() the audio used.

    python scripts/suno_place_shots.py --prefix fa --clips .cache/suno/further-assessment-clips.json --folder "Music/Albums/further-assessment"
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

from suno_common import safe, vault_root

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--clips", required=True)
    ap.add_argument("--folder", required=True, help="vault-relative album folder")
    args = ap.parse_args()
    dest = vault_root() / args.folder
    clips = json.loads(Path(args.clips).read_text(encoding="utf-8"))
    moved = 0
    for c in clips:
        n = c.get("n", 0)
        src = ROOT / f"shot-{args.prefix}-{n:02d}.png"
        if not src.exists():
            print(f"  missing: shot-{args.prefix}-{n:02d}.png ({c.get('title')})")
            continue
        out = dest / f"{n:02d}-{safe(c.get('title'))}-suno-screenshot.png"
        src.replace(out)
        moved += 1
    print(f"placed {moved}/{len(clips)} screenshots into {args.folder}")


if __name__ == "__main__":
    main()
