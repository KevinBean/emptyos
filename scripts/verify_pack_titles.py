#!/usr/bin/env python3
"""Check that a candidate picture pack will actually yield photographs.

A pack row names a Wikipedia article; the photo is that article's *free-licensed
lead image*. Whether one exists is not guessable — 27 of the 130 shipped Animals
entries needed a title different from the name, because a bare noun lands on a
genus page (``Deer``), a disambiguation page (``Crane``, ``Seal``, ``Mole``) or
a page whose lead image is a range map. Authoring a pack without checking means
discovering it one download at a time.

This is Phase A of the fetcher, run against candidates *before* anything is
written: one batched request per 50 titles, no downloads. 40 candidates plus
their alternates cost about two requests and a second, which is what makes a
pre-write gate affordable at all.

    python scripts/verify_pack_titles.py candidates.json
    python scripts/verify_pack_titles.py candidates.json --json out.json

``candidates.json`` is a list of rows, each needing at least ``slug`` and
``wiki``; an optional ``wiki_alt`` (string or list) supplies fallbacks tried in
order. Anything else on the row is passed through untouched.

Three things are checked beyond "does it resolve":

* **Shared lead file** — two rows resolving to the SAME image. That is the most
  severe defect a pack can carry, because it makes a photo-to-name question
  genuinely unanswerable, and it is detectable here rather than after the
  download.
* **Collision with an object already in the store** — a new row must not claim
  a photo a shipped object is already using.
* **Likely a drawing** — the lead image's filename says it is a botanical plate
  or an engraving rather than a photograph. Advisory; see `looks_like_a_drawing`.

Exit code is the number of rows that resolved to nothing, so this can gate.

It does NOT prove the photo shows one clear specimen. Nothing mechanical does;
that is what the by-eye contact-sheet pass in `/eos-picture-pack-review` is for.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps" / "extension" / "english-learning" / "dictionary"
PACKS = APP / "packs"
IMAGE_INDEX = ROOT / "data" / "apps" / "dictionary" / "images" / "_index.json"

WIKI_HOST = "https://en.wikipedia.org"
COMMONS_HOST = "https://commons.wikimedia.org"
BATCH = 50  # the action API's documented ceiling for a titles= list
UA = "EmptyOS-picture-packs/1.0 (pack authoring; contact via repo)"
THROTTLE_S = 0.35


def _api_get(host: str, params: dict) -> dict:
    url = f"{host}/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def lookup_lead_files(titles: list[str]) -> dict[str, str]:
    """Article title -> free-licensed lead image filename, for those that have one.

    Mirrors picture_images._lookup_lead_files: same params, same batching, same
    normalized/redirects resolution. MediaWiki rewrites the title it echoes back
    (underscores to spaces, redirects followed), so the reply must be keyed
    through those maps rather than by the string we sent.
    """
    out: dict[str, str] = {}
    for i in range(0, len(titles), BATCH):
        chunk = titles[i:i + BATCH]
        if i:
            time.sleep(THROTTLE_S)
        try:
            d = _api_get(WIKI_HOST, {
                "action": "query", "format": "json", "formatversion": "2",
                "redirects": "1", "prop": "pageimages", "pilicense": "free",
                "piprop": "name", "titles": "|".join(chunk),
            })
        except Exception as e:
            print(f"  ! batch {i // BATCH + 1} failed: {e}", file=sys.stderr)
            continue
        q = d.get("query", {})
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
        pages = {p.get("title"): p for p in q.get("pages", [])}
        for t in chunk:
            resolved = redir.get(norm.get(t, t), norm.get(t, t))
            f = (pages.get(resolved) or {}).get("pageimage")
            if f:
                out[t] = f
    return out


# The heuristic lives in picture_catalog (the app's pure leaf) so the script and
# the in-app composer cannot drift apart — CLAUDE.md rule 9, extracted on the
# second consumer. Loaded by path because scripts/ is not on the app import path.
def _catalog():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_vpt_catalog", APP / "picture_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


looks_like_a_drawing = _catalog().looks_like_a_drawing


def _alts(row: dict) -> list[str]:
    a = row.get("wiki_alt") or []
    if isinstance(a, str):
        a = [a]
    return [str(x).strip() for x in a if str(x).strip()]


def _shipped_photo_files() -> dict[str, str]:
    """slug -> lead filename, for objects whose photo is already downloaded."""
    if not IMAGE_INDEX.exists():
        return {}
    try:
        idx = json.loads(IMAGE_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for slug, rec in idx.items():
        url = (rec or {}).get("source_url") or ""
        if url:
            out[slug] = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    return out


def lookup_files_exist(files: list[str]) -> dict[str, dict]:
    """Commons filename -> {licence, thumb} for those that resolve, free-licensed.

    Phase B, used only for `image_hint` rows. A pinned file is not covered by
    Phase A's `pilicense=free` filter, so it needs checking on its own terms.
    """
    out: dict[str, dict] = {}
    for i in range(0, len(files), BATCH):
        chunk = files[i:i + BATCH]
        if i:
            time.sleep(THROTTLE_S)
        try:
            d = _api_get(COMMONS_HOST, {
                "action": "query", "format": "json", "formatversion": "2",
                "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": "500",
                "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl",
                "titles": "|".join(f"File:{f}" for f in chunk),
            })
        except Exception as e:
            print(f"  ! commons batch {i // BATCH + 1} failed: {e}", file=sys.stderr)
            continue
        for p in d.get("query", {}).get("pages", []):
            ii = (p.get("imageinfo") or [{}])[0]
            em = ii.get("extmetadata", {}) or {}
            name = p.get("title", "").split(":", 1)[-1].replace("_", " ")
            if ii.get("thumburl"):
                out[name] = {"license": em.get("LicenseShortName", {}).get("value", "")}
    return out


def verify(rows: list[dict]) -> dict:
    resolved: dict[str, dict] = {}
    missing: list[dict] = []

    # An `image_hint` pins the photo, and the fetcher honours it BEFORE the
    # article lead — so checking the lead for these rows reports on a value that
    # will never be used, and alarms on exactly the rows a human already fixed.
    pinned = [r for r in rows if str(r.get("image_hint") or "").strip()]
    rest = [r for r in rows if not str(r.get("image_hint") or "").strip()]
    pins = lookup_files_exist([str(r["image_hint"]).strip() for r in pinned]) if pinned else {}
    for row in pinned:
        slug = str(row.get("slug") or "").strip()
        hint = str(row["image_hint"]).strip()
        info = pins.get(hint.replace("_", " "))
        if info:
            resolved[slug] = {"row": row, "wiki": str(row.get("wiki") or ""),
                              "file": hint, "via": "pinned",
                              "license": info.get("license", "")}
        else:
            missing.append(row)

    # Round 1: every remaining primary title in one batched pass.
    primaries = [str(r.get("wiki") or "").strip() for r in rest]
    hits = lookup_lead_files([t for t in primaries if t])

    for row, title in zip(rest, primaries):
        slug = str(row.get("slug") or "").strip()
        if title and title in hits:
            resolved[slug] = {"row": row, "wiki": title, "file": hits[title], "via": "primary"}
        else:
            missing.append(row)

    # Round 2: every alternate of every miss, again in ONE pass — a 6-row repair
    # is one request, not six.
    alt_titles, alt_owner = [], {}
    for row in [r for r in missing if not str(r.get("image_hint") or "").strip()]:
        for t in _alts(row):
            alt_titles.append(t)
            alt_owner.setdefault(t, str(row.get("slug") or "").strip())
    alt_hits = lookup_lead_files(alt_titles) if alt_titles else {}

    still_missing = []
    for row in missing:
        if str(row.get("image_hint") or "").strip():
            still_missing.append({
                "slug": str(row.get("slug") or "").strip(),
                "tried": [f"image_hint:{row['image_hint']}"],
            })
            continue
        slug = str(row.get("slug") or "").strip()
        won = next((t for t in _alts(row) if t in alt_hits), "")
        if won:
            resolved[slug] = {"row": row, "wiki": won, "file": alt_hits[won],
                              "via": "alt", "tried": str(row.get("wiki") or "")}
        else:
            still_missing.append({
                "slug": slug,
                "tried": [str(row.get("wiki") or "")] + _alts(row),
            })

    # Two rows on one photo makes a photo-to-name question unanswerable.
    by_file: dict[str, list[str]] = {}
    for slug, info in resolved.items():
        by_file.setdefault(info["file"], []).append(slug)
    dupes = {f: s for f, s in by_file.items() if len(s) > 1}

    shipped = _shipped_photo_files()
    clashes = {
        slug: [s for s, f in shipped.items() if f == info["file"] and s != slug]
        for slug, info in resolved.items()
    }
    clashes = {k: v for k, v in clashes.items() if v}

    drawings = {s: i["file"] for s, i in resolved.items()
                if looks_like_a_drawing(i["file"])}

    return {"resolved": resolved, "missing": still_missing,
            "duplicates": dupes, "shipped_clashes": clashes,
            "drawings": drawings}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("candidates", help="JSON list of rows with slug + wiki (+ wiki_alt)")
    ap.add_argument("--json", dest="out", default="", help="write the full report here")
    args = ap.parse_args()

    rows = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("rows") or []
    print(f"checking {len(rows)} candidates against Wikimedia ...\n")

    rep = verify(rows)

    for slug, info in sorted(rep["resolved"].items()):
        mark = {"primary": "OK ", "alt": "ALT", "pinned": "PIN"}[info["via"]]
        via = f"   ({info['tried']} -> {info['wiki']})" if info["via"] == "alt" else ""
        print(f"  {mark} {slug:<18} {info['wiki']:<28} {info['file'][:44]}{via}")
    for m in rep["missing"]:
        print(f"  --  {m['slug']:<18} no free lead image; tried {m['tried']}")

    print(f"\n  resolved {len(rep['resolved'])} · unresolved {len(rep['missing'])}")
    if rep["duplicates"]:
        print("\n  SHARED PHOTO — these rows would share one image, making the")
        print("  photo-to-name question unanswerable. Fix before writing:")
        for f, slugs in rep["duplicates"].items():
            print(f"    {slugs} -> {f}")
    if rep["drawings"]:
        print("\n  LIKELY A DRAWING, not a photograph — the filename says so.")
        print("  Advisory: fix with a culinary article title or an image_hint.")
        for slug, f in sorted(rep["drawings"].items()):
            print(f"    {slug:<18} {f}")
    if rep["shipped_clashes"]:
        print("\n  CLASH WITH A SHIPPED OBJECT (same photo already in use):")
        for slug, others in rep["shipped_clashes"].items():
            print(f"    {slug} vs {others}")

    if args.out:
        Path(args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\n  report -> {args.out}")

    return len(rep["missing"])


if __name__ == "__main__":
    sys.exit(main())
