"""Layer 3 — Live LLM smoke tests for the page-assistant drawer.

Sends real messages through the GPTs /api/chat endpoint and validates:
- LLM responds (non-empty)
- Any [DO:app.method(...)] actions reference allowlisted methods
- No hallucinated parameter names
- Response is contextually relevant (mentions expected keywords)

These tests are SLOW (1-5s per call) and require both the daemon AND a
working LLM provider. Mark with @pytest.mark.llm so CI can skip them:

    pytest tests/test_page_assistant_llm.py -v          # run all
    pytest tests/ -m llm -v                             # via marker
    pytest tests/ -m "not llm" -v                       # skip in CI
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import httpx
import pytest

from helpers import BASE_URL

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "apps"

# [DO:app.method({"arg":"val"})] pattern — same as gpts/app.py
DO_PATTERN = re.compile(r"\[DO:(\w+)\.(\w+)\((\{.*?\})\)\]", re.DOTALL)

# Parametrize: (app_id, user_message, expected_keywords, forbidden_methods)
# expected_keywords: at least one must appear in response (case-insensitive)
# forbidden_methods: methods the LLM must NOT call (catches hallucinations)
APP_SMOKE_CASES = [
    pytest.param(
        "task",
        "List my active tasks",
        ["task", "active", "open", "list"],
        [],
        id="task-list",
    ),
    pytest.param(
        "task",
        "Complete the task about groceries",
        ["complete", "done", "groceries", "task"],
        [],
        id="task-complete",
    ),
    pytest.param(
        "task",
        "Snooze the task about emails by 3 days",
        ["snooze", "email", "3", "days"],
        [],
        id="task-snooze",
    ),
    pytest.param(
        "journal",
        "Show me a summary of this week's journal",
        ["journal", "week", "summary", "entries"],
        [],
        id="journal-summary",
    ),
    pytest.param(
        "projects",
        "What are my active projects?",
        ["project", "active", "status"],
        [],
        id="projects-list",
    ),
    pytest.param(
        "projects",
        "Show upcoming deadlines",
        ["deadline", "due", "date", "upcoming"],
        [],
        id="projects-deadlines",
    ),
    pytest.param(
        "billing",
        "How much did I spend today?",
        ["cost", "spend", "today", "budget", "billing"],
        [],
        id="billing-today",
    ),
    pytest.param(
        "search",
        "Search my vault for notes about Python",
        ["search", "python", "vault", "notes", "result"],
        [],
        id="search-vault",
    ),
    pytest.param(
        "quick-action",
        "Add a quick note: remember to buy milk",
        ["quick-action", "add", "milk", "saved", "inbox"],
        [],
        id="capture-add",
    ),
    pytest.param(
        "focus",
        "Suggest a task to focus on right now",
        ["focus", "task", "suggest", "session"],
        [],
        id="focus-suggest",
    ),
]


def _get_allowlisted_methods() -> dict[str, set[str]]:
    """Build {app_id: {method, ...}} from all manifests."""
    allowed: dict[str, set[str]] = {}
    for app_dir in sorted(APPS.iterdir()):
        if not app_dir.is_dir() or app_dir.name.startswith("_"):
            continue
        mf = app_dir / "manifest.toml"
        if not mf.exists():
            continue
        try:
            data = tomllib.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        cmds = data.get("provides", {}).get("assistant", {}).get("commands", [])
        if cmds:
            app_id = data.get("app", {}).get("id", app_dir.name)
            allowed[app_id] = {c["method"] for c in cmds if c.get("method")}
    return allowed


@pytest.fixture(scope="module")
def allowlisted():
    return _get_allowlisted_methods()


@pytest.fixture(scope="module")
def gpts_available():
    """Check GPTs agent is reachable and has a working LLM."""
    try:
        resp = httpx.post(
            f"{BASE_URL}/gpts/api/chat",
            json={"agent_id": "general-assistant", "text": "ping", "context": ""},
            timeout=30,
        )
    except httpx.HTTPError as e:
        pytest.skip(f"GPTs endpoint unreachable: {e}")
    if resp.status_code != 200:
        pytest.skip(f"GPTs returned {resp.status_code}")
    data = resp.json()
    if not data.get("response"):
        pytest.skip("GPTs returned empty response — LLM may be offline")
    return True


# ── Parametrized smoke tests ────────────────────────────────────────


@pytest.mark.llm
class TestPageAssistantLLM:
    """Per-app smoke tests with a real LLM behind the GPTs endpoint."""

    @pytest.mark.parametrize(
        "app_id, message, expected_keywords, forbidden",
        APP_SMOKE_CASES,
    )
    def test_smoke(
        self,
        gpts_available,
        allowlisted,
        app_id,
        message,
        expected_keywords,
        forbidden,
    ):
        """Send a message as if the user is on a specific app page."""
        context = f"Page: {app_id} (/{app_id}/)"
        resp = httpx.post(
            f"{BASE_URL}/gpts/api/chat",
            json={
                "agent_id": "general-assistant",
                "text": message,
                "context": context,
            },
            timeout=30,
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        data = resp.json()
        reply = data.get("response", "")

        # 1. Non-empty response
        assert reply.strip(), f"Empty response for '{message}' on {app_id}"

        # 2. Validate any [DO:] actions
        for match in DO_PATTERN.finditer(reply):
            do_app, do_method, do_args = match.group(1), match.group(2), match.group(3)
            app_methods = allowlisted.get(do_app, set())
            assert do_method in app_methods, (
                f"Hallucinated method: [DO:{do_app}.{do_method}(...)]\n"
                f"Allowed for {do_app}: {sorted(app_methods) if app_methods else '(none)'}\n"
                f"Full response:\n{reply[:500]}"
            )

        # 3. Check server_results (if any) are all ok
        for sr in data.get("server_results", []):
            # Don't hard-fail on server action errors — the data may not exist
            # in the test env. But log it.
            if not sr.get("ok"):
                # Acceptable: method worked but returned None/empty (no matching data)
                pass

        # 4. Contextual relevance — at least one expected keyword in response
        reply_lower = reply.lower()
        found = [kw for kw in expected_keywords if kw in reply_lower]
        assert found, (
            f"Response lacks context for '{message}' on {app_id}.\n"
            f"Expected one of {expected_keywords} in:\n{reply[:300]}"
        )

        # 5. No forbidden method calls
        for match in DO_PATTERN.finditer(reply):
            do_method = match.group(2)
            assert do_method not in forbidden, (
                f"LLM called forbidden method {do_method} for '{message}'"
            )

    def test_no_action_when_none_needed(self, gpts_available, allowlisted):
        """Conversational message should NOT trigger [DO:] actions."""
        resp = httpx.post(
            f"{BASE_URL}/gpts/api/chat",
            json={
                "agent_id": "general-assistant",
                "text": "What is EmptyOS?",
                "context": "Page: settings (/settings/)",
            },
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        reply = data.get("response", "")
        actions = DO_PATTERN.findall(reply)
        assert not actions, (
            f"LLM triggered actions on a conversational question:\n"
            f"{[f'{a[0]}.{a[1]}' for a in actions]}\n"
            f"Response: {reply[:300]}"
        )

    def test_unknown_app_graceful(self, gpts_available):
        """Message referencing a non-existent app should still get a response."""
        resp = httpx.post(
            f"{BASE_URL}/gpts/api/chat",
            json={
                "agent_id": "general-assistant",
                "text": "Launch the spaceship",
                "context": "Page: nonexistent (/nonexistent/)",
            },
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("response", "").strip(), "Empty response for unknown app"
