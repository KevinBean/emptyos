"""Batch-process multiple Suno albums from a combined JSON (paced).

Reads .cache/suno/remaining.json => {folder: {pid, name, clips:[{n,id,title,created,mp3,mp4}]}}
Modes:
  download  - write per-album clips file + download MP3/MP4 (delays between tracks/albums)
  ids       - print the convert_wav id list per album (paste into the in-browser firing)
  wav       - poll CDN + download WAV for every track
  manifest  - write downloaded-audio.md + copyright-evidence.md per album

Folder->vault path: 'Healing-Sessions-English' lives under 70_Media/Music/;
everything else under 10_Projects/YouTube-Music-Channel/songs/.

    python scripts/suno_batch.py download --delay 5
    python scripts/suno_batch.py wav
    python scripts/suno_batch.py manifest
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from suno_common import download, safe, vault_root, wav_ready

# override with SUNO_COMBINED=<path to combined json> for a different album set
COMBINED = Path(os.environ.get("SUNO_COMBINED")
                or Path(__file__).resolve().parent.parent / ".cache/suno/remaining.json")
TODAY = "2026-06-20"
# Suno account handle for manifests; set SUNO_OWNER=<handle> (no leading @).
_OWNER = os.environ.get("SUNO_OWNER", "").lstrip("@").strip()
OWNER_TAG = f"@{_OWNER}" if _OWNER else "the account owner"


def album_path(vault: Path, folder: str, info: dict | None = None) -> Path:
    # explicit per-album vault-relative path wins (set in the combined JSON)
    if info and info.get("path"):
        return vault / info["path"]
    if folder == "Healing-Sessions-English":
        return vault / "70_Media/Music" / folder
    return vault / "10_Projects/YouTube-Music-Channel/songs" / folder


def clips_file(folder: str) -> Path:
    return COMBINED.parent / f"{folder}-clips.json"


def load() -> dict:
    return json.loads(COMBINED.read_text(encoding="utf-8"))


def do_download(delay: float) -> None:
    vault = vault_root()
    data = load()
    for folder, info in data.items():
        clips = info.get("clips", [])
        path = album_path(vault, folder, info)
        path.mkdir(parents=True, exist_ok=True)
        clips_file(folder).write_text(json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n=== {folder}  ({info.get('name')}) — {len(clips)} tracks -> {path.relative_to(vault)} ===")
        report = []
        for ti, c in enumerate(clips):
            base = f"{c.get('n',0):02d}-{safe(c.get('title'))}"
            row = {"n": c.get("n"), "title": c.get("title"), "id": c.get("id"), "created": c.get("created"), "files": {}}
            for ext in ("mp3", "mp4"):
                url = (c.get(ext) or "").strip()
                if not url:
                    continue
                dest = path / f"{base}.{ext}"
                if dest.exists():
                    row["files"][ext] = {"ok": True, "bytes": dest.stat().st_size, "note": "exists"}
                    continue
                res = download(url, dest)
                row["files"][ext] = res
                print(f"  [{'OK ' if res['ok'] else 'FAIL'}] {base}.{ext}  {res['bytes']:,}  {res['note']}")
                time.sleep(delay * 0.4)
            report.append(row)
            if ti < len(clips) - 1:
                time.sleep(delay)
        (path / "download-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(delay)  # gap between albums


def do_ids() -> None:
    data = load()
    for folder, info in data.items():
        ids = [c["id"] for c in info.get("clips", [])]
        print(f"// {folder} ({info.get('name')}) {len(ids)} ids")
        print(json.dumps(ids))


def do_wav() -> None:
    vault = vault_root()
    data = load()
    for folder, info in data.items():
        path = album_path(vault, folder, info)
        clips = info.get("clips", [])
        pending = {}
        for c in clips:
            base = f"{c.get('n',0):02d}-{safe(c.get('title'))}"
            dest = path / f"{base}.wav"
            if dest.exists():
                continue
            pending[c["id"]] = {"url": f"https://cdn1.suno.ai/{c['id']}.wav", "dest": dest, "base": base}
        if not pending:
            print(f"=== {folder}: all WAV present ===")
            continue
        print(f"\n=== {folder}: fetching {len(pending)} WAV ===")
        deadline = time.time() + 600
        done = {}
        while pending and time.time() < deadline:
            for sid in list(pending):
                if wav_ready(pending[sid]["url"]) > 0:
                    p = pending.pop(sid)
                    res = download(p["url"], p["dest"])
                    print(f"  [{'OK ' if res['ok'] else 'FAIL'}] {p['base']}.wav  {res['bytes']:,}  {res['note']}")
                    done[sid] = res
                    time.sleep(2)
            if pending:
                time.sleep(8)
        for sid in pending:
            print(f"  [PENDING] {pending[sid]['base']}.wav not ready yet")
        rp = path / "download-report.json"
        if rp.exists():
            rep = json.loads(rp.read_text(encoding="utf-8"))
            for row in rep:
                if row["id"] in done:
                    row.setdefault("files", {})["wav"] = done[row["id"]]
            rp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")


def do_manifest() -> None:
    vault = vault_root()
    data = load()
    for folder, info in data.items():
        path = album_path(vault, folder, info)
        clips = info.get("clips", [])
        name = info.get("name") or folder
        pid = info.get("pid")
        report = {}
        rp = path / "download-report.json"
        if rp.exists():
            for r in json.loads(rp.read_text(encoding="utf-8")):
                report[r["id"]] = r
        shots = sorted(p.name for p in path.glob("*.png"))
        pl = f"https://suno.com/playlist/{pid}"

        def b(c):
            return f"{c.get('n',0):02d}-{safe(c.get('title'))}"

        L = ["---", f"title: Downloaded audio - {name}", "type: source-manifest", f"album: {name}",
             f"source: {pl}", f"downloaded: {TODAY}", "tags:", "  - music", "  - audio", "  - suno", "---", "",
             f"# Downloaded audio - {name}", "", f"Source playlist: {pl}", f"Owner: {OWNER_TAG}", "",
             "## Files", "", "| # | Track | Audio | Video | WAV | Source ID |", "|---|---|---|---|---|---|"]
        for c in clips:
            rr = report.get(c["id"], {}).get("files", {})
            L.append(f"| {c.get('n')} | {c.get('title')} | {'[[%s.mp3]]'%b(c) if 'mp3' in rr else '—'} | "
                     f"{'[[%s.mp4]]'%b(c) if 'mp4' in rr else '— (none)'} | {'[[%s.wav]]'%b(c) if 'wav' in rr else '—'} | {c['id']} |")
        L += ["", "## Notes", "", "- MP3/MP4 from Suno CDN; WAV via convert_wav + CDN. Sizes in `download-report.json`.",
              "", "Evidence: [[copyright-evidence]]", ""]
        (path / "downloaded-audio.md").write_text("\n".join(L), encoding="utf-8")

        E = ["---", f"title: Copyright evidence - {name}", "type: evidence", f"album: {name}",
             f"source: {pl}", f"created: {TODAY}", "tags:", "  - music", "  - copyright", "  - evidence", "  - suno", "---", "",
             f"# Copyright evidence - {name}", "", "Source/publication archive evidence (not legal advice).", "",
             f"- Public playlist: {pl}", f"- Creator: {OWNER_TAG}", "- Download manifest: [[downloaded-audio]]", "",
             "## Generation timestamps (Suno API)", "", "| # | Track | Source URL | Generated UTC |", "|---|---|---|---|"]
        for c in clips:
            E.append(f"| {c.get('n')} | {c.get('title')} | https://suno.com/song/{c['id']} | {c.get('created')} |")
        if shots:
            E += ["", "## Source screenshots", ""] + [f"- [[{s}]]" for s in shots]
        E += ["", "## Paid service proof", "", f"Pending — a Suno billing screenshot showing {OWNER_TAG} on the Pro plan.",
              "", "## Legal", "", "- Suno ToS: https://suno.com/terms (paid-tier outputs assigned to subscriber).", ""]
        (path / "copyright-evidence.md").write_text("\n".join(E), encoding="utf-8")
        print(f"  manifest: {folder}  ({len(shots)} screenshots linked)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "download"
    delay = 5.0
    if "--delay" in sys.argv:
        delay = float(sys.argv[sys.argv.index("--delay") + 1])
    if mode == "download":
        do_download(delay)
    elif mode == "ids":
        do_ids()
    elif mode == "wav":
        do_wav()
    elif mode == "manifest":
        do_manifest()
    else:
        print(f"unknown mode: {mode}")
