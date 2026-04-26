"""Shared constants and utilities for EmptyOS E2E tests."""

TEST_PREFIX = "PLAYWRIGHT-TEST-"
BASE_URL = "http://localhost:9000"

# All app web prefixes (dynamically overridden by conftest if server is up)
ALL_APP_PREFIXES = [
    "/3d-studio/", "/ai-queue/", "/app-analytics/", "/app-gen/", "/assistant/",
    "/billing/", "/bookmarks/", "/briefing/", "/quick-action/",
    "/contacts/", "/dictionary/", "/digest/",
    "/divination/", "/english/", "/expense/", "/fiction-engine/", "/finance/",
    "/focus/", "/git/", "/gpts/", "/healing/", "/hello/", "/hub/",
    "/integrity/", "/items/", "/jobs/", "/journal/", "/lessons/", "/link/",
    "/media/", "/meditation/", "/model-bench/",
    "/music-studio/", "/news-center/", "/note/", "/nutrition/",
    "/places/", "/podcast/", "/projects/", "/quickref/",
    "/quotes/", "/reactor/", "/recipes/", "/reflect/", "/release/",
    "/reminders/", "/review/", "/search/", "/settings/", "/shadowing/",
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
        "/api/activity-presets", "/api/sleep-stats",
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
    "hub": [
        "/api/today", "/api/health-score", "/api/what-now", "/api/countdowns",
        "/api/goals", "/api/wellness", "/api/streaks", "/api/month-compare",
    ],
    "quotes": ["/api/quote"],
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
        "/api/weekly", "/api/achievements", "/api/goal", "/api/config",
        "/api/breaks", "/api/distraction-stats",
    ],
    "timeline": ["/api/events"],
    "news-center": ["/api/sources", "/api/articles", "/api/stats"],
    "gpts": ["/api/agents"],
    # System apps from Phase 2
    "quick-action": [
        "/api/list", "/api/stats", "/api/recent", "/api/pending",
    ],
    "assistant": [
        "/api/sessions", "/api/slash-commands", "/api/providers",
    ],
    "music-studio": [
        "/api/library/list", "/api/library/stats", "/api/library/albums",
        "/api/lyrics/styles", "/api/lyrics/history",
        "/api/compose/styles", "/api/compose/status", "/api/compose/history",
        "/api/visual/history", "/api/visual/songs",
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
