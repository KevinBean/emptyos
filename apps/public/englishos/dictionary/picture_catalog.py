"""Picture Dictionary — object store, pack membership, and lookup.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md rule 4).
Owns: reading ``packs/`` into the in-memory indexes every other module reads
(``items`` by slug, ``by_category``, ``by_pack``, ``order``), and the name -> slug
resolution the voice/assistant verbs need.

**An object is stored once and referred to from many packs.** ``chicken`` is one
line in ``objects.jsonl``, one row in picture-progress.json, one cached photo and
one ``pic:chicken`` card — and it appears in both Animals (as ``farm``) and Food
(as ``meat``). That is why ``category`` lives on the *membership edge* and not on
the object: it is empirically pack-scoped, it is the quiz's distractor pool (which
must match the pack the learner opened), and its labels are a pack's property.

Before this split, membership lived inside the object row, where there was only
room for one — so the second pack to claim a slug lost it silently. Both loader
paths now record membership for a slug that is already defined, and only the first
body defines the object.

``by_category`` keys are QUALIFIED — ``animals:sea``, not ``sea`` — because generic
group ids (``tools``, ``containers``, ``on-the-table``) collide across themed packs
by construction, and an unqualified key would merge two packs' pools with exactly
the silence this split removes. ``resolve_category`` accepts the bare form when it
is unambiguous, so URLs, CLI arguments and saved quiz sessions keep working.

Deliberately PURE — no ``self``, no kernel, no network. ``load_packs`` takes a
directory and returns indexes, so ``tests/test_unit_dictionary_pictures.py``
exercises the real shipped pack with no daemon running.

Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# ─── Bind to DictionaryApp class as ─────────────────────────────────
#   (nothing — this module is pure functions called as picture_catalog.fn(...),
#    never bound onto the class. The spine holds the loaded indexes.)
# ────────────────────────────────────────────────────────────────────

# `category` is deliberately absent: it belongs to a membership, not to a thing.
REQUIRED_FIELDS = ("slug", "name", "chinese", "wiki")

# A legacy single-file pack carries its membership inline, so there it IS required.
LEGACY_REQUIRED_FIELDS = REQUIRED_FIELDS + ("category",)

DEFAULT_OBJECTS_FILE = "objects.jsonl"

# The pack ships one emoji per entry; this is only the last-resort tile.
FALLBACK_EMOJI = "🐾"

# COMPAT SHIM, legacy path only. A .pack.json carries its own group labels, which
# is where they belong — a label is a pack's property, not a global constant. A
# legacy single-file .jsonl has nowhere to put one, so these six survive to keep
# the shipped Animals pack reading "Sea life" rather than "Sea" while it is still
# in the old format. Delete this when no legacy pack remains; do NOT add rows for
# new packs — put the label in the pack file.
LEGACY_CATEGORY_LABELS = {
    "mammals": "Mammals",
    "birds": "Birds",
    "sea": "Sea life",
    "reptiles": "Reptiles & amphibians",
    "insects": "Insects & bugs",
    "farm": "Farm & pets",
}


# A lead image whose FILENAME says it is a drawing rather than a photograph.
#
# Plant and food packs hit this constantly and animal packs barely do, for a
# findable reason: Wikipedia's article on a plant is about the *species*, and its
# lead is often an 18th- or 19th-century botanical plate (Köhler's
# Medizinal-Pflanzen supplies a great many of them). An animal's article leads
# with a photo of the animal.
#
# Measured before being trusted, per .claude/rules/audits.md: 0 hits across the
# 130 hand-reviewed photos of the shipped Animals pack (0% false positive), 5 of
# 42 Food candidates — every one of them a real defect. ADVISORY, never a
# rejection: the fix is a better article title or an image_hint, and only a human
# can pick which.
_ILLUSTRATION = re.compile(
    r"koeh|köhler|illustration|medizinal|pflanzen|\b1[6789]\d\d\b|botanical"
    r"|lithograph|engraving|drawing|plate_?\d|woodwill|flora_|_flowering|bloeiend"
    # An SVG lead is a vector diagram. The fetcher rasterises it happily, so the
    # cached file looks like any other photo and the tell survives only on the
    # SOURCE name — added after `Forklift_Truck-no_lines.svg` sailed through with
    # no drawing keyword in it. 0 hits across the 173 hand-reviewed shipped photos.
    r"|\.svg\b",
    re.I,
)


def looks_like_a_drawing(filename: str) -> bool:
    """Cheap pre-download tell that a lead image is drawn, not photographed.

    Catches a class the fetcher reports as a clean success. It cannot catch the
    rest — a photo of the plant in a field rather than the food on a bench reads
    as an ordinary filename — which is why the by-eye contact-sheet pass still
    happens (`/eos-picture-pack-review`).
    """
    return bool(_ILLUSTRATION.search(filename or ""))


def slugify(text: str) -> str:
    """A url-safe slug. Used for user-added entries, never to re-derive a
    shipped slug — progress is keyed by slug, so re-deriving one would orphan
    the learner's history the moment a display name was corrected."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def normalise_object(row: dict) -> dict | None:
    """One objects.jsonl line -> an object, or None when the line is unusable.

    Carries identity only. A row's ``category`` is ignored here even when present
    (a legacy pack line has one); the caller reads it for the membership edge.

    Fails soft on purpose: a single malformed line in a hand-edited store should
    cost that one object, not the whole app.
    """
    if not isinstance(row, dict):
        return None
    if any(not str(row.get(f) or "").strip() for f in REQUIRED_FIELDS):
        return None
    slug = str(row["slug"]).strip()
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        return None
    return {
        "slug": slug,
        "name": str(row["name"]).strip(),
        "chinese": str(row["chinese"]).strip(),
        "pinyin": str(row.get("pinyin") or "").strip(),
        "emoji": str(row.get("emoji") or FALLBACK_EMOJI).strip() or FALLBACK_EMOJI,
        "wiki": str(row["wiki"]).strip(),
        "hint": str(row.get("hint") or "").strip(),
        # Optional escape hatch for the handful whose article lead image is a
        # range map rather than a photo: a Commons filename, bare (no "File:"
        # prefix) — e.g. "Alces alces 1.jpg". The value "none" means the lead
        # image is wrong and the card keeps its emoji (picture_images.NO_PHOTO).
        "image_hint": str(row.get("image_hint") or "").strip(),
    }


def _read_json(path: Path) -> dict:
    """Fail-soft JSON read; a broken file yields {} so the app still boots."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _jsonl(path: Path):
    """Yield each parseable object from a .jsonl file. Never raises."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            yield row


def _group_label(gid: str, given: str) -> str:
    return (given or "").strip() or gid.replace("-", " ").capitalize()


def _membership_groups(packs_dir: Path, pack: dict, define, warnings: list) -> list:
    """-> [(group_id, label, [slug])] for one pack, in file order.

    Two on-disk shapes, discriminated by which key the meta.json row carries:

    * ``members`` -> a .pack.json membership file over the shared object store.
    * ``file``    -> a legacy single-file .jsonl carrying whole rows. Kept because
      the no-meta.json fallback in load_packs is an existing "drop a file in and
      it works" affordance, and because it makes the migration revertable.

    ``define`` is the caller's object-defining closure; it returns False for a slug
    that is already known, and we record the membership anyway. That is the fix.
    """
    pid = str(pack.get("id") or "")

    members_file = str(pack.get("members") or "").strip()
    if members_file:
        doc = _read_json(packs_dir / members_file)
        if not doc:
            warnings.append(
                f"pack '{pid}': membership file '{members_file}' is missing or unreadable"
            )
            return []
        out = []
        for g in doc.get("groups") or []:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("id") or "").strip()
            if not gid:
                continue
            members = [str(s).strip() for s in (g.get("members") or []) if str(s).strip()]
            out.append((gid, _group_label(gid, g.get("label")), members))
        return out

    legacy_file = str(pack.get("file") or "").strip()
    if not legacy_file:
        warnings.append(f"pack '{pid}': declares neither 'members' nor 'file'")
        return []

    groups: dict[str, list] = {}
    group_order: list[str] = []
    for row in _jsonl(packs_dir / legacy_file):
        if any(not str(row.get(f) or "").strip() for f in LEGACY_REQUIRED_FIELDS):
            continue
        obj = normalise_object(row)
        if not obj:
            continue
        define(obj)  # first body wins; a repeat still joins this pack below
        gid = str(row["category"]).strip()
        if gid not in groups:
            groups[gid] = []
            group_order.append(gid)
        groups[gid].append(obj["slug"])
    return [
        (gid, _group_label(gid, LEGACY_CATEGORY_LABELS.get(gid, "")), groups[gid])
        for gid in group_order
    ]


def load_packs(packs_dir: Path) -> dict:
    """Read the object store and every pack's membership into the shared indexes.

    Returns ``{"items", "by_category", "by_pack", "order", "packs", "categories",
    "warnings"}``. Never raises: a missing directory yields empty indexes so the
    app still boots and the UI can say so.

    ``items[slug]`` carries ``packs`` (a list) and ``categories`` (pack -> group),
    plus the scalars ``pack`` / ``category`` / ``category_key`` derived from the
    PRIMARY pack — the first in meta.json order that contains it. The scalars are
    a display convenience, not a truth; anything choosing a quiz pool must use the
    pack it was opened in (see picture_quiz.build_rounds).
    """
    packs_dir = Path(packs_dir)
    meta = _read_json(packs_dir / "meta.json")
    objects_file = str(meta.get("objects") or DEFAULT_OBJECTS_FILE)

    packs = meta.get("packs") or []
    if not packs:
        # Drop-a-file-in fallback: one pack per legacy .jsonl, object store aside.
        packs = [
            {"id": p.stem, "title": p.stem.title(), "file": p.name}
            for p in sorted(packs_dir.glob("*.jsonl"))
            if p.name != objects_file
        ]

    items: dict[str, dict] = {}
    order: list[str] = []
    warnings: list[str] = []

    def define(obj: dict | None) -> bool:
        """First body wins. Returns False when the slug is already known — the
        caller still records the membership, which is the whole bug fix: a slug
        seen twice joins a second pack instead of vanishing from it."""
        if not obj or obj["slug"] in items:
            return False
        items[obj["slug"]] = dict(obj, packs=[], categories={})
        order.append(obj["slug"])
        return True

    for row in _jsonl(packs_dir / objects_file):
        define(normalise_object(row))

    by_category: dict[str, list[str]] = {}
    by_pack: dict[str, list[str]] = {}
    categories: list[dict] = []
    pack_meta: list[dict] = []

    for pack in packs:
        pid = str(pack.get("id") or "").strip()
        if not pid:
            continue
        groups = _membership_groups(packs_dir, pack, define, warnings)
        by_pack.setdefault(pid, [])
        gmeta = []
        for gid, label, members in groups:
            key = f"{pid}:{gid}"
            bucket = by_category.setdefault(key, [])
            for slug in members:
                it = items.get(slug)
                if it is None:
                    warnings.append(f"{key} references unknown object '{slug}'")
                    continue
                if pid in it["categories"]:
                    warnings.append(
                        f"'{slug}' is in pack '{pid}' twice "
                        f"({it['categories'][pid]}, {gid}) — kept the first"
                    )
                    continue
                it["categories"][pid] = gid
                it["packs"].append(pid)
                bucket.append(slug)
                by_pack[pid].append(slug)
            gmeta.append({"id": gid, "label": label, "count": len(bucket)})
            categories.append({
                "id": key, "group": gid, "label": label,
                "pack": pid, "pack_title": str(pack.get("title") or pid),
                "count": len(bucket),
            })
        pack_meta.append({
            "id": pid,
            "title": str(pack.get("title") or pid),
            "emoji": str(pack.get("emoji") or ""),
            "groups": gmeta,
            "count": len(by_pack[pid]),
        })

    for it in items.values():
        it["pack"] = it["packs"][0] if it["packs"] else ""
        it["category"] = it["categories"].get(it["pack"], "")
        it["category_key"] = f"{it['pack']}:{it['category']}" if it["pack"] else ""
        if not it["packs"]:
            warnings.append(f"'{it['slug']}' is in no pack — unreachable in the UI")

    return {
        "items": items,
        "by_category": by_category,
        "by_pack": by_pack,
        "order": order,
        "packs": pack_meta,
        "categories": categories,
        "warnings": warnings,
    }


def resolve_category(by_category: dict, key: str) -> str:
    """A caller-supplied category -> a qualified ``<pack>:<group>`` key.

    Accepts the qualified form directly, and accepts a bare group id when exactly
    one pack defines it — which is every URL, CLI argument and saved quiz session
    written before packs got their own namespace. Returns "" for unknown *or
    ambiguous*, so the caller answers in band rather than guessing which pack the
    learner meant.
    """
    key = (key or "").strip()
    if not key:
        return ""
    if key in by_category:
        return key
    hits = [k for k in by_category if k.split(":", 1)[-1] == key]
    return hits[0] if len(hits) == 1 else ""


def resolve_name(items: dict, query: str) -> dict | None:
    """Find one item from a spoken or typed name.

    Exact slug -> exact name -> chinese -> startswith -> substring. Ordered so a
    voice verb saying "seal" cannot land on "sea lion" while a real prefix match
    exists.
    """
    q = (query or "").strip().lower()
    if not q:
        return None
    if q in items:
        return items[q]
    slug_q = slugify(q)
    if slug_q in items:
        return items[slug_q]
    for it in items.values():
        if it["name"].lower() == q or it["chinese"] == query.strip():
            return it
    for it in items.values():
        if it["name"].lower().startswith(q):
            return it
    for it in items.values():
        if q in it["name"].lower():
            return it
    return None


# A saved vocabulary note's filename carries a locale suffix on some entries
# (`Tiger (en-US).md`), and the word itself may be capitalised. Strip both
# before matching.
_LOCALE_SUFFIX = re.compile(r"\s*\([a-z]{2}-[A-Z]{2}\)\s*$")


def exact_match(items: dict, word: str) -> dict | None:
    """Find the pack entry for a saved vocabulary word — EXACT matches only.

    Deliberately not ``resolve_name``. That one falls back to startswith and
    substring, which is right for a voice verb ("show me a heron") and wrong
    here: a vocabulary note for `crane` (the machine), `seal` (to close), `mole`
    (the unit) or `bat` (the cricket kind) would silently acquire an animal
    photograph. Fuzzy matching cannot tell a homonym from a hit, so this does
    not try — it matches the slug or the full name, and nothing else.

    Homonyms survive even exact matching (`sloth` the animal vs the sin), which
    is why the caller labels the photo with the pack it came from rather than
    presenting it as the word's own illustration.
    """
    q = _LOCALE_SUFFIX.sub("", (word or "").strip()).strip().lower()
    if not q:
        return None
    if q in items:
        return items[q]
    slug_q = slugify(q)
    if slug_q in items:
        return items[slug_q]
    for it in items.values():
        if it["name"].lower() == q:
            return it
    return None
