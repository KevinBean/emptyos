"""Backfill `downloaded-audio.md` manifests for albums that have audio but none.

Reads `70_Media/Music/_suno-index.json` (written by suno_vault_index.py) and,
for each album whose status is "AUDIO + IDS (no manifest)", writes a manifest in
the One-Walk-Before-the-Build format.

Honest by construction:
  - Tracks table = note frontmatter (authoritative).
  - Audio table  = files on disk + byte sizes (authoritative; size 0 = flagged).
  - Pairing      = best-effort normalized-title match, clearly labelled.

Never overwrites an existing downloaded-audio.md. Additive only.

    python scripts/suno_backfill_manifests.py --dry-run   # preview, write nothing
    python scripts/suno_backfill_manifests.py             # write manifests
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from suno_common import vault_root

AUDIO_EXTS = {".mp3", ".wav", ".mp4", ".m4a", ".flac"}
TARGET_STATUS = "AUDIO + IDS (no manifest)"
TODAY = "2026-06-20"


def norm(s: str) -> str:
    """Normalize a title/filename for fuzzy matching."""
    s = s.lower()
    s = re.sub(r"\.[a-z0-9]{2,4}$", "", s)              # drop extension
    s = re.sub(r"^\s*(track\s*\d+[:\-.]?|^\d+\s*[·\-.]?)", "", s)  # leading track no.
    s = re.sub(r"[\s\-_·.,:;()\[\]/\\'\"!?]+", "", s)   # punctuation + space
    return s.strip()


def pair_audio(tracks: list[dict], audio_files: list[tuple[str, int]]) -> tuple[dict, list[tuple[str, int]]]:
    """Best-effort map track-index -> [audio paths]; return (mapping, leftovers)."""
    mapping: dict[int, list[str]] = {i: [] for i in range(len(tracks))}
    used = set()
    norm_tracks = [(i, norm(t["title"])) for i, t in enumerate(tracks)]
    for path, _size in audio_files:
        stem = norm(Path(path).name)
        best = None
        for i, nt in norm_tracks:
            if not nt:
                continue
            if nt == stem or nt in stem or stem in nt:
                # prefer the longest-overlap track
                if best is None or len(nt) > len(norm_tracks[best][1]):
                    best = next(k for k, (idx, _) in enumerate(norm_tracks) if idx == i)
                    best = i
        if best is not None:
            mapping[best].append(path)
            used.add(path)
    leftovers = [(p, s) for (p, s) in audio_files if p not in used]
    return mapping, leftovers


def build_manifest(album: dict, folder: Path) -> str:
    tracks = album["tracks"]
    # Top-level audio only — the final deliverable tracks. Recursing pulls in
    # MV-generation scratch (mv-*/, vocal stems, clip fragments) = noise.
    audio_files: list[tuple[str, int]] = []
    for f in sorted(folder.glob("*")):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
            try:
                audio_files.append((f.name, f.stat().st_size))
            except Exception:
                audio_files.append((f.name, 0))
    mapping, leftovers = pair_audio(tracks, audio_files)

    playlists = sorted({pid for t in tracks for pid in t["playlist_ids"]})
    src_line = (f"https://suno.com/playlist/{playlists[0]}" if playlists
                else "(no playlist URL recorded in notes)")

    L = [
        "---",
        f"title: Downloaded audio - {album['album']}",
        "type: source-manifest",
        f"album: {album['album']}",
        f"source: {src_line}",
        f"backfilled: {TODAY}",
        "tags:",
        "  - music",
        "  - audio",
        "  - suno",
        "---",
        "",
        f"# Downloaded audio - {album['album']}",
        "",
        f"> Auto-backfilled {TODAY} by `scripts/suno_backfill_manifests.py` from the "
        "existing notes + on-disk audio. Track-to-file pairing is best-effort "
        "(normalized title match); the **Audio files on disk** table below is the "
        "authoritative file list. Verify pairings before relying on them.",
        "",
        f"Source playlist: {src_line}",
        "",
        "## Tracks (from notes)",
        "",
        "| # | Track | Suno song ID | Likely audio (auto-matched) | Note |",
        "|---|---|---|---|---|",
    ]
    for i, t in enumerate(tracks):
        ids = ", ".join(t["song_ids"]) or "—"
        matched = ", ".join(f"[[{Path(p).name}]]" for p in mapping[i]) or "— (unmatched)"
        L.append(f"| {i+1} | {t['title']} | {ids} | {matched} | [[{Path(t['note']).stem}]] |")

    L += ["", "## Audio files on disk (authoritative)", "",
          "| File | Size (bytes) | OK |", "|---|---|---|"]
    for rel, size in audio_files:
        ok = "✓" if size > 0 else "⚠ ZERO"
        L.append(f"| [[{Path(rel).name}]] | {size:,} | {ok} |")

    if leftovers:
        L += ["", "## Audio not matched to a track note", ""]
        for rel, size in leftovers:
            L.append(f"- [[{Path(rel).name}]] ({size:,} bytes)")

    miss = [t["title"] for i, t in enumerate(tracks) if not mapping[i]]
    if miss:
        L += ["", "## Track notes with no matched audio (possible download gap)", ""]
        L += [f"- {m}" for m in miss]

    L += ["", "## Notes", "",
          "- Generated from existing local material; NOT a fresh Suno download.",
          "- Copyright/evidence screenshots not captured for this album — add a "
          "`copyright-evidence.md` (One-Walk format) if this album needs rights proof.",
          ""]
    return "\n".join(L)


def main() -> None:
    dry = "--dry-run" in sys.argv
    vault = vault_root()
    data = json.loads((vault / "70_Media/Music/_suno-index.json").read_text(encoding="utf-8"))
    targets = [a for a in data["albums"] if a["status"] == TARGET_STATUS]
    print(f"Target albums (status='{TARGET_STATUS}'): {len(targets)}\n")

    wrote = skipped = 0
    for a in targets:
        folder = vault / a["folder"]
        out = folder / "downloaded-audio.md"
        if out.exists():
            print(f"  SKIP (exists): {a['folder']}/downloaded-audio.md")
            skipped += 1
            continue
        md = build_manifest(a, folder)
        if dry:
            print(f"  WOULD WRITE: {a['folder']}/downloaded-audio.md "
                  f"({len(a['tracks'])} tracks, {len(a['audio'])} audio)")
        else:
            out.write_text(md, encoding="utf-8")
            print(f"  WROTE: {a['folder']}/downloaded-audio.md "
                  f"({len(a['tracks'])} tracks, {len(a['audio'])} audio)")
        wrote += 1

    print(f"\n{'Would write' if dry else 'Wrote'}: {wrote}   Skipped (already had one): {skipped}")
    if dry:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")


if __name__ == "__main__":
    main()
