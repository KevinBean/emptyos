"""Picture packs — compose a pack from a description.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md rule 4).
Owns: turning "things you see at an airport" into a verified, reviewable pack
proposal, and — only after the user confirms it — writing that pack to disk.

**This is the authoring path, not the learner path.** PICTURE-PACKS.md refuses
`think` for learners and gives its reason: quiz distractors are *better*
deterministic, and a fun-fact generator would add a hallucination surface to an
app whose content is otherwise verified. Both still hold. Here the model's output
is a *search query*: nothing it says reaches a learner without passing a live
Wikimedia lookup AND a human confirm, and the model pill mounts on the composer
panel only, so a learner who never authors a pack never sees one.

The load-bearing stage is verification, not generation. A model gets the article
title wrong often — the shipped Animals pack needed a title different from the
name for 27 of 130 entries — so every candidate is checked against Phase A of the
real fetcher BEFORE anything is written. 40 candidates plus their alternates cost
two batched requests and about a second, which is what makes a pre-write gate
affordable at all.

Propose -> preview -> confirm, impact-shaped (`.claude/rules/proposed-action.md`).
A proposal writes nothing; apply writes once, after a staleness check.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._lookup_lead_files`` (picture_images),
``self.start_prefetch`` (picture_images), ``picture_catalog`` (pure leaf).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import parse_llm_json, web_route
from emptyos.sdk.utils import safe_path_segment

from . import picture_catalog
from .prompts import PROMPTS

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ──────────────────────────────────
#   composer_enabled        = _compose.composer_enabled
#   _proposal_path          = _compose._proposal_path
#   _read_proposal          = _compose._read_proposal
#   _write_proposal         = _compose._write_proposal
#   _compose_pack           = _compose._compose_pack
#   _verify_candidates      = _compose._verify_candidates
#   api_pack_propose        = _compose.api_pack_propose
#   api_pack_proposal       = _compose.api_pack_proposal
#   api_pack_proposals      = _compose.api_pack_proposals
#   api_pack_proposal_edit  = _compose.api_pack_proposal_edit
#   api_pack_proposal_apply = _compose.api_pack_proposal_apply
#   api_pack_proposal_reject = _compose.api_pack_proposal_reject
#   _retitle_round          = _compose._retitle_round
#   _prune_proposals        = _compose._prune_proposals
#   (_mark_duplicate_photos, known_groups, existing_counts, merge_group,
#    merge_members and read_pack_groups are pure — called as _compose.fn(...),
#    not bound)
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

MAX_GROUPS = 6
MIN_GROUP = 4  # a group below this cannot fill a four-option quiz round
PROPOSAL_TTL_S = 7 * 24 * 3600


# ─── Pure — no self, no kernel, no network ───────────────────────────


def _strip_fence(text: str) -> str:
    """Drop a ```json fence if the model added one despite being told not to."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_compose_reply(text: str) -> dict | None:
    """The model's reply -> ``{"pack": {...}, "items": [...]}``, or None.

    Tolerant of a fence and of a bare items array (some models drop the wrapper),
    because a reply that is 95% right should cost a repair rather than the run.
    """
    try:
        # parse_llm_json RAISES on unusable input. A model that answers in prose
        # ("sorry, I cannot...") is a normal outcome here, not an exception —
        # the caller turns it into a readable status, not a stack trace.
        data = parse_llm_json(_strip_fence(text))
    except Exception:
        return None
    if isinstance(data, list):
        data = {"pack": {}, "items": data}
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None
    pack = data.get("pack")
    return {"pack": pack if isinstance(pack, dict) else {}, "items": items}


def plan_from_reply(reply: dict, *, cap: int, known_slugs: set[str]) -> dict:
    """Normalise the model's items into candidate rows.

    Returns ``{"pack", "rows", "dropped", "capped"}``. ``dropped`` carries a
    reason per row rather than silently shrinking the list — a truncated pack
    that reports nothing reads as "it only found nine things".
    """
    pack = reply.get("pack") or {}
    labels = {}
    for g in pack.get("groups") or []:
        if isinstance(g, dict) and str(g.get("id") or "").strip():
            labels[str(g["id"]).strip()] = str(g.get("label") or "").strip()

    rows: list[dict] = []
    dropped: list[dict] = []
    seen: set[str] = set()

    for raw in reply.get("items") or []:
        if not isinstance(raw, dict):
            dropped.append({"slug": "", "reason": "not an object"})
            continue
        name = str(raw.get("name") or "").strip()
        slug = str(raw.get("slug") or "").strip() or picture_catalog.slugify(name)
        if not slug or not re.fullmatch(r"[a-z0-9-]+", slug):
            dropped.append({"slug": slug or name, "reason": "slug is not url-safe"})
            continue
        if slug in seen:
            dropped.append({"slug": slug, "reason": "proposed twice"})
            continue
        missing = [f for f in ("name", "chinese", "wiki") if not str(raw.get(f) or "").strip()]
        if missing:
            dropped.append({"slug": slug, "reason": f"missing {', '.join(missing)}"})
            continue
        group = str(raw.get("group") or "").strip()
        if not group:
            dropped.append({"slug": slug, "reason": "no group"})
            continue

        alt = raw.get("wiki_alt") or []
        if isinstance(alt, str):
            alt = [alt]
        seen.add(slug)
        rows.append({
            "slug": slug,
            "name": name,
            "chinese": str(raw.get("chinese") or "").strip(),
            "pinyin": str(raw.get("pinyin") or "").strip(),
            "emoji": str(raw.get("emoji") or "").strip(),
            "wiki": str(raw["wiki"]).strip(),
            "wiki_alt": [str(a).strip() for a in alt if str(a).strip()][:2],
            "hint": str(raw.get("hint") or "").strip(),
            "group": group,
            "already_known": slug in known_slugs,
        })

    capped = 0
    if cap and len(rows) > cap:
        capped = len(rows) - cap
        rows = rows[:cap]

    return {
        "pack": {"title": str(pack.get("title") or "").strip(),
                 "emoji": str(pack.get("emoji") or "").strip(),
                 "labels": labels},
        "rows": rows,
        "dropped": dropped,
        "capped": capped,
    }


def classify_against_store(rows: list[dict], items: dict) -> None:
    """Stamp each row with how it relates to the existing object store.

    Uses ``exact_match``, never ``resolve_name``: fuzzy matching cannot tell a
    homonym from a hit, and a composer that silently absorbed `sea lion` into
    `seal` would be that bug at batch scale.

    ``reuse`` is the point of the object store — the pack gets a membership row
    and no second object, so the thing keeps its one progress record and its one
    downloaded photo.

    ``same-article`` is the name-blind case. A row whose ``wiki`` an existing
    object already uses is that object under a second name, so it would take the
    same photo — and the quiz then has a round whose picture matches two of its
    four options. Measured on the vegetables pack: the model proposed
    ``courgette``/``aubergine``/``pepper``/``beet-green`` beside the shipped
    ``zucchini``/``eggplant``/``bell-pepper``/``beetroot``, four duplicate
    concepts, and every other gate passed them — ``exact_match`` compares names
    (they differ), and ``_mark_duplicate_photos`` compares fetched files, which
    a ``reuse`` row never re-fetches. The prompt already forbids this and names
    courgette/zucchini as its own example; the model did it anyway, which is why
    it needs a gate and not just an instruction.
    """
    by_wiki = {}
    for slug, it in items.items():
        w = str(it.get("wiki") or "").strip().lower()
        if w:
            by_wiki.setdefault(w, slug)

    for row in rows:
        hit = picture_catalog.exact_match(items, row["slug"]) or \
            picture_catalog.exact_match(items, row["name"])
        if not hit:
            twin = by_wiki.get(str(row.get("wiki") or "").strip().lower())
            if twin:
                row["verdict"] = "same-article"
                row["same_as"] = twin
                continue
            row["verdict"] = "new"
            continue
        row["reuse_slug"] = hit["slug"]
        if str(hit.get("wiki") or "") != row["wiki"]:
            # Never silently retitle a shipped object: a downloaded photo and a
            # learner's history are keyed to it.
            row["verdict"] = "reuse-conflict"
            row["shipped_wiki"] = hit.get("wiki", "")
        else:
            row["verdict"] = "reuse"


def summarise(rows: list[dict], existing: dict | None = None) -> dict:
    """``existing`` is {group_id: member_count} already on disk, for a proposal
    that EXTENDS a pack. Without it, adding two words to a 28-member group would
    read as a thin group of 2 and block the apply on a pack that is fine — the
    gate has to judge the pack that will exist, not the delta."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("verdict", "?")] = counts.get(r.get("verdict", "?"), 0) + 1
    groups: dict[str, int] = dict(existing or {})
    for r in rows:
        if r.get("verdict") in ("new", "reuse"):
            groups[r["group"]] = groups.get(r["group"], 0) + 1
    # `min_group` travels with the summary so the page can size a merge without
    # hardcoding 4 — a second literal that would drift from picture_quiz's
    # OPTIONS_PER_ROUND the moment either moved.
    return {"verdicts": counts, "groups": groups, "min_group": MIN_GROUP,
            "thin_groups": {g: n for g, n in groups.items() if n < MIN_GROUP}}


def mark_already_in_pack(rows: list[dict], held: set[str]) -> int:
    """Stamp rows the TARGET PACK already holds as ``in-pack``. -> how many.

    A slug already in the object store is ``reuse``, which is right for a NEW
    pack — it joins with no second object. Extending is different: if the pack
    already holds it, the row writes nothing at all. Left as ``reuse`` it stays
    in ``keep``, so the thin gate counts a member the write then drops, and a
    group can ship below the four-item floor with the apply reporting success.
    Measured: gate said grains=4, the pack shipped 3, and quizzing that category
    returned no rounds at all.

    A verdict rather than a silent skip, so it is on screen before the write —
    which is the whole point of reviewing a proposal.
    """
    n = 0
    for r in rows:
        if r.get("verdict") in ("new", "reuse") and r.get("slug") in held:
            r["verdict"] = "in-pack"
            n += 1
    return n


def pack_held(prop: dict) -> set[str]:
    """Every slug the pack being extended already holds. ``set()`` for a new pack."""
    return {s for g in (prop.get("existing_groups") or {}).values()
            for s in (g.get("members") or [])}


def existing_counts(prop: dict) -> dict:
    """{group_id: members already on disk} for a proposal that extends a pack.

    ``{}`` for a new pack, which is what makes ``summarise`` behave exactly as
    it did before extending existed.
    """
    return {gid: int(g.get("count") or 0)
            for gid, g in (prop.get("existing_groups") or {}).items()}


def known_groups(prop: dict) -> set[str]:
    """Every group id this proposal may file a row under.

    Declared groups AND the groups rows actually carry. A model routinely emits
    an item whose ``group`` was never declared in the pack block, and that row is
    still a real candidate, so its group has to be a legal merge target — else
    the only repair for it would be dropping it.

    Ids only: the page renders labels from the proposal's own ``pack.labels``,
    so carrying them here as well would be a second copy that could disagree.

    For a proposal that EXTENDS a pack, the pack's own groups count too — a
    learner merging a thin new group into an established one is the main reason
    to reach for merge here, and those groups carry no proposal row.
    """
    out = {str(g).strip() for g in ((prop.get("pack") or {}).get("labels") or {})}
    out |= {str(r.get("group") or "").strip() for r in prop.get("rows") or []}
    out |= {str(g).strip() for g in (prop.get("existing_groups") or {})}
    return out - {""}


def merge_group(rows: list[dict], source: str, into: str) -> int:
    """Move every row in ``source`` into ``into``. Returns how many moved.

    The repair the apply gate names first. A thin group's rows are verified,
    photo-checked candidates; the other repair — dropping them — spends real
    content to clear a warning that is only about how the pack is arranged.

    Moves every row, not just the applicable ones: a ``no-photo`` row left
    behind in an emptied group would resurrect it the moment its title is
    repaired, re-thinning a group the user had already merged away.
    """
    moved = 0
    for r in rows:
        if str(r.get("group") or "").strip() == source:
            r["group"] = into
            moved += 1
    return moved


def read_pack_groups(packs_dir: Path, pack_id: str) -> list[dict]:
    """A pack's groups as they are on disk, in file order, or []."""
    try:
        doc = json.loads((Path(packs_dir) / f"{pack_id}.pack.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for g in doc.get("groups") or []:
        if isinstance(g, dict) and str(g.get("id") or "").strip():
            out.append({"id": str(g["id"]).strip(),
                        "label": str(g.get("label") or ""),
                        "members": [str(s) for s in (g.get("members") or []) if str(s).strip()]})
    return out


def merge_members(existing: list[dict], labels: dict, members: dict) -> tuple[dict, dict]:
    """Fold new membership into a pack's existing groups. -> (members, labels).

    Existing groups keep their file order, their labels and their members; a new
    slug joins its group at the end, and a group nobody had before is appended.
    A slug the pack already holds is not added again — not to its own group, and
    not to a DIFFERENT one. The second case is the one that bites: `load_packs`
    keeps the first membership and warns, so the pack file lists the slug twice
    while the quiz pool holds it once — and `existing_counts` reads the file, so
    a group of 4-with-a-duplicate would clear the four-item gate on a pool of 3.

    Without this, extending a pack means resupplying its whole member list,
    because ``write_pack`` rewrites the membership file wholesale.
    """
    out: dict[str, list[str]] = {}
    out_labels = dict(labels or {})
    for g in existing:
        out[g["id"]] = list(g["members"])
        # A label already on disk wins: it is what the learner sees today, and
        # a fresh proposal's label for the same id is a guess at it.
        if g["label"]:
            out_labels[g["id"]] = g["label"]
    held = {s for slugs in out.values() for s in slugs}
    for gid, slugs in (members or {}).items():
        bucket = out.setdefault(gid, [])
        for s in slugs:
            if s in held:
                continue
            bucket.append(s)
            held.add(s)
    return out, out_labels


def write_pack(packs_dir: Path, *, pack_id: str, title: str, emoji: str,
               labels: dict, members: dict, new_objects: list[dict],
               merge: bool = False) -> dict:
    """THE write seam. Appends objects, writes membership, registers in meta.

    Everything else in pack authoring is a read or a proposal; this is the one
    function that changes what the app ships from the composer. ``install_pack.py``
    is a second, independent writer — it hand-rolls the same three writes — so the
    two CAN drift, and a change here should be mirrored there.

    ``merge=True`` folds ``members`` into whatever the pack already has instead
    of replacing it. The default stays replace, because that is what both
    existing callers mean and a silent merge would make a re-install additive.
    """
    packs_dir = Path(packs_dir)
    obj_path = packs_dir / "objects.jsonl"

    if merge:
        members, labels = merge_members(read_pack_groups(packs_dir, pack_id),
                                        labels, members)

    if new_objects:
        existing = obj_path.read_text(encoding="utf-8") if obj_path.exists() else ""
        tail = "" if (not existing or existing.endswith("\n")) else "\n"
        with obj_path.open("a", encoding="utf-8", newline="\n") as f:
            if tail:
                f.write(tail)
            for obj in new_objects:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    (packs_dir / f"{pack_id}.pack.json").write_text(json.dumps({
        "id": pack_id,
        "groups": [{"id": g,
                    "label": labels.get(g) or g.replace("-", " ").capitalize(),
                    "members": slugs}
                   for g, slugs in members.items()],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    meta_path = packs_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta.setdefault("schema", 2)
    meta.setdefault("objects", "objects.jsonl")
    meta.setdefault("packs", [])
    meta["packs"] = [p for p in meta["packs"] if p.get("id") != pack_id]
    meta["packs"].append({"id": pack_id, "title": title, "emoji": emoji,
                          "members": f"{pack_id}.pack.json"})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")

    return {"objects_added": len(new_objects), "groups": len(members)}


# ─── Bound to the app ────────────────────────────────────────────────


# Words that carry no meaning in a pack id. A description is a sentence
# ("things you see at an airport") and slugifying it whole gives an id nobody
# wants to see on a chip or in a URL.
_STOP = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or",
         "you", "your", "i", "my", "we", "things", "stuff", "see", "find",
         "use", "used", "common", "everyday", "some", "that", "this", "with"}


def _id_from(description: str, *, limit: int = 3) -> str:
    """A short pack id from a free-text description."""
    words = [w for w in picture_catalog.slugify(description).split("-") if w]
    kept = [w for w in words if w not in _STOP] or words
    return "-".join(kept[:limit])


def composer_enabled(self) -> bool:
    """Dark by default. Schema key is namespaced because the settings store is
    global; the TOML key stays bare so check_dark_flags.py finds it."""
    return bool(self.setting_or_config(
        "dictionary.feature.pack-composer.enabled", False,
        config_key="feature.pack-composer.enabled"))


def _proposal_path(self, pid: str) -> Path:
    return self.data_subdir("proposals") / f"{safe_path_segment(pid)}.json"


def _read_proposal(self, pid: str) -> dict | None:
    p = self._proposal_path(pid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_proposal(self, prop: dict) -> None:
    path = self._proposal_path(prop["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(prop, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


async def _verify_candidates(self, rows: list[dict]) -> None:
    """Stamp every row with whether it will actually yield a photograph.

    Phase A of the real fetcher, reused verbatim via ``_lookup_lead_files`` — a
    title that returns a pageimage under ``pilicense=free`` is precisely "will
    yield a photo when we prefetch". Batched 50 per request, so the whole set
    plus its alternates is two calls.

    Three rounds at most, and the retitle round runs ONCE for the entire
    remaining set. A second retitle round is where a model starts inventing
    plausible article titles, and the human review is the cheaper backstop.
    """
    live = [r for r in rows if r.get("verdict") in ("new", "reuse-conflict")]
    if not live:
        return

    hits = await self._lookup_lead_files([r["wiki"] for r in live if r["wiki"]])
    misses = []
    for r in live:
        if r["wiki"] in hits:
            r["file"] = hits[r["wiki"]]
            r["wiki_source"] = "primary"
        else:
            misses.append(r)

    if misses:
        alts = [t for r in misses for t in r.get("wiki_alt") or []]
        alt_hits = await self._lookup_lead_files(alts) if alts else {}
        still = []
        for r in misses:
            won = next((t for t in r.get("wiki_alt") or [] if t in alt_hits), "")
            if won:
                r["wiki_tried"] = r["wiki"]
                r["wiki"] = won
                r["file"] = alt_hits[won]
                r["wiki_source"] = "alt"
            else:
                still.append(r)
        misses = still

    if misses:
        await self._retitle_round(misses)

    for r in live:
        if not r.get("file"):
            r["verdict"] = "no-photo"
            r["tried"] = [r.get("wiki_tried") or r["wiki"]] + list(r.get("wiki_alt") or [])
        elif picture_catalog.looks_like_a_drawing(r["file"]):
            # Advisory, not a rejection — the fix is a better title or a pinned
            # file, and only a human can pick which.
            r["warning"] = "the lead image looks like a drawing, not a photograph"

    _mark_duplicate_photos(rows)


def _mark_duplicate_photos(rows: list[dict]) -> None:
    """Two rows on one photo makes a photo-to-name question unanswerable.

    Detectable from the Phase-A filename alone, so it costs nothing and lands
    before any download. Re-run over the whole set whenever rows are added: a
    new row can collide with one already accepted.
    """
    by_file: dict[str, list[str]] = {}
    for r in rows:
        if r.get("file"):
            by_file.setdefault(r["file"], []).append(r["slug"])
    for r in rows:
        mates = [s for s in by_file.get(r.get("file") or "", []) if s != r["slug"]]
        if mates:
            r["verdict"] = "duplicate-photo"
            r["shares_with"] = mates
        elif r.get("verdict") == "duplicate-photo":
            # A collision can be resolved by dropping the other row.
            r["verdict"] = "new" if not r.get("reuse_slug") else "reuse"
            r.pop("shares_with", None)


async def _retitle_round(self, misses: list[dict]) -> None:
    """One model call for the whole remaining set, then one final batched check."""
    try:
        payload = [{"name": r["name"],
                    "tried": [r["wiki"]] + list(r.get("wiki_alt") or []),
                    "why": "resolved to nothing"} for r in misses]
        reply = await self.think(
            json.dumps(payload, ensure_ascii=False),
            system=PROMPTS.pack_retitle_system,
            domain="text",
        )
        fixes = parse_llm_json(_strip_fence(reply))
        if not isinstance(fixes, list):
            return
        by_name = {str(f.get("name") or "").strip().lower(): str(f.get("wiki") or "").strip()
                   for f in fixes if isinstance(f, dict)}
    except Exception as e:
        self.log_warn(f"pack composer: retitle round failed ({e})")
        return

    proposed = {r["slug"]: by_name.get(r["name"].lower(), "") for r in misses}
    titles = [t for t in proposed.values() if t]
    if not titles:
        return
    hits = await self._lookup_lead_files(titles)
    for r in misses:
        t = proposed.get(r["slug"]) or ""
        if t and t in hits:
            r["wiki_tried"] = r["wiki"]
            r["wiki"] = t
            r["file"] = hits[t]
            r["wiki_source"] = "retitled"


async def _compose_pack(self, pid: str) -> None:
    """The background job. Never raises — every exit writes a terminal status."""
    prop = self._read_proposal(pid)
    if not prop:
        return
    try:
        cap = int(self.setting_or_config("picture-dict.compose_max_items", 40) or 40)
        known = sorted(self.items)
        prop["stage"] = "thinking"
        self._write_proposal(prop)

        # Extending: name the groups that already exist so the model files items
        # into them. Without this it invents a parallel vocabulary ("veg" beside
        # a shipped "vegetables"), and every new group starts under the
        # four-item floor even though the pack it joins is healthy.
        extend_block = ""
        if prop.get("extends"):
            rows_txt = "\n".join(
                f"  {gid} ({g.get('label') or gid}) — {g.get('count', 0)} items already"
                for gid, g in (prop.get("existing_groups") or {}).items()
            )
            extend_block = PROMPTS.pack_extend_block.format(
                pack_id=prop["extends"], groups=rows_txt)

        user = (
            f"Theme: {prop['description']}\n"
            f"{extend_block}\n"
            f"At most {cap} items.\n"
            f"Already in the store, do not re-propose these slugs:\n"
            + ", ".join(known)
        )
        system = PROMPTS.pack_compose_system.replace("<CAP>", str(cap))
        reply = await self.think(user, system=system, domain="text")

        parsed = parse_compose_reply(reply)
        if not parsed:
            # Carry a sample. "did not return usable JSON" with nothing to look
            # at is an error the reader cannot act on — and the usual cause
            # (a refusal, a preamble, a truncated reply) is obvious on sight.
            sample = (reply or "").strip()
            prop.update(status="error", stage="",
                        error="the model did not return usable JSON",
                        raw_sample=sample[:600],
                        raw_len=len(reply or ""))
            self._write_proposal(prop)
            self.log_warn(f"pack composer: unusable reply ({len(reply or '')} chars): "
                          f"{sample[:200]}")
            return

        plan = plan_from_reply(parsed, cap=cap, known_slugs=set(known))
        rows = plan["rows"]
        classify_against_store(rows, self.items)
        # Extending: a slug the target pack already holds writes nothing, so say
        # so on the review card rather than letting it look applicable.
        mark_already_in_pack(rows, pack_held(prop))

        prop["stage"] = "verifying"
        self._write_proposal(prop)
        await self._verify_candidates(rows)

        prop.update(
            status="ready", stage="",
            pack=plan["pack"], rows=rows,
            dropped=plan["dropped"], capped=plan["capped"],
            summary=summarise([r for r in rows if r.get("verdict") in ("new", "reuse")],
                              existing_counts(prop)),
            # The staleness fingerprint: what the dedupe was decided against.
            fingerprint=sorted(self.items),
        )
        self._write_proposal(prop)
        await self.emit("dictionary:pack_proposed",
                        {"id": pid, "pack_id": prop["pack_id"],
                         "rows": len(rows)})
    except Exception as e:
        prop.update(status="error", stage="", error=str(e) or type(e).__name__)
        self._write_proposal(prop)
        self.log_warn(f"pack composer: {pid} failed ({e})")


# ─── Routes ──────────────────────────────────────────────────────────


@web_route("POST", "/api/picture/packs/propose")
async def api_pack_propose(self, request):
    """Start a proposal. Writes no pack, downloads no image, returns immediately.

    Backgrounded because one think plus 2-4 Wikimedia round trips is well over
    the ~30s line where the request 500s while the job keeps running unseen.
    """
    if not self.composer_enabled():
        return {"error": "the pack composer is off "
                         "([apps.dictionary] feature.pack-composer.enabled)"}
    body = await request.json() if await request.body() else {}
    description = str(body.get("description") or "").strip()
    if not description:
        return {"error": "describe the pack you want"}
    pack_id = picture_catalog.slugify(str(body.get("pack_id") or "")) or _id_from(description)
    if not pack_id:
        return {"error": "could not derive a pack id from that description"}

    # Extending is opt-in. Inferring it from "this id already exists" would turn
    # a colliding auto-derived id — `_id_from("food in the fridge")` is `food` —
    # into a silent write into the shipped Food pack.
    extend = bool(body.get("extend"))
    if pack_id in self.by_pack and not extend:
        return {"error": f"a pack called '{pack_id}' already exists"}
    if extend and pack_id not in self.by_pack:
        return {"error": f"no pack called '{pack_id}' to extend"}

    existing_groups = {}
    if extend:
        for g in read_pack_groups(Path(__file__).parent / "packs", pack_id):
            existing_groups[g["id"]] = {"label": g["label"], "count": len(g["members"]),
                                        "members": list(g["members"])}

    self._prune_proposals()
    pid = f"p{int(time.time() * 1000):x}"
    prop = {"id": pid, "pack_id": pack_id, "description": description,
            "status": "running", "stage": "queued", "created": time.time(),
            "rows": [], "dropped": [], "capped": 0,
            "extends": pack_id if extend else "",
            "existing_groups": existing_groups}
    self._write_proposal(prop)
    self.spawn_background(self._compose_pack(pid),
                          label=f"dictionary pack composer {pack_id}")
    return {"ok": True, "id": pid, "pack_id": pack_id, "status": "running"}


@web_route("GET", "/api/picture/packs/proposal/{pid}")
async def api_pack_proposal(self, request):
    if not self.composer_enabled():
        return {"error": "the pack composer is off"}
    prop = self._read_proposal(request.path_params.get("pid", ""))
    if not prop:
        return {"error": "no such proposal"}
    return prop


@web_route("GET", "/api/picture/packs/proposals")
async def api_pack_proposals(self, request):
    """Proposals still worth reopening, newest first.

    A proposal outlives the tab that made it — it sits on disk for
    ``PROPOSAL_TTL_S`` — but the page only ever held one in memory, set by
    ``propose()``. So a reload orphaned it: the rows were verified, the photos
    checked, and the only way back was to re-propose the whole pack. That is
    the cost the apply gate's repairs exist to avoid, so it cannot be the price
    of using them.

    Selected on CONTENT, not status. ``applied`` is out because the pack is
    already written, and anything with no rows is out because there is nothing
    to lose — which is what a proposal that died during composition looks like.

    ``error`` is deliberately NOT excluded: apply flips a proposal to ``error``
    when ``write_pack`` raises, and that record still holds every verified row.
    Hiding it would strand the exact work this list exists to protect.

    There is no ``rejected`` to filter — reject unlinks the file.
    """
    if not self.composer_enabled():
        return {"error": "the pack composer is off"}
    out = []
    for f in sorted(self.data_subdir("proposals").glob("*.json")):
        try:
            prop = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue  # a hand-edited, half-written or just-deleted file costs only itself
        rows = len(prop.get("rows") or [])
        if prop.get("status") == "applied" or not rows:
            continue
        summary = prop.get("summary") or {}
        out.append({
            "id": prop.get("id", ""),
            "pack_id": prop.get("pack_id", ""),
            "description": prop.get("description", ""),
            "status": prop.get("status", ""),
            # `row_count`, not `rows`: the by-id endpoint returns the record,
            # whose `rows` is the list itself. One name, two types, one function
            # apart is how a caller ends up rendering "[object Object] words".
            "row_count": rows,
            "thin_groups": summary.get("thin_groups") or {},
            # The record's own timestamp, never the file's mtime: every stage
            # transition and every edit rewrites the file, so mtime would sort a
            # proposal you merged today above one composed an hour ago. It also
            # keeps stat() — and its race with the two live deleters — out of
            # the loop entirely.
            "created": prop.get("created", 0),
        })
    out.sort(key=lambda p: p["created"], reverse=True)
    return {"proposals": out}


@web_route("POST", "/api/picture/packs/proposal/{pid}/edit")
async def api_pack_proposal_edit(self, request):
    """Drop a row, merge a whole group away, or set a title yourself. Writes no pack.

    A hand-set title is re-verified on the spot — one round trip, user watching —
    because a row written with an unchecked `wiki` renders an emoji tile forever
    and looks like a designed absence rather than an authoring mistake.

    Two verbs name no row and so are handled before the slug lookup rather than
    inside the per-row chain: ``merge_group`` names a group, and ``add`` carries
    the rows it is adding. The guard demanded a slug for both, which made ``add``
    reachable only by passing the slug of an unrelated row.
    """
    if not self.composer_enabled():
        return {"error": "the pack composer is off"}
    pid = request.path_params.get("pid", "")
    prop = self._read_proposal(pid)
    if not prop:
        return {"error": "no such proposal"}
    if prop.get("status") != "ready":
        return {"error": f"proposal is {prop.get('status')}, not ready"}

    body = await request.json() if await request.body() else {}
    merge_from = str(body.get("merge_group") or "").strip()
    add = body.get("add")
    slug = str(body.get("slug") or "").strip()
    row = next((r for r in prop["rows"] if r["slug"] == slug), None)
    # Only the per-row verbs need a row. `add` builds fresh rows from the body
    # and never reads this one, but the guard demanded a slug anyway — so the
    # verb whose own comment calls it "the missing verb" was reachable only by
    # passing the slug of some unrelated row.
    #
    # `not add`, NOT `add is None`: this has to mirror the `elif add:` below
    # exactly. Testing for None let a falsy-but-present `add` ([] or 0) past the
    # guard and then past its own branch, into a per-row branch holding
    # row=None — a 500 on `wiki`/`group`, and worse on `drop`, which filters by
    # slug rather than reading the row and so silently recorded a "removed by
    # hand" entry for a row that never existed.
    if not row and not merge_from and not add:
        return {"error": f"no row for '{slug}'"}

    if merge_from:
        # Both ends are validated against the proposal's own groups. An unchecked
        # target is worse than a refusal here: a typo would file the rows under a
        # brand-new one-item group, manufacturing a thin group while clearing one.
        into = str(body.get("into") or "").strip()
        groups = known_groups(prop)
        if merge_from not in groups:
            return {"error": f"no group '{merge_from}' in this proposal"}
        if into not in groups:
            return {"error": f"no group '{into}' in this proposal"}
        if into == merge_from:
            return {"error": "a group cannot merge into itself"}
        merge_group(prop["rows"], merge_from, into)
    elif add:
        # The missing verb. A model under-delivers as readily as it over-reaches
        # — it proposed 12 then 17 vehicles for a theme with forty photographable
        # things — and without this the only repair is to throw the whole
        # proposal away. Added rows take the SAME verification path as generated
        # ones; nothing enters a pack unchecked because a human typed it.
        rows = add if isinstance(add, list) else [add]
        plan = plan_from_reply({"pack": {}, "items": rows}, cap=0,
                               known_slugs=set(self.items))
        have = {r["slug"] for r in prop["rows"]}
        fresh = [r for r in plan["rows"] if r["slug"] not in have]
        if not fresh:
            return {"error": "nothing new to add",
                    "rejected": plan["dropped"],
                    "already_present": [r["slug"] for r in plan["rows"]
                                        if r["slug"] in have]}
        classify_against_store(fresh, self.items)
        mark_already_in_pack(fresh, pack_held(prop))
        await self._verify_candidates(fresh)
        prop["rows"].extend(fresh)
        prop.setdefault("dropped", []).extend(plan["dropped"])
        # The duplicate-photo check compares rows against each other, so it has
        # to run over the WHOLE set again — a new row can collide with an old one.
        _mark_duplicate_photos(prop["rows"])
    elif body.get("drop"):
        prop["rows"] = [r for r in prop["rows"] if r["slug"] != slug]
        prop["dropped"].append({"slug": slug, "reason": "removed by hand"})
    elif str(body.get("wiki") or "").strip():
        row["wiki"] = str(body["wiki"]).strip()
        row["wiki_alt"] = []
        row.pop("file", None)
        row.pop("warning", None)
        row["verdict"] = "new" if not row.get("reuse_slug") else row["verdict"]
        await self._verify_candidates([row])
        row["wiki_source"] = "by-hand"
    elif str(body.get("group") or "").strip():
        row["group"] = str(body["group"]).strip()
    else:
        return {"error": "nothing to change"}

    prop["summary"] = summarise([r for r in prop["rows"]
                                 if r.get("verdict") in ("new", "reuse")],
                                existing_counts(prop))
    self._write_proposal(prop)
    return prop


@web_route("POST", "/api/picture/packs/proposal/{pid}/apply")
async def api_pack_proposal_apply(self, request):
    """Commit exactly the rows the user saw. The only write in this module."""
    if not self.composer_enabled():
        return {"error": "the pack composer is off"}
    pid = request.path_params.get("pid", "")
    prop = self._read_proposal(pid)
    if not prop:
        return {"error": "no such proposal"}
    if prop.get("status") == "applied":
        return {"error": "already applied"}
    if prop.get("status") != "ready":
        return {"error": f"proposal is {prop.get('status')}, not ready"}

    # Staleness: the dedupe was decided against a snapshot of the store. If a
    # planned-new slug now exists, applying would create a second object for a
    # thing that already has one — the exact bug the object store removes.
    now = set(self.items)
    before = set(prop.get("fingerprint") or [])
    appeared = sorted((now - before) & {r["slug"] for r in prop["rows"]})
    vanished = sorted(before - now)
    if appeared or vanished:
        return {"error": "the object store changed since this proposal was built; "
                         "re-propose it",
                "appeared": appeared, "vanished": vanished}

    # Re-read the pack from DISK rather than trusting the propose-time snapshot:
    # the gate and the write have to answer from the same state, and the pack
    # can move under a proposal (a sibling apply, `install_pack.py`, a hand edit).
    extending = bool(prop.get("extends"))
    disk_groups = read_pack_groups(Path(__file__).parent / "packs", prop["pack_id"]) \
        if extending else []
    if extending and not disk_groups and (prop.get("existing_groups") or {}):
        # It had groups when proposed and has none now. Merging would silently
        # become a full replacement of a pack that still has members.
        return {"error": f"cannot read the '{prop['pack_id']}' pack membership any "
                         "more; re-propose against it"}
    if extending and mark_already_in_pack(
            prop["rows"], {s for g in disk_groups for s in g["members"]}):
        # Persist BEFORE the gate can return. The marking is what makes the
        # thin count true, so a refusal that does not save it leaves the card
        # showing four applicable rows beside a message saying the group has
        # three — the user cannot see why they were refused.
        prop["summary"] = summarise(
            [r for r in prop["rows"] if r.get("verdict") in ("new", "reuse")],
            {g["id"]: len(g["members"]) for g in disk_groups})
        self._write_proposal(prop)

    keep = [r for r in prop["rows"] if r.get("verdict") in ("new", "reuse")]
    if not keep:
        return {"error": "nothing to apply"}
    # Counts from that same fresh read, so the gate judges the pack the write
    # will actually produce.
    thin = summarise(keep, {g["id"]: len(g["members"]) for g in disk_groups})["thin_groups"]
    if thin:
        return {"error": "some groups are too small to build a four-option quiz "
                         "round; merge or drop them", "thin_groups": thin}

    members: dict[str, list[str]] = {}
    for r in keep:
        members.setdefault(r["group"], []).append(r["slug"])
    new_objects = [
        {k: r[k] for k in ("slug", "name", "chinese", "pinyin", "emoji", "wiki", "hint")
         if r.get(k)}
        for r in keep if r["verdict"] == "new"
    ]

    prop["status"] = "applied"          # before the write, so a double-click no-ops
    prop["applied_at"] = time.time()
    self._write_proposal(prop)

    shipped = next((p for p in (self.pack_index or []) if p.get("id") == prop["pack_id"]), {}) \
        if extending else {}

    try:
        res = write_pack(
            Path(__file__).parent / "packs",
            pack_id=prop["pack_id"],
            # The model is told to send a title and sometimes does not; a pack
            # with an empty name renders a blank chip. Extending keeps the
            # pack's own title and emoji — a proposal that adds four words does
            # not get to rename the pack a learner already knows.
            title=(shipped.get("title") if extending else "")
            or prop["pack"].get("title") or prop["pack_id"].replace("-", " ").title(),
            # No `or` fallback when extending: a pack with no emoji has chosen
            # none, and the model's invention is not an improvement on that.
            emoji=shipped.get("emoji", "") if extending else prop["pack"].get("emoji", ""),
            labels=prop["pack"].get("labels") or {},
            members=members,
            new_objects=new_objects,
            merge=extending,
        )
    except Exception as e:
        prop.update(status="error", error=f"write failed: {e}")
        self._write_proposal(prop)
        return {"error": f"write failed: {e}"}

    # A pack is read at setup(), so anything running against the stale in-memory
    # copy would re-download the OLD sources and report success.
    self.reload_packs()

    fetched = [r["slug"] for r in keep if r["verdict"] == "new"]
    if fetched:
        self.spawn_background(self.start_prefetch(slugs=fetched),
                              label=f"dictionary prefetch {prop['pack_id']}")

    await self.emit("dictionary:pack_created", {
        "pack_id": prop["pack_id"], "new": len(new_objects),
        "reused": len(keep) - len(new_objects), "groups": len(members),
    })
    return {"ok": True, "pack_id": prop["pack_id"], "new": len(new_objects),
            "reused": len(keep) - len(new_objects), "fetching": len(fetched),
            "review": f"/eos-picture-pack-review {prop['pack_id']}", **res}


@web_route("POST", "/api/picture/packs/proposal/{pid}/reject")
async def api_pack_proposal_reject(self, request):
    if not self.composer_enabled():
        return {"error": "the pack composer is off"}
    pid = request.path_params.get("pid", "")
    prop = self._read_proposal(pid)
    if not prop:
        return {"error": "no such proposal"}
    self._proposal_path(pid).unlink(missing_ok=True)
    await self.emit("dictionary:pack_proposal_rejected", {"id": pid})
    return {"ok": True}


def _prune_proposals(self) -> None:
    cutoff = time.time() - PROPOSAL_TTL_S
    try:
        for f in self.data_subdir("proposals").glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        pass
