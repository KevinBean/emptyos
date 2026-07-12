"""scoped_retrieval — pure scope-routing + escalation logic for two-tier vault retrieval.

The companion answers *faster* by retrieving from a **limited, relevant slice**
of the vault (a folder subtree and/or a tag set) before — or concurrently with —
the whole-vault pass. This module is the **pure core**: turning a query + a coarse
interest profile into 1-3 :class:`Scope`s, rendering a scope menu for an optional
LLM router, and classifying a retrieval outcome. It does no I/O and never touches
the kernel, so it unit-tests without a daemon.

The orchestration that actually reads the vault + builds an embedding index lives
in ``BaseApp.scoped_retrieve`` (it needs ``self``). Split per the
``sdk/html_element_edit.py`` (pure) + ``sdk/pipeline.py`` (orchestrator) precedent.

Mirrors the agent-bus L0/L2 pattern (``bus_index`` → ``select`` → ``bus_context``):
scan a cheap menu, drill the few. Here the "menu" is the user's interest profile
(project/area names + top tags) and the "drill" is a scoped embedding search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Standard PARA label → folder base. `vault_interest_profile` keys are the
# label half (`10_Projects` → `projects`); this maps back to the folder path a
# `vault_query(folder=...)` needs. Override via `key_to_folder` for tests / non-PARA.
PARA_KEY_FOLDER: dict[str, str] = {
    "projects": "10_Projects",
    "areas": "20_Areas",
    "resources": "30_Resources",
}

# Token words that name a folder/tag directly — only consulted on an exact token hit.
SCOPE_ALIASES: dict[str, "Scope"] = {}  # populated after Scope is defined (below)

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "do", "does", "did", "how", "what",
    "why", "when", "where", "who", "which", "this", "that", "these", "those",
    "my", "me", "i", "you", "it", "its", "about", "from", "into", "can", "should",
    "would", "could", "tell", "show", "give", "find", "get", "list", "any", "all",
})

# A token must be longer than this (latin) to be considered a scope signal.
_MIN_TOKEN_LEN = 3
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # letters only (latin + CJK)
_RAW_TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)  # keeps short tokens (kb, hv) + digits
_CJK_RE = re.compile(r"[　-鿿豈-﫿＀-￯]")


@dataclass
class Scope:
    """A narrow vault slice: an exact folder subtree and/or a (hierarchical) tag set."""

    folder: str | None = None
    tags: list[str] | None = None
    label: str = ""

    def key(self) -> tuple:
        return (self.folder, tuple(self.tags or ()))


@dataclass
class ScopedResult:
    """Outcome of a scoped retrieval. ``tier == "scoped"`` with snippets is the
    only case a caller should answer from; every other tier signals the caller
    to fall through to (or also run) the whole-vault path."""

    snippets: list[dict] = field(default_factory=list)   # [{path, name, text, score}]
    scopes: list[Scope] = field(default_factory=list)
    top_score: float = 0.0
    n_candidates: int = 0
    tier: str = "no-scope"  # scoped | no-scope | no-embeddings | scope-too-broad | empty-slice

    @property
    def usable(self) -> bool:
        return self.tier == "scoped" and bool(self.snippets)


def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s or ""))


def _slugify(name: str) -> str:
    """`Career Strategy` → `career-strategy` for loose folder-name matching."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _tokens(query: str) -> list[str]:
    """Lowercased latin word tokens > _MIN_TOKEN_LEN, stopwords dropped.

    CJK queries have no whitespace word boundaries, so latin tokenization
    yields few/none for them — `select_scopes_deterministic` handles CJK by
    raw-substring containment instead (see below).
    """
    out = []
    for t in _TOKEN_RE.findall((query or "").lower()):
        if len(t) > _MIN_TOKEN_LEN and t not in _STOPWORDS:
            out.append(t)
    return out


def _raw_tokens(query: str) -> set[str]:
    """All word tokens lowercased, incl. short ones (kb, hv) — for *exact* membership
    tests against curated/known sets (aliases, profile tags) where short keys matter."""
    return {t for t in _RAW_TOKEN_RE.findall((query or "").lower()) if t not in _STOPWORDS}


def select_scopes_deterministic(
    query: str,
    profile: dict,
    *,
    max_scopes: int = 3,
    key_to_folder: dict[str, str] | None = None,
) -> list[Scope]:
    """Pick up to ``max_scopes`` scopes from a query + interest profile — no LLM, no I/O.

    ``profile`` is the :meth:`BaseApp.vault_interest_profile` shape::

        {"projects": [names...], "areas": [names...], "tags": [[tag, count], ...]}

    Strategy (conservative — returns ``[]`` on no clear signal, so a generic
    query correctly has *no* scoped phase and falls to the whole-vault path):

    - **tag match** — a query token equal-or-prefix to a profile tag → ``Scope(tags=[tag])``.
    - **folder-entry match** — a token (or, for CJK, a raw-substring) matching a
      project/area/resource subdir name → ``Scope(folder="<base>/<name>")``.
    - **alias match** — an explicit folder word (``journal``/``kb``/``project``…) → its scope.

    Earlier matches rank first; deduped by ``Scope.key()``; capped to ``max_scopes``.
    """
    k2f = key_to_folder or PARA_KEY_FOLDER
    toks = _tokens(query)                 # filtered signal tokens (len>3) — fuzzy slug match
    raw_toks = _raw_tokens(query)         # all tokens incl. short — exact known-set match
    cjk = _has_cjk(query)
    raw = (query or "").lower()
    scopes: list[Scope] = []
    seen: set[tuple] = set()

    def add(s: Scope) -> None:
        k = s.key()
        if k not in seen:
            seen.add(k)
            scopes.append(s)

    # 1) Explicit folder/tag alias words (exact token hit against the curated set).
    for tok in raw_toks:
        sc = SCOPE_ALIASES.get(tok)
        if sc is not None:
            add(Scope(folder=sc.folder, tags=list(sc.tags) if sc.tags else None,
                      label=sc.label or tok))

    # 2) Tag match against the interest profile's top tags (exact, short tags ok).
    profile_tags = [str(t[0]) for t in (profile.get("tags") or []) if t]
    for tag in profile_tags:
        tl = tag.lower()
        leaf = tl.split("/")[-1]
        hit = (tl in raw_toks or leaf in raw_toks)
        if not hit and cjk:
            # CJK tag: match by raw containment (no whitespace tokenization).
            hit = bool(_has_cjk(tag)) and (tl in raw or leaf in raw)
        if hit:
            add(Scope(tags=[tag], label=f"#{tag}"))

    # 3) Folder-entry (project / area / resource subdir) match.
    for key, base in k2f.items():
        for name in (profile.get(key) or []):
            nl = str(name).lower()
            slug = _slugify(name)
            hit = (nl in toks or slug in toks
                   or any(part in toks for part in slug.split("-") if len(part) > _MIN_TOKEN_LEN))
            if not hit and cjk and _has_cjk(name):
                hit = nl in raw
            if hit:
                add(Scope(folder=f"{base}/{name}", label=str(name)))

    return scopes[:max_scopes]


def scope_menu(
    profile: dict,
    *,
    max_items: int = 12,
    key_to_folder: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render a ``{key: description}`` menu of candidate scopes for :meth:`BaseApp.select`.

    Keys are stable, parseable tokens (``tag:<t>`` / ``folder:<rel>``) plus the
    sentinel ``__all__`` ("no narrowing — search the whole vault"). Used only by
    the optional ``router="llm"`` mode; the deterministic router never calls this.
    """
    k2f = key_to_folder or PARA_KEY_FOLDER
    menu: dict[str, str] = {"__all__": "No clear topic — search the whole vault"}
    for tag, _ in (profile.get("tags") or []):
        if len(menu) > max_items:
            break
        menu[f"tag:{tag}"] = f"Notes tagged #{tag}"
    for key, base in k2f.items():
        for name in (profile.get(key) or []):
            if len(menu) > max_items:
                break
            menu[f"folder:{base}/{name}"] = f"The {name} {key[:-1] if key.endswith('s') else key}"
    return menu


def parse_scope_key(key: str) -> Scope | None:
    """Inverse of :func:`scope_menu` keys. ``__all__`` / unknown → ``None`` (no scope)."""
    if not key or key == "__all__":
        return None
    if key.startswith("tag:"):
        t = key[4:].strip()
        return Scope(tags=[t], label=f"#{t}") if t else None
    if key.startswith("folder:"):
        f = key[7:].strip()
        return Scope(folder=f, label=f.split("/")[-1]) if f else None
    return None


def should_escalate(
    hits: list[tuple],
    *,
    min_hits: int = 1,
    min_top_score: float = 0.45,
) -> bool:
    """True ⇒ the scoped slice is too weak; the caller should run the whole-vault path.

    ``hits`` is the ``EmbeddingIndex.search`` return ``[(item, score), ...]``.
    ``min_top_score`` (0.45) sits **above** the inclusion floor (0.30) — a hit can
    be good enough to *show* yet the slice still judged insufficient on its own.
    """
    if len(hits) < min_hits:
        return True
    top = hits[0][1] if hits else 0.0
    return top < min_top_score


# --- Alias table (built here so it can reference Scope) ---
# Only narrow, usually-bounded scopes belong here. A whole top-level folder
# (10_Projects / 30_Resources / 40_Archive) is almost always too broad to be a
# useful scope — it would just trip the per-scope breadth cap — so those are
# intentionally NOT aliased; the specific project/area NAME match (step 3) is
# what scopes into a single project. A too-broad tag (kb) self-drops via the cap.
SCOPE_ALIASES.update({
    "journal": Scope(folder="50_Journal", label="journal"),
    "journals": Scope(folder="50_Journal", label="journal"),
    "diary": Scope(folder="50_Journal", label="journal"),
    "inbox": Scope(folder="00_Inbox", label="inbox"),
    "kb": Scope(tags=["kb"], label="#kb"),
    "knowledge": Scope(tags=["kb"], label="#kb"),
})
