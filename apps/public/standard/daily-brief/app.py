"""Daily Brief — a curated morning digest, distilled by AI, filed once a day.

Pulls a small set of RSS/JSON feeds (the source registry, overridable in
config), distills the lot into one ruthlessly short brief via ``self.think()``,
files an AI-authored vault note under ``daily-brief/outputs/``, and surfaces the
result on its own page + a hub tile + an Aura voice intent.

Design notes
------------
* The runtime machinery (pluggable LLM, scheduler, publish surface) is the
  platform's, not this app's — the app is just orchestration: a source list,
  a fetch+parse step, one think() call, and three output surfaces.
* Feeds are public; only fetched *feed* text is sent to the model, never vault
  content (CLAUDE.md rule 19). The think() call routes through the normal
  provider chain + cloud-consent gate.
* Output is AI-authored → ``outputs/`` subfolder + ``author: ai`` frontmatter
  (.claude/rules/authorship-boundary.md).
* The schedule defaults OFF — the app exists but won't auto-fire until the user
  enables it (the video-digest ``auto_run`` convention). HTTP fetch + parse are
  pure I/O; the parse runs off the event loop is unnecessary (xml is tiny), but
  the fetch is fully async via aiohttp.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from datetime import date as date_cls

from emptyos.sdk import (
    BaseApp,
    cli_command,
    source_fencer,
    web_route,
)

from . import articles as _articles_mod
from . import feeds as _feeds
from . import markets as _markets
from . import snapshots as _snapshots
from . import sources as _sources_mod
from .shared import (
    ARTICLE_SUMMARY_SYSTEM,
    DEFAULT_TICKERS,
    DIGEST_SYSTEM,
    MARKET_SYSTEM,
    PERSONALIZE_SYSTEM,
    _LANG,
    _MAX_FEED_BYTES,
    _TAG_RE,
    _UA,
    _WS_RE,
    _compute_indicators,
    _ema_series,
    _macd_hist,
    _md_esc,
    _notify_text,
    _rsi,
    _sma,
    _strip_html,
    _text,
)

# Untrusted external feeds → harden against billion-laughs / XXE. Prefer
# defusedxml (refuses DTDs + entity expansion); fall back to stdlib so a
# machine that hasn't `pip install`ed yet still boots (with a size cap below
# as a second line of defence).
try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring  # type: ignore
except ImportError:  # pragma: no cover - fallback path
    from xml.etree.ElementTree import fromstring as _xml_fromstring

log = logging.getLogger("emptyos.daily-brief")

_JOB_ID = "daily-brief-generate"

# Curated defaults — RSS feeds that are reliable, no Cloudflare TLS games, and
# slanted toward energy + dev (override per-machine via emptyos.toml
# [apps.daily-brief] sources = [...]). Each: id, name, url, kind, category.
DEFAULT_SOURCES: list[dict] = [
    {"id": "reneweconomy", "name": "RenewEconomy", "url": "https://reneweconomy.com.au/feed/",
     "kind": "rss", "category": "energy"},
    {"id": "pv-magazine-au", "name": "PV Magazine AU",
     "url": "https://www.pv-magazine-australia.com/feed/", "kind": "rss", "category": "energy"},
    {"id": "hacker-news", "name": "Hacker News", "url": "https://hnrss.org/frontpage?points=150",
     "kind": "rss", "category": "tech"},
    {"id": "ars-technica", "name": "Ars Technica",
     "url": "https://feeds.arstechnica.com/arstechnica/index", "kind": "rss", "category": "tech"},
    {"id": "the-verge", "name": "The Verge", "url": "https://www.theverge.com/rss/index.xml",
     "kind": "rss", "category": "tech"},
]

BRIEF_SYSTEM = """You are a sharp, time-respecting news editor writing one person's private morning brief.

You are given a list of fresh headlines (with one-line descriptions and links) pulled from their feeds overnight. Your job: pick the few that genuinely matter and write a brief they can finish in 90 seconds.

RULES
- Write ONLY in {lang}. ({lang_name})
- Pick at most {max_items} items total — the most consequential, not one-per-feed. Dropping a whole category is fine if nothing there matters today.
- Group under short `## ` section headers (e.g. Energy, Tech) ONLY if you have 2+ items in a section; otherwise a flat list.
- One item per line: `- **[exact title](exact url)** — <one sharp sentence on why it matters>`. Copy the title and URL VERBATIM from the input; never invent or alter a URL.
- The "why it matters" sentence is the value. Make it specific and concrete — a number, a consequence, a "so what". Not a restatement of the title.
- {focus_clause}
- Start with a single bold one-line `**TL;DR:**` capturing the day's single biggest thing, then the items.

NEVER DO
- Never summarize every headline — most days, most items are noise. Cutting is the job.
- Never editorialize, moralize, or add hype words ("groundbreaking", "game-changer").
- Never invent facts, numbers, titles, or URLs not present in the input.
- Never add a preamble, sign-off, or "here is your brief" wrapper. Output the brief markdown directly.
- Never output a section header with zero or one items under it."""


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class DailyBriefApp(BaseApp):

    # ── Lifecycle ──────────────────────────────────────────────
    async def setup(self):
        await super().setup()
        self._register_schedule()
        log.info("daily-brief started")

    async def teardown(self):
        self.remove_cron_job(_JOB_ID)
        await super().teardown()

    # ── Schedule wiring ────────────────────────────────────────
    def _register_schedule(self):
        if not self._cfg("enabled", False):
            self.remove_cron_job(_JOB_ID)
            return
        cron = str(self._cfg("schedule_cron", "0 7 * * *")).strip() or "0 7 * * *"
        ok = self.add_cron_job_logged(
            _JOB_ID, self._scheduled_run, cron=cron, crash_event="brief_crash",
        )
        self.log_activity({"event": "schedule_registered" if ok else "schedule_failed",
                           "cron": cron})

    async def _scheduled_run(self):
        await self.generate(trigger="schedule")

    # ── Config resolution ──────────────────────────────────────
    def _cfg(self, key: str, default):
        """Settings panel (``daily-brief.<key>``) → emptyos.toml → default."""
        v = self.setting(f"daily-brief.{key}", None)
        if v is None:
            v = self.app_config(key, None)
        return default if v is None else v

    def _int_cfg(self, key: str, default: int) -> int:
        try:
            return max(1, int(self._cfg(key, default)))
        except (TypeError, ValueError):
            return default

    def _builtin_sources(self) -> list[dict]:
        """Built-in source registry: config override or shipped defaults.
        Only ``kind in {rss, json}`` are fetched; unknown kinds are skipped.
        These can be disabled (via the Sources tab) but never removed."""
        raw = self.app_config("sources", None)
        srcs = raw if isinstance(raw, list) and raw else DEFAULT_SOURCES
        out = []
        for s in srcs:
            if not isinstance(s, dict) or not s.get("url"):
                continue
            if s.get("enabled") is False:
                continue
            out.append({
                "id": s.get("id") or s.get("name") or s["url"],
                "name": s.get("name") or s.get("id") or s["url"],
                "url": s["url"],
                "kind": (s.get("kind") or "rss").lower(),
                "category": s.get("category") or "general",
            })
        return out

    def _sources(self) -> list[dict]:
        """Resolved ENABLED source registry for fetching: built-in + user-added
        custom feeds, deduped by url, minus per-source disable overrides (both
        managed via the Sources tab / sources.py)."""
        disabled = self._disabled_ids()
        seen, out = set(), []
        for s in self._builtin_sources() + self._custom_sources():
            if s["url"] in seen or s["id"] in disabled:
                continue
            seen.add(s["url"])
            out.append(s)
        return out

    def _locale(self) -> str:
        loc = str(self._cfg("locale", "zh")).strip().lower()
        return loc if loc in _LANG else "zh"

    @staticmethod
    def _maybe_iso_date(value: str | None) -> date_cls | None:
        if not value:
            return None
        m = _ISO_DATE_RE.search(str(value))
        if not m:
            return None
        try:
            return date_cls.fromisoformat(m.group(0))
        except ValueError:
            return None

    @staticmethod
    def _unavailable(name: str, error: str) -> dict:
        return {"available": False, "source": name, "error": error}

    # ── Generate the brief ─────────────────────────────────────
    async def generate(self, trigger: str = "manual") -> dict:
        date = datetime.now().strftime("%Y-%m-%d")
        try:
            items, status = await self._fetch_all()
            if not items:
                raise RuntimeError("no items fetched — all sources empty or unreachable")
            self._ingest_articles(items)  # persist the firehose for the Inbox view
            brief_md = await self._distill(items)
            # Optional market pulse — fail-soft, never blocks the news brief.
            market_md, quotes = "", []
            if self._markets_enabled():
                try:
                    quotes = await self._fetch_quotes()
                    if quotes:
                        market_md = await self._distill_markets(quotes)
                except Exception as e:  # noqa: BLE001 — markets are a bonus, not load-bearing
                    self.log_activity({"event": "markets_failed", "error": str(e)[:200]})
            note_path = self._write_note(date, brief_md, items, status,
                                         market_md=market_md, quotes=quotes)
            record = {
                "ok": True, "date": date, "trigger": trigger,
                "generated_at": datetime.now(UTC).isoformat(),
                "brief_md": brief_md, "items": items, "sources": status,
                "item_count": len(items), "note_path": note_path,
                "market_md": market_md, "quotes": quotes,
                "provenance": self.last_provenance(),
            }
            self._record(record)
            await self.emit("daily-brief:generated", {
                "date": date, "item_count": len(items), "note_path": note_path,
            })
            await self._deliver(record)
            self.log_activity({"event": "generated", "date": date, "trigger": trigger,
                               "items": len(items)})
            return record
        except Exception as e:  # noqa: BLE001 — fail-soft, record + surface
            record = {"ok": False, "date": date, "trigger": trigger,
                      "generated_at": datetime.now(UTC).isoformat(),
                      "error": str(e)[:400]}
            self._record(record)
            await self.emit("daily-brief:failed", {"date": date, "error": record["error"]})
            self.log_activity({"event": "failed", "date": date, "error": record["error"]})
            return record

    async def _deliver(self, record: dict) -> dict:
        """Push the finished brief at the user instead of waiting to be visited.

        A morning brief that only exists in a browser tab is a page, not a
        brief — the app generated on a cron and filed a note, and nothing ever
        told anyone. Routed through the shared proactive gate, so restraint
        (master toggle, per-kind mute, quiet hours, daily cap, dedup) is the
        engine's job and not re-implemented here; there is deliberately no
        second app-level on/off switch to keep in sync with it.

        Dedup is per date, so a manual re-run after the scheduled one doesn't
        nudge twice. Fail-soft: delivery never breaks generation — the brief is
        already written and recorded by the time we get here.
        """
        try:
            return await self.proactive_notify(
                "daily-brief",
                _notify_text(record.get("brief_md", ""), int(record.get("item_count", 0) or 0),
                             self._locale()),
                urgency="normal",
                dedup_key=f"daily-brief:{record.get('date', '')}",
                link={"text": "Read the brief", "href": "/daily-brief/"},
            )
        except Exception as e:  # noqa: BLE001 — the brief is already saved
            self.log_activity({"event": "deliver_failed", "error": str(e)[:200]})
            return {"delivered": False, "reason": "error"}

    async def _distill(self, items: list[dict]) -> str:
        loc = self._locale()
        lang_name, lang = _LANG[loc]
        max_items = self._int_cfg("max_items", 8)
        focus = str(self._cfg("focus", "") or "").strip()
        focus_clause = (
            f"This reader cares about: {focus}. Rank and frame items through that lens — "
            "lead with what touches those topics, and make the 'why it matters' line speak to them."
            if focus else
            "Rank by general consequence; no special-interest lens."
        )
        system = BRIEF_SYSTEM.format(
            lang=lang, lang_name=lang_name, max_items=max_items, focus_clause=focus_clause,
        )
        lines = []
        for it in items:
            desc = f" — {it['summary']}" if it["summary"] else ""
            lines.append(f"[{it['category']}] {it['title']} <{it['url']}>{desc}")
        # Dark-flagged injection hardening: feed titles/summaries are external
        # text (source_fencer no-ops with the flag off → byte-identical).
        fencer = source_fencer(self)
        headlines = fencer.wrap("\n".join(lines), label="rss feed items")
        user = "Today's fetched headlines:\n\n" + headlines
        out = await self.think(user, domain="reason", system=fencer.system(system), temperature=0.4)
        return (out if isinstance(out, str) else str(out)).strip()

    def _write_note(self, date: str, brief_md: str, items: list[dict],
                    status: list[dict], *, market_md: str = "",
                    quotes: list[dict] | None = None) -> str:
        rel = f"30_Resources/EmptyOS/daily-brief/outputs/{date}.md"
        ok_n = sum(1 for s in status if s["ok"])
        market_block = ""
        if market_md:
            heading = "市场速览" if self._locale() == "zh" else "Market pulse"
            market_block = f"\n\n## {heading}\n\n{market_md}\n"
        body = (
            f"{brief_md}{market_block}\n\n---\n\n"
            f"## All headlines\n\n"
            + "\n".join(f"- [{_md_esc(it['title'])}]({it['url']}) "
                        f"· _{it['source']}_" for it in items)
            + f"\n\n<sub>{ok_n}/{len(status)} sources"
            + (f" · {len(quotes or [])} instruments" if market_md else "")
            + f" · generated {datetime.now().strftime('%H:%M')}</sub>\n"
        )
        self.vault_create_note(rel, {
            "tags": ["daily-brief"],
            "author": "ai",
            "lifecycle": "snapshot",
            "date": date,
            "as_of": date,
            "item_count": len(items),
        }, body)
        return rel

    def _record(self, record: dict):
        state = self.load_state(default={}) or {}
        state["last_run"] = record
        history = state.get("history", [])
        # History keeps the rendered brief but drops the bulky per-item arrays.
        history.insert(0, {k: record.get(k) for k in
                           ("ok", "date", "trigger", "generated_at", "brief_md",
                            "market_md", "item_count", "note_path", "error")})
        state["history"] = history[:30]
        self.save_state(state)

    # ── Status ─────────────────────────────────────────────────
    def _status(self) -> dict:
        state = self.load_state(default={}) or {}
        last = state.get("last_run") or {}
        return {
            "enabled": bool(self._cfg("enabled", False)),
            "locale": self._locale(),
            "markets_enabled": self._markets_enabled(),
            "focus": str(self._cfg("focus", "") or ""),
            # True once the user has saved their own focus (vs the manifest
            # default) — the first-run "tailor to your vault" step keys off this.
            "focus_custom": self.setting("daily-brief.focus", None) is not None,
            "next_run": self.get_cron_job_next_fire(_JOB_ID),
            "never_run": not bool(last),
            "last_ok": bool(last.get("ok")),
            "last_date": last.get("date"),
            "last_error": last.get("error"),
            "item_count": last.get("item_count", 0),
            "sources": [{"name": s["name"], "ok": s["ok"], "count": s["count"]}
                        for s in (last.get("sources") or [])],
            "source_count": len(self._sources()),
            "digest_count": len(self._digest()),
            "unread_count": sum(1 for a in self._articles() if not a.get("read")),
        }

    @web_route("GET", "/api/status")
    async def api_status(self, request):
        return self._status()

    @web_route("GET", "/api/today")
    async def api_today(self, request):
        """The latest brief in full (markdown + items + source status)."""
        state = self.load_state(default={}) or {}
        last = state.get("last_run") or {}
        return {
            "ok": bool(last.get("ok")),
            "date": last.get("date"),
            "brief_md": last.get("brief_md", ""),
            "market_md": last.get("market_md", ""),
            "quotes": last.get("quotes", []),
            "items": last.get("items", []),
            "sources": last.get("sources", []),
            "error": last.get("error"),
            "never_run": not bool(last),
        }

    @web_route("GET", "/api/history")
    async def api_history(self, request):
        state = self.load_state(default={}) or {}
        return {"history": state.get("history", [])}

    @web_route("GET", "/api/sources")
    async def api_sources(self, request):
        return {"sources": self._sources()}

    @web_route("POST", "/api/run-now")
    async def api_run_now(self, request):
        return await self.generate(trigger="manual")

    @web_route("POST", "/api/reschedule")
    async def api_reschedule(self, request):
        self._register_schedule()
        return {"ok": True, "enabled": bool(self._cfg("enabled", False)),
                "next_run": self.get_cron_job_next_fire(_JOB_ID)}

    # ── CLI ────────────────────────────────────────────────────
    @cli_command("status")
    async def cli_status(self):
        s = self._status()
        if s["never_run"]:
            print("Daily brief: never run")
        else:
            flag = "ok" if s["last_ok"] else "FAILED"
            print(f"Daily brief {s['last_date']}: {flag} · {s['item_count']} stories "
                  f"· schedule {'on' if s['enabled'] else 'off'}")

    @cli_command("now")
    async def cli_now(self):
        r = await self.generate(trigger="cli")
        print("OK" if r["ok"] else f"FAILED: {r.get('error')}")

    # ── Hub panel ──────────────────────────────────────────────
    async def panel_today(self) -> dict | None:
        s = self._status()
        if s["never_run"]:
            return {"label": "Daily Brief", "value": "—"}
        if not s["last_ok"]:
            return {"label": "Daily Brief", "value": "failed", "tone": "warn"}
        today = datetime.now().strftime("%Y-%m-%d")
        if s["last_date"] == today:
            return {"label": "Daily Brief", "value": f"{s['item_count']} stories"}
        return {"label": "Daily Brief", "value": "yesterday", "tone": "warn"}

    # ── Voice intent ───────────────────────────────────────────
    async def voice_today(self) -> dict:
        s = self._status()
        if s["never_run"] or not s["last_ok"]:
            return {"say": "There's no brief yet — run it from the Daily Brief app first."}
        today = datetime.now().strftime("%Y-%m-%d")
        when = "today" if s["last_date"] == today else f"on {s['last_date']}"
        return {
            "say": f"Your brief {when} has {s['item_count']} stories.",
            "link": {"text": "Open today's brief", "href": "/daily-brief/"},
        }

    # ── Articles (extracted to articles.py) ──
    _digest_file          = _articles_mod._digest_file
    _digest               = _articles_mod._digest
    _save_digest          = _articles_mod._save_digest
    api_action_targets    = _articles_mod.api_action_targets
    api_article_save      = _articles_mod.api_article_save
    api_digest            = _articles_mod.api_digest
    api_digest_add        = _articles_mod.api_digest_add
    api_digest_remove     = _articles_mod.api_digest_remove
    _compile_digest       = _articles_mod._compile_digest
    api_digest_compile    = _articles_mod.api_digest_compile
    _articles_file        = _articles_mod._articles_file
    _articles             = _articles_mod._articles
    _save_articles        = _articles_mod._save_articles
    _article_id           = _articles_mod._article_id
    _ingest_articles      = _articles_mod._ingest_articles
    headlines             = _articles_mod.headlines
    get_articles          = _articles_mod.get_articles
    api_articles          = _articles_mod.api_articles
    api_article_read      = _articles_mod.api_article_read
    api_articles_read_all = _articles_mod.api_articles_read_all
    api_article_summarize = _articles_mod.api_article_summarize

    # ── Feeds (extracted to feeds.py) ──
    _fetch_all  = _feeds._fetch_all
    _read_body  = _feeds._read_body
    _fetch_one  = _feeds._fetch_one
    _parse_feed = _feeds._parse_feed
    _parse_json = _feeds._parse_json

    # ── Sources / subscriptions (extracted to sources.py) ──
    _sources_file        = _sources_mod._sources_file
    _sources_state       = _sources_mod._sources_state
    _save_sources_state  = _sources_mod._save_sources_state
    _custom_sources      = _sources_mod._custom_sources
    _disabled_ids        = _sources_mod._disabled_ids
    _all_sources_managed = _sources_mod._all_sources_managed
    _validate_source     = _sources_mod._validate_source
    api_sources_manage   = _sources_mod.api_sources_manage
    api_sources_add      = _sources_mod.api_sources_add
    api_sources_remove   = _sources_mod.api_sources_remove
    api_sources_toggle   = _sources_mod.api_sources_toggle

    # ── Markets (extracted to markets.py) ──
    _markets_enabled = _markets._markets_enabled
    _tickers         = _markets._tickers
    _fetch_quotes    = _markets._fetch_quotes
    _fetch_quote     = _markets._fetch_quote
    _distill_markets = _markets._distill_markets

    # ── Snapshots (extracted to snapshots.py) ──
    _task_snapshot           = _snapshots._task_snapshot
    _project_snapshot        = _snapshots._project_snapshot
    _journal_snapshot        = _snapshots._journal_snapshot
    _people_snapshot         = _snapshots._people_snapshot
    _audit_snapshot          = _snapshots._audit_snapshot
    _command_center_snapshot = _snapshots._command_center_snapshot
    _vault_signals           = _snapshots._vault_signals
    _derive_focus            = _snapshots._derive_focus
    api_personalize          = _snapshots.api_personalize
    api_command_center       = _snapshots.api_command_center
    _SIGNAL_LABELS           = _snapshots._SIGNAL_LABELS

