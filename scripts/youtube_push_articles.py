#!/usr/bin/env python3
"""Push rendered article slideshow videos to the AI Engineering YouTube channel.

Discovers every  {vault}/30_Resources/Published/media/podcast-<slug>.mp4 , maps
each to its post at  posts/<slug>.md  for title/summary/tags, and uploads via the
youtube connector's client (plugins/youtube/client.py). Runs OUTSIDE the daemon —
no restart needed after `scripts/youtube_auth.py`.

SAFETY, by construction:
  - Dry-run by DEFAULT. It prints exactly what it would upload and stops. Pass
    --yes to actually upload (outbound publish stays behind an explicit gate).
  - Verifies the connected channel title matches --expect-channel (default
    "engineering", case-insensitive substring) and REFUSES otherwise unless
    --force — so 6 EmptyOS articles can't accidentally land on the music channel.
  - Uploads PRIVATE by default. Flip visibility in YouTube Studio after review,
    or pass --privacy unlisted|public deliberately.
  - Skips any video whose title already exists on the channel (dedupe), so
    re-runs are safe.

Usage:
  python scripts/youtube_auth.py                 # once — pick the AI Engineering channel
  python scripts/youtube_push_articles.py        # dry run — shows the plan
  python scripts/youtube_push_articles.py --yes  # upload (private)
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts/ modules

import youtube_common as yt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _load_client():
    return yt.load_client()


def _vault_root() -> Path:
    cfg = REPO / "emptyos.toml"
    if cfg.exists():
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        p = (data.get("notes", {}) or {}).get("path")
        if p:
            return Path(p)
    raise SystemExit("Could not read notes.path from emptyos.toml")


def _frontmatter(md_path: Path) -> dict:
    """Parse a post's YAML frontmatter into a dict, plus a `_body` first-paragraph.

    Full YAML parse first; on failure (some posts have mangled-escape fields like
    image_prompt) fall back to targeted line extraction of title/summary/tags so
    one bad field never drops the whole block."""
    text = md_path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    block = m.group(1)
    fm: dict = {}
    try:
        import yaml
        parsed = yaml.safe_load(block)
        if isinstance(parsed, dict):
            fm = parsed
    except Exception:
        fm = {}
    if not fm.get("title"):
        mt = re.search(r'^title:\s*"?(.+?)"?\s*$', block, re.MULTILINE)
        if mt:
            fm["title"] = mt.group(1)
    if not fm.get("summary"):
        ms = re.search(r'^summary:\s*"?(.+?)"?\s*$', block, re.MULTILINE)
        if ms:
            fm["summary"] = ms.group(1)
    if not fm.get("tags"):
        mtag = re.search(r'^tags:\s*\n((?:\s*-\s*.+\n?)+)', block, re.MULTILINE)
        if mtag:
            fm["tags"] = [ln.strip()[1:].strip() for ln in mtag.group(1).splitlines() if ln.strip().startswith("-")]
    # First prose paragraph of the body — a description fallback for summary-less posts.
    body = text[m.end():]
    for para in re.split(r"\n\s*\n", body):
        p = para.strip()
        if p and not p.startswith(("#", "!", "<", ">", "|", "-", "```")):
            fm["_body"] = re.sub(r"\s+", " ", p)[:400]
            break
    return fm


def _build_description(fm: dict, slug: str, site_base: str) -> str:
    parts = []
    summary = (fm.get("summary") or fm.get("_body") or "").strip()
    if summary:
        parts.append(summary)
    if site_base:
        parts.append(f"Read the full article: {site_base.rstrip('/')}/{slug}")
    tags = fm.get("tags") or []
    if isinstance(tags, list) and tags:
        hashtags = " ".join(
            "#" + re.sub(r"[^0-9A-Za-z]", "", str(t)) for t in tags if str(t).strip()
        )
        if hashtags:
            parts.append(hashtags)
    return "\n\n".join(parts)


def _normalize_tags(fm: dict) -> list[str]:
    tags = fm.get("tags") or []
    return [str(t).strip() for t in tags if str(t).strip()] if isinstance(tags, list) else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="actually upload (default is a dry run)")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    ap.add_argument("--expect-channel", default="engineering",
                    help="refuse unless the connected channel title contains this (case-insensitive)")
    ap.add_argument("--force", action="store_true", help="upload even if the channel guard fails")
    ap.add_argument("--only", default="", help="only push the video whose slug contains this")
    ap.add_argument("--site-base", default="", help="base URL for an article link in the description")
    ap.add_argument("--category", default="28", help="YouTube category id (28=Science & Tech)")
    args = ap.parse_args()

    client = _load_client()

    creds = yt.connect(client)
    if creds is None:
        return 2

    if not yt.guard_channel(client, creds, args.expect_channel, force=args.force):
        return 3

    vault = _vault_root()
    media_dir = vault / "30_Resources" / "Published" / "media"
    posts_dir = vault / "30_Resources" / "Published" / "posts"
    videos = sorted(media_dir.glob("podcast-*.mp4"))
    if args.only:
        videos = [v for v in videos if args.only in v.stem]
    if not videos:
        print(f"No podcast-*.mp4 found in {media_dir}")
        return 1

    existing = client.list_uploaded_titles(creds)
    print(f"Channel already has {len(existing)} uploads (used for dedupe).\n")

    plan = []
    for vid in videos:
        slug = vid.stem[len("podcast-"):]
        post = posts_dir / f"{slug}.md"
        fm = _frontmatter(post) if post.exists() else {}
        title = (fm.get("title") or slug.replace("-", " ")).strip().strip('"')
        plan.append({
            "path": vid,
            "slug": slug,
            "title": title,
            "description": _build_description(fm, slug, args.site_base),
            "tags": _normalize_tags(fm),
            "has_post": post.exists(),
            "dup": title.strip() in existing,
        })

    mode = "UPLOAD" if args.yes else "DRY RUN"
    print(f"=== {mode} · privacy={args.privacy} · {len(plan)} videos ===\n")
    for p in plan:
        size_mb = p["path"].stat().st_size / 1e6
        flag = "  [SKIP: already uploaded]" if p["dup"] else ("" if p["has_post"] else "  [no post — slug title]")
        print(f"• {p['title']}{flag}")
        print(f"    file: {p['path'].name} ({size_mb:.1f} MB)  tags: {', '.join(p['tags']) or '—'}")

    if not args.yes:
        print("\nDry run only. Re-run with --yes to upload (private).")
        return 0

    print()
    results = []
    for p in plan:
        if p["dup"]:
            print(f"SKIP (dup): {p['title']}")
            continue
        print(f"Uploading: {p['title']} ...")
        try:
            res = client.upload_video(
                creds, p["path"], title=p["title"], description=p["description"],
                tags=p["tags"], category_id=args.category, privacy=args.privacy,
                progress=lambda pct: print(f"  {pct}%", end="\r"),
            )
            print(f"  done → {res['url']}  ({res['privacy']})")
            results.append(res)
        except Exception as e:
            print(f"  FAILED: {e}")

    print(f"\nUploaded {len(results)} video(s) as {args.privacy}.")
    if results:
        print("Review + set visibility in YouTube Studio: https://studio.youtube.com/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
