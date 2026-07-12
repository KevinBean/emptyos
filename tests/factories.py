"""Test data factories. All payloads use TEST_PREFIX + UUID suffix for cleanup."""

import uuid

from helpers import TEST_PREFIX


def _tag(prefix="item"):
    """Generate a unique TEST_PREFIX-prefixed identifier."""
    return f"{TEST_PREFIX}{prefix}-{uuid.uuid4().hex[:6]}"


def expense(amount=5, desc="coffee"):
    """smart-add format: '<amount> <description>'."""
    return {"text": f"{amount} {_tag(desc)}"}


def expense_full(amount=5.0, category="Dining", desc="coffee", date=None):
    """Structured expense for /api/add."""
    payload = {
        "amount": float(amount),
        "category": category,
        "description": _tag(desc),
    }
    if date:
        payload["date"] = date
    return payload


def journal_entry(text="entry", mood="good"):
    return {"text": _tag(text), "mood": mood}


def capture(text="capture", tag="idea"):
    return {"text": _tag(text), "tag": tag}


def habit(name="habit", frequency="daily", icon="✅", target=1):
    return {
        "name": _tag(name),
        "frequency": frequency,
        "icon": icon,
        "target": target,
    }


def bookmark(url=None, title="bookmark", tags=None):
    return {
        "url": url or f"https://example.com/{uuid.uuid4().hex[:8]}",
        "title": _tag(title),
        "tags": tags or [],
    }


def recipe(name="recipe", difficulty="easy"):
    return {
        "name": _tag(name),
        "difficulty": difficulty,
        "ingredients": [{"name": "test ingredient", "amount": "1 cup"}],
        "steps": ["test step 1", "test step 2"],
        "tags": [],
    }


def workout_session(exercise="pushup", sets=3, reps=10):
    return {
        "type": "strength",
        "name": _tag("workout"),
        "exercises": [{"name": exercise, "sets": sets, "reps": reps}],
        "duration_min": 30,
    }


def sleep_log(bedtime="23:00", wake="07:00", quality=4):
    return {
        "bedtime": bedtime,
        "wake": wake,
        "quality": quality,
        "notes": _tag("sleep"),
    }


def assistant_session(name="session", backend=None):
    payload = {"name": _tag(name)}
    if backend:
        payload["backend"] = backend
    return payload


def agent_session(name="session"):
    """Payload for POST /agent/api/sessions."""
    return {"name": _tag(name)}


def focus_complete(minutes=1, task="task"):
    return {"minutes": minutes, "task": _tag(task)}


def project_task(text="task"):
    return {"text": _tag(text)}


def settings_test_key():
    """Generate a namespaced test key safe to write/delete."""
    return f"test.{TEST_PREFIX}{uuid.uuid4().hex[:6]}"
