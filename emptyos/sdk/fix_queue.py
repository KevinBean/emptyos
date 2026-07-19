"""Fix-prompt queue — shared on-disk contract for the test-fix-verify loop.

Friction sources (``apps/dogfood-agent`` persona runs, ``apps/trace-miner``
syslog mining) write fix-prompts that ``apps/fix-agent`` reads and verifies.
This module is the single source of truth for that contract: the queue
directory, the filename slug, the frontmatter + ``## What the persona
reported`` block that fix-agent's ``_parse_prompt_meta`` parses, and the
queue-index / move-to-done lifecycle (each close appends a row to the
``done/_ledger.jsonl`` history with its disposition — see ``DISPOSITIONS``
and ``.claude/rules/loop-traceability.md``).

Keeping the writers in sync with the reader is the whole point — a silent
divergence here breaks cross-app verify. New friction sources should build
their prompt via ``render_frontmatter`` + ``persona_scenario_line`` +
``friction_block`` (or the ``BaseApp.surface_friction`` wrapper) rather than
restating the format. ``apps/dogfood-agent/friction.py`` predates this module
and migrates when next touched (CLAUDE.md vault-migration convention).

See ``.claude/rules/test-fix-verify-loop.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

# fix-agent reads prompts from this dir (via dogfood-agent's api_queue glob).
FIX_PROMPTS_REL = "apps/dogfood-agent/fix-prompts"
# Heading whose `> ...` quote fix-agent's _parse_prompt_meta extracts as
# friction_text. Changing this string is a cross-app breaking change.
FRICTION_HEADING = "## What the persona reported"
# Heading carrying the optional source-location hint (apps/.../file:LINE) from
# the platform locator. Lives in the BODY (not just frontmatter) so it survives
# fix-agent's strip_frontmatter and reaches the claude-cli fixer.
WHERE_HEADING = "## Where to look"
_KIND_ORDER = {"bug": 0, "confusing": 1, "missing": 2}
# How a retired prompt closed. "done" is the legacy default; the rest reuse the
# gap-analysis registry vocabulary (planned/deferred/declined/shipped, plus
# "dismissed" for noise/not-a-bug) so findings and feature gaps share one
# lifecycle instead of a fifth. See .claude/rules/loop-traceability.md.
DISPOSITIONS = ("shipped", "planned", "deferred", "declined", "dismissed", "done")
# What durable learning a close produced — stamped at close time (default
# "none"): the loop's compound-learning outcome, projected by loop receipts.
LEARNING_OUTCOMES = (
    "regression-test",
    "conformance-case",
    "lesson",
    "rule",
    "design-principle",
    "none",
)


def slug_for_key(key: str) -> str:
    """Friction keys contain '::' and arbitrary chars — reduce to a path-safe
    .md filename. Deterministic so duplicate friction updates the same file.

    Keys whose normalized slug exceeds 80 chars keep a stable 8-hex digest of
    the FULL slug as a suffix, so two distinct long keys can never truncate to
    the same filename and silently merge unrelated findings. Slugs at or under
    80 chars are byte-identical to the historical form."""
    slug = re.sub(r"[^a-z0-9]+", "-", (key or "").lower()).strip("-")
    if not slug:
        return "friction.md"
    if len(slug) > 80:
        digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[:71].rstrip('-')}-{digest}"
    return slug + ".md"


# ui-walk trace identity (loop-traceability). Shared by the promote bridge
# (scripts/ui_walk_promote.py) and fix-agent's attest evidence check so the
# two sides can never disagree on a steplog row's trace key.
TRACE_PREFIX = "ui-walk"


def slugify_trace(text: str, maxlen: int = 48) -> str:
    """Deterministic slug for legacy free-text trace identities."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:maxlen].strip("-")


def steplog_row_trace(row: dict, usecase_id: str, walk_id: str) -> dict:
    """Stable trace identity for one ui-walk steplog row (explicit fields win;
    legacy rows derive deterministically from the free-text ``usecase``)."""
    uc = str(row.get("usecase_id") or usecase_id)
    milestone = str(row.get("milestone_id") or slugify_trace(str(row.get("usecase") or ""))) or "ungrouped"
    step_id = f"s{row.get('step') or 0}"
    return {
        "usecase_id": uc,
        "milestone_id": milestone,
        "step_id": step_id,
        "walk_id": walk_id,
        "key": f"{TRACE_PREFIX}::{uc}::{milestone}::{step_id}",
    }


def render_frontmatter(fields: dict) -> str:
    """Render an ordered ``--- k: v ---`` block. Values are stringified as-is;
    callers pass lists pre-rendered (e.g. ``"['trace-miner']"``) to match the
    historical dogfood format the parser tolerates."""
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def persona_scenario_line(persona: str, scenario: str) -> str:
    """The ``**Persona**: X · **Scenario**: Y`` line fix-agent regexes for."""
    return f"**Persona**: {persona} · **Scenario**: {scenario}"


def friction_block(text: str, turn=None) -> str:
    """The canonical friction quote block. The ``> {text}`` line is what
    fix-agent's verify path reads back as the friction to retest."""
    head = FRICTION_HEADING + (f" (turn {turn})" if turn not in (None, "") else "")
    return f"{head}\n> {text}"


# Frontmatter fields a close-ledger row should carry forward from the prompt.
_LEDGER_FM_KEYS = (
    "key", "source", "usecase_id", "milestone_id", "step_id", "walk_id",
    "learning_outcome", "learning_ref",
)


def ledger_info_from_prompt(content: str) -> dict:
    """Pull the ledger-relevant frontmatter fields out of a fix-prompt — the
    trace identity + close stamps a ``move_to_done`` ledger row carries so a
    retired item stays traceable to its originating use case."""
    out: dict = {}
    lines = (content or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return out
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        k, sep, v = ln.partition(":")
        if sep and k.strip() in _LEDGER_FM_KEYS and v.strip():
            out[k.strip()] = v.strip()
    return out


def where_to_look_block(source_hint: str) -> str:
    """Optional ``## Where to look`` body section pointing at a source location
    (e.g. ``apps/foo/pages/index.html:142``) surfaced by the platform locator.
    Returns ``""`` when there's no hint. The line is a best-effort pointer (the
    file is stable, the line may drift), not a patch coordinate."""
    hint = (source_hint or "").strip()
    if not hint:
        return ""
    return f"{WHERE_HEADING}\n`{hint}` (best-effort line — verify before editing)"


class FixPromptQueue:
    """Directory + index + lifecycle for the shared fix-prompt queue."""

    def __init__(self, data_dir):
        self._root = Path(data_dir) / FIX_PROMPTS_REL

    @property
    def dir(self) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "done").mkdir(exist_ok=True)
        return self._root

    def slug(self, key: str) -> str:
        return slug_for_key(key)

    def write(self, filename: str, content: str) -> Path:
        p = self.dir / filename
        p.write_text(content, encoding="utf-8")
        return p

    def move_to_done(self, filename: str, *, disposition: str = "done", info: dict | None = None) -> bool:
        """Retire a pending prompt into ``done/`` and record the close in the
        append-only ledger. ``disposition`` says HOW it closed (see
        ``DISPOSITIONS``); ``info`` carries optional trace/learning fields
        (``key / source / usecase_id / milestone_id / step_id / walk_id /
        learning_outcome / learning_ref / by``). The ledger append is
        fail-soft — a ledger problem never blocks the retire."""
        src = self.dir / filename
        if not src.exists():
            return False
        try:
            src.rename(self.dir / "done" / filename)
        except Exception:
            return False
        row = {
            "ts": datetime.now(UTC).isoformat(),
            "filename": filename,
            "disposition": disposition or "done",
        }
        for k, v in (info or {}).items():
            if v not in (None, "") and k not in row:
                row[k] = v
        try:
            self.append_ledger(row)
        except Exception:
            pass
        return True

    def append_ledger(self, row: dict) -> None:
        """Append one JSON line to ``done/_ledger.jsonl`` — the close history.
        The leading ``_`` keeps it out of the queue globs (``rebuild_index``,
        ``api_queue``). Append-only: corrections are new rows, and the last
        row per filename wins in projections."""
        path = self.dir / "done" / "_ledger.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def ledger_by_filename(self, limit: int = 500) -> dict[str, dict]:
        """Last ledger row per filename (one bounded read; later rows win —
        append-only corrections supersede earlier closes)."""
        out: dict[str, dict] = {}
        for row in self.read_ledger(limit):
            fn = row.get("filename")
            if isinstance(fn, str) and fn:
                out[fn] = row
        return out

    def read_ledger(self, limit: int = 500) -> list[dict]:
        """Last ``limit`` ledger rows, oldest→newest (``limit <= 0`` = all).
        Single-file bounded read — completed items stay inspectable without
        scanning ``done/`` — and malformed lines are skipped, not raised."""
        path = self.dir / "done" / "_ledger.jsonl"
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        if limit and limit > 0:
            lines = lines[-int(limit):]
        out: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out

    def rebuild_index(self) -> None:
        """Maintain ``_queue.md`` — one file listing all pending items in
        priority order (bug > confusing > missing, then by recency). Format
        matches dogfood-agent's historical index so either writer is safe."""
        d = self.dir
        items = []
        for p in d.glob("*.md"):
            if p.name.startswith("_"):
                continue
            try:
                head = p.read_text(encoding="utf-8").splitlines()[:20]
                meta = {"kind": "?", "app": "", "last_seen": "", "count": 1}
                for line in head:
                    for key in ("kind", "app", "last_seen", "count"):
                        if line.startswith(key + ":"):
                            val = line.split(":", 1)[1].strip()
                            meta[key] = int(val) if key == "count" and val.isdigit() else val
                items.append({"file": p.name, **meta})
            except Exception:
                continue
        items.sort(key=lambda x: x["last_seen"], reverse=True)
        items.sort(key=lambda x: _KIND_ORDER.get(x["kind"], 9))
        lines = [
            "# Dogfood fix-prompt queue",
            "",
            f"_{len(items)} pending — auto-generated. Process with: read a file → fix → mv to `done/`._",
            "",
        ]
        if not items:
            lines.append("(empty — nothing to fix right now)")
        else:
            for it in items:
                app_tag = f" · app `{it['app']}`" if it.get("app") else ""
                lines.append(
                    f"- **{it['kind']}**{app_tag} · `{it['file']}` · seen {it['count']}× · last {it['last_seen'] or '?'}"
                )
        (d / "_queue.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
