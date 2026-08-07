"""Shared constants and utilities for EmptyOS E2E tests."""
import importlib.util
import os
import sys
import types
from pathlib import Path

TEST_PREFIX = "PLAYWRIGHT-TEST-"

# Sentinel days for apps whose writes land in a *shared, real* vault note.
# A worklog day note is one file per calendar day, and its Plan/Update sections
# are REPLACED on write — so a test targeting date.today() silently destroys
# whatever the user wrote that morning, and the leak-guard backstop cannot undo
# it (it refuses to auto-edit prose, correctly). Pointing writes at a synthetic
# past day makes the whole file a test artifact: deletable as a unit, and no
# real note is ever opened. Cleanup lives in conftest's Worklog block.
WORKLOG_TEST_DATE = "1990-01-02"
# Separate day so the employer-conflict test owns a pristine employer slot
# (employer is day-level state — the first writer wins for the whole file).
WORKLOG_EMPLOYER_TEST_DATE = "1990-01-03"
WORKLOG_TEST_DATES = (WORKLOG_TEST_DATE, WORKLOG_EMPLOYER_TEST_DATE)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def app_path(app_id: str) -> Path:
    """Resolve an app's on-disk dir anywhere in the track tree (public/
    extension/personal), via the shared depth-agnostic scanner.

    Replaces hardcoded ``REPO/"apps"/<id>`` in tests now that apps live under
    track folders (apps/public/<group>/<id>, apps/extension/<group>/<id>).
    Personal apps stay at apps/personal/<id> and resolve the same way.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from emptyos.sdk.app_layout import resolve_app_dir

    d = resolve_app_dir(_REPO_ROOT / "apps", app_id, include_personal=True)
    if d is None:
        raise FileNotFoundError(f"app '{app_id}' not found under apps/")
    return d


def requires_app(app_id: str, *, file: str = ""):
    """Module-level skip marker for a test that needs a possibly-absent app.

    ``apps/personal/`` is gitignored, so those apps exist only on the authoring
    machine. A tracked test that loads one **fails** in a fresh clone rather
    than skipping, and a failing test reads as a regression — indistinguishable
    from something actually broken. Measured 2026-08-01: 41 such failures plus
    two collection errors that aborted the whole run.

    Use at module level, not per loader::

        pytestmark = requires_app("music-studio", file="source_quality.py")

    Per-loader guards are what produced the partial ones — a file with three
    loaders had a skip on one, so five tests failed while the rest skipped —
    and they never cover a test whose dependency is a ``read_text()`` rather
    than a fixture.

    ``file`` narrows the check to one artifact inside the app (a module, a
    page) for tests that read it directly.
    """
    import pytest

    try:
        app_dir = app_path(app_id)
    except FileNotFoundError:
        return pytest.mark.skipif(True, reason=f"app '{app_id}' is not installed")
    present = (app_dir / file).exists() if file else True
    what = f"{app_id}/{file}" if file else app_id
    return pytest.mark.skipif(not present, reason=f"'{what}' is not installed")


def load_app_module(app_id: str, module: str, *, preload: tuple[str, ...] = ()):
    """Load ``<app_dir>/<module>.py`` as ``apps.<app_id>.<module>`` with the
    parent packages registered in ``sys.modules``, so the app's relative
    imports resolve without booting the kernel (the standard unit-test shape
    from ``.claude/rules/multi-module-apps.md`` § Test fixtures).

    ``preload`` names sibling modules the target imports relatively (e.g.
    ``("shared",)`` when it does ``from .shared import ...``). Already-loaded
    modules are reused, so multiple test files share one exec per module.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    app_dir = app_path(app_id)
    if "apps" not in sys.modules:
        apps_pkg = types.ModuleType("apps")
        apps_pkg.__path__ = [str(_REPO_ROOT / "apps")]
        sys.modules["apps"] = apps_pkg
    pkg_name = f"apps.{app_id}"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(app_dir)]
        sys.modules[pkg_name] = pkg

    def _load(name: str):
        full = f"{pkg_name}.{name}"
        if full in sys.modules:
            return sys.modules[full]
        spec = importlib.util.spec_from_file_location(full, app_dir / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        return mod

    for name in preload:
        _load(name)
    return _load(module)


# Allow override so tests can target a sandbox-pool member (`:9002+`) or the
# dogfood daemon (`:9001`) without editing the file. Default is the main
# user-owned daemon; CI uses the default.
# 127.0.0.1, never "localhost": the daemon binds IPv4 loopback only, while
# localhost resolves to ::1 first on Windows. With the daemon UP that costs a
# failed probe; with it DOWN nothing listens on ::1, so connect() BLOCKS instead
# of refusing — which hangs the whole suite rather than skipping it.
# See .claude/rules/environment.md.
BASE_URL = os.environ.get("EOS_TEST_BASE_URL", "http://127.0.0.1:9000")

# All app web prefixes (dynamically overridden by conftest if server is up)
ALL_APP_PREFIXES = [
    "/3d-studio/", "/ai-queue/", "/app-analytics/", "/app-gen/", "/assistant/",
    "/billing/", "/bookmarks/", "/briefing/", "/quick-action/",
    "/condition-map/", "/contacts/", "/dictionary/", "/digest/",
    "/divination/", "/english/", "/expense/", "/fiction-engine/", "/finance/",
    "/focus/", "/git/", "/healing/", "/hello/", "/hub/", "/improv/",
    "/integrity/", "/items/", "/jobs/", "/journal/", "/lessons/", "/link/",
    "/media/", "/meditation/", "/model-bench/",
    "/music-studio/", "/news-center/", "/note/", "/nutrition/",
    "/places/", "/podcast/", "/projects/", "/quickref/",
    "/quotes/", "/rag-eval/", "/reactor/", "/recipes/", "/reflect/", "/release/",
    "/reminders/", "/review/", "/rooms/", "/search/", "/settings/", "/shadowing/",
    "/speaking/", "/staff/", "/studio/",
    "/system-log/", "/task/", "/timeline/", "/tracker/", "/tts/",
    "/voice-review/", "/weather/", "/web-analytics/",
]
# Note: compose, lyrics, music, mv-creator retired — consolidated into music-studio.
# comfyui-app retired — consolidated into studio (workflows tab).
# sleep, workout, habits retired — consolidated into healing (sleep/workout/habits tabs).
# plugin-gen retired — merged into app-gen. sheath-voltage retired — merged into cable.
# run has a custom UI (see tests/test_sys_run.py); tmpl, cable use auto-UI — API-only.

# GET endpoints per app (non-LLM). Used by Tier 2 parametrized tests.
APP_GET_ENDPOINTS = {
    "expense": [
        "/api/list", "/api/summary", "/api/budget", "/api/presets",
        "/api/heatmap", "/api/week-compare", "/api/recurring",
        "/api/category-trend", "/api/ytd", "/api/daily-avg",
    ],
    "journal": [
        "/api/today", "/api/recent", "/api/heatmap", "/api/mood-trend",
        "/api/streak", "/api/word-count", "/api/milestones", "/api/pins",
        "/api/templates",
    ],
    "healing": [
        "/api/trend", "/api/history", "/api/streak", "/api/care-check",
        "/api/correlations", "/api/mood-calendar", "/api/dreams",
        "/api/activity-presets", "/api/sleep/stats",
    ],
    "nutrition": [
        "/api/today", "/api/targets", "/api/streak", "/api/weekly-stats",
        "/api/food-db", "/api/calorie-ranking", "/api/protein-streak",
        "/api/water", "/api/favorites", "/api/recent-foods",
        "/api/weight", "/api/weight-stats",
    ],
    "task": [
        "/api/tasks", "/api/list", "/api/calendar", "/api/focus",
        "/api/tags", "/api/stats", "/api/by-context", "/api/recurring",
    ],
    "items": [
        "/api/items", "/api/warranty-alerts", "/api/categories",
        "/api/locations", "/api/stats",
    ],
    "search": ["/api/recent", "/api/stats"],
    "billing": ["/api/today", "/api/monthly", "/api/usage", "/api/rates", "/api/budget"],
    "reactor": ["/api/log"],
    "integrity": ["/api/audit"],
    "staff": ["/api/staff", "/api/activity"],
    # Hub rebuilt into a companion command surface (home-companion redesign) —
    # the old panel-wall endpoints (today/goals/streaks/wellness/…) were replaced
    # by a deterministic digest + lazy next-move + panel aggregation.
    "hub": [
        "/api/digest", "/api/next-move", "/api/panels", "/api/panels/all",
    ],
    "quotes": ["/api/quote"],
    "improv": ["/api/exercises", "/api/capabilities", "/api/sessions"],
    "contacts": ["/api/list"],
    "projects": [
        "/api/list", "/api/projects", "/api/deadlines", "/api/all-tasks",
        "/api/type-config",
    ],
    "media": ["/api/list", "/api/stats", "/api/highlights", "/api/hl-stats"],
    # "music" retired — replaced by "music-studio" (see APP_GET_ENDPOINTS below)
    "places": ["/api/places", "/api/categories", "/api/stats"],
    "quickref": ["/api/cards"],
    "settings": ["/api/config", "/api/shortcuts", "/api/get"],
    "app-analytics": ["/api/analytics", "/api/active-apps", "/api/daily", "/api/vault/stats"],
    "english": ["/api/stats", "/api/activity", "/api/dashboard", "/api/level"],
    "focus": [
        "/api/stats", "/api/history", "/api/streak", "/api/heatmap",
        "/api/weekly", "/api/goal", "/api/config",
        "/api/breaks", "/api/distraction-stats",
    ],
    "timeline": ["/api/events"],
    "news-center": ["/api/sources", "/api/articles", "/api/stats"],
    "rooms": ["/api/agents"],
    # System apps from Phase 2
    "quick-action": [
        "/api/list", "/api/stats", "/api/recent", "/api/pending",
    ],
    "assistant": [
        "/api/sessions", "/api/slash-commands", "/api/providers",
    ],
    # Library + lyrics surfaces were folded into compose/visual (music_library
    # rework) — these are the current GET endpoints.
    "music-studio": [
        "/api/compose/styles", "/api/compose/status", "/api/compose/history",
        "/api/visual/history", "/api/visual/songs", "/api/visual/modes",
        "/api/visual/presets", "/api/visual/runs",
    ],
    # Personal apps
    "bookmarks": [
        "/api/bookmarks", "/api/tags", "/api/stats",
    ],
    "weather": [
        "/api/current", "/api/forecast", "/api/history", "/api/config-status",
    ],
    "recipes": [
        "/api/recipes", "/api/tags", "/api/stats",
    ],
    "briefing": [
        "/api/briefing", "/api/frogs", "/api/health-score", "/api/what-now",
        "/api/weather", "/api/schedule", "/api/yesterday", "/api/upcoming",
        "/api/daily-progress", "/api/events", "/api/birthdays",
    ],
}

# System-level GET endpoints
SYSTEM_ENDPOINTS = [
    "/api/health",
    "/api/apps",
    "/api/capabilities",
    "/api/events",
    "/api/plugins",
    "/api/services",
]

# Endpoints requiring LLM (skip when unavailable)
LLM_ENDPOINTS = {
    "/expense/api/ai-insight",
    "/healing/api/insight",
    "/journal/api/reflect",
    "/journal/api/ai-reflect",
    "/search/api/ask",
    "/search/api/suggest",
    "/nutrition/api/suggestion",
    "/nutrition/api/plan",
    "/hub/api/narrative",
    "/briefing/api/brief",
    "/briefing/api/ai-summary",
    "/briefing/api/nudge",
    "/focus/api/suggest",
    "/quick-action/api/smart-add",
    "/recipes/api/generate",
    "/recipes/api/suggest",
    "/bookmarks/api/save",  # may use LLM for title/summary extraction
}


def assert_list_response(resp, min_len=0):
    """Assert response is JSON list with at least min_len items."""
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
    data = resp.json()
    assert isinstance(data, list), f"Expected list, got {type(data).__name__}: {str(data)[:300]}"
    assert len(data) >= min_len, f"Expected >= {min_len} items, got {len(data)}"
    return data


def assert_dict_response(resp, required_keys=None):
    """Assert response is JSON dict with required keys."""
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
    data = resp.json()
    assert isinstance(data, dict), f"Expected dict, got {type(data).__name__}: {str(data)[:300]}"
    if required_keys:
        missing = [k for k in required_keys if k not in data]
        assert not missing, f"Missing keys {missing} in response: {list(data.keys())}"
    return data


def assert_ok(resp):
    """Assert status 200 and return JSON body."""
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
    return resp.json()
