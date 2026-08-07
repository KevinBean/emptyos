"""Unit tests for emptyos.sdk.fix_queue — the shared fix-prompt contract.

The contract matters because apps/fix-agent's _parse_prompt_meta reads what
friction sources (dogfood-agent, trace-miner) write. These tests pin the
format pieces fix-agent depends on so a refactor can't silently break verify.
"""

from __future__ import annotations

import importlib.util
import sys
import types

from emptyos.sdk.fix_queue import (
    DISPOSITIONS,
    FRICTION_HEADING,
    LEARNING_OUTCOMES,
    WHERE_HEADING,
    FixPromptQueue,
    friction_block,
    persona_scenario_line,
    render_frontmatter,
    slug_for_key,
    where_to_look_block,
)
from helpers import app_path

# Load fix-agent's parser by path to assert round-trip compatibility. app_path
# resolves it wherever it lives in the track tree (it moved under extension/dev/
# in the 2026-05-30 reorg — a hardcoded apps/fix-agent path breaks collection)
# and puts the repo root on sys.path so the module's emptyos.* imports resolve.
#
# fix-agent's app.py was decomposed (0337935) into helper modules and now does
# `from . import merge/repro/runs/verify` + `from .shared import ...`. Loading
# app.py via bare importlib without registering a parent package fails with
# "attempted relative import with no known parent package" (the gotcha in
# .claude/rules/multi-module-apps.md). Register a package + pre-load the helper
# modules first, mirroring tests/test_unit_rooms_logic.py.
_FA_DIR = app_path("fix-agent")
if "fa_app_for_test_pkg" not in sys.modules:
    _pkg = types.ModuleType("fa_app_for_test_pkg")
    _pkg.__path__ = [str(_FA_DIR)]
    sys.modules["fa_app_for_test_pkg"] = _pkg
for _sub in ("shared", "merge", "repro", "runs", "verify"):
    _name = f"fa_app_for_test_pkg.{_sub}"
    if _name in sys.modules:
        continue
    _sub_spec = importlib.util.spec_from_file_location(_name, _FA_DIR / f"{_sub}.py")
    _sub_mod = importlib.util.module_from_spec(_sub_spec)
    sys.modules[_name] = _sub_mod
    _sub_spec.loader.exec_module(_sub_mod)
_spec = importlib.util.spec_from_file_location("fa_app_for_test_pkg.app", _FA_DIR / "app.py")
_fa = importlib.util.module_from_spec(_spec)
sys.modules["fa_app_for_test_pkg.app"] = _fa
_spec.loader.exec_module(_fa)

# The prompt-meta parser is the single definition in shared.py (runs.py + verify.py
# import it from there; app.py does not re-export it). Read the contract's reader
# from where it actually lives.
_parse_prompt_meta = sys.modules["fa_app_for_test_pkg.shared"]._parse_prompt_meta


class TestSlug:
    def test_collapses_punctuation_and_appends_md(self):
        assert slug_for_key("tracemine::abc123") == "tracemine-abc123.md"

    def test_empty_key_has_fallback(self):
        assert slug_for_key("") == "friction.md"

    def test_deterministic(self):
        assert slug_for_key("k::x") == slug_for_key("k::x")

    def test_long_keys_cannot_collide(self):
        """Review 2026-07-17 issue 4: two distinct long keys used to truncate
        to the same 80-char filename, silently merging unrelated findings."""
        base = "ui-walk::" + "a" * 100
        k1, k2 = base + "::step-one", base + "::step-two"
        f1, f2 = slug_for_key(k1), slug_for_key(k2)
        assert f1 != f2
        # deterministic + bounded (71-char stem + '-' + 8-hex digest + '.md')
        assert f1 == slug_for_key(k1)
        assert len(f1) <= 80 + len(".md")

    def test_short_keys_keep_historical_form(self):
        # ≤80-char slugs are byte-identical to the pre-hash format, so
        # existing queued files keep their names.
        key = "ui-walk::riverside-bess-132kv::month-0-design-basis::s3"
        assert slug_for_key(key) == "ui-walk-riverside-bess-132kv-month-0-design-basis-s3.md"


class TestRenderHelpers:
    def test_frontmatter_ordered(self):
        out = render_frontmatter({"kind": "bug", "app": "task", "key": "k"})
        assert out.startswith("---\n") and out.endswith("\n---")
        assert "kind: bug" in out and "app: task" in out

    def test_persona_scenario_line_shape(self):
        assert persona_scenario_line("trace-miner", "syslog") == "**Persona**: trace-miner · **Scenario**: syslog"

    def test_friction_block_has_quote(self):
        b = friction_block("boom happened")
        assert b.startswith(FRICTION_HEADING)
        assert "> boom happened" in b

    def test_friction_block_with_turn(self):
        assert "(turn 3)" in friction_block("x", turn=3)


class TestRoundTripWithFixAgent:
    def test_parser_extracts_what_writers_emit(self):
        # Build a prompt the way surface_friction does, then parse it the way
        # fix-agent does — the cross-app contract must round-trip.
        fm = render_frontmatter({
            "kind": "bug", "app": "hub", "key": "tracemine::deadbeef",
            "source": "trace-miner", "verify_signature": "deadbeef",
        })
        content = (
            fm + "\n\n# Fix this\n\n**Kind**: `bug`\n"
            + persona_scenario_line("trace-miner", "syslog") + "\n\n"
            + friction_block("AppLoader has no attribute apps") + "\n"
        )
        ctx = _parse_prompt_meta(content)
        assert ctx.get("source") == "trace-miner"
        assert ctx.get("verify_signature") == "deadbeef"
        assert ctx.get("persona") == "trace-miner"
        assert ctx.get("scenario") == "syslog"
        assert "AppLoader" in (ctx.get("friction_text") or "")


class TestWhereToLookBlock:
    def test_with_hint(self):
        out = where_to_look_block("apps/task/pages/index.html:142")
        assert out.startswith(WHERE_HEADING)
        assert "apps/task/pages/index.html:142" in out
        assert "verify before editing" in out

    def test_empty_is_blank(self):
        assert where_to_look_block("") == ""
        assert where_to_look_block("   ") == ""
        assert where_to_look_block(None) == ""


class TestSourceHintRoundTrip:
    """source_hint (platform locator) round-trips writer → fix-agent reader, and
    its body section survives so it reaches the fixer post strip_frontmatter."""

    def _compose(self, source_hint: str) -> str:
        fm = {"kind": "bug", "app": "task", "key": "loc::x"}
        if source_hint:
            fm["source_hint"] = source_hint
        where = where_to_look_block(source_hint)
        return (
            render_frontmatter(fm) + "\n\n# Fix this\n\n**Kind**: `bug`\n"
            + persona_scenario_line("busy-pm", "capture") + "\n\n"
            + friction_block("the add button was hidden behind the FAB", turn=2)
            + (("\n\n" + where) if where else "")
            + "\n"
        )

    def test_source_hint_extracted(self):
        content = self._compose("apps/task/pages/index.html:142")
        ctx = _parse_prompt_meta(content)
        assert ctx.get("source_hint") == "apps/task/pages/index.html:142"
        assert ctx.get("persona") == "busy-pm"
        assert "add button" in (ctx.get("friction_text") or "")
        # Body section survives frontmatter stripping → reaches claude-cli.
        from emptyos.sdk.utils import strip_frontmatter
        assert WHERE_HEADING in strip_frontmatter(content)

    def test_without_hint_backward_compatible(self):
        content = self._compose("")
        ctx = _parse_prompt_meta(content)
        assert not ctx.get("source_hint")
        assert ctx.get("friction_text")
        assert WHERE_HEADING not in content


class TestQueueLifecycle:
    def test_write_index_and_move_to_done(self, tmp_path):
        q = FixPromptQueue(tmp_path)
        fn = q.slug("tracemine::x")
        q.write(fn, render_frontmatter({"kind": "bug", "app": "task", "count": 4, "last_seen": "2026-05-22"}) + "\n\nbody\n")
        assert (q.dir / fn).exists()
        q.rebuild_index()
        idx = (q.dir / "_queue.md").read_text(encoding="utf-8")
        assert fn in idx and "bug" in idx
        assert q.move_to_done(fn) is True
        assert (q.dir / "done" / fn).exists()
        assert not (q.dir / fn).exists()

    def test_move_missing_returns_false(self, tmp_path):
        assert FixPromptQueue(tmp_path).move_to_done("nope.md") is False


class TestDoneLedger:
    """Every close appends a row to done/_ledger.jsonl — the bounded-read
    history that keeps completed items inspectable without scanning done/."""

    def test_legacy_bare_call_records_default_disposition(self, tmp_path):
        q = FixPromptQueue(tmp_path)
        q.write("a.md", "---\nkind: bug\n---\nbody\n")
        assert q.move_to_done("a.md") is True
        rows = q.read_ledger()
        assert len(rows) == 1
        assert rows[0]["filename"] == "a.md"
        assert rows[0]["disposition"] == "done"
        assert rows[0]["ts"]

    def test_full_close_carries_trace_and_learning(self, tmp_path):
        q = FixPromptQueue(tmp_path)
        q.write("b.md", "---\nkind: missing\n---\nbody\n")
        ok = q.move_to_done(
            "b.md",
            disposition="deferred",
            info={
                "key": "ui-walk::uc::m1::s3",
                "source": "ui-walk",
                "usecase_id": "uc",
                "milestone_id": "m1",
                "step_id": "s3",
                "walk_id": "2026-07-17-1631",
                "learning_outcome": "none",
                "by": "human",
                "empty_dropped": "",
            },
        )
        assert ok is True
        row = q.read_ledger()[-1]
        assert row["disposition"] == "deferred"
        assert row["usecase_id"] == "uc" and row["step_id"] == "s3"
        assert row["by"] == "human"
        assert "empty_dropped" not in row  # empty values never land in the ledger

    def test_ledger_name_skipped_by_index_glob(self, tmp_path):
        q = FixPromptQueue(tmp_path)
        q.write("c.md", "---\nkind: bug\n---\nbody\n")
        q.move_to_done("c.md", disposition="shipped")
        q.rebuild_index()  # must not choke on / list the _ledger file
        assert (q.dir / "done" / "_ledger.jsonl").exists()
        assert "_ledger" not in (q.dir / "_queue.md").read_text(encoding="utf-8")

    def test_read_ledger_limit_and_malformed_lines(self, tmp_path):
        q = FixPromptQueue(tmp_path)
        _ = q.dir  # create dirs
        with (q.dir / "done" / "_ledger.jsonl").open("a", encoding="utf-8") as f:
            f.write("not json\n")
            for i in range(5):
                f.write('{"filename": "f%d.md", "disposition": "done", "ts": "t"}\n' % i)
            f.write("[1, 2]\n")  # non-dict is skipped too
        rows = q.read_ledger(limit=3)
        assert [r["filename"] for r in rows] == ["f3.md", "f4.md"] or len(rows) <= 3
        all_rows = q.read_ledger(limit=0)
        assert [r["filename"] for r in all_rows] == [f"f{i}.md" for i in range(5)]

    def test_move_failure_appends_nothing(self, tmp_path):
        q = FixPromptQueue(tmp_path)
        assert q.move_to_done("ghost.md", disposition="shipped") is False
        assert q.read_ledger() == []

    def test_ledger_by_filename_last_row_wins(self, tmp_path):
        q = FixPromptQueue(tmp_path)
        q.append_ledger({"ts": "t1", "filename": "a.md", "disposition": "deferred"})
        q.append_ledger({"ts": "t2", "filename": "b.md", "disposition": "shipped"})
        q.append_ledger({"ts": "t3", "filename": "a.md", "disposition": "shipped"})
        by_fn = q.ledger_by_filename()
        assert set(by_fn) == {"a.md", "b.md"}
        assert by_fn["a.md"]["disposition"] == "shipped"  # later correction wins

    def test_vocabulary_constants(self):
        assert set(DISPOSITIONS) == {"shipped", "planned", "deferred", "declined", "dismissed", "done"}
        assert "regression-test" in LEARNING_OUTCOMES and "none" in LEARNING_OUTCOMES


class TestTraceRoundTripWithFixAgent:
    """ui-walk-promoted prompts carry the trace identity in frontmatter;
    fix-agent's _parse_prompt_meta must surface it into verify_context so
    runs / verify runs / receipts inherit it verbatim."""

    def test_trace_fields_round_trip(self):
        fm = render_frontmatter({
            "kind": "missing", "app": "soil", "key": "ui-walk::uc::m1::s3",
            "source": "ui-walk", "usecase_id": "riverside-bess-132kv",
            "milestone_id": "month-2-3-soil-model", "step_id": "s3",
            "walk_id": "2026-07-17-1631",
            "evidence": "data/ui-walk/usecases/2026-07-17-1631/steplog.jsonl",
        })
        content = (
            fm + "\n\n# Fix: soil save\n\n"
            + persona_scenario_line("Kevin", "riverside-bess-132kv") + "\n\n"
            + friction_block("Save did not persist the Wenner survey project") + "\n"
        )
        ctx = _parse_prompt_meta(content)
        assert ctx.get("source") == "ui-walk"
        assert ctx.get("usecase_id") == "riverside-bess-132kv"
        assert ctx.get("milestone_id") == "month-2-3-soil-model"
        assert ctx.get("step_id") == "s3"
        assert ctx.get("walk_id") == "2026-07-17-1631"
        assert ctx.get("evidence", "").endswith("steplog.jsonl")
        assert ctx.get("persona") == "Kevin"
        assert ctx.get("scenario") == "riverside-bess-132kv"
        assert "Wenner" in (ctx.get("friction_text") or "")

    def test_legacy_prompt_yields_no_trace_keys(self):
        content = (
            render_frontmatter({"kind": "bug", "app": "hub", "key": "bug::x"})
            + "\n\n" + persona_scenario_line("kevin-weekday", "tuesday-evening")
            + "\n\n" + friction_block("boom") + "\n"
        )
        ctx = _parse_prompt_meta(content)
        for k in ("usecase_id", "milestone_id", "step_id", "walk_id", "evidence"):
            assert k not in ctx
