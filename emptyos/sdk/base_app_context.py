"""BaseApp context family — agent-bus context, memory recall, 4D timeline.

Extracted from base_app.py to keep the BaseApp spine atomic (P4 Atomic,
CLAUDE.md rule 4). Source split only — every function here is re-bound onto
``BaseApp`` in base_app.py's class body, so the public API (``self.bus_*``,
``self.recall``, ``self.timeline``, …) is unchanged. Owns: agent-context-bus
loading (``bus_context`` / ``bus_index`` / ``bus_menu`` / ``bus_assemble``),
memory ``recall`` + episodic memory (``remember_episode`` / ``recall_episodes``),
the deep-link ``app_link`` builder, the tabular ``query`` / ``query_notes``
fail-soft wrappers, and the per-entity 4D ``timeline`` aggregator.

Cross-module callers reach methods here via ``self.X`` after re-binding
(``timeline`` reaches spine ``git_log`` and vault-family methods via ``self``).
Reaches into other modules: no cross-module reach (all via ``self``).
Do not import from ``.base_app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base_app import BaseApp  # noqa: F401 — for type hints only


# ─── Bind to BaseApp class as ────────────────────────────────────────
#   bus_context      = _ctx.bus_context
#   bus_index        = _ctx.bus_index
#   bus_menu         = _ctx.bus_menu
#   bus_assemble     = _ctx.bus_assemble
#   recall           = _ctx.recall
#   _episodic_store  = _ctx._episodic_store
#   remember_episode = _ctx.remember_episode
#   recall_episodes  = _ctx.recall_episodes
#   app_link         = _ctx.app_link
#   query            = _ctx.query
#   query_notes      = _ctx.query_notes
#   timeline         = _ctx.timeline
# Adding a new method here? Add a matching binding line in base_app.py.
# ─────────────────────────────────────────────────────────────────────


def bus_context(
    self,
    *,
    sections: list[str] | None = None,
    rules: list[str] | None = None,
    skills: list[str] | None = None,
    workspace: Path | str | None = None,
    missing: str = "skip",
) -> str:
    """Compose a system-prompt block from the Agent Context Bus.

    Reads named entries from ``<workspace>/.agent-bus/`` (the canonical
    store managed by :mod:`emptyos.sdk.agent_bus`) and concatenates them
    as a single markdown string suitable for passing to ``self.think(
    ..., system=...)``.

    Use this when an internal think surface (an app's ``self.think``, an
    ``eos staff`` cron agent, an ``eos rooms`` agent participant) needs
    the same architectural awareness an external CLI like claude-cli would
    get from booting into ``CLAUDE.md``. Selective by design — don't drag
    the entire boot prompt into every think call; pull only the rules
    and sections the agent actually needs.

    Args:
        sections: section keys under ``.agent-bus/sections/<key>.md``
            (e.g. ``"claude-architecture"``, ``"claude-development-rules"``).
        rules: rule names under ``.agent-bus/rules/<name>.md`` — the
            ``.md`` suffix is optional.
        skills: skill names under ``.agent-bus/skills/<name>/SKILL.md``.
        workspace: workspace root. Defaults to :py:attr:`repo_root`. Pass
            ``self.vault_root`` to read the vault's bus (e.g. for a staff
            agent that works in vault terms, not EOS-internals terms).
        missing: ``"skip"`` (default) to silently drop missing entries,
            or ``"error"`` to raise ``KeyError`` on the first missing one.

    Returns:
        A markdown string with one ``## <kind>: <name>`` heading per
        included entry. Empty string when nothing matched. Never raises
        unless ``missing="error"``.

    Example::

        ctx = self.bus_context(
            rules=["vault-operator", "multi-module-apps"],
            sections=["claude-architecture"],
        )
        answer = await self.think(prompt, system=ctx + "\\n\\n" + persona)
    """
    from emptyos.sdk.agent_bus import load_rule, load_section, load_skill

    ws = Path(workspace) if workspace is not None else self.repo_root
    parts: list[str] = []

    for key in sections or []:
        body = load_section(ws, key)
        if body is None:
            if missing == "error":
                raise KeyError(f"bus section not found: {key}")
            continue
        parts.append(f"## section: {key}\n\n{body.rstrip()}")

    for name in rules or []:
        body = load_rule(ws, name)
        if body is None:
            if missing == "error":
                raise KeyError(f"bus rule not found: {name}")
            continue
        display = name[:-3] if name.endswith(".md") else name
        parts.append(f"## rule: {display}\n\n{body.rstrip()}")

    for name in skills or []:
        body = load_skill(ws, name)
        if body is None:
            if missing == "error":
                raise KeyError(f"bus skill not found: {name}")
            continue
        parts.append(f"## skill: {name}\n\n{body.rstrip()}")

    return "\n\n".join(parts)


def bus_index(
    self,
    *,
    rules: bool = True,
    sections: bool = True,
    skills: bool = True,
    workspace: Path | str | None = None,
) -> list[dict]:
    """The L0 abstract menu — ``[{kind, name, abstract}]`` for the bus.

    A cheap, token-light listing of *what's available* so an agent can scan
    one-liners and pick which entries to load in full. The drill step is the
    existing :meth:`bus_context` — ``bus_index()`` is the "scan", then
    ``bus_context(rules=[picked])`` is the "load". Reads the same
    ``.agent-bus/`` store, so menu and drill never disagree.

    Each abstract comes from the entry's ``abstract:`` frontmatter, then a
    skill's ``description:``, then its first heading line. Never raises.
    """
    ws = Path(workspace) if workspace is not None else self.repo_root
    from emptyos.sdk.agent_bus import list_bus_entries

    try:
        return list_bus_entries(ws, rules=rules, sections=sections, skills=skills)
    except Exception:
        return []


def bus_menu(
    self,
    *,
    rules: bool = True,
    sections: bool = True,
    skills: bool = True,
    workspace: Path | str | None = None,
) -> str:
    """:meth:`bus_index` rendered as a compact markdown menu for prompts.

    One line per entry: ``- <kind>:<name> — <abstract>``. Inject this into a
    ``self.think`` system prompt and ask the model which names it needs, then
    load those with :meth:`bus_context`.
    """
    return "\n".join(
        f"- {e['kind']}:{e['name']} — {e['abstract']}"
        for e in self.bus_index(
            rules=rules, sections=sections, skills=skills, workspace=workspace
        )
    )


def bus_assemble(
    self,
    boot_file: str = "CLAUDE.md",
    *,
    workspace: Path | str | None = None,
) -> str:
    """Return the full reassembled text of a boot file from the bus.

    Equivalent bytes to what ``eos bus ripple`` would write to disk for
    ``boot_file``. Use sparingly — the full CLAUDE.md is large; most
    ``self.think`` callers want :meth:`bus_context` instead.

    Returns ``""`` if the bus isn't initialized for this workspace.
    """
    from emptyos.sdk.agent_bus import assemble_boot_file, load_toml

    ws = Path(workspace) if workspace is not None else self.repo_root
    bus_dir = ws / ".agent-bus"
    manifest_path = bus_dir / "manifest.toml"
    if not manifest_path.exists():
        return ""
    manifest = load_toml(manifest_path)
    slug = boot_file.split(".")[0].lower()
    key = f"assembly_{slug}"
    if key not in manifest:
        return ""
    try:
        return assemble_boot_file(manifest, bus_dir / "sections", key)
    except FileNotFoundError:
        return ""


async def recall(
    self,
    query: str,
    *,
    tags: list[str] | None = None,
    items: list[dict] | None = None,
    text_fn=None,
    top_k: int = 8,
    half_life_days: float = 30.0,
    weights: tuple[float, float, float] = (1.2, 0.3, 0.1),
    min_sim: float = 0.0,
    now=None,
) -> list[dict]:
    """Rank vault notes (or supplied items) by blended
    similarity + recency-decay + salience.

        score = w_sim·cosine(query) + w_time·ebbinghaus(age) + w_emo·(salience/10)

    Borrowed from moeru-ai/airi's memory scorer (issue #879): a recall that
    surfaces *relevant + recent + emotionally salient* notes instead of pure
    nearest-neighbour or substring match. The inverse verb is plain note
    deletion — callers already own their forget paths.

    Give exactly one input mode:
      • ``tags=[...]``        — candidates from ``vault_query(tags)``; embed
        text is ``description`` + a body excerpt; ``created``/``updated``/
        ``salience``/``mood`` read from frontmatter.
      • ``items=[...]`` (+ optional ``text_fn``) — caller supplies dicts;
        ``text_fn(item)`` is the embed text (falls back to item
        ``text``/``body``/``description``); recency + salience read from item
        keys or ``item['properties']``.

    Degrades gracefully: when ``embeddings_available`` is False the
    similarity term is 0 and ranking falls back to recency + salience —
    never raises. ``min_sim`` drops weak semantic hits *before* blending
    (only applied when embeddings are on; set 0 to keep every candidate).

    Returns up to ``top_k`` dicts — the original row/item augmented with
    ``score``/``sim``/``recency``/``salience`` — sorted by score desc.
    """
    from emptyos.sdk.embeddings import cosine
    from emptyos.sdk.utils import (
        ebbinghaus_decay,
        iso_age_days,
        salience_from_mood,
    )

    if (tags is None) == (items is None):
        raise ValueError("recall() needs exactly one of tags= or items=")

    w_sim, w_time, w_emo = weights

    def _salience_of(src: dict) -> float:
        raw = src.get("salience")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return 0.0
        return salience_from_mood(src.get("mood"))

    cands: list[dict] = []
    if tags is not None:
        for row in self.vault_query(tags=tags) or []:
            rel = row.get("path") or ""
            props = row.get("properties") or {}
            body = ""
            try:
                raw = self.vault_read_at(rel)
                if raw.startswith("---"):
                    end = raw.find("\n---", 3)
                    raw = raw[end + 4:] if end != -1 else raw
                body = raw.strip()
            except Exception:
                body = ""
            text = " ".join(p for p in [props.get("description") or "", body] if p)[:1500]
            cands.append({
                "_row": row,
                "_text": text,
                "_created": props.get("created") or "",
                "_updated": props.get("updated") or props.get("created") or "",
                "_salience": _salience_of(props),
            })
    else:
        for it in items or []:
            props = it.get("properties") if isinstance(it.get("properties"), dict) else it
            if text_fn is not None:
                text = text_fn(it) or ""
            else:
                text = it.get("text") or it.get("body") or it.get("description") or ""
            cands.append({
                "_row": it,
                "_text": str(text)[:1500],
                "_created": props.get("created") or "",
                "_updated": props.get("updated") or props.get("created") or "",
                "_salience": _salience_of(props),
            })

    if not cands:
        return []

    embeds_on = self.embeddings_available and bool((query or "").strip())
    sims = [0.0] * len(cands)
    if embeds_on:
        q_emb = await self.embed_text(query)
        c_embs = await self.embed_texts([c["_text"] for c in cands])
        sims = [cosine(q_emb, e) for e in c_embs]

    scored: list[dict] = []
    for c, sim in zip(cands, sims):
        if embeds_on and sim < min_sim:
            continue
        age = iso_age_days(c["_updated"] or c["_created"], now)
        recency = ebbinghaus_decay(age, half_life_days) if age is not None else 0.0
        sal = max(-10.0, min(10.0, c["_salience"]))
        score = w_sim * sim + w_time * recency + w_emo * (sal / 10.0)
        out = dict(c["_row"])
        out.update({"score": score, "sim": sim, "recency": recency, "salience": sal})
        scored.append(out)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


# ── Episodic memory — the worker remembers its own past sessions ──────────
# Distilled session digests in data/apps/<id>/episodic/episodes.jsonl.
# recall_episodes reuses recall() above (similarity+recency+salience), so
# there's no new ranking code. Agent telemetry → data/, never the vault.


def _episodic_store(self, scope: str = ""):
    """Resolve an EpisodicStore for this app, optionally scoped to a subkey
    (e.g. a staff agent id) so distinct actors keep isolated histories."""
    from emptyos.sdk.episodic import EpisodicStore, _safe_id

    base = self.data_dir if not scope else self.data_subdir("scopes", _safe_id(scope))
    return EpisodicStore(base)


def remember_episode(
    self,
    session_id: str,
    *,
    task: str,
    outcome: str,
    decisions: "list | tuple" = (),
    salience: float = 0.0,
    scope: str = "",
) -> bool:
    """Append a distilled episode (one finished session) to this app's log.

    The *distillation* — turning a raw session into a one-paragraph
    ``task``/``outcome``/``decisions`` digest — is the caller's job (usually
    one bounded ``think()`` call). This just persists the shape. Fail-soft:
    returns True/False, never raises. ``scope`` isolates a sub-actor's log
    (e.g. one staff agent) under ``<data_dir>/scopes/<scope>/``.
    """
    from emptyos.sdk.episodic import episode_record

    rec = episode_record(
        session_id=session_id,
        task=task,
        outcome=outcome,
        decisions=decisions,
        salience=salience,
        app=self.manifest.id,
    )
    return self._episodic_store(scope).write_episode(rec)


async def recall_episodes(
    self,
    query: str,
    *,
    top_k: int = 5,
    half_life_days: float = 30.0,
    scan_limit: int = 200,
    scope: str = "",
) -> list[dict]:
    """Rank this app's own past episodes by relevance to ``query``.

    Loads the ``scan_limit`` newest episodes and ranks them via
    ``self.recall(items=...)`` — similarity + recency-decay + salience,
    degrading to recency+salience when embeddings are off. Returns up to
    ``top_k`` episode dicts augmented with ``score``/``sim``/``recency``.
    Empty list when there's no history (never raises). ``scope`` reads a
    sub-actor's isolated log (mirrors ``remember_episode``).
    """
    episodes = self._episodic_store(scope).read_episodes(limit=scan_limit)
    if not episodes:
        return []

    # Episodes are newest-first; a session may have several (one per turn).
    # Keep only the newest per session so a chatty session doesn't crowd out
    # other sessions in the ranking.
    seen: set = set()
    deduped: list[dict] = []
    for ep in episodes:
        key = ep.get("session_id")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ep)
    episodes = deduped

    def _text(ep: dict) -> str:
        parts = [ep.get("task") or "", ep.get("outcome") or ""]
        parts.extend(ep.get("decisions") or [])
        return " ".join(p for p in parts if p)

    return await self.recall(
        query,
        items=episodes,
        text_fn=_text,
        top_k=top_k,
        half_life_days=half_life_days,
    )


def app_link(
    self,
    prefix: str,
    params: dict | None = None,
    *,
    label: str = "Open",
    run: bool = True,
) -> dict:
    """Build a stateful deep-link into an app: a shareable URL that opens the
    target app with `params` carried as query string + (when `run`) `run=1`
    so the app prefills its form and auto-computes.

    The producer half of the "open the answer in its real tool, with the
    case loaded" pattern (see ``.claude/rules/deep-link-to-app.md``). An
    intent/verb that already holds its inputs returns one of these in its
    ``link`` field; the companion (rail + Aura) renders it as a clickable
    chip, and the target page consumes the params via ``EOS_UI.prefillForm``.

    Pass **inputs only** — the target re-computes its own result with its
    verified engine, so the number shown there is never taken on faith from
    the chat. ``None``/empty-string params are dropped. The URL is
    self-contained + bookmarkable (the params ARE the state — no server
    record), so it survives restart / sharing to a colleague.

    Returns ``{"text": label, "href": "/<prefix>/?<query>[&run=1]"}`` —
    the existing voice-intent ``link`` shape. ``prefix`` may be given with or
    without surrounding slashes (``"short-circuit"`` / ``"/short-circuit/"``).
    """
    from urllib.parse import urlencode

    pairs = []
    for k, v in (params or {}).items():
        if v is None:
            continue
        s = v if isinstance(v, str) else (
            "true" if v is True else "false" if v is False else str(v)
        )
        if s == "":
            continue
        pairs.append((k, s))
    if run:
        pairs.append(("run", "1"))
    slug = prefix.strip("/")
    query = urlencode(pairs)
    href = f"/{slug}/" + (f"?{query}" if query else "")
    return {"text": label, "href": href}


def query(
    self,
    sql: str,
    *,
    tables: dict[str, list[dict]] | None = None,
    files: dict | None = None,
    params: list | None = None,
) -> list[dict] | dict:
    """Ephemeral in-memory SQL over row-dicts and/or CSV / read-only SQLite files.

    Text-first data layer (``.claude/rules/text-first-data.md``): the DB is
    a per-call compute engine, never a store. ``tables`` maps name →
    list-of-dicts (``parse_markdown_table`` / ``vault_query`` / ``list_all``
    output); ``files`` maps name → ``.csv`` path or ``.db``/``.sqlite`` path
    (ATTACHed read-only, tables reachable as ``<name>.<table>``).

    Returns rows, or ``{"error", "hint"}`` when the ``data`` extra is
    missing or a source path is wrong — never raises.
    """
    from emptyos.sdk.tabular import QueryUnavailable, query_mixed

    try:
        return query_mixed(sql, tables=tables, sources=files, params=params)
    except QueryUnavailable as e:
        return {"error": str(e), "hint": "pip install 'emptyos[data]'"}
    except FileNotFoundError as e:
        return {"error": f"file not found: {e}"}


def query_notes(
    self,
    sql: str,
    tags: list[str] | None = None,
    folder: str | None = None,
    *,
    table: str = "notes",
    **properties,
) -> list[dict] | dict:
    """SQL over vault notes: ``vault_query`` → flat rows → ephemeral SQL.

    Frontmatter properties merge up one level (collisions with path/name/
    folder land as ``prop_<key>``); ``tags`` joins to a comma-separated
    string. Frontmatter values are strings — pure numeric literals arrive
    typed, everything else needs ``TRY_CAST`` in the SQL.
    """
    from emptyos.sdk.tabular import flatten_note_records

    rows = flatten_note_records(self.vault_query(tags=tags, folder=folder, **properties))
    return self.query(sql, tables={table: rows})


async def timeline(self, entity_path: str, *, kind: str = "note") -> dict:
    """Aggregate past / future / now for a vault entity.

    Past sources: created/updated frontmatter, ## Timeline section bullets,
    ``git log``, syslog filtered for entity-mention.
    Future sources: due/expires_at/next_review frontmatter, scheduler jobs
    whose id mentions the entity stem, rooms.list_pending args filtered,
    reminders.upcoming_for (feature-detected).
    Now: frontmatter snapshot + tags + lifecycle + status.

    Every source is wrapped in try/except — one bad reader doesn't break
    the panel. Returns {past[], future[], now{}} with past sorted desc and
    future sorted asc by ts.
    """
    from datetime import datetime

    def _ts_iso(ts) -> str:
        if isinstance(ts, (int, float)):
            try:
                return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
            except Exception:
                return ""
        return str(ts or "")

    past: list[dict] = []
    future: list[dict] = []
    now: dict = {"path": entity_path, "kind": kind}

    # NOW from frontmatter + index
    fm: dict = {}
    try:
        fm = self.vault_get_properties(entity_path) or {}
    except Exception:
        fm = {}
    now["frontmatter"] = fm
    now["status"] = fm.get("status")
    now["lifecycle"] = fm.get("lifecycle")
    now["tags"] = self.vault_tags(entity_path)

    # PAST — created/updated from frontmatter
    if fm.get("created"):
        past.append(
            {
                "ts": str(fm["created"]),
                "source": "vault",
                "kind": "created",
                "text": "Note created",
            }
        )
    if fm.get("updated") and fm.get("updated") != fm.get("created"):
        past.append(
            {
                "ts": str(fm["updated"]),
                "source": "vault",
                "kind": "updated",
                "text": "Last modified",
            }
        )

    # PAST — ## Timeline section bullets ("YYYY-MM-DD — text")
    try:
        tl = self.vault_read_section(entity_path, "Timeline") or ""
        for line in tl.splitlines():
            s = line.strip().lstrip("-").strip()
            if not s or len(s) < 10:
                continue
            head = s[:10]
            if head.count("-") != 2:
                continue
            txt = s[10:].lstrip(" —-:").strip()
            past.append(
                {
                    "ts": head,
                    "source": "timeline-section",
                    "kind": "milestone",
                    "text": txt or s,
                }
            )
    except Exception:
        pass

    # PAST — git log
    try:
        for row in await self.git_log(entity_path, limit=20):
            past.append(
                {
                    "ts": row["ts"],
                    "source": "git",
                    "kind": "commit",
                    "text": row["subject"],
                    "link": row["hash"][:8],
                }
            )
    except Exception:
        pass

    # PAST — syslog filtered for entity-mention
    try:
        stem = Path(entity_path).stem
        for row in self.kernel.syslog.query(limit=300):
            blob = (row.get("message") or "") + " " + json.dumps(row.get("data") or {})
            if entity_path in blob or (stem and stem in blob):
                past.append(
                    {
                        "ts": _ts_iso(row.get("ts")),
                        "source": row.get("source") or "syslog",
                        "kind": row.get("level") or "info",
                        "text": row.get("message") or "",
                    }
                )
    except Exception:
        pass

    # FUTURE — frontmatter date-shaped fields
    for key, label in [
        ("due", "Due"),
        ("due_ts", "Due"),
        ("expires_at", "Expires"),
        ("next_review", "Next review"),
        ("deadline", "Deadline"),
    ]:
        v = fm.get(key)
        if v:
            future.append(
                {"ts": str(v), "source": "vault", "kind": key, "text": label}
            )

    # FUTURE — scheduler jobs whose id mentions entity stem
    try:
        stem = Path(entity_path).stem
        sched = getattr(self.kernel, "scheduler", None)
        if sched and stem:
            for job in sched.jobs:
                jid = job.get("id") or ""
                if stem in jid:
                    future.append(
                        {
                            "ts": job.get("next_run") or "",
                            "source": "scheduler",
                            "kind": "job",
                            "text": jid,
                        }
                    )
    except Exception:
        pass

    # FUTURE — rooms pending actions whose args mention the entity
    try:
        stem = Path(entity_path).stem
        pending = await self.call_app("rooms", "list_pending", status="pending")
        if pending and isinstance(pending, list):
            for p in pending:
                blob = json.dumps(p.get("args") or {})
                if entity_path in blob or (stem and stem in blob):
                    future.append(
                        {
                            "ts": p.get("ts") or "",
                            "source": "rooms",
                            "kind": "pending",
                            "text": f"{p.get('app','?')}.{p.get('method','?')} pending review",
                        }
                    )
    except Exception:
        pass

    # FUTURE — reminders upcoming for this entity (feature-detect)
    try:
        upcoming = await self.call_app("reminders", "upcoming_for", entity=entity_path)
        if upcoming and isinstance(upcoming, list):
            for r in upcoming:
                future.append(
                    {
                        "ts": r.get("due") or r.get("ts") or "",
                        "source": "reminders",
                        "kind": "reminder",
                        "text": r.get("note") or r.get("text") or "Reminder",
                    }
                )
    except Exception:
        pass

    past.sort(key=lambda x: x.get("ts") or "", reverse=True)
    future.sort(key=lambda x: x.get("ts") or "")
    return {"past": past, "future": future, "now": now}
