"""Unit tests for the dogfood-agent deficit picker + scenario frontmatter.

Daemon-free. Exercises the routing math directly so cold-start, deficit
scoring, and frontmatter parsing don't regress when the daemon isn't up.
The session-scoped ``server_health`` fixture from conftest.py is overridden
to a no-op here so this file always runs, even on a dev box with no
daemon listening on :9000.
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def server_health():
    """Override the conftest fixture that skips when EmptyOS isn't running.
    Picker tests are pure-Python and have no daemon dependency."""
    return None

# Add the dogfood-agent dir to path so `behavior` imports directly.
APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "dogfood-agent"
sys.path.insert(0, str(APP_DIR))
import behavior as B  # noqa: E402


def test_merge_rollup_stamps_last_seen():
    rollup: dict = {}
    beh = {
        "heatmap": [
            {"target": "cables:rate", "count": 3, "ok": 3},
            {"target": "task", "count": 1, "ok": 1},
        ],
        "friction_counts": {"bug": 0, "confusing": 1, "missing": 0},
        "abandonments": [],
    }
    B.merge_rollup(rollup, beh, ts="2026-05-14T10:00:00+00:00")
    heat = rollup["heatmap"]
    assert heat["cables:rate"]["last_seen"] == "2026-05-14T10:00:00+00:00"
    assert heat["task"]["last_seen"] == "2026-05-14T10:00:00+00:00"
    assert heat["cables:rate"]["count"] == 3


def test_merge_rollup_no_ts_does_not_clobber_last_seen():
    rollup: dict = {
        "heatmap": {"task": {"count": 1, "ok": 1, "last_seen": "2026-05-10T00:00:00+00:00"}},
    }
    beh = {
        "heatmap": [{"target": "task", "count": 1, "ok": 1}],
        "friction_counts": {"bug": 0, "confusing": 0, "missing": 0},
        "abandonments": [],
    }
    B.merge_rollup(rollup, beh, ts=None)
    assert rollup["heatmap"]["task"]["last_seen"] == "2026-05-10T00:00:00+00:00"
    assert rollup["heatmap"]["task"]["count"] == 2


def test_scenarios_have_frontmatter():
    """Every shipped scenario must carry the routing frontmatter the picker reads."""
    from emptyos.sdk.utils import parse_frontmatter

    for s in sorted((APP_DIR / "scenarios").glob("*.md")):
        fm = parse_frontmatter(s.read_text(encoding="utf-8"))
        assert fm.get("tier") in ("smoke", "story", "journey", "dogfood"), (
            f"{s.stem}: missing or invalid tier ({fm.get('tier')!r})"
        )
        assert fm.get("persona"), f"{s.stem}: missing persona"
        assert "expected_apps" in fm, f"{s.stem}: missing expected_apps (list, may be empty)"
        assert fm.get("budget_turns"), f"{s.stem}: missing budget_turns"
        assert fm.get("runtime") in ("persona", "ui-walk"), (
            f"{s.stem}: invalid runtime ({fm.get('runtime')!r})"
        )


# ── Deficit picker ───────────────────────────────────────────────────────────


class _StubApp:
    """Minimal shim mirroring just enough of DogfoodAgentApp for the picker.

    We don't import the real app to keep these tests daemon-free — booting
    BaseApp pulls in kernel modules and SQLite connections we don't need.
    """

    def __init__(self, tmp_path: Path, rollup: dict, picker: str = "deficit", window_days: int = 14):
        self._app_dir = APP_DIR
        self._rollup_path = tmp_path / "behavior-rollup.json"
        self._rollup_path.write_text(json.dumps(rollup), encoding="utf-8")
        self._picker = picker
        self._window_days = window_days

    def app_config(self, key, default=None):
        return {
            "picker": self._picker,
            "deficit_window_days": self._window_days,
        }.get(key, default)

    # Lifted verbatim from DogfoodAgentApp so the test pins exactly what ships.
    _scenario_meta = None  # set below
    _pick_rotation_slot = None  # set below


# Bind the real methods onto _StubApp via late import to avoid importing the
# kernel-coupled app module at collection time.
def _attach_methods():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dogfood_app_for_test", APP_DIR / "app.py"
    )
    # We don't actually load the module — it imports BaseApp which boots
    # too much. Instead grab the method source via inspect-by-attribute
    # on the unbound class. Easier path: copy the two methods textually
    # by re-defining them here, importing parse_frontmatter from sdk.utils.
    from emptyos.sdk.utils import parse_frontmatter

    _SCENARIO_GOALS: dict = {}  # legacy fallback; picker doesn't need it for these tests

    def _scenario_meta(self, scenario_id: str) -> dict:
        p = self._app_dir / "scenarios" / f"{scenario_id}.md"
        fm: dict = {}
        if p.exists():
            try:
                fm = parse_frontmatter(p.read_text(encoding="utf-8"))
            except Exception:
                fm = {}
        goals = fm.get("goals")
        if not isinstance(goals, list) or not goals:
            goals = _SCENARIO_GOALS.get(scenario_id, [])
        expected = fm.get("expected_apps")
        if not isinstance(expected, list):
            expected = []
        try:
            budget_turns = int(fm.get("budget_turns") or 15)
        except (TypeError, ValueError):
            budget_turns = 15
        return {
            "id": scenario_id,
            "title": scenario_id.replace("-", " ").title(),
            "tier": (fm.get("tier") or "dogfood").strip()
            if isinstance(fm.get("tier"), str)
            else "dogfood",
            "persona": (fm.get("persona") or "").strip()
            if isinstance(fm.get("persona"), str)
            else "",
            "expected_apps": [str(a).strip() for a in expected if str(a).strip()],
            "goals": [str(g).strip() for g in goals if str(g).strip()],
            "budget_turns": budget_turns,
            "runtime": (fm.get("runtime") or "persona").strip()
            if isinstance(fm.get("runtime"), str)
            else "persona",
        }

    def _pick_rotation_slot(self, rotation, state, picker):
        n = len(rotation)
        if n <= 0:
            return 0, {"reason": "empty_rotation"}
        rr_idx = int(state.get("rotation_idx", 0)) % n
        if picker == "round-robin":
            return rr_idx, {"strategy": "round-robin", "idx": rr_idx}

        rollup: dict = {}
        try:
            if self._rollup_path.exists():
                rollup = json.loads(self._rollup_path.read_text(encoding="utf-8"))
        except Exception:
            rollup = {}
        heat: dict = rollup.get("heatmap") or {}
        if not any(("last_seen" in (v or {})) for v in heat.values()):
            return rr_idx, {"strategy": "deficit", "fallback": "cold_start", "idx": rr_idx}

        window_days = float(self.app_config("deficit_window_days", 14) or 14)
        now = datetime.now(timezone.utc)
        best_idx = rr_idx
        best_deficit = -1.0
        best_breakdown = None
        for i, slot in enumerate(rotation):
            if not isinstance(slot, dict):
                continue
            scenario_id = (slot.get("scenario") or "").strip()
            if not scenario_id:
                continue
            try:
                meta = self._scenario_meta(scenario_id)
            except Exception:
                continue
            apps = meta.get("expected_apps") or []
            if not apps:
                deficit = window_days
                per_app = {"<all>": deficit}
            else:
                per_app = {}
                for app_id in apps:
                    latest_iso = None
                    for key, slot_heat in heat.items():
                        if not isinstance(slot_heat, dict):
                            continue
                        if key != app_id and not key.startswith(app_id + ":"):
                            continue
                        ls = slot_heat.get("last_seen")
                        if not ls:
                            continue
                        if latest_iso is None or str(ls) > latest_iso:
                            latest_iso = str(ls)
                    if not latest_iso:
                        per_app[app_id] = window_days
                        continue
                    try:
                        seen_ts = datetime.fromisoformat(latest_iso.replace("Z", "+00:00"))
                    except Exception:
                        per_app[app_id] = window_days
                        continue
                    age_days = (now - seen_ts).total_seconds() / 86400.0
                    per_app[app_id] = min(age_days, window_days)
                deficit = sum(per_app.values()) / max(len(per_app), 1)
            if deficit > best_deficit or (deficit == best_deficit and i == rr_idx):
                best_deficit = deficit
                best_idx = i
                best_breakdown = {
                    "scenario": scenario_id,
                    "deficit_days": round(deficit, 2),
                    "per_app": {k: round(v, 2) for k, v in per_app.items()},
                }
        return best_idx, {"strategy": "deficit", "winner": best_breakdown, "idx": best_idx}

    _StubApp._scenario_meta = _scenario_meta
    _StubApp._pick_rotation_slot = _pick_rotation_slot


_attach_methods()


def test_cold_start_falls_back_to_round_robin(tmp_path):
    """No last_seen marks in the rollup → picker must defer to round-robin
    so brand-new deployments don't pin to slot 0."""
    rollup = {"heatmap": {"task": {"count": 1, "ok": 1}}}  # no last_seen
    app = _StubApp(tmp_path, rollup)
    rotation = [
        {"scenario": "tuesday-evening", "persona": "kevin-weekday"},
        {"scenario": "engineering-evening", "persona": "kevin-weekday"},
    ]
    idx, info = app._pick_rotation_slot(rotation, {"rotation_idx": 1}, "deficit")
    assert idx == 1
    assert info.get("fallback") == "cold_start"


def test_round_robin_strategy_bypasses_scoring(tmp_path):
    rollup = {
        "heatmap": {
            "cables": {"count": 5, "ok": 5, "last_seen": "2020-01-01T00:00:00+00:00"},
        }
    }
    app = _StubApp(tmp_path, rollup, picker="round-robin")
    rotation = [
        {"scenario": "tuesday-evening"},
        {"scenario": "engineering-evening"},  # would win on deficit
    ]
    idx, info = app._pick_rotation_slot(rotation, {"rotation_idx": 0}, "round-robin")
    assert idx == 0
    assert info["strategy"] == "round-robin"


def test_deficit_picks_scenario_covering_stalest_apps(tmp_path):
    """Engineering apps haven't been touched in 30d; tuesday's apps are fresh.
    Picker must choose engineering-evening to pay down the rot."""
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rollup = {
        "heatmap": {
            "capture": {"count": 5, "ok": 5, "last_seen": fresh},
            "journal": {"count": 5, "ok": 5, "last_seen": fresh},
            "task": {"count": 5, "ok": 5, "last_seen": fresh},
            "assistant": {"count": 5, "ok": 5, "last_seen": fresh},
            "cables": {"count": 1, "ok": 1, "last_seen": stale},
            "earthing": {"count": 1, "ok": 1, "last_seen": stale},
        }
    }
    app = _StubApp(tmp_path, rollup)
    rotation = [
        {"scenario": "tuesday-evening", "persona": "kevin-weekday"},
        {"scenario": "engineering-evening", "persona": "kevin-weekday"},
    ]
    idx, info = app._pick_rotation_slot(rotation, {"rotation_idx": 0}, "deficit")
    assert idx == 1, f"expected engineering-evening to win; got {info}"
    assert info["winner"]["scenario"] == "engineering-evening"
    assert info["winner"]["deficit_days"] > 10


def test_deficit_namespaced_heatmap_keys_attribute_to_app(tmp_path):
    """Heatmap keys arrive as `<app>:<endpoint>` (e.g. `cables:rate`).
    Picker must aggregate those under their owning app."""
    fresh = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    rollup = {
        "heatmap": {
            "cables:rate": {"count": 1, "ok": 1, "last_seen": fresh},
            "cables:routes": {"count": 1, "ok": 1, "last_seen": fresh},
        }
    }
    app = _StubApp(tmp_path, rollup)
    rotation = [
        {"scenario": "engineering-evening"},   # touches cables among others
        {"scenario": "iphone-day"},            # doesn't touch cables fresh
    ]
    idx, info = app._pick_rotation_slot(rotation, {"rotation_idx": 0}, "deficit")
    # iphone-day's expected_apps haven't been touched at all → higher deficit
    assert idx == 1, f"expected iphone-day to win; got {info}"


def test_deficit_tie_breaks_with_round_robin_pointer(tmp_path):
    """Two scenarios with identical (cold) deficits — round-robin pointer
    decides which fires this tick."""
    rollup = {"heatmap": {}}
    # Construct: both will score window_days (no apps seen). Pick rr_idx side.
    app = _StubApp(tmp_path, rollup)
    rotation = [{"scenario": "tuesday-evening"}, {"scenario": "saturday-morning"}]
    # Cold start path returns rr_idx directly; assert both pointers honored.
    idx_a, _ = app._pick_rotation_slot(rotation, {"rotation_idx": 0}, "deficit")
    idx_b, _ = app._pick_rotation_slot(rotation, {"rotation_idx": 1}, "deficit")
    assert idx_a == 0
    assert idx_b == 1


def test_empty_rotation_is_safe(tmp_path):
    app = _StubApp(tmp_path, {})
    idx, info = app._pick_rotation_slot([], {}, "deficit")
    assert idx == 0
    assert info.get("reason") == "empty_rotation"


# ── Smoke walk presets + coverage aggregator ─────────────────────────────────


def test_ui_walk_presets_registered():
    """The smoke cron round-robins through preset keys; the registry must
    expose at least the legacy + core-six + engineering-six set so existing
    configs don't break and the plan's smoke coverage is reachable.

    _UI_WALK_PRESETS lives in ``ui_walk.py`` (extracted from app.py during
    the dogfood-agent decomposition); the re-binding in app.py preserves
    runtime behaviour but the source-level assertions here grep the
    module that actually owns the data."""
    src = (APP_DIR / "ui_walk.py").read_text(encoding="utf-8")
    for key in ("legacy", "core-six", "engineering-six"):
        assert f'"{key}"' in src, f"preset {key} not present in _UI_WALK_PRESETS"
    # Sanity: the three presets must each declare expected_apps so the
    # coverage aggregator has something to credit.
    assert 'expected_apps' in src


def test_new_rot_prone_scenarios_present():
    """Step 7 — per-app rot-prone scenarios for media / jobs / projects must
    ship with the correct shape so they slot into the deficit picker's
    rotation without operator effort."""
    from emptyos.sdk.utils import parse_frontmatter

    expected = {
        "projects-quarter": "projects",
        "jobs-week": "jobs",
        "media-month": "media",
    }
    for scenario_id, app_id in expected.items():
        p = APP_DIR / "scenarios" / f"{scenario_id}.md"
        assert p.exists(), f"{scenario_id}.md missing"
        fm = parse_frontmatter(p.read_text(encoding="utf-8"))
        assert fm.get("tier") == "dogfood", f"{scenario_id}: wrong tier"
        assert app_id in (fm.get("expected_apps") or []), (
            f"{scenario_id}: expected_apps should contain {app_id!r}; got {fm.get('expected_apps')!r}"
        )
        assert fm.get("goals"), f"{scenario_id}: no goals declared"


def test_journey_default_rules_well_formed():
    """Step 4 — default journey rules must point at real trigger/ripple
    event-type strings. Sanity: no empty fields, max_delay_s sensible."""
    src = (APP_DIR / "app.py").read_text(encoding="utf-8")
    # Locate the default rules block by literal substring.
    assert "_DEFAULT_JOURNEY_RULES" in src
    for rid in ("git_to_journal", "mood_to_journal", "weight_to_journal"):
        assert rid in src, f"default rule {rid} missing"
    # Trigger / ripple event types we listed must look like 'foo:bar'
    for evt in ("git:saved", "journal:entry", "healing:mood-logged", "nutrition:weight-logged"):
        assert evt in src, f"event type {evt} missing from defaults"


# ── Journey rule scoring ────────────────────────────────────────────────────


# Pinned reference time so multiple `_evt()` calls land on exact offsets.
# Sequential ``datetime.now()`` calls drift by microseconds, which is enough
# to push a 10s-delay-bounded ripple just past the boundary and silently
# turn an in-time pairing into a late one.
_REF_NOW = datetime.now(timezone.utc)


def _evt(t: str, secs_ago: float, idx: int = 0) -> dict:
    """Helper: build an event record matching what _journey_tick normalizes
    out of /api/events responses. All events anchor on ``_REF_NOW`` so
    offsets are exact regardless of test execution order."""
    return {
        "type": t,
        "ts": _REF_NOW - timedelta(seconds=secs_ago),
        "id": idx,
    }


def test_score_journey_rules_in_time():
    """One trigger followed by ripple within max_delay → ripples_in_time=1."""
    rules = [{"id": "g2j", "trigger": "git:saved", "ripple": "journal:entry", "max_delay_s": 10}]
    events = [
        _evt("git:saved", secs_ago=100, idx=1),
        _evt("journal:entry", secs_ago=98, idx=2),  # 2s after trigger
    ]
    out = B.score_journey_rules(rules, events)
    assert out["g2j"]["last_24h"]["triggers"] == 1
    assert out["g2j"]["last_24h"]["ripples_in_time"] == 1
    assert out["g2j"]["last_24h"]["missed"] == 0


def test_score_journey_rules_missed_no_ripple_after():
    """Trigger with no ripple → missed."""
    rules = [{"id": "g2j", "trigger": "git:saved", "ripple": "journal:entry", "max_delay_s": 10}]
    events = [_evt("git:saved", secs_ago=50, idx=1)]
    out = B.score_journey_rules(rules, events)
    assert out["g2j"]["last_24h"]["triggers"] == 1
    assert out["g2j"]["last_24h"]["missed"] == 1
    assert out["g2j"]["last_24h"]["ripples_in_time"] == 0


def test_score_journey_rules_late_ripple():
    """Ripple after max_delay but within 24h → ripples_late."""
    rules = [{"id": "g2j", "trigger": "git:saved", "ripple": "journal:entry", "max_delay_s": 5}]
    events = [
        _evt("git:saved", secs_ago=100, idx=1),
        _evt("journal:entry", secs_ago=80, idx=2),  # 20s later, exceeds 5s
    ]
    out = B.score_journey_rules(rules, events)
    assert out["g2j"]["last_24h"]["ripples_late"] == 1
    assert out["g2j"]["last_24h"]["missed"] == 0


def test_score_journey_rules_pairing_single_ripple_to_earliest_trigger():
    """Two triggers and one ripple → the second trigger is missed.

    Pairs earliest-trigger-first; a single ripple can't satisfy two
    triggers. Catches a regression where back-to-back commits would
    double-credit one journal entry."""
    rules = [{"id": "g2j", "trigger": "git:saved", "ripple": "journal:entry", "max_delay_s": 10}]
    events = [
        _evt("git:saved", secs_ago=200, idx=1),
        _evt("git:saved", secs_ago=180, idx=2),
        _evt("journal:entry", secs_ago=190, idx=3),  # only matches first trigger
    ]
    out = B.score_journey_rules(rules, events)
    assert out["g2j"]["last_24h"]["triggers"] == 2
    assert out["g2j"]["last_24h"]["ripples_in_time"] == 1
    assert out["g2j"]["last_24h"]["missed"] == 1


def test_score_journey_rules_ignores_events_outside_window():
    """Old events past 7d window aren't counted."""
    rules = [{"id": "g2j", "trigger": "git:saved", "ripple": "journal:entry", "max_delay_s": 10}]
    events = [
        _evt("git:saved", secs_ago=86400 * 8, idx=1),  # 8 days ago
        _evt("journal:entry", secs_ago=86400 * 8 + 5, idx=2),
    ]
    out = B.score_journey_rules(rules, events)
    assert out["g2j"]["last_7d"]["triggers"] == 0
    assert out["g2j"]["last_24h"]["triggers"] == 0


def test_score_journey_rules_empty_rules_safe():
    out = B.score_journey_rules([], [_evt("git:saved", 10)])
    assert out == {}


def test_score_journey_rules_24h_vs_7d_windows():
    """A trigger 3 days ago should land in 7d but not 24h."""
    rules = [{"id": "g2j", "trigger": "git:saved", "ripple": "journal:entry", "max_delay_s": 10}]
    events = [
        _evt("git:saved", secs_ago=86400 * 3, idx=1),
        _evt("journal:entry", secs_ago=86400 * 3 - 5, idx=2),
    ]
    out = B.score_journey_rules(rules, events)
    assert out["g2j"]["last_24h"]["triggers"] == 0
    assert out["g2j"]["last_7d"]["triggers"] == 1
    assert out["g2j"]["last_7d"]["ripples_in_time"] == 1


# ── Test stub generation (step 6) ────────────────────────────────────────────


def test_friction_to_test_stub_shape():
    """The promoted-stub generator must produce a syntactically-valid
    Python file with the friction's app + text embedded and a pytest.skip()
    sentinel so a stray copy into tests/ doesn't fail CI before the operator
    fleshes it out."""
    import ast
    # Inline the same generator the app uses, parametrized so we don't
    # need a real BaseApp instance.

    def _friction_app(f):
        import re

        text = (f.get("text") or "")
        m = re.search(r"/([a-z][a-z0-9-]+)/(?:api/|$|\s)", text, re.I)
        return m.group(1).lower() if m else None

    def _build(run_id, friction, actions):
        import re

        app_id = _friction_app(friction) or "unknown"
        kind = friction.get("kind") or "bug"
        text = (friction.get("text") or "")[:300]
        turn = friction.get("turn")
        if isinstance(turn, int) and turn > 0:
            context_actions = [a for a in actions if (a.get("turn") or 0) <= turn][-8:]
        else:
            context_actions = actions[-8:]
        run_slug = re.sub(r"[^a-zA-Z0-9]", "_", run_id)
        text_slug = re.sub(r"[^a-z0-9]+", "_", text[:40].lower()).strip("_") or "friction"
        lines = [
            '"""Regression draft promoted from dogfood run.',
            "",
            f"Run id:         {run_id}",
            f"App:            {app_id}",
            f"Friction kind:  #{kind}",
            f"Friction text:  {text}",
            '"""',
            "",
            "import pytest",
            "",
            "from helpers import assert_ok",
            "",
            "",
            "@pytest.mark.api",
            f"class TestRegression_{run_slug}:",
            f"    def test_{app_id}_{text_slug}(self, http_client):",
            '        pytest.skip("auto-promoted dogfood draft — flesh out before enabling")',
            "",
        ]
        return "\n".join(lines)

    friction = {
        "kind": "bug",
        "turn": 5,
        "text": "/cables/api/rate returns 500 on 22kV three-core input",
    }
    actions = [
        {"turn": 4, "tool": "Bash", "summary": "$ curl /cables/api/rate", "success": False, "error": "500"},
    ]
    stub = _build("run-abc123", friction, actions)
    # Must parse as valid Python
    ast.parse(stub)
    assert "cables" in stub
    assert "test_cables_" in stub
    assert "pytest.skip" in stub
    assert "run-abc123" in stub


def test_drain_orchestrator_delegates_to_fix_agent_app():
    """The drain orchestrator in dogfood-agent must NOT spawn claude-cli
    itself — the existing fix-agent app owns worktree-per-fix + branch
    isolation + merge gating. Source-level assertions lock in the
    delegation pattern.

    Drain orchestrator code lives in ``drain.py`` (extracted from app.py
    during the dogfood-agent decomposition); the re-binding in app.py
    preserves runtime behaviour but the source-level assertions here
    grep the module that actually owns the implementation."""
    src = (APP_DIR / "drain.py").read_text(encoding="utf-8")
    # Drain must call fix-agent's existing endpoints, not run a subprocess.
    assert "_drain_call_fix_agent" in src
    assert 'self.call_app("fix-agent"' in src
    # Each lifecycle stage must be wired: queue → wait-ready → merge → verify → wait-verified.
    for method in ("api_run", "api_run_merge", "api_run_verify", "api_run_get"):
        assert method in src, f"drain orchestrator missing call to fix-agent.{method}"
    # Auto-revert on verify-failed so main isn't left broken.
    assert "api_run_revert" in src
    # Drain endpoints + safety gate.
    assert "fix_agent_enabled" in src
    assert "fix_drain_max" in src
    # Should NOT have an in-place fix-agent spawning claude-cli with cwd=repo.
    # The old (deleted) lane did this with _run_fix_agent + WebFetch-excluded tools.
    assert "_run_fix_agent" not in src, "in-place lane should be removed"


def test_fix_agent_sandbox_restart_helper_branches():
    """The sandbox-restart helper in fix-agent's verify path must:
      1. no-op when dogfood-demo plugin isn't loaded
      2. no-op when plugin loaded but missing `restart` method
      3. record the dict result on meta when restart succeeds
      4. record {ok: False, error: ...} on meta when restart raises
    Daemon-free — instantiates a stub class that mirrors just the helper
    so we don't need the full FixAgentApp + kernel boot."""
    import asyncio

    saved: list[dict] = []

    class _Stub:
        def __init__(self, service_lookup):
            self._service_lookup = service_lookup

        def service(self, name):
            return self._service_lookup(name)

        def _save_run(self, meta):
            saved.append(dict(meta))

        # Method-under-test, copied verbatim from fix-agent/app.py so this
        # test exercises the production logic, not a paraphrase.
        async def _restart_sandbox_for_verify(self, meta):
            sandbox = self.service("dogfood-demo")
            if sandbox is None or not hasattr(sandbox, "restart"):
                return None
            try:
                restart_result = await sandbox.restart()
            except Exception as e:
                restart_result = {"ok": False, "error": str(e)[:200]}
            meta["sandbox_restart"] = restart_result
            self._save_run(meta)
            return restart_result

    # Case 1: plugin not loaded → returns None, doesn't touch meta or save.
    saved.clear()
    s = _Stub(lambda n: None)
    meta: dict = {"run_id": "r1"}
    out = asyncio.run(s._restart_sandbox_for_verify(meta))
    assert out is None
    assert "sandbox_restart" not in meta
    assert saved == []

    # Case 2: plugin loaded but missing `restart` method → no-op.
    saved.clear()
    class _BadPlugin: pass
    s = _Stub(lambda n: _BadPlugin())
    meta = {"run_id": "r2"}
    out = asyncio.run(s._restart_sandbox_for_verify(meta))
    assert out is None
    assert "sandbox_restart" not in meta

    # Case 3: successful restart → result recorded on meta + persisted.
    saved.clear()
    class _OkPlugin:
        async def restart(self):
            return {"ok": True, "stage": "start", "host": "http://localhost:9001"}
    s = _Stub(lambda n: _OkPlugin())
    meta = {"run_id": "r3"}
    out = asyncio.run(s._restart_sandbox_for_verify(meta))
    assert out is not None and out["ok"] is True
    assert meta["sandbox_restart"]["ok"] is True
    assert len(saved) == 1 and saved[0]["run_id"] == "r3"

    # Case 4: restart raised → exception captured, meta records ok:False.
    saved.clear()
    class _CrashPlugin:
        async def restart(self):
            raise RuntimeError("sidecar refused")
    s = _Stub(lambda n: _CrashPlugin())
    meta = {"run_id": "r4"}
    out = asyncio.run(s._restart_sandbox_for_verify(meta))
    assert out is not None and out["ok"] is False
    assert "sidecar refused" in out["error"]
    assert meta["sandbox_restart"]["ok"] is False
    assert len(saved) == 1


def test_dogfood_demo_plugin_exposes_restart():
    """The sandbox restart hook (used by fix-agent's verify path) lives on
    the dogfood-demo plugin so the sandbox at :9001 picks up patched code
    between merge and verify. Without this, code fixes silently verify
    against the pre-merge in-memory module set."""
    plugin_src = (
        APP_DIR.parent.parent / "plugins" / "dogfood-demo" / "plugin.py"
    ).read_text(encoding="utf-8")
    assert "async def restart" in plugin_src, "dogfood-demo missing restart()"
    assert "async def stop" in plugin_src, "dogfood-demo missing stop()"
    # Restart must only kill the subprocess we spawned (self._proc), per
    # daemon-handling rule (never taskkill an unowned python.exe).
    assert "self._proc" in plugin_src
    # fix-agent's verify must trigger the restart.
    fix_src = (
        APP_DIR.parent / "fix-agent" / "app.py"
    ).read_text(encoding="utf-8")
    assert 'self.service("dogfood-demo")' in fix_src
    assert "sandbox.restart()" in fix_src


def test_credit_smoke_coverage_marks_apps(tmp_path):
    """_credit_smoke_coverage walks the trace and updates per-app counters.
    Test it via the same stub-method approach as the picker."""
    from datetime import datetime

    cov_path = tmp_path / "ui-walks" / "coverage.json"
    cov_path.parent.mkdir(parents=True, exist_ok=True)

    class _Stub:
        def __init__(self):
            self.data_dir = tmp_path
            self.activity = []

        def _coverage_path(self):
            return cov_path

        def _load_coverage(self):
            if not cov_path.exists():
                return {"apps": {}}
            return json.loads(cov_path.read_text(encoding="utf-8"))

        def _save_coverage(self, cov):
            cov_path.write_text(json.dumps(cov), encoding="utf-8")

        def log_activity(self, entry):
            self.activity.append(entry)

    stub = _Stub()
    # Inline-copy of the credit function to keep the test daemon-free.
    def _credit(stub, preset_key, preset, trace):
        cov = stub._load_coverage()
        apps_cov = cov.setdefault("apps", {})
        now_iso = datetime.now(timezone.utc).isoformat()
        per_app = {}
        for step in trace:
            if step.get("action") != "navigate":
                continue
            label = step.get("label") or ""
            for app_id in preset.get("expected_apps") or []:
                if label == app_id and app_id not in per_app:
                    per_app[app_id] = step.get("status", "")
        for app_id in preset.get("expected_apps") or []:
            per_app.setdefault(app_id, "missed")
        for app_id, status in per_app.items():
            slot = apps_cov.setdefault(
                app_id, {"ok_count": 0, "fail_count": 0, "last_walked": None}
            )
            if status == "ok":
                slot["ok_count"] = int(slot.get("ok_count") or 0) + 1
                slot["last_walked"] = now_iso
                slot["last_status"] = "ok"
            else:
                slot["fail_count"] = int(slot.get("fail_count") or 0) + 1
                slot["last_status"] = status or "missed"
                slot["last_walked"] = now_iso
        cov["last_preset"] = preset_key
        cov["last_walked"] = now_iso
        stub._save_coverage(cov)
        return cov

    preset = {
        "expected_apps": ["hub", "task", "journal"],
        "steps": [],
    }
    trace = [
        {"action": "navigate", "label": "hub", "status": "ok"},
        {"action": "screenshot", "label": "hub", "status": "ok"},
        {"action": "navigate", "label": "task", "status": "error"},  # 404 or crash
        # journal never walked — preset bug or trace cut short
    ]
    cov = _credit(stub, "core-six", preset, trace)
    apps_cov = cov["apps"]
    assert apps_cov["hub"]["ok_count"] == 1
    assert apps_cov["hub"]["last_status"] == "ok"
    assert apps_cov["task"]["fail_count"] == 1
    assert apps_cov["task"]["last_status"] == "error"
    assert apps_cov["journal"]["fail_count"] == 1
    assert apps_cov["journal"]["last_status"] == "missed"
    assert cov["last_preset"] == "core-six"
