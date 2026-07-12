"""Link captured Suno songs to existing vault song notes.

Walks <output>/metadata/*.json (from suno_capture.py) and matches each Suno track
to a vault song note by normalized title. For each match:
  - updates frontmatter: suno_url, suno_id, suno_version, suno_plays, suno_likes
  - copies <output>/screenshots/{id}.png into the note's folder as suno-{id}.png

Vault song notes must have:
  - frontmatter starting with `---`
  - either `type: song` or a `song` tag, AND a `title:` field

Usage:
    python scripts/suno_link_to_vault.py --captured scratch/suno-songs --songs-dir "10_Projects/YouTube-Music-Channel/songs"
    python scripts/suno_link_to_vault.py ... --apply

Vault path is read from emptyos.toml `[notes] path` unless --vault is passed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path


def norm(s: str) -> str:
    """Normalize title for matching (handles album numbering, parens, slashes, mashups)."""
    s = s.strip()
    s = re.sub(r"^(?:Track\s*)?\d+\s*[·.:\-—]\s*", "", s, flags=re.I)
    s = re.sub(r"^\d+\s+", "", s)
    s = re.sub(r"[\(（\[].*?[\)）\]]", "", s)
    s = s.split("/")[0]
    s = s.split(" x ")[0]
    s = s.replace("（", "").replace("）", "")
    s = re.sub(r"\s+", "", s).lower()
    return s


def parse_frontmatter(text: str):
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    return text[3:end].strip("\n"), text[end + 4 :].lstrip("\n")


def update_fm(fm: str, updates: dict[str, str]) -> str:
    lines = fm.split("\n")
    seen = set()
    out = []
    for line in lines:
        m = re.match(r"^([a-zA-Z_][\w-]*)\s*:", line)
        if m and m.group(1) in updates:
            seen.add(m.group(1))
            out.append(f"{m.group(1)}: {updates[m.group(1)]}")
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}: {v}")
    return "\n".join(out)


def load_vault_notes(songs_dir: Path) -> list[dict]:
    out = []
    if not songs_dir.exists():
        return out
    for md in songs_dir.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, body = parse_frontmatter(text)
        if fm is None:
            continue
        is_song = bool(
            re.search(r"^type:\s*song\b", fm, re.M) or re.search(r"^\s*-\s*song\s*$", fm, re.M)
        )
        title_match = re.search(r"^title:\s*(.+)$", fm, re.M)
        if not is_song and not title_match:
            continue
        title = title_match.group(1).strip().strip('"').strip("'") if title_match else md.stem
        out.append(
            {
                "folder": md.parent,
                "md": md,
                "title": title,
                "title_norm": norm(title),
                "fm": fm,
                "body": body,
            }
        )
    return out


def load_suno_songs(captured: Path) -> list[dict]:
    out = []
    for f in sorted((captured / "metadata").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        d["title_norm"] = norm(d["title"])
        out.append(d)
    return out


def resolve_vault(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    cfg = Path("emptyos.toml")
    if cfg.exists():
        path = tomllib.load(cfg.open("rb")).get("notes", {}).get("path")
        if path:
            return Path(path)
    raise SystemExit("vault path: pass --vault or set [notes] path in emptyos.toml")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--captured", required=True, help="Output dir from suno_capture.py")
    ap.add_argument(
        "--vault", default=None, help="Vault root (defaults to emptyos.toml [notes] path)"
    )
    ap.add_argument("--songs-dir", required=True, help="Vault-relative dir to scan for song notes")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = ap.parse_args()

    vault = resolve_vault(args.vault)
    songs_dir = (vault / args.songs_dir).resolve()
    captured = Path(args.captured).resolve()
    shots = captured / "screenshots"

    notes = load_vault_notes(songs_dir)
    songs = load_suno_songs(captured)
    print(f"vault notes: {len(notes)} under {songs_dir}")
    print(f"suno songs:  {len(songs)} under {captured}")

    note_idx: dict[str, list[dict]] = {}
    for n in notes:
        note_idx.setdefault(n["title_norm"], []).append(n)

    matched, unmatched = [], []
    by_note: dict[str, list[dict]] = {}
    note_by_md: dict[str, dict] = {str(n["md"]): n for n in notes}
    used_md: set[str] = set()
    for s in songs:
        cands = note_idx.get(s["title_norm"], [])
        if not cands:
            unmatched.append(s)
            continue
        # Prefer exact title match, then a still-unused candidate, else first
        st_lower = s["title"].strip().lower()
        exact = [c for c in cands if c["title"].strip().lower() == st_lower]
        if exact:
            n = exact[0]
        else:
            free = [c for c in cands if str(c["md"]) not in used_md]
            n = free[0] if free else cands[0]
        used_md.add(str(n["md"]))
        by_note.setdefault(str(n["md"]), []).append(s)
        matched.append((s, n))

    print(f"\n[match] {len(matched)} suno → vault note ({len(by_note)} distinct notes)")
    print(f"[unmatched suno] {len(unmatched)} (sketches without vault notes)")

    multi = [(k, ss) for k, ss in by_note.items() if len(ss) > 1]
    if multi:
        print(f"\n[multi] {len(multi)} notes with >1 suno take")
        for k, ss in multi:
            print(f"  {Path(k).parent.name}/{Path(k).name}: {[x['title'] for x in ss]}")

    if not args.apply:
        print("\n(dry run — pass --apply to write)")
        return 0

    print("\n[applying]")
    for md_str, ss in by_note.items():
        md = Path(md_str)
        ss = sorted(ss, key=lambda x: -(x.get("plays") or 0))
        primary, alts = ss[0], ss[1:]
        n = note_by_md[md_str]
        updates = {
            "suno_url": primary["url"],
            "suno_id": primary["id"],
            "suno_version": primary.get("version") or "",
            "suno_plays": str(primary.get("plays") or ""),
            "suno_likes": str(primary.get("likes") or ""),
        }
        if alts:
            updates["suno_alts"] = '"' + ", ".join(a["url"] for a in alts) + '"'
        new_fm = update_fm(n["fm"], updates)
        n["md"].write_text(f"---\n{new_fm}\n---\n\n{n['body']}", encoding="utf-8")
        for s in ss:
            src = shots / f"{s['id']}.png"
            if src.exists():
                shutil.copy2(src, md.parent / f"suno-{s['id']}.png")
        print(f"  ok  {md.parent.name}/{md.name}  (primary={primary['id']}, alts={len(alts)})")
    print(f"\n[done] linked {len(by_note)} notes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
