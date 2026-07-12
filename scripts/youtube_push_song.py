"""Push one prepared song release folder to the music YouTube channel.

Reads a song's `release/release-package.md` (the human-approved upload spec) and
uploads its release master, then completes the video with the packaged thumbnail
and subtitle track.

    python scripts/youtube_push_song.py "<song dir>"          # dry run (default)
    python scripts/youtube_push_song.py "<song dir>" --yes    # actually upload

Defaults are the safe ones: PRIVATE, music category, dry run. The channel guard
refuses to upload unless the connected channel title matches --expect-channel,
so an engineering video can never land on the music channel (or vice versa).

Standalone by design — reuses plugins/youtube/client.py without booting the
kernel, so a release needs no daemon restart. Mirror of youtube_push_articles.py.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts/ modules

import youtube_common as yt  # noqa: E402

MUSIC_PROFILE = "music"


def review_master(master: Path) -> bool:
    """Technical QA on the release master. True when it is safe to upload.

    A bad master is public the moment visibility is flipped in Studio, so the
    catastrophic failures (no audio track, zero-length, a frozen or black clip)
    are caught here rather than by a listener. Calibrated against four real
    songs: freeze fractions came in at 0-56%, well under the 90% hard floor.

    A fully-static single-image visualizer WOULD freeze at ~100% and hard-fail.
    That is what --skip-qa is for: the human, not the checker, owns that call.
    """
    from emptyos.sdk.media.review import review_video

    v = asyncio.run(review_video(master, require_audio=True))
    m = v.metrics
    dur, frozen = m.get("duration_s") or 0, m.get("freeze_s") or 0
    print(f"  qa:         {dur:.0f}s, audio={m.get('has_audio')}, "
          f"frozen={frozen / dur:.0%}" if dur else "  qa:         (unreadable)")
    for note in v.soft:
        print(f"  ! {note}")
    for note in v.hard:
        print(f"  ✗ {note}")
    if not v.ok:
        print("\nREFUSING: the master failed technical QA (above).\n"
              "Fix the master, or pass --skip-qa if you have judged it fine.")
    return v.ok


def _fenced_block(body: str, heading: str) -> str:
    """Text of the first ``` fence under a `## <heading>` section."""
    m = re.search(
        rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", body, re.M | re.S
    )
    if not m:
        return ""
    fence = re.search(r"```(?:\w+)?\n(.*?)```", m.group(1), re.S)
    return fence.group(1).strip() if fence else ""


def _bullet_field(body: str, label: str) -> str:
    """Value of a `- **Label:** value` bullet (backticks stripped)."""
    m = re.search(rf"^-\s+\*\*{re.escape(label)}:\*\*\s*(.+)$", body, re.M)
    return m.group(1).strip().strip("`").strip() if m else ""


def parse_package(pkg: Path) -> dict:
    """Pull the upload spec out of a release-package.md."""
    body = pkg.read_text(encoding="utf-8")
    tags_raw = _fenced_block(body, "Tags")
    return {
        "title": _bullet_field(body, "Title"),
        "privacy": (_bullet_field(body, "Privacy") or "private").lower(),
        "description": _fenced_block(body, "Description"),
        "tags": [t.strip() for t in tags_raw.split(",") if t.strip()],
        "pinned_comment": _fenced_block(body, "Pinned comment"),
    }


def _pick(candidates: list[Path], kind: str) -> Path | None:
    """First of a sorted candidate list. Sorted because glob order is
    filesystem-dependent — an unsorted [0] would upload a different file on a
    different machine. Ambiguity is surfaced, not silently resolved: the dry run
    is where a human catches the wrong pick."""
    if not candidates:
        return None
    ranked = sorted(candidates)
    if len(ranked) > 1:
        rest = ", ".join(p.name for p in ranked[1:])
        print(f"  ! {len(ranked)} {kind} files — using {ranked[0].name} (ignoring: {rest})")
    return ranked[0]


def find_assets(release_dir: Path) -> dict:
    """Locate the master / thumbnail / subtitles, ignoring the teaser."""
    masters = [p for p in release_dir.glob("*.mp4") if "teaser" not in p.stem.lower()]
    return {
        "master": _pick(masters, "master"),
        "thumbnail": _pick(list(release_dir.glob("*.png")), "thumbnail"),
        "subtitles": _pick(list(release_dir.glob("*.srt")), "subtitle"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("song_dir", help="song folder containing release/release-package.md")
    ap.add_argument("--yes", action="store_true", help="actually upload (default is a dry run)")
    # No "public" here by design. Going public is irreversible and skips the
    # private review this script exists to protect — Studio owns that flip, which
    # is also why the client's update_video can never touch visibility.
    ap.add_argument("--privacy", default="", choices=["", "private", "unlisted"],
                    help="override the package's Privacy field")
    ap.add_argument("--expect-channel", default="Unsaid Signal",
                    help="refuse unless the connected channel title contains this")
    ap.add_argument("--force", action="store_true", help="upload even if the channel guard fails")
    ap.add_argument("--skip-qa", action="store_true", help="upload even if the master fails QA")
    ap.add_argument("--category", default="10", help="YouTube category id (10=Music)")
    ap.add_argument("--language", default="zh-Hans", help="BCP-47 tag for the subtitle track")
    ap.add_argument("--profile", default=MUSIC_PROFILE, help="YouTube token profile")
    args = ap.parse_args()

    song_dir = Path(args.song_dir)
    release_dir = song_dir / "release" if (song_dir / "release").is_dir() else song_dir
    pkg_file = release_dir / "release-package.md"
    if not pkg_file.exists():
        print(f"No release-package.md under {release_dir}")
        return 1

    pkg = parse_package(pkg_file)
    assets = find_assets(release_dir)
    privacy = args.privacy or pkg["privacy"]
    if privacy not in ("private", "unlisted"):
        # A package asking for "public" is refused, never silently downgraded —
        # the spec and what actually shipped must not disagree.
        print(f"Unusable privacy {privacy!r} — use private or unlisted; publish from Studio.")
        return 1
    if not assets["master"]:
        print(f"No release master .mp4 found in {release_dir}")
        return 1
    if not pkg["title"]:
        print("Package has no **Title:** bullet — refusing to guess one.")
        return 1

    client = yt.load_client()
    creds = yt.connect(client, args.profile)
    if creds is None:
        return 2

    if not yt.guard_channel(client, creds, args.expect_channel,
                            force=args.force, profile=args.profile):
        return 3

    if pkg["title"].strip() in client.list_uploaded_titles(creds):
        print(f"\nSKIP: a video titled {pkg['title']!r} is already on this channel.")
        return 0

    print(f"\n  title:      {pkg['title']}")
    print(f"  privacy:    {privacy}")
    print(f"  category:   {args.category}")
    print(f"  master:     {assets['master'].name}")
    print(f"  thumbnail:  {assets['thumbnail'].name if assets['thumbnail'] else '(none)'}")
    print(f"  subtitles:  {assets['subtitles'].name if assets['subtitles'] else '(none)'}")
    print(f"  tags:       {len(pkg['tags'])}")

    # QA runs on the dry run too — the verdict is worth seeing BEFORE committing
    # to --yes, which is the whole point of having a dry run.
    qa_ok = review_master(assets["master"])
    if not qa_ok and not args.skip_qa:
        return 4

    if not args.yes:
        print("\nDry run. Re-run with --yes to upload.")
        return 0

    print("\nUploading...")
    res = client.upload_video(
        creds, assets["master"],
        title=pkg["title"], description=pkg["description"], tags=pkg["tags"],
        category_id=args.category, privacy=privacy,
        progress=lambda pct: print(f"  {pct}%", end="\r", flush=True),
    )
    video_id = res["id"]
    print(f"\n  uploaded: {res['url']}  ({privacy})")

    # Completion steps are best-effort: the video exists, so a thumbnail or
    # caption failure must not read as a failed release — just report it.
    if assets["thumbnail"]:
        try:
            client.set_thumbnail(creds, video_id, assets["thumbnail"])
            print("  thumbnail: set")
        except Exception as e:
            print(f"  thumbnail: FAILED — attach it in Studio ({e})")
    if assets["subtitles"]:
        try:
            client.upload_caption(creds, video_id, assets["subtitles"], language=args.language)
            print(f"  subtitles: uploaded ({args.language})")
        except Exception as e:
            print(f"  subtitles: FAILED — attach it in Studio ({e})")

    if pkg["pinned_comment"]:
        print("\nPinned comment (post + pin by hand — no comment scope by design):\n")
        print(pkg["pinned_comment"])
    print(f"\nDone. Review privately, then publish from Studio:\n  {res['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
