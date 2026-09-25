"""Link Manager — the inverted link index. Pure string↔structure transforms.

Why this module exists at all: every question the app answers (backlinks,
orphans, broken links, stats) is a lookup in one map, and the app was computing
each by walking the vault. Worse, it resolved a link target by **bare stem
only**, so the vault's most common shape was invisible to it:

    measured 2026-08-17 over a 400-note sample of the live vault —
    855 path-form links (`[[10_Projects/demo/demo]]`), 507 bare stems.

That is ~63% of real links unresolvable, which is what produced a reported
19,394 orphans out of 23,578 notes (82%) — a number nobody can act on. The
definition was also wrong (see `orphan_report`), so the two faults compounded.

No kernel import, no I/O: the app reads files and hands `{rel_path: text}` in.
Everything here is unit-testable without a daemon.
"""

from __future__ import annotations

import re

# Group 1 is the target; an `|alias` is display text and never part of it.
WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# Code regions come from the shared markdown module — `resolve_wikilinks` needs
# the same regions to preserve code verbatim, so the scanner lives there and
# this reads it (CLAUDE.md principle 9, extracted at the second consumer).
#
# Why it matters here: measured on the live vault, the three most-referenced
# "broken links" were `${block.reference}`, `" + block.reference +"` and
# `${refId}` — JavaScript template literals in fenced examples, ~5,500 phantom
# links. It cut both ways: a `[[foo]]` in a code sample was also marking `foo`
# as *referenced*, so a genuinely orphaned note could be hidden by an example
# that merely mentioned it.
from emptyos.sdk.markdown_render import strip_code  # noqa: E402  (after WIKILINK)
from emptyos.sdk.utils import normalize_link_target, note_stem_key  # noqa: E402


def find_links(text: str) -> list[str]:
    """Every wikilink target in a note, ignoring anything inside code."""
    return WIKILINK.findall(strip_code(text))


# Target/path normalisation is shared with `vault-graph`, which resolves the
# same wikilinks from three other sites (CLAUDE.md principle 9 — extracted at
# the second consumer, after the copies had already diverged over anchors).
normalize_target = normalize_link_target
_stem_key = note_stem_key


def _path_key(rel_path: str) -> str:
    """A note's path in the same comparable form as a link target."""
    return normalize_link_target(rel_path)


def build_index(notes: dict[str, str]) -> dict:
    """Build the inverted index from ``{rel_path: file_text}``.

    Returns a dict with:
      ``paths``       — every rel_path, sorted
      ``out``         — {rel_path: set(rel_path)} resolved outgoing edges
      ``inn``         — {rel_path: set(rel_path)} resolved incoming edges
      ``unresolved``  — {raw_target: sorted[rel_path]} links pointing nowhere
      ``ambiguous``   — sorted stems that name more than one note
      ``total_links`` — every wikilink occurrence, resolved or not
    """
    by_path: dict[str, str] = {}
    by_stem: dict[str, list[str]] = {}
    for rel in notes:
        by_path[_path_key(rel)] = rel
        by_stem.setdefault(_stem_key(rel), []).append(rel)

    out: dict[str, set[str]] = {rel: set() for rel in notes}
    inn: dict[str, set[str]] = {rel: set() for rel in notes}
    unresolved: dict[str, set[str]] = {}
    total = 0

    for rel, text in notes.items():
        for raw in find_links(text):
            total += 1
            targets = resolve(raw, by_path, by_stem)
            if not targets:
                unresolved.setdefault(str(raw).strip(), set()).add(rel)
                continue
            for tgt in targets:
                if tgt == rel:
                    continue  # a self-link is not a connection
                out[rel].add(tgt)
                inn[tgt].add(rel)

    return {
        "paths": sorted(notes),
        "out": out,
        "inn": inn,
        "unresolved": {k: sorted(v) for k, v in unresolved.items()},
        "ambiguous": sorted(s for s, v in by_stem.items() if len(v) > 1),
        "total_links": total,
    }


def resolve(raw: str, by_path: dict[str, str], by_stem: dict[str, list[str]]) -> list[str]:
    """Resolve one raw link target to the notes it names.

    Path form wins over stem form, because it is more specific. A stem that
    names several notes resolves to **all** of them rather than picking one:
    guessing would invent a connection the vault does not state, and marking
    every candidate as referenced errs toward *fewer* false orphans — which is
    the direction that matters when the whole point is a list someone can act
    on. Such stems are reported in ``ambiguous`` so the ambiguity stays visible
    rather than silently absorbed.
    """
    key = normalize_target(raw)
    if not key:
        return []
    hit = by_path.get(key)
    if hit:
        return [hit]
    return list(by_stem.get(key, []))


def orphan_report(index: dict) -> dict:
    """Split "orphan" into the two populations the old code conflated.

    ``orphans``      — degree 0: nothing links in **and** nothing links out.
                       Genuinely disconnected, and the actionable list.
    ``unreferenced`` — nothing links in, but the note links out. The old
                       ``orphans()`` returned exactly this and called it orphan,
                       which is why a note pointing at fifty others was
                       reported as one.

    Reported separately because they call for different work: an orphan needs
    connecting, an unreferenced note usually just isn't a destination.

    ``broken_only`` is a third, small population carved out of ``orphans``: a
    note that *does* link out, but every target is unresolved. It looks
    disconnected in the graph and reads as an orphan, while the author plainly
    did link it — so the action is fixing a link, not writing one.
    """
    inn, out = index["inn"], index["out"]
    tried = set()
    for srcs in index["unresolved"].values():
        tried.update(srcs)
    orphans, unreferenced, broken_only = [], [], []
    for rel in index["paths"]:
        has_in, has_out = bool(inn.get(rel)), bool(out.get(rel))
        if not has_in and not has_out:
            (broken_only if rel in tried else orphans).append(rel)
        elif not has_in:
            unreferenced.append(rel)
    return {
        "orphans": orphans,
        "unreferenced": unreferenced,
        "broken_only": broken_only,
    }


def backlinks_for(index: dict, target: str) -> list[str]:
    """Notes linking to ``target``, which may be a stem, a path, or a rel_path.

    Exact, not fuzzy. The old implementation delegated to the ``search``
    capability with `[[title]]` as the query; search tokenizes, so it returned
    200 files on the live vault where a literal grep found 7 — ~96% false
    positives on the app's headline verb, with the true results possibly past
    the result cap entirely.
    """
    paths = index["paths"]
    by_path = {_path_key(p): p for p in paths}
    by_stem: dict[str, list[str]] = {}
    for p in paths:
        by_stem.setdefault(_stem_key(p), []).append(p)
    hits: set[str] = set()
    for rel in resolve(target, by_path, by_stem):
        hits |= index["inn"].get(rel, set())
    return sorted(hits)


def broken_links(index: dict, limit: int = 0) -> list[dict]:
    """Unresolved link targets, most-referenced first.

    Ranked by how many notes point at the missing target: one typo repeated
    across twenty notes is worth more than twenty one-off dead ends.
    """
    rows = [
        {"target": t, "sources": srcs, "count": len(srcs)}
        for t, srcs in index["unresolved"].items()
    ]
    rows.sort(key=lambda r: (-r["count"], r["target"]))
    return rows[:limit] if limit else rows
