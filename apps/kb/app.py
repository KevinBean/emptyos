"""Knowledge Base — vault-resident, domain-general note browser.

Every KB note carries `tag: kb` and is typed by `kind`:
  concept / formula / reference / clause / case / lesson / doc / moc

The corpus is queried by tag (not folder) and partitioned by `domain`
(power-systems, cable-thermal, ...). Backlinks are computed from frontmatter
`related:` + body `[[wikilinks]]` + parsed `references:` citations. The
`implemented_in:` field links formulas to code paths checked against the repo.

`kind: doc` notes compose other KB notes via a JSON-encoded `paragraphs_json`
frontmatter field — each paragraph has `noteRefs: [slug | slug#section]` that
the renderer resolves at view time. Docs live at `30_Resources/EmptyOS/kb/docs/`
by default (overridable via `[apps.kb] docs_dir` in `emptyos.toml`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, extract_wikilinks, on_event, web_route

KINDS = ("concept", "formula", "reference", "clause", "case", "lesson", "doc", "moc")
DEFAULT_DOCS_DIR = "30_Resources/EmptyOS/kb/docs"

# Citation parser — matches free-text strings like:
#   "IEC 60287-1-1:2023 §5.1.3 (description)"
#   "CIGRE TB 880 §4.6.4"
#   "IEC 60228 (description)"   ← standard-only, clause=None
# Unmatched strings fall through to plain-text passthrough.
_CITATION_RE = re.compile(
    r"""^\s*
    (?P<standard>
        IEC\s*\d+(?:[-‑–]\d+)*           # IEC 60287, IEC 60287-1-1
        | CIGRE\s+TB\s+\d+                # CIGRE TB 880
        | TB\s+\d+                        # TB 880 (CIGRE-omitted shorthand)
        | AS/?NZS\s+\d+(?:\.\d+)*         # AS/NZS 3008.1.1
        | IEEE\s+\d+                      # IEEE 80, IEEE 998
    )
    (?:\s*[:(\s]\s*(?P<edition>\d{4})\)?)?  # :2023, (2023), or " 2023"
    (?:[\s,]+§\s*(?P<clause>             # § followed by a clause number
        \d+(?:\.\d+)*(?:[a-z])?           #   5.1.3, 4.6.4.1a
        (?:\s*[\-–]\s*\d+(?:\.\d+)*)?    #   5.1.4–5.1.5 (range)
    ))?
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", str(s).strip().lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s or "untitled"


def _slug_of(path: str) -> str:
    return Path(path).stem


def _related_targets(props: dict) -> set[str]:
    """Slugs referenced in frontmatter `related` (accepts string or [[slug]] forms)."""
    out: set[str] = set()
    for v in props.get("related") or []:
        s = str(v).strip().strip("[]").strip()
        if s:
            out.add(s)
    return out


def _norm_standard(s: str) -> str:
    """Normalize standard name: uppercase, normalize hyphens, single space between letters/digits."""
    s = (s or "").upper().strip()
    s = s.replace("‑", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    # "IEC60287" → "IEC 60287"
    s = re.sub(r"([A-Z])(\d)", r"\1 \2", s)
    # Collapse repeated spaces after the insertion
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_clause(s: str | None) -> str | None:
    """Normalize clause: strip §, collapse whitespace. '§ 5.1.3' → '5.1.3'."""
    if s is None:
        return None
    raw = str(s).replace("§", "").replace("¶", "").strip().lower()
    raw = re.sub(r"\s+", "", raw)
    # Normalize en-dash / em-dash / non-breaking hyphen → hyphen in ranges
    raw = raw.replace("‑", "-").replace("–", "-").replace("—", "-")
    return raw or None


def _clause_sort_key(clause: str | None) -> tuple:
    """Sort key for clauses: '5.1.10' > '5.1.2' (numeric, not lexical); 'None' first."""
    if not clause:
        return ()
    out = []
    for part in str(clause).split("."):
        m = re.match(r"^(\d+)([a-z]?)$", part, re.IGNORECASE)
        if m:
            out.append((int(m.group(1)), m.group(2).lower()))
        else:
            # Non-numeric segment (e.g. range '4-5' or letters) — sort as 0 with raw tail
            out.append((0, part.lower()))
    return tuple(out)


def _parse_citation(text: str) -> tuple[str, str | None, str | None] | None:
    """Parse a free-text citation string into (standard, edition, clause).

    Returns None when no standard pattern matches.
    For range citations like '§5.1.4–5.1.5', returns the lower bound only.
    """
    if not text:
        return None
    m = _CITATION_RE.match(text)
    if not m:
        return None
    standard = _norm_standard(m.group("standard") or "")
    edition = m.group("edition")
    edition = str(edition) if edition else None
    clause = m.group("clause")
    if clause:
        # Range like '5.1.4-5.1.5' → take lower bound
        clause = re.split(r"\s*[\-–—]\s*", clause, maxsplit=1)[0]
    clause = _norm_clause(clause)
    if not standard:
        return None
    return (standard, edition, clause)


class KBApp(BaseApp):
    # Reference index keyed by (standard_norm, edition_or_None, clause_norm_or_None) → slug.
    # Built at setup() and rebuilt on vault:changed when a kb-tagged note touched.
    _ref_index: dict[tuple[str, str | None, str | None], str]
    # Inbound citation index: for each reference-note slug, the set of KB note slugs
    # that cite it via the parsed `references:` field. Powers citation backlinks.
    _inbound_citations: dict[str, set[str]]
    # Code-path → list of formula slugs that declare `implemented_in:` matching it.
    # Powers "which formulas does this code path implement?" reverse-lookup.
    _implementation_index: dict[str, list[str]]

    async def setup(self):
        await super().setup()
        self._ref_index = {}
        self._inbound_citations = {}
        self._implementation_index = {}
        self._build_ref_index()
        self._build_implementation_index()

    @on_event("vault:changed")
    async def _on_vault_changed(self, payload: dict):
        # Cheap to rebuild; the kb-tagged set is small relative to the full vault.
        self._build_ref_index()
        self._build_implementation_index()

    def _build_ref_index(self) -> None:
        all_notes = self._all_notes()
        # Forward: (standard, edition, clause) → ref_slug
        idx: dict[tuple[str, str | None, str | None], str] = {}
        for n in all_notes:
            props = n.get("properties", {}) or {}
            if props.get("kind") != "clause":
                continue
            standard = _norm_standard(props.get("standard") or "")
            if not standard:
                continue
            edition = props.get("edition")
            edition = str(edition) if edition else None
            clause = _norm_clause(props.get("clause")) if props.get("clause") else None
            slug = _slug_of(n.get("path", ""))
            if not slug:
                continue
            idx[(standard, edition, clause)] = slug
        self._ref_index = idx
        # Inbound: walk every kb note's `references:` field, parse, lookup, record citers.
        # Use the same _lookup_ref fallback semantics as get_note's resolution path.
        inbound: dict[str, set[str]] = {}
        for n in all_notes:
            citer_slug = _slug_of(n.get("path", ""))
            if not citer_slug:
                continue
            props = n.get("properties", {}) or {}
            for raw in props.get("references") or []:
                parsed = _parse_citation(str(raw))
                if not parsed:
                    continue
                standard, edition, clause = parsed
                ref_slug = self._lookup_ref(standard, edition, clause)
                if ref_slug:
                    inbound.setdefault(ref_slug, set()).add(citer_slug)
        self._inbound_citations = inbound

    def _build_implementation_index(self) -> None:
        """Reverse index: code path → [slug] of formula/concept notes implementing it.

        Reads each KB note's `implemented_in:` field and partitions the slugs by
        normalized code path (the part before `::symbol` if present). Lets apps
        and CI ask "which KB notes does this file implement?"
        """
        idx: dict[str, list[str]] = {}
        for n in self._all_notes():
            slug = _slug_of(n.get("path", ""))
            if not slug:
                continue
            props = n.get("properties", {}) or {}
            for ref in props.get("implemented_in") or []:
                kind, code_path, _ = self._parse_impl_ref(ref)
                # method-only refs don't carry a code path; skip for the reverse
                # lookup since callers will query by file path.
                if kind == "method" or not code_path:
                    continue
                idx.setdefault(code_path, []).append(slug)
        # Stable ordering inside each bucket
        for path_key in idx:
            idx[path_key].sort()
        self._implementation_index = idx

    def _resolve_references(self, refs: list) -> list[dict]:
        """Convert free-text references into structured {text, target_slug, in_kb} dicts.

        Falls through with target_slug=None for un-parseable / un-matched strings so
        the UI render path is uniform. Match order: exact triple → drop edition (try
        any edition with same clause) → drop clause (try any edition with same
        standard whole-standard) → no match.
        """
        out: list[dict] = []
        for raw in refs or []:
            text = str(raw).strip()
            if not text:
                continue
            parsed = _parse_citation(text)
            target = None
            if parsed:
                standard, edition, clause = parsed
                target = self._lookup_ref(standard, edition, clause)
            out.append({"text": text, "target_slug": target, "in_kb": target is not None})
        return out

    def _clauses_for_standard(self, standard_id: str) -> list[dict]:
        """List reference notes whose normalized `standard:` starts with `standard_id`.

        Useful for a standard's landing page that aggregates all its clause notes.
        Prefix-match so e.g. `IEC 60287` collects both `IEC 60287-1-1` and `IEC 60287-2-1`.
        Sorted by (standard, edition desc, clause numerically).
        """
        target = _norm_standard(standard_id)
        if not target:
            return []
        rows: list[dict] = []
        for n in self._all_notes():
            props = n.get("properties", {}) or {}
            if props.get("kind") != "clause":
                continue
            std = _norm_standard(props.get("standard") or "")
            if not std or not std.startswith(target):
                continue
            # Clause as stored in frontmatter may have leading '§' — strip it; the UI prepends.
            raw_clause = str(props.get("clause") or "").replace("§", "").strip()
            rows.append({
                "slug": _slug_of(n.get("path", "")),
                "standard": std,
                "edition": str(props.get("edition")) if props.get("edition") else None,
                "clause": raw_clause,
                "clause_title": props.get("clause_title") or "",
                "title": props.get("title") or n.get("name") or "",
            })
        rows.sort(key=lambda r: (
            r["standard"],
            # Newer edition first within the same standard (None last)
            -(int(r["edition"]) if r["edition"] and r["edition"].isdigit() else -1),
            _clause_sort_key(_norm_clause(r["clause"])),
        ))
        return rows

    def _lookup_ref(self, standard: str, edition: str | None, clause: str | None) -> str | None:
        """Match (standard, edition, clause) against the index with progressive fallback.

        Citations may omit edition or use a wider/older edition; the index always
        carries whatever the reference note declared. So we:
          1. Try exact match.
          2. If exact fails, scan all editions of (standard, clause) and pick the
             newest edition that matches (or any if all editions are None).
          3. If clause-specific match fails, try whole-standard match.
        """
        # 1. Exact
        key = (standard, edition, clause)
        if key in self._ref_index:
            return self._ref_index[key]
        # 2. Drop edition — scan for any edition with this (standard, clause)
        candidates = [
            (s, e, c, slug) for (s, e, c), slug in self._ref_index.items()
            if s == standard and c == clause
        ]
        if candidates:
            # Prefer the newest edition (lexical sort works for 4-digit years; None last)
            candidates.sort(key=lambda x: (x[1] or ""), reverse=True)
            return candidates[0][3]
        # 3. Whole-standard fallback
        whole = self._ref_index.get((standard, None, None))
        if whole:
            return whole
        # 4. Any edition of whole-standard
        any_whole = [slug for (s, e, c), slug in self._ref_index.items() if s == standard and c is None]
        if any_whole:
            return any_whole[0]
        return None

    # ---------- per-app vault paths (override in `[apps.kb]` of emptyos.toml) ----------

    def _docs_dir(self) -> str:
        return self.app_config("docs_dir", DEFAULT_DOCS_DIR)

    def _doc_path(self, slug: str) -> str:
        return f"{self._docs_dir()}/{slug}.md"

    # ---------- core queries ----------

    def _all_notes(self) -> list[dict]:
        return self.vault_query(tags=["kb"]) or []

    def _summarize(self, n: dict) -> dict:
        props = n.get("properties", {}) or {}
        return {
            "slug": _slug_of(n.get("path", "")),
            "path": n.get("path", ""),
            "name": n.get("name", ""),
            "title": props.get("title", ""),
            "kind": props.get("kind", ""),
            "domain": props.get("domain", ""),
            "topic": props.get("topic", ""),
            "tags": n.get("tags", []) or [],
        }

    # ---------- core (used by both API and CLI) ----------

    def list_domains(self) -> dict:
        notes = self._all_notes()
        domains: dict[str, dict[str, int]] = {}
        for n in notes:
            d = (n.get("properties", {}) or {}).get("domain") or "unknown"
            k = (n.get("properties", {}) or {}).get("kind") or "unknown"
            domains.setdefault(d, {"_total": 0})
            domains[d]["_total"] += 1
            domains[d][k] = domains[d].get(k, 0) + 1
        out = []
        for d, counts in sorted(domains.items()):
            out.append({"domain": d, "total": counts.pop("_total"), "by_kind": counts})
        return {"domains": out, "total_notes": len(notes)}

    def list_notes(self, domain: str = "", kind: str = "", topic: str = "") -> dict:
        out = []
        for n in self._all_notes():
            s = self._summarize(n)
            if domain and s["domain"] != domain:
                continue
            if kind and s["kind"] != kind:
                continue
            if topic and s["topic"] != topic:
                continue
            out.append(s)
        out.sort(key=lambda x: (x["domain"], x["kind"], x["slug"]))
        return {"notes": out, "count": len(out)}

    async def get_note(self, slug: str) -> dict:
        all_notes = self._all_notes()
        match = next(
            (n for n in all_notes if _slug_of(n.get("path", "")) == slug),
            None,
        )
        if not match:
            return {"error": "not found", "slug": slug}
        path = match.get("path", "")
        props = self.vault_get_properties(path) or (match.get("properties") or {})
        # Resolve free-text references into structured {text, target_slug, in_kb} so the
        # UI can render them as clickable links to reference notes. In-place mutation
        # preserves the existing `data.properties.references` consumer contract.
        if isinstance(props.get("references"), list):
            props["references"] = self._resolve_references(props.get("references") or [])
        body = self.vault_read_body(path)
        idx = self._kind_index(all_notes)

        # Outgoing edges: frontmatter related/verified_against + body [[wikilinks]] that resolve to KB notes
        out_slugs = _related_targets(props)
        for v in props.get("verified_against") or []:
            s = str(v).strip().strip("[]").strip()
            if s:
                out_slugs.add(s)
        body_slugs = extract_wikilinks(body) & set(idx.keys())
        out_slugs |= body_slugs

        def _enrich(s: str) -> dict:
            meta = idx.get(s, {})
            return {"slug": s, "kind": meta.get("kind", ""), "domain": meta.get("domain", ""), "in_kb": s in idx}

        outgoing = sorted([_enrich(s) for s in out_slugs if s != slug], key=lambda r: (not r["in_kb"], r["kind"], r["slug"]))

        # Reference-landing aggregation: when this note is `kind: reference` with a
        # declared `standard_id:`, surface every clause note that belongs to that
        # source for the UI's "Clauses" sidebar.
        clauses: list[dict] = []
        if props.get("kind") == "reference" and props.get("standard_id"):
            clauses = self._clauses_for_standard(str(props["standard_id"]))

        return {
            "slug": slug,
            "path": path,
            "name": match.get("name", ""),
            "properties": props,
            "body": body,
            "backlinks": self._kb_backlinks(slug, all_notes),
            "outgoing": outgoing,
            "clauses": clauses,
            "implemented_in_status": self._check_implemented_in(props.get("implemented_in") or []),
        }

    async def health(self) -> dict:
        broken: list[dict] = []
        orphans: list[dict] = []
        formulas_missing_verification: list[dict] = []
        uncited_references: list[dict] = []
        repo_root = Path(self.kernel.config.path).parent
        all_notes = self._all_notes()
        for n in all_notes:
            s = self._summarize(n)
            props = n.get("properties", {}) or {}
            for ref in props.get("implemented_in") or []:
                kind, code_path, _ = self._parse_impl_ref(ref)
                # `method:` refs are pointer-only; nothing to verify.
                if kind == "method":
                    continue
                if code_path and not (repo_root / code_path).exists():
                    broken.append({"slug": s["slug"], "ref": ref})
            if s["kind"] == "formula" and not (props.get("verified_against") or []):
                formulas_missing_verification.append(s)
            if s["kind"] == "clause":
                # Clause notes are 'uncited' iff no other KB note cites them
                # (via parsed references:, related:, or body wikilinks).
                if not self._kb_backlinks(s["slug"], all_notes):
                    uncited_references.append(s)
                continue
            if not self._kb_backlinks(s["slug"], all_notes) and not (props.get("related") or []):
                orphans.append(s)
        return {
            "broken_implemented_in": broken,
            "formulas_missing_verification": formulas_missing_verification,
            "orphans": orphans,
            "uncited_references": uncited_references,
        }

    # ---------- API ----------

    @web_route("GET", "/api/domains")
    async def api_domains(self, request):
        return self.list_domains()

    @web_route("GET", "/api/notes")
    async def api_notes(self, request):
        return self.list_notes(
            domain=request.query_params.get("domain", ""),
            kind=request.query_params.get("kind", ""),
            topic=request.query_params.get("topic", ""),
        )

    @web_route("GET", "/api/notes/{slug}")
    async def api_note_detail(self, request):
        slug = request.path_params.get("slug", "")
        result = await self.get_note(slug)
        if isinstance(result, dict) and "error" not in result:
            await self.emit("kb:viewed", {"slug": slug})
        return result

    @web_route("GET", "/api/notes/{slug}/section/{section:path}")
    async def api_note_section(self, request):
        """Return one section of a KB note's body.

        `section` is the heading text (`## Section Name`) — case-sensitive match
        on the visible heading text after the `#`s. Used by EOS_UI.transclude when
        a noteRef carries a `#section` anchor.
        """
        slug = request.path_params.get("slug", "")
        section = request.path_params.get("section", "")
        all_notes = self._all_notes()
        match = next((n for n in all_notes if _slug_of(n.get("path", "")) == slug), None)
        if not match:
            return {"error": "not found", "slug": slug}
        path = match.get("path", "")
        body = self.vault_read_section(path, section) or ""
        props = self.vault_get_properties(path) or {}
        return {
            "slug": slug,
            "section": section,
            "title": props.get("title") or slug.replace("-", " "),
            "kind": props.get("kind", ""),
            "body": body,
            "path": path,
        }

    @web_route("GET", "/api/health")
    async def api_health(self, request):
        return await self.health()

    @web_route("GET", "/api/references")
    async def api_references(self, request):
        """Return the (standard, edition, clause) → slug index for chatbot / agents."""
        return {
            "references": [
                {
                    "standard": std,
                    "edition": ed,
                    "clause": cl,
                    "slug": slug,
                }
                for (std, ed, cl), slug in sorted(self._ref_index.items())
            ],
            "count": len(self._ref_index),
        }

    @web_route("GET", "/api/implementations/{code_path:path}")
    async def api_implementations(self, request):
        """Reverse-lookup: which KB notes declare `implemented_in:` matching the given code path?

        Path is normalized by stripping any leading `/` and the optional `::symbol` suffix.
        Returns `{code_path, slugs: [{slug, kind, title}]}`. Powers PR-review surfaces
        and runtime "explain this code" features.
        """
        raw = request.path_params.get("code_path", "")
        code_path = str(raw).lstrip("/").split("::", 1)[0].strip()
        slugs = self._implementation_index.get(code_path, [])
        # Enrich with kind/title so callers don't need a second round-trip.
        enriched: list[dict] = []
        notes_by_slug = {_slug_of(n.get("path", "")): n for n in self._all_notes()}
        for s in slugs:
            n = notes_by_slug.get(s)
            if not n:
                continue
            props = n.get("properties", {}) or {}
            enriched.append({
                "slug": s,
                "kind": props.get("kind", ""),
                "title": props.get("title") or s.replace("-", " "),
                "path": n.get("path", ""),
            })
        return {"code_path": code_path, "slugs": enriched, "count": len(enriched)}

    # ---------- helpers ----------

    def _kb_backlinks(self, slug: str, all_notes: list[dict]) -> list[dict]:
        """Find KB notes that cite `slug` via frontmatter `related`, body wikilinks,
        or — for `kind: reference` targets — via parsed `references:` citations.

        Returns enriched rows {slug, kind, domain} so the UI can color/route.
        Scoped to the KB tag set so we don't drag in unrelated vault references.
        """
        # Citation-derived citers (for reference notes) come from the precomputed index.
        citation_citers = self._inbound_citations.get(slug, set())
        out: list[dict] = []
        seen: set[str] = set()
        for n in all_notes:
            other_slug = _slug_of(n.get("path", ""))
            if other_slug == slug or not other_slug:
                continue
            props = n.get("properties", {}) or {}
            cites = slug in _related_targets(props) or other_slug in citation_citers
            if not cites:
                body = self.vault_read_body(n.get("path", ""))
                cites = slug in extract_wikilinks(body)
            if cites and other_slug not in seen:
                seen.add(other_slug)
                out.append({
                    "slug": other_slug,
                    "kind": props.get("kind", ""),
                    "domain": props.get("domain", ""),
                })
        out.sort(key=lambda r: (r["kind"], r["slug"]))
        return out

    def _kind_index(self, all_notes: list[dict]) -> dict[str, dict]:
        """Map slug -> {kind, domain} for quick lookups (outgoing-edge enrichment)."""
        return {
            _slug_of(n.get("path", "")): {
                "kind": (n.get("properties", {}) or {}).get("kind", ""),
                "domain": (n.get("properties", {}) or {}).get("domain", ""),
            }
            for n in all_notes
            if _slug_of(n.get("path", ""))
        }

    @staticmethod
    def _parse_impl_ref(ref) -> tuple[str, str, str]:
        """Split an `implemented_in:` entry into (kind, code_path, symbol).

        Vault frontmatter is flat-only, so nested entries like
            implemented_in:
              - path: engines/thermal/fem/solver.py
              - method: cables.ampacity.fem
        get serialized as literal strings `"path: ..."` / `"method: ..."`. We
        strip the prefix so the existence check sees a real repo path. `method:`
        refs are pointer-only (no file path to verify); callers should skip the
        existence check for kind="method".
        """
        sref = str(ref).strip()
        kind = "path"
        body = sref
        if sref.lower().startswith("path:"):
            body = sref.split(":", 1)[1].strip()
        elif sref.lower().startswith("method:"):
            body = sref.split(":", 1)[1].strip()
            kind = "method"
        # Accept several symbol separators: canonical `::`, em/en/hyphen dashes
        # used in free-text refs like "foo.py — symbol_a, symbol_b".
        code_path = body
        sym = ""
        for sep in ("::", " — ", " – ", " - "):
            if sep in body:
                code_path, _, sym = body.partition(sep)
                break
        return kind, code_path.strip(), sym.strip()

    def _check_implemented_in(self, refs: list) -> list[dict]:
        repo_root = Path(self.kernel.config.path).parent
        out = []
        for ref in refs:
            kind, code_path, sym = self._parse_impl_ref(ref)
            sref = str(ref).strip()
            if kind == "method":
                out.append({"ref": sref, "path": "", "symbol": code_path, "kind": "method", "exists": None})
                continue
            ok = bool(code_path) and (repo_root / code_path).exists()
            out.append({"ref": sref, "path": code_path, "symbol": sym, "kind": "path", "exists": ok})
        return out

    # ---------- documents (paragraph composition over notes) ----------

    def _parse_paragraphs(self, props: dict) -> list[dict]:
        """Decode paragraphs from the document's flat-frontmatter field."""
        paras = self.vault_decode_json(props.get("paragraphs_json"), default=[])
        return paras if isinstance(paras, list) else []

    def _encode_paragraphs(self, paras: list[dict]) -> str:
        return self.vault_encode_json(paras)

    @staticmethod
    def _normalize_paragraphs(paras) -> list[dict]:
        """Accept paragraphs in the docs.html-native shape `{title, content,
        noteRefs}` OR the LLM-friendly shape `{heading, text}` that personas
        and ad-hoc consumers tend to emit. Rewrite the latter into the
        former so the docs viewer renders them. Idempotent — values already
        on `title`/`content` are preserved when both shapes are present.
        """
        if not isinstance(paras, list):
            return []
        out: list[dict] = []
        for p in paras:
            if not isinstance(p, dict):
                continue
            title = p.get("title") or p.get("heading") or ""
            content = p.get("content") or p.get("text") or ""
            note_refs = p.get("noteRefs") or p.get("blockRefs") or []
            norm = {
                "title": title,
                "content": content,
                "noteRefs": list(note_refs) if isinstance(note_refs, list) else [],
            }
            out.append(norm)
        return out

    @staticmethod
    def _para_refs(para: dict) -> list[str]:
        """Return paragraph references, preferring `noteRefs` and falling back to
        legacy `blockRefs` for in-flight content authored before the rename."""
        refs = para.get("noteRefs") or para.get("blockRefs") or []
        return [str(r) for r in refs if r]

    def list_docs(self) -> dict:
        rows = self.vault_query(tags=["kb"]) or []
        out = []
        for n in rows:
            props = n.get("properties", {}) or {}
            if props.get("kind") != "doc":
                continue
            slug = _slug_of(n.get("path", ""))
            paras = self._parse_paragraphs(props)
            out.append({
                "id": slug,
                "slug": slug,
                "path": n.get("path", ""),
                "title": props.get("title") or slug.replace("-", " "),
                "paragraph_count": len(paras),
                "note_refs": sum(len(self._para_refs(p)) for p in paras),
                "updated": props.get("updated", ""),
            })
        out.sort(key=lambda r: r["slug"])
        return {"docs": out, "count": len(out)}

    async def get_doc(self, slug: str) -> dict:
        rel = self._doc_path(slug)
        if not (self.vault_root / rel).exists():
            return {"error": "not found", "slug": slug}
        props = self.vault_get_properties(rel) or {}
        body = self.vault_read_body(rel)
        return {
            "slug": slug,
            "path": rel,
            "title": props.get("title") or slug.replace("-", " "),
            "paragraphs": self._parse_paragraphs(props),
            "body": body,
            "properties": props,
        }

    async def create_doc(self, title: str, paragraphs: list[dict] | None = None) -> dict:
        title = (title or "").strip()
        if not title:
            return {"error": "title required"}
        slug = _slugify(title)
        rel = self._doc_path(slug)
        if (self.vault_root / rel).exists():
            return {"error": "doc already exists", "slug": slug}
        paras = self._normalize_paragraphs(paragraphs or [])
        fm = {
            "title": title,
            "tags": ["kb"],
            "kind": "doc",
            "paragraphs_json": self._encode_paragraphs(paras),
            "created": datetime.now().date().isoformat(),
            "updated": datetime.now().date().isoformat(),
        }
        self.vault_create_note(rel, fm, f"# {title}\n\nEdit paragraphs in the KB Documents tab.\n")
        await self.emit("kb:doc_created", {"slug": slug, "path": rel})
        return {"ok": True, "slug": slug, "path": rel}

    async def update_doc(self, slug: str, title: str | None = None, paragraphs: list[dict] | None = None) -> dict:
        rel = self._doc_path(slug)
        if not (self.vault_root / rel).exists():
            return {"error": "not found", "slug": slug}
        updates: dict = {"updated": datetime.now().date().isoformat()}
        if title:
            updates["title"] = title
        if paragraphs is not None:
            updates["paragraphs_json"] = self._encode_paragraphs(self._normalize_paragraphs(paragraphs))
        self.vault_update(rel, updates)
        await self.emit("kb:doc_updated", {"slug": slug})
        return {"ok": True, "slug": slug}

    async def render_doc(self, slug: str) -> dict:
        """Render a doc by resolving every noteRef to its current body.

        Each paragraph's `noteRefs` (with `blockRefs` accepted for legacy content)
        may be a bare slug or `slug#section-name`. The bare-slug form pulls the
        whole note body; the anchored form pulls just the named `##` section via
        `BaseApp.vault_read_section`.

        Returns paragraphs annotated with
            rendered: [{ref, title, body, path, kind, section?, missing?}]
        so the client can render block-by-block without re-fetching.
        """
        doc = await self.get_doc(slug)
        if "error" in doc:
            return doc
        idx = self._slug_to_path()
        for para in doc.get("paragraphs") or []:
            rendered: list[dict] = []
            for raw in self._para_refs(para):
                ref = raw.strip()
                target_slug, _, section = ref.partition("#")
                target_slug = target_slug.strip()
                section = section.strip() or None
                note_path = idx.get(target_slug)
                if not note_path:
                    rendered.append({"ref": ref, "slug": target_slug, "section": section, "missing": True})
                    continue
                props = self.vault_get_properties(note_path) or {}
                if section:
                    body = self.vault_read_section(note_path, section) or ""
                else:
                    body = self.vault_read_body(note_path)
                rendered.append({
                    "ref": ref,
                    "slug": target_slug,
                    "section": section,
                    "title": props.get("title") or target_slug.replace("-", " "),
                    "kind": props.get("kind", ""),
                    "body": body,
                    "path": note_path,
                })
            para["rendered"] = rendered
        return doc

    def _slug_to_path(self) -> dict[str, str]:
        """Build a slug → path map across every kb-tagged note. Used by render_doc."""
        out: dict[str, str] = {}
        for n in self._all_notes():
            slug = _slug_of(n.get("path", ""))
            if slug:
                out[slug] = n.get("path", "")
        return out

    # ---------- block/doc API ----------

    @web_route("GET", "/api/docs")
    async def api_docs(self, request):
        return self.list_docs()

    @web_route("GET", "/api/docs/{slug}")
    async def api_doc_detail(self, request):
        return await self.render_doc(request.path_params.get("slug", ""))

    @web_route("POST", "/api/docs")
    async def api_doc_create(self, request):
        body = await request.json()
        return await self.create_doc(
            title=body.get("title", ""),
            paragraphs=body.get("paragraphs"),
        )

    @web_route("POST", "/api/docs/{slug}/update")
    async def api_doc_update(self, request):
        body = await request.json()
        slug = request.path_params.get("slug", "")
        return await self.update_doc(
            slug,
            title=body.get("title"),
            paragraphs=body.get("paragraphs"),
        )

    async def delete_doc(self, slug: str) -> dict:
        rel = self._doc_path(slug)
        abs_path = self.vault_root / rel
        if not abs_path.exists():
            return {"error": "not found", "slug": slug}
        abs_path.unlink()
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            vi.index_file(rel)
        await self.emit("kb:doc_deleted", {"slug": slug})
        return {"ok": True, "slug": slug}

    @web_route("DELETE", "/api/docs/{slug}")
    async def api_doc_delete(self, request):
        return await self.delete_doc(request.path_params.get("slug", ""))

    # ---------- action templates ----------

    SUMMARIZE_NOTES_SYSTEM = (
        "You compress N knowledge notes into one summary. The user picked these "
        "notes because they share a theme — find that thread and surface it.\n\n"
        "Do NOT:\n"
        "- Restate every note verbatim or in order.\n"
        "- Hedge ('this seems to suggest', 'one might consider…').\n"
        "- Add citations, links, or note slugs — the reader has them already.\n"
        "- Output headers, frontmatter, or 'Summary:' prefixes."
    )

    async def action_summarize_notes(self, items: list | None = None, style: str = "bullet", **_) -> dict:
        """Action template: collapse N KB notes into one summary.

        items — list of note slugs (or note paths; basename is taken as slug).
        style — "bullet" (5-9 tight bullets) or "paragraph" (3-5 prose sentences).
        Returns {summary, used_slugs, missing} so the UI can show what landed.
        """
        slugs = []
        for it in items or []:
            s = str(it).strip()
            if not s:
                continue
            slugs.append(Path(s).stem if "/" in s or "\\" in s else s)
        if not slugs:
            return {"error": "no notes selected"}
        bodies = []
        missing = []
        for s in slugs:
            n = await self.get_note(s)
            if "error" in n:
                missing.append(s)
            else:
                title = (n.get("properties") or {}).get("title") or n.get("name") or s
                bodies.append(f"## {title}\n{n.get('body','')}\n")
        if not bodies:
            return {"error": "no readable notes", "missing": missing}
        joined = "\n".join(bodies)
        if style == "paragraph":
            tail = "Write 3-5 sentences as a single paragraph. No bullets."
        else:
            tail = "Write 5-9 tight bullets, each a single line. No prose."
        prompt = f"Summarize these notes. {tail}\n\nNotes:\n{joined}"
        summary = await self.think(
            prompt,
            system=self.SUMMARIZE_NOTES_SYSTEM,
            domain="text",
            temperature=0.4,
        )
        return {
            "summary": summary,
            "used_slugs": [s for s in slugs if s not in missing],
            "missing": missing,
            "style": style,
        }

    # ---------- hub panel ----------

    async def panel_summary(self) -> dict | None:
        notes = self._all_notes()
        if not notes:
            return None
        domains = {(n.get("properties", {}) or {}).get("domain") for n in notes}
        domains.discard(None)
        return {
            "label": "Knowledge Base",
            "value": f"{len(notes)} notes",
            "detail": f"{len(domains)} domain{'s' if len(domains) != 1 else ''}",
            "link": "/kb/",
        }

    # ---------- CLI ----------

    @cli_command("kb")
    async def cli_kb(self, action: str = "list", domain: str = "", kind: str = ""):
        """eos kb {list|domains|note <slug>|health}"""
        if action == "domains":
            for d in self.list_domains()["domains"]:
                self.print_rich(f"[bold]{d['domain']}[/bold]  {d['total']} notes  {d['by_kind']}")
        elif action == "health":
            data = await self.health()
            self.print_rich(f"Broken implemented_in: {len(data['broken_implemented_in'])}")
            self.print_rich(
                f"Formulas missing verification: {len(data['formulas_missing_verification'])}"
            )
            self.print_rich(f"Orphans: {len(data['orphans'])}")
        elif action == "note":
            data = await self.get_note(domain or kind)
            if "error" in data:
                self.print_rich(f"[red]{data['error']}[/red]")
            else:
                self.print_rich(f"[bold]{data['name']}[/bold]")
                self.print_rich(f"  path: {data['path']}")
                self.print_rich(f"  backlinks: {len(data['backlinks'])}")
        else:
            data = self.list_notes(domain=domain, kind=kind)
            for n in data["notes"]:
                self.print_rich(f"  [{n['kind']:8}] {n['slug']:40} [dim]{n['domain']}[/dim]")
            self.print_rich(f"\n[dim]{data['count']} note(s)[/dim]")
