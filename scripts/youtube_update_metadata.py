#!/usr/bin/env python3
"""Apply designed title/description/tags to already-uploaded AI Engineering videos.

Reads the metadata map (default:
{vault}/10_Projects/YouTube-AI-Engineering/article_videos_metadata.json), and for
each video id sets its snippet via the youtube connector's update_video. Runs
OUTSIDE the daemon.

Requires the youtube.force-ssl scope, so re-run scripts/youtube_auth.py once after
the scope was widened (the old upload-only token can't edit metadata).

SAFETY:
  - Dry-run by DEFAULT — prints the new title/description/tags per video and
    stops. Pass --yes to apply.
  - snippet-only update: visibility is NEVER touched, so private videos stay
    private. There is no delete path.
  - Channel-guarded (default expects "engineering" in the connected channel
    title) so edits can't hit the wrong channel.

Usage:
  python scripts/youtube_auth.py                    # re-auth once (widened scope)
  python scripts/youtube_update_metadata.py         # dry run
  python scripts/youtube_update_metadata.py --yes   # apply
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_client():
    spec = importlib.util.spec_from_file_location(
        "youtube_client", REPO / "plugins" / "youtube" / "client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _default_meta_path() -> Path:
    cfg = REPO / "emptyos.toml"
    vault = None
    if cfg.exists():
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        vault = (data.get("notes", {}) or {}).get("path")
    base = Path(vault) if vault else REPO
    return base / "10_Projects" / "YouTube-AI-Engineering" / "article_videos_metadata.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="apply (default is a dry run)")
    ap.add_argument("--meta", default="", help="metadata JSON path (default: vault AI-Engineering project)")
    ap.add_argument("--expect-channel", default="engineering")
    ap.add_argument("--force", action="store_true", help="edit even if the channel guard fails")
    ap.add_argument("--only", default="", help="only update this video id")
    ap.add_argument("--category", default="28", help="YouTube category id")
    args = ap.parse_args()

    meta_path = Path(args.meta) if args.meta else _default_meta_path()
    if not meta_path.exists():
        print(f"Metadata file not found: {meta_path}")
        return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    footer = meta.get("_footer", "")
    videos = meta.get("videos", {})
    if args.only:
        videos = {k: v for k, v in videos.items() if k == args.only}
    if not videos:
        print("No videos in metadata (after --only filter).")
        return 1

    client = _load_client()
    creds = client.load_credentials(REPO / "data" / "secrets")
    if creds is None:
        print("Not connected. Run:  python scripts/youtube_auth.py  first.")
        return 2
    # Applying needs the widened scope (force-ssl). A dry run only reads, so it
    # works on the current upload-only token — review first, re-auth, then apply.
    if args.yes:
        scopes = set(getattr(creds, "scopes", None) or [])
        if "https://www.googleapis.com/auth/youtube.force-ssl" not in scopes:
            print("Token lacks youtube.force-ssl (metadata edit scope). The scope "
                  "was widened — re-run:  python scripts/youtube_auth.py  then retry.")
            return 3

    ch = client.get_channel(creds)
    ch_title = ch.get("title", "")
    print(f"Connected channel: {ch_title}  (id: {ch.get('id','?')})")
    if args.expect_channel.lower() not in ch_title.lower() and not args.force:
        print(f"REFUSING: channel title does not contain '{args.expect_channel}'. "
              "Re-auth to the right channel or pass --force.")
        return 3

    mode = "APPLY" if args.yes else "DRY RUN"
    print(f"\n=== {mode} · {len(videos)} videos ===\n")
    applied = 0
    for vid, m in videos.items():
        title = m.get("title", "")
        desc = (m.get("description", "") + footer).strip()
        tags = m.get("tags", [])
        print(f"• {vid}  https://youtu.be/{vid}")
        print(f"    title: {title}")
        print(f"    tags : {', '.join(tags)}")
        print(f"    desc : {desc.splitlines()[0][:100] if desc else ''}...")
        if not args.yes:
            print()
            continue
        try:
            res = client.update_video(
                creds, vid, title=title, description=desc, tags=tags,
                category_id=args.category,
            )
            print(f"    → updated: {res['title']}\n")
            applied += 1
        except Exception as e:
            print(f"    FAILED: {e}\n")

    if not args.yes:
        print("Dry run only. Re-run with --yes to apply.")
        return 0
    print(f"Updated {applied}/{len(videos)} videos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
