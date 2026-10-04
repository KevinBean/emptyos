"""Scan the external vault for all Suno song material and emit a master index.

Read-only over the vault. Walks the known music roots, extracts every Suno
song/playlist id+url from note frontmatter/body, lists sibling audio files, and
classifies each album's archive status. Writes a human markdown index plus a
JSON sidecar used as the match-key for the later suno.com/me/playlists diff.

Vault path comes from emptyos.toml [notes].path — no hardcoded personal path.
Re-runnable: regenerate after every download pass.

    python scripts/suno_vault_index.py            # write index into the vault
    python scripts/suno_vault_index.py --stdout    # print summary only
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from suno_common import vault_root

# Music roots relative to the vault. Add more here if new ones appear.
ROOTS = [
    "70_Media/Music",
    "Music/Albums",
    "10_Projects/YouTube-Music-Channel/songs",
]

AUDIO_EXTS = {".mp3", ".wav", ".mp4", ".m4a", ".flac"}
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
SONG_URL = re.compile(r"suno\.com/song/(" + UUID + ")")
PLAYLIST_URL = re.compile(r"suno\.com/playlist/(" + UUID + ")")
BARE_UUID = re.compile(UUID)
LYRIC_MARKERS = re.compile(r"\[(Verse|Chorus|Intro|Outro|Bridge|Hook|Pre-Chorus)", re.I)


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def front_title(text: str, fallback: str) -> str:
    m = re.search(r"^title:\s*(.+)$", text, re.M)
    return m.group(1).strip().strip("'\"") if m else fallback


def scan_note(p: Path) -> dict:
    text = read(p)
    song_ids = sorted(set(SONG_URL.findall(text)))
    playlist_ids = sorted(set(PLAYLIST_URL.findall(text)))
    # A bare suno_id: <uuid> frontmatter line, only if no song-url already found.
    if not song_ids:
        m = re.search(r"^(?:suno_id|song_id|id):\s*(" + UUID + ")", text, re.M)
        if m:
            song_ids = [m.group(1)]
    return {
        "title": front_title(text, p.stem),
        "note": p.name,
        "song_ids": song_ids,
        "playlist_ids": playlist_ids,
        "has_lyrics": bool(LYRIC_MARKERS.search(text)),
    }


def folder_audio(folder: Path) -> list[str]:
    out = []
    for f in sorted(folder.rglob("*")):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
            out.append(str(f.relative_to(folder)).replace("\\", "/"))
    return out


def has_manifest(folder: Path) -> bool:
    return any((folder / n).exists() for n in ("downloaded-audio.md", "copyright-evidence.md"))


def status(tracks: list[dict], audio: list[str], manifest: bool) -> str:
    n_audio = len(audio)
    any_id = any(t["song_ids"] for t in tracks)
    if n_audio and manifest:
        return "FULLY ARCHIVED"
    if n_audio and any_id:
        return "AUDIO + IDS (no manifest)"
    if n_audio:
        return "AUDIO ONLY"
    if any_id:
        return "IDS ONLY (not downloaded)"
    return "NOTES ONLY (not on Suno)"


def main() -> None:
    vault = vault_root()
    albums = []  # one entry per album/single folder
    for root_rel in ROOTS:
        root = vault / root_rel
        if not root.exists():
            continue
        # Flat .md directly under a root = standalone album-in-one-file.
        for p in sorted(root.glob("*.md")):
            note = scan_note(p)
            albums.append({
                "root": root_rel, "album": p.stem, "folder": root_rel,
                "tracks": [note], "audio": [], "manifest": False,
                "status": status([note], [], False),
            })
        # Sub-folders = album/single dirs.
        for d in sorted([d for d in root.iterdir() if d.is_dir()]):
            track_notes = [scan_note(p) for p in sorted(d.glob("*.md"))]
            if not track_notes and not folder_audio(d):
                continue
            audio = folder_audio(d)
            manifest = has_manifest(d)
            albums.append({
                "root": root_rel, "album": d.name,
                "folder": f"{root_rel}/{d.name}",
                "tracks": track_notes, "audio": audio, "manifest": manifest,
                "status": status(track_notes, audio, manifest),
            })

    # Flat list of every Suno-linked track = the diff match-key.
    diff_key = []
    for a in albums:
        for t in a["tracks"]:
            for sid in t["song_ids"]:
                diff_key.append({
                    "song_id": sid, "title": t["title"], "album": a["album"],
                    "folder": a["folder"], "note": t["note"],
                    "has_audio": bool(a["audio"]),
                })

    all_playlists = sorted({pid for a in albums for t in a["tracks"] for pid in t["playlist_ids"]})

    # ---- write JSON sidecar (machine match-key) ----
    out_dir = vault / "70_Media/Music"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_suno-index.json").write_text(
        json.dumps({"albums": albums, "diff_key": diff_key,
                    "known_playlists": all_playlists}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # ---- write markdown index ----
    lines = [
        "---", "title: Suno master index", "type: index",
        "tags:", "  - music", "  - suno", "  - index", "---", "",
        "# Suno master index",
        "",
        "> Auto-generated by `scripts/suno_vault_index.py`. Re-run after any "
        "download pass. The JSON sidecar `_suno-index.json` is the match-key "
        "for diffing against suno.com/me/playlists.",
        "",
        f"- Albums/singles scanned: **{len(albums)}**",
        f"- Suno-linked tracks (have a song id): **{len(diff_key)}**",
        f"- Distinct playlist URLs recorded in notes: **{len(all_playlists)}**",
        "",
        "## Albums by archive status",
        "",
        "| Album | Root | Tracks | w/ Suno ID | Audio files | Manifest | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    order = {"FULLY ARCHIVED": 0, "AUDIO + IDS (no manifest)": 1, "AUDIO ONLY": 2,
             "IDS ONLY (not downloaded)": 3, "NOTES ONLY (not on Suno)": 4}
    for a in sorted(albums, key=lambda x: (order.get(x["status"], 9), x["root"], x["album"])):
        n_id = sum(1 for t in a["tracks"] if t["song_ids"])
        lines.append(
            f"| {a['album']} | {a['root']} | {len(a['tracks'])} | {n_id} | "
            f"{len(a['audio'])} | {'YES' if a['manifest'] else 'no'} | {a['status']} |")

    lines += ["", "## Known playlist URLs in notes", ""]
    lines += [f"- https://suno.com/playlist/{pid}" for pid in all_playlists] or ["- (none recorded)"]

    lines += ["", "## Every Suno-linked track (diff match-key)", "",
              "| Title | Album | Song ID | Local audio? | Note |",
              "|---|---|---|---|---|"]
    for t in sorted(diff_key, key=lambda x: (x["album"], x["title"])):
        lines.append(f"| {t['title']} | {t['album']} | `{t['song_id']}` | "
                     f"{'yes' if t['has_audio'] else 'NO'} | {t['folder']}/{t['note']} |")

    (out_dir / "_suno-index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- console summary ----
    print(f"Vault: {vault}")
    print(f"Albums/singles: {len(albums)}  |  Suno-linked tracks: {len(diff_key)}  "
          f"|  Playlists in notes: {len(all_playlists)}")
    from collections import Counter
    for st, n in Counter(a["status"] for a in albums).most_common():
        print(f"  {n:3d}  {st}")
    print(f"\nWrote:\n  {out_dir / '_suno-index.md'}\n  {out_dir / '_suno-index.json'}")
    missing_audio = [t for t in diff_key if not t["has_audio"]]
    print(f"\nSuno-linked tracks with NO local audio (download candidates): {len(missing_audio)}")
    for t in missing_audio[:40]:
        print(f"  - {t['title']}  ({t['album']})  {t['song_id']}")
    if len(missing_audio) > 40:
        print(f"  ... +{len(missing_audio) - 40} more")


if __name__ == "__main__":
    main()
