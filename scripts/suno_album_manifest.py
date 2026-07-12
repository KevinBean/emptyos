"""Write downloaded-audio.md + copyright-evidence.md for a downloaded Suno album.

Reads the album folder's clips JSON (--clips) + download-report.json (written by
suno_download_album.py) and emits the two One-Walk-format manifests, using the
Suno API's authoritative generation timestamps. Lists any *-screenshot.png found
in the folder as evidence. Reusable across Category-A albums.

    python scripts/suno_album_manifest.py --folder "70_Media/Music/不识" \
        --clips .cache/suno/buishi-clips.json --playlist 7ff9b51f-... --owner <handle> --name 不识
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from suno_common import safe, vault_root

TODAY = "2026-06-20"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--clips", required=True)
    ap.add_argument("--playlist", required=True, help="Suno playlist UUID")
    ap.add_argument("--owner", default="", help="Suno account handle (no leading @)")
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    owner = (args.owner or "").lstrip("@").strip()
    owner_tag = f"@{owner}" if owner else "the account owner"

    vault = vault_root()
    folder = vault / args.folder
    clips = json.loads(Path(args.clips).read_text(encoding="utf-8"))
    report = {}
    rp = folder / "download-report.json"
    if rp.exists():
        for r in json.loads(rp.read_text(encoding="utf-8")):
            report[r["id"]] = r
    shots = sorted(set(p.name for p in folder.glob("*.png")))
    pl_url = f"https://suno.com/playlist/{args.playlist}"

    def base(c):
        return f"{c.get('n',0):02d}-{safe(c.get('title'))}"

    # ---- downloaded-audio.md ----
    L = ["---", f"title: Downloaded audio - {args.name}", "type: source-manifest",
         f"album: {args.name}", f"source: {pl_url}", f"downloaded: {TODAY}",
         "tags:", "  - music", "  - audio", "  - suno", "---", "",
         f"# Downloaded audio - {args.name}", "",
         f"Source playlist: {pl_url}", f"Owner: {owner_tag} (verified logged-in in the Browser plugin)", "",
         "## Files", "", "| # | Track | Audio | Video | Source ID |", "|---|---|---|---|---|"]
    for c in clips:
        b = base(c); rid = c.get("id"); rr = report.get(rid, {}).get("files", {})
        mp3 = f"[[{b}.mp3]]" if "mp3" in rr else "—"
        mp4 = f"[[{b}.mp4]]" if "mp4" in rr else "— (no video on Suno)"
        wav = f" / [[{b}.wav]]" if "wav" in rr else ""
        L.append(f"| {c.get('n')} | {c.get('title')} | {mp3}{wav} | {mp4} | {rid} |")
    L += ["", "## Download notes", "",
          "- MP3 + MP4 pulled from Suno's CDN via the authenticated playlist API (owner account).",
          "- WAV: see copyright-evidence / pending (Suno WAV is generated on demand).",
          "- File sizes verified against server Content-Length (see `download-report.json`).",
          "", "Evidence note: [[copyright-evidence]]", ""]
    (folder / "downloaded-audio.md").write_text("\n".join(L), encoding="utf-8")

    # ---- copyright-evidence.md ----
    E = ["---", f"title: Copyright evidence - {args.name}", "type: evidence",
         f"album: {args.name}", f"source: {pl_url}", f"created: {TODAY}",
         "tags:", "  - music", "  - copyright", "  - evidence", "  - suno", "---", "",
         f"# Copyright evidence - {args.name}", "",
         "Source/publication archive evidence. Evidence organization, not legal advice.", "",
         "## Source identity", "",
         f"- Public playlist: {pl_url}", f"- Creator: {owner_tag}",
         f"- Download manifest: [[downloaded-audio]]", "",
         "## Generation timestamps", "",
         "Timestamps from the Suno API clip payload (`created_at`).", "",
         "| # | Track | Source URL | Generated UTC |", "|---|---|---|---|"]
    for c in clips:
        E.append(f"| {c.get('n')} | {c.get('title')} | https://suno.com/song/{c.get('id')} | {c.get('created')} |")
    E += ["", "## Local archive files", "",
          "- MP3/MP4 per track in this folder; see [[downloaded-audio]] + `download-report.json`.", ""]
    if shots:
        E += ["## Source screenshots", "", "| Screenshot |", "|---|"]
        E += [f"| [[{s}]] |" for s in shots]
        E += [""]
    E += ["## Paid service proof", "",
          "Pending capture — a Suno billing/subscription screenshot showing "+owner_tag+"'s account on a paid (Pro) plan around the generation dates is the strongest add.",
          "", "## Legal/reference links", "",
          "- Suno Terms of Service: https://suno.com/terms — paid-tier outputs assigned to the subscriber.",
          "- U.S. Copyright Office AI page: https://www.copyright.gov/ai/", ""]
    (folder / "copyright-evidence.md").write_text("\n".join(E), encoding="utf-8")

    print(f"Wrote: {args.folder}/downloaded-audio.md")
    print(f"Wrote: {args.folder}/copyright-evidence.md")
    print(f"Screenshots linked: {len(shots)}")


if __name__ == "__main__":
    main()
