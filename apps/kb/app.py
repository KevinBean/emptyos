"""Knowledge Base — vault-resident, domain-general note browser.

Reads notes tagged `kb` from the vault, partitioned by `domain` (power-systems,
cable-thermal, ...) and `kind` (concept, formula, standard, case, lesson).
Computes backlinks across the KB (frontmatter `related` + body wikilinks) and
checks `implemented_in` code paths against the repo.
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, extract_wikilinks, web_route

KINDS = ("concept", "formula", "standard", "case", "lesson")


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


class KBApp(BaseApp):
    async def setup(self):
        await super().setup()

    # ---------- core queries ----------

    def _all_notes(self) -> list[dict]:
        return self.vault_query(tags=["kb"]) or []

    def _summarize(self, n: dict) -> dict:
        props = n.get("properties", {}) or {}
        return {
            "slug": _slug_of(n.get("path", "")),
            "path": n.get("path", ""),
            "name": n.get("name", ""),
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

        return {
            "slug": slug,
            "path": path,
            "name": match.get("name", ""),
            "properties": props,
            "body": body,
            "backlinks": self._kb_backlinks(slug, all_notes),
            "outgoing": outgoing,
            "implemented_in_status": self._check_implemented_in(props.get("implemented_in") or []),
        }

    async def health(self) -> dict:
        broken: list[dict] = []
        orphans: list[dict] = []
        formulas_missing_verification: list[dict] = []
        repo_root = Path(self.kernel.config.path).parent
        all_notes = self._all_notes()
        for n in all_notes:
            s = self._summarize(n)
            props = n.get("properties", {}) or {}
            for ref in props.get("implemented_in") or []:
                code_path = str(ref).split("::", 1)[0].strip()
                if code_path and not (repo_root / code_path).exists():
                    broken.append({"slug": s["slug"], "ref": ref})
            if s["kind"] == "formula" and not (props.get("verified_against") or []):
                formulas_missing_verification.append(s)
            if not self._kb_backlinks(s["slug"], all_notes) and not (props.get("related") or []):
                orphans.append(s)
        return {
            "broken_implemented_in": broken,
            "formulas_missing_verification": formulas_missing_verification,
            "orphans": orphans,
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

    @web_route("GET", "/api/health")
    async def api_health(self, request):
        return await self.health()

    # ---------- helpers ----------

    def _kb_backlinks(self, slug: str, all_notes: list[dict]) -> list[dict]:
        """Find KB notes that cite `slug` via frontmatter `related` or body wikilinks.

        Returns enriched rows {slug, kind, domain} so the UI can color/route.
        Scoped to the KB tag set so we don't drag in unrelated vault references.
        """
        out: list[dict] = []
        for n in all_notes:
            other_slug = _slug_of(n.get("path", ""))
            if other_slug == slug or not other_slug:
                continue
            props = n.get("properties", {}) or {}
            cites = slug in _related_targets(props)
            if not cites:
                body = self.vault_read_body(n.get("path", ""))
                cites = slug in extract_wikilinks(body)
            if cites:
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

    def _check_implemented_in(self, refs: list) -> list[dict]:
        repo_root = Path(self.kernel.config.path).parent
        out = []
        for ref in refs:
            sref = str(ref)
            code_path, _, sym = sref.partition("::")
            ok = bool(code_path) and (repo_root / code_path.strip()).exists()
            out.append({"ref": sref, "path": code_path.strip(), "symbol": sym, "exists": ok})
        return out

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
