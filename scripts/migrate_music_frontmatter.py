import os
"""Migrate music vault notes to standard frontmatter format.

Scans the entire songs directory (recursively) for .md files.
For each song/album note, extracts metadata from markdown tables,
headers, and content, then writes standard YAML frontmatter.

Safety:
- Never deletes or moves files
- Preserves all body content unchanged
- Skips files that already have complete frontmatter
- Dry-run by default (--apply to write)

Usage:
    python scripts/migrate_music_frontmatter.py              # dry-run
    python scripts/migrate_music_frontmatter.py --apply      # write changes
    python scripts/migrate_music_frontmatter.py --albums     # also generate missing album notes
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VAULT = Path(os.environ.get("EOS_VAULT", "."))
SONGS_DIR = VAULT / "10_Projects/YouTube-Music-Channel/songs"

# Standard frontmatter fields for songs
SONG_FIELDS = [
    "type", "title", "title_en", "artist", "album", "track_number",
    "tags", "status", "genre", "language", "energy", "mood", "theme",
    "bpm", "key", "voice", "persona", "duration",
    "created", "published",
    "suno_url", "suno_model", "youtube_url", "youtube_id",
]

# Fields we consider "complete" — if all present, skip the file
REQUIRED_FIELDS = {"type", "title", "tags", "status", "created"}


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter, return (fm_dict, body)."""
    fm = {}
    body = content
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            fm_text = content[4:end]
            body = content[end + 4:].lstrip("\n")
            for line in fm_text.split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("- "):
                    # Part of a list — append to last key
                    if _last_key and _last_key in fm:
                        if isinstance(fm[_last_key], list):
                            fm[_last_key].append(line[2:].strip())
                    continue
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    _last_key = k  # noqa: F841
                    if v == "":
                        fm[k] = ""
                    elif v.startswith("[") and v.endswith("]"):
                        fm[k] = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",")]
                    else:
                        fm[k] = v
                    continue
                _last_key = None  # noqa: F841
    # Handle list parsing properly
    _last_key = None
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            fm = {}
            fm_text = content[4:end]
            body = content[end + 4:].lstrip("\n")
            current_key = None
            for line in fm_text.split("\n"):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("- ") and current_key is not None:
                    val = stripped[2:].strip()
                    if not isinstance(fm.get(current_key), list):
                        fm[current_key] = [fm[current_key]] if fm.get(current_key) else []
                    fm[current_key].append(val)
                elif ":" in stripped:
                    k, v = stripped.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    current_key = k
                    if v == "":
                        fm[k] = ""
                    elif v.startswith("[") and v.endswith("]"):
                        fm[k] = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
                    else:
                        fm[k] = v
    return fm, body


def extract_from_markdown_table(body: str) -> dict:
    """Extract key-value pairs from markdown tables like | 项目 | 内容 |."""
    extracted = {}
    # Match table rows: | key | value |
    for m in re.finditer(r'\|\s*(.+?)\s*\|\s*(.+?)\s*\|', body):
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if key in ("项目", "内容", "field", "value", "---", "------", "-"):
            continue
        if "---" in key:
            continue

        # Map Chinese/English keys to standard fields
        key_map = {
            "类型": "genre", "曲风": "genre", "style": "genre",
            "bpm": "bpm",
            "创建日期": "created", "生成日期": "created", "created": "created",
            "语言": "language", "language": "language",
            "系列": "series",
            "persona": "persona",
            "suno 模型": "suno_model", "suno模型": "suno_model",
            "suno 链接": "suno_url", "suno链接": "suno_url", "suno": "suno_url",
            "youtube": "youtube_url",
            "专辑": "album",
            "关联": "related",
            "频道": "channel",
            "energy": "energy",
            "theme": "theme",
            "mood": "mood",
            "voice": "voice",
            "for": "for_person",
            "歌名": "title",
        }
        mapped = key_map.get(key)
        if mapped:
            # Clean up value — remove markdown links for URLs
            if mapped in ("suno_url", "youtube_url"):
                url_match = re.search(r'\((https?://[^\)]+)\)', val)
                if url_match:
                    val = url_match.group(1)
                elif val.startswith("http"):
                    val = val
            elif mapped == "album":
                # Keep wikilink format
                if "[[" not in val:
                    val = f"[[{val}]]"
            extracted[mapped] = val

    return extracted


def extract_from_bullet_list(body: str) -> dict:
    """Extract from - **key**: value bullet lists."""
    extracted = {}
    for m in re.finditer(r'-\s+\*\*(.+?)\*\*\s*[:：]\s*(.+)', body):
        key = m.group(1).strip().lower()
        val = m.group(2).strip()

        key_map = {
            "歌名": "title", "曲风": "genre", "生成日期": "created",
            "语言": "language", "系列": "series", "bpm": "bpm",
            "style": "genre", "for": "for_person",
        }
        mapped = key_map.get(key)
        if mapped:
            extracted[mapped] = val

    return extracted


def extract_suno_url(body: str) -> str | None:
    """Find suno.com URLs in body."""
    m = re.search(r'https://suno\.com/song/[\w-]+', body)
    return m.group(0) if m else None


def extract_youtube_url(body: str) -> str | None:
    """Find youtube URLs in body."""
    m = re.search(r'https://(?:www\.)?youtube\.com/watch\?v=([\w-]+)', body)
    if m:
        return m.group(0)
    m = re.search(r'https://youtu\.be/([\w-]+)', body)
    return m.group(0) if m else None


def extract_youtube_id(body: str) -> str | None:
    """Extract YouTube video ID."""
    m = re.search(r'youtube\.com/watch\?v=([\w-]+)', body)
    if m:
        return m.group(1)
    m = re.search(r'youtu\.be/([\w-]+)', body)
    return m.group(1) if m else None


def extract_album_link(body: str) -> str | None:
    """Find [[_Album-Name]] wikilinks."""
    m = re.search(r'\[\[(_[^\]]*[Aa]lbum[^\]]*)\]\]', body)
    return f"[[{m.group(1)}]]" if m else None


def detect_language(body: str) -> str:
    """Rough language detection from lyrics."""
    # Count CJK characters vs latin
    cjk = len(re.findall(r'[\u4e00-\u9fff]', body))
    latin = len(re.findall(r'[a-zA-Z]', body))
    jp = len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', body))
    if jp > 10:
        return "ja"
    if cjk > latin:
        return "zh"
    if latin > cjk * 2:
        return "en"
    return "zh-en"


def guess_created_from_dirname(dir_name: str) -> str | None:
    """Extract date from directory names like 2026-01-02__One_Step_就好."""
    m = re.match(r'(\d{4}-\d{2}-\d{2})__', dir_name)
    return m.group(1) if m else None


def is_album_note(path: Path) -> bool:
    """Check if this is an album note (starts with _ and contains Album)."""
    return path.stem.startswith("_") and "album" in path.stem.lower()


def build_song_frontmatter(path: Path, existing_fm: dict, body: str) -> dict:
    """Build standardized song frontmatter from all available sources."""
    fm = dict(existing_fm)  # Start with what we have

    # Extract from markdown tables and bullet lists
    table_data = extract_from_markdown_table(body)
    bullet_data = extract_from_bullet_list(body)

    # Merge — existing frontmatter wins, then table, then bullets
    for src in (bullet_data, table_data):
        for k, v in src.items():
            if k not in fm or not fm[k]:
                fm[k] = v

    # Always set type
    fm["type"] = "song"

    # Title — from frontmatter, or h1, or filename
    if not fm.get("title"):
        h1 = re.search(r'^#\s+(?:《)?(.+?)(?:》)?\s*$', body, re.MULTILINE)
        if h1:
            fm["title"] = h1.group(1).strip()
        else:
            fm["title"] = path.stem.replace("-", " ").replace("_", " ")

    # Artist
    if not fm.get("artist"):
        fm["artist"] = "3:30 Channel"

    # Album — from body wikilinks
    if not fm.get("album"):
        album = extract_album_link(body)
        if album:
            fm["album"] = album

    # Tags — ensure song + music
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    if "song" not in tags:
        tags.insert(0, "song")
    if "music" not in tags:
        tags.append("music")
    fm["tags"] = tags

    # Status normalization
    status_map = {
        "published": "released", "complete": "released",
        "suno-complete": "released", "uploaded": "released",
        "": "draft", "unknown": "draft",
    }
    status = fm.get("status", "draft")
    fm["status"] = status_map.get(status, status)

    # Created — from frontmatter, directory name, or body
    if not fm.get("created"):
        parent_name = path.parent.name
        date = guess_created_from_dirname(parent_name)
        if date:
            fm["created"] = date

    # Language detection + normalization
    if not fm.get("language"):
        fm["language"] = detect_language(body)
    else:
        lang_norm = {
            "中文": "zh", "chinese": "zh", "Chinese": "zh",
            "英文": "en", "english": "en", "English": "en",
            "日文": "ja", "japanese": "ja", "Japanese": "ja",
            "English + Japanese": "en-ja",
        }
        fm["language"] = lang_norm.get(fm["language"], fm["language"])

    # URLs from body
    if not fm.get("suno_url"):
        suno = extract_suno_url(body)
        if suno:
            fm["suno_url"] = suno
    if not fm.get("youtube_url"):
        yt = extract_youtube_url(body)
        if yt:
            fm["youtube_url"] = yt
    if not fm.get("youtube_id"):
        yt_id = extract_youtube_id(body)
        if yt_id:
            fm["youtube_id"] = yt_id

    # BPM — extract number
    if fm.get("bpm"):
        bpm_match = re.search(r'(\d+)', str(fm["bpm"]))
        if bpm_match:
            fm["bpm"] = bpm_match.group(1)

    # Genre from style prompt or genre field
    if not fm.get("genre") and fm.get("style"):
        fm["genre"] = fm["style"]
        del fm["style"]

    return fm


def format_frontmatter(fm: dict) -> str:
    """Format frontmatter dict as YAML string."""
    lines = ["---"]

    # Ordered output — important fields first
    order = [
        "type", "title", "title_en", "artist", "album", "track_number",
        "tags", "status", "genre", "language",
        "energy", "mood", "theme", "persona", "voice",
        "bpm", "key", "duration",
        "created", "published",
        "suno_url", "suno_model", "youtube_url", "youtube_id",
        "series", "related", "channel", "for_person", "source",
    ]

    written = set()
    for key in order:
        if key in fm and fm[key] not in ("", None, []):
            written.add(key)
            val = fm[key]
            if isinstance(val, list):
                lines.append(f"{key}:")
                for item in val:
                    lines.append(f"  - {item}")
            elif isinstance(val, str) and ("[[" in val or '"' in val or ":" in val):
                lines.append(f'{key}: "{val}"')
            else:
                lines.append(f"{key}: {val}")

    # Any remaining fields not in order
    for key, val in fm.items():
        if key in written or val in ("", None, []):
            continue
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {item}")
        elif isinstance(val, str) and ("[[" in val or '"' in val or ":" in val):
            lines.append(f'{key}: "{val}"')
        else:
            lines.append(f"{key}: {val}")

    lines.append("---")
    return "\n".join(lines)


def process_song(path: Path, apply: bool) -> dict:
    """Process a single song file. Returns summary."""
    content = path.read_text(encoding="utf-8")
    existing_fm, body = parse_frontmatter(content)

    # Check if already complete
    if existing_fm.get("type") == "song" and REQUIRED_FIELDS.issubset(existing_fm.keys()):
        return {"path": str(path), "status": "skip", "reason": "already complete"}

    new_fm = build_song_frontmatter(path, existing_fm, body)

    # Calculate what changed
    added = {k: v for k, v in new_fm.items() if k not in existing_fm and v not in ("", None, [])}
    changed = {k: v for k, v in new_fm.items() if k in existing_fm and existing_fm[k] != v and v not in ("", None, [])}

    if not added and not changed:
        return {"path": str(path), "status": "skip", "reason": "no changes needed"}

    result = {
        "path": str(path),
        "status": "would_update" if not apply else "updated",
        "title": new_fm.get("title", "?"),
        "added": list(added.keys()),
        "changed": list(changed.keys()),
    }

    if apply:
        new_content = format_frontmatter(new_fm) + "\n\n" + body
        path.write_text(new_content, encoding="utf-8")

    return result


def scan_all_songs(songs_dir: Path) -> list[Path]:
    """Recursively find all song .md files."""
    songs = []
    for f in sorted(songs_dir.rglob("*.md")):
        if is_album_note(f):
            continue
        songs.append(f)
    return songs


def scan_albums(songs_dir: Path) -> list[Path]:
    """Find all album notes."""
    albums = []
    for f in sorted(songs_dir.rglob("*.md")):
        if is_album_note(f):
            albums.append(f)
    return albums


def check_album_frontmatter(path: Path, apply: bool) -> dict:
    """Ensure album notes have standard frontmatter."""
    content = path.read_text(encoding="utf-8")
    existing_fm, body = parse_frontmatter(content)

    if existing_fm.get("type") == "album" and REQUIRED_FIELDS.issubset(existing_fm.keys()):
        return {"path": str(path), "status": "skip", "reason": "already complete"}

    fm = dict(existing_fm)
    fm["type"] = "album"
    if not fm.get("title"):
        h1 = re.search(r'^#\s+(.+)$', body, re.MULTILINE)
        fm["title"] = h1.group(1).strip() if h1 else path.stem.lstrip("_").replace("-", " ")
    if not fm.get("artist"):
        fm["artist"] = "3:30 Channel"
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    if "album" not in tags:
        tags.insert(0, "album")
    if "music" not in tags:
        tags.append("music")
    fm["tags"] = tags
    if not fm.get("status"):
        fm["status"] = "released"

    added = {k: v for k, v in fm.items() if k not in existing_fm and v not in ("", None, [])}
    changed = {k: v for k, v in fm.items() if k in existing_fm and existing_fm[k] != v}

    if not added and not changed:
        return {"path": str(path), "status": "skip", "reason": "no changes needed"}

    result = {
        "path": str(path),
        "status": "would_update" if not apply else "updated",
        "title": fm.get("title", "?"),
        "added": list(added.keys()),
        "changed": list(changed.keys()),
    }

    if apply:
        new_content = format_frontmatter(fm) + "\n\n" + body
        path.write_text(new_content, encoding="utf-8")

    return result


def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Migrate music notes to standard frontmatter")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    parser.add_argument("--albums", action="store_true", help="Also process album notes")
    args = parser.parse_args()

    if not SONGS_DIR.exists():
        print(f"Songs directory not found: {SONGS_DIR}")
        sys.exit(1)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Music Frontmatter Migration ({mode})")
    print(f"{'='*60}\n")

    # Process songs
    songs = scan_all_songs(SONGS_DIR)
    print(f"Found {len(songs)} song files\n")

    updated = []
    skipped = []
    for path in songs:
        result = process_song(path, args.apply)
        if result["status"] in ("updated", "would_update"):
            updated.append(result)
        else:
            skipped.append(result)

    # Process albums
    if args.albums:
        albums = scan_albums(SONGS_DIR)
        print(f"\nFound {len(albums)} album files\n")
        for path in albums:
            result = check_album_frontmatter(path, args.apply)
            if result["status"] in ("updated", "would_update"):
                updated.append(result)
            else:
                skipped.append(result)

    # Report
    print(f"\n--- Results ---")
    print(f"Total files scanned: {len(songs) + (len(albums) if args.albums else 0)}")
    print(f"Would update: {len(updated)}")
    print(f"Skipped: {len(skipped)}")

    if updated:
        print(f"\n--- {'Updated' if args.apply else 'Would Update'} ---")
        for r in updated:
            rel = Path(r["path"]).relative_to(SONGS_DIR)
            added_str = ", ".join(r.get("added", []))
            changed_str = ", ".join(r.get("changed", []))
            parts = []
            if added_str:
                parts.append(f"+{added_str}")
            if changed_str:
                parts.append(f"~{changed_str}")
            print(f"  {r.get('title', '?'):30s} | {rel}")
            if parts:
                print(f"  {'':30s}   {'; '.join(parts)}")

    if not args.apply and updated:
        print(f"\n  Run with --apply to write changes.")
        print(f"  Run with --apply --albums to also update album notes.")

    print()


if __name__ == "__main__":
    main()
