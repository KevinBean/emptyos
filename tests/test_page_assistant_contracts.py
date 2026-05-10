"""Contract tests for the per-app assistant drawer (page-assistant.js + rooms).

Two layers:
- Static (Layer 1): no LLM, reads manifests + app.py + page HTML. Catches
  manifest drift (broken refs, missing inverses, web routes exposed via [DO:])
  and inconsistent EOS.registerActions blocks.
- Prompt contract (Layer 2): hits the running daemon's
  /rooms/api/debug/system-prompt/<agent> endpoint and asserts the built prompt
  is well-formed — every allowlisted method appears, signatures are real
  (not empty parens that invite the LLM to hallucinate args), prompt fits in
  the token budget.

conftest already skips the session if EmptyOS isn't running on :9000.
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
SKIP_DIRS = {"_example", "_retired", "tmpl"}

_METHOD_RE = re.compile(
    r"^\s+(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.MULTILINE,
)
_WEB_ROUTE_DEF_RE = re.compile(
    r"@web_route\([^)]*\)\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)",
)
_REGISTER_BLOCK_RE = re.compile(
    r"EOS\.registerActions\s*\(\s*\{(?P<dict>.*?)\}\s*,\s*\[(?P<desc>.*?)\]",
    re.DOTALL,
)


def _iter_app_dirs():
    """Core apps + personal apps, skipping templates and retired."""
    for app_dir in sorted(APPS.iterdir()):
        if not app_dir.is_dir() or app_dir.name in SKIP_DIRS:
            continue
        if app_dir.name == "personal":
            for sub in sorted(app_dir.iterdir()):
                if sub.is_dir() and sub.name not in SKIP_DIRS:
                    yield sub
        else:
            yield app_dir


def _load_manifest(app_dir: Path) -> dict | None:
    mf = app_dir / "manifest.toml"
    if not mf.exists():
        return None
    try:
        return tomllib.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return None


def _assistant_commands(app_dir: Path):
    """(app_id, [command dicts]) or None when the app doesn't declare any."""
    mf = _load_manifest(app_dir)
    if not mf:
        return None
    cmds = mf.get("provides", {}).get("assistant", {}).get("commands", [])
    if not cmds:
        return None
    return mf.get("app", {}).get("id", app_dir.name), cmds


def _py_methods(app_dir: Path) -> set[str]:
    ap = app_dir / "app.py"
    if not ap.exists():
        return set()
    return set(_METHOD_RE.findall(ap.read_text(encoding="utf-8")))


def _web_route_methods(app_dir: Path) -> set[str]:
    ap = app_dir / "app.py"
    if not ap.exists():
        return set()
    return set(_WEB_ROUTE_DEF_RE.findall(ap.read_text(encoding="utf-8")))


# ── Tests ─────────────────────────────────────────────────────────────


def test_manifest_methods_exist():
    """Every [provides.assistant].commands.method resolves to a real method."""
    bad: list[str] = []
    for app_dir in _iter_app_dirs():
        entry = _assistant_commands(app_dir)
        if not entry:
            continue
        app_id, cmds = entry
        methods = _py_methods(app_dir)
        for cmd in cmds:
            m = cmd.get("method")
            if m and m not in methods:
                bad.append(f"{app_id}.{m} (slash {cmd.get('slash', '?')})")
    assert not bad, (
        "Manifest references methods that don't exist in app.py:\n  "
        + "\n  ".join(bad)
    )


def test_inverse_methods_exist():
    """Every declared inverse resolves to a real method on the same app.

    A broken inverse means /api/undo crashes the moment a user tries it.
    """
    bad: list[str] = []
    for app_dir in _iter_app_dirs():
        entry = _assistant_commands(app_dir)
        if not entry:
            continue
        app_id, cmds = entry
        methods = _py_methods(app_dir)
        for cmd in cmds:
            inv = cmd.get("inverse")
            if inv and inv not in methods:
                bad.append(
                    f"{app_id}.{cmd.get('method')} → inverse='{inv}' not found"
                )
    assert not bad, "Declared inverses don't exist:\n  " + "\n  ".join(bad)


def test_methods_not_web_routes():
    """Exposed methods must not be @web_route handlers.

    Web routes take a `request` arg — call_app() passes JSON-parsed kwargs,
    which the handler won't know how to handle. Expose plain async methods.
    """
    bad: list[str] = []
    for app_dir in _iter_app_dirs():
        entry = _assistant_commands(app_dir)
        if not entry:
            continue
        app_id, cmds = entry
        routes = _web_route_methods(app_dir)
        for cmd in cmds:
            m = cmd.get("method")
            if m and m in routes:
                bad.append(
                    f"{app_id}.{m} is a @web_route — not callable via [DO:]"
                )
    assert not bad, "Web routes exposed to assistant:\n  " + "\n  ".join(bad)


def test_registered_actions_consistent():
    """In each page's EOS.registerActions call, every description name has a
    matching function in the actions dict.
    """
    issues: list[str] = []
    for app_dir in _iter_app_dirs():
        index = app_dir / "pages" / "index.html"
        if not index.exists():
            continue
        html = index.read_text(encoding="utf-8")
        for m in _REGISTER_BLOCK_RE.finditer(html):
            dict_block = m.group("dict")
            desc_block = m.group("desc")
            dict_keys = set(
                re.findall(r"(\w+)\s*:\s*function", dict_block)
            )
            desc_names = set(
                re.findall(r"name\s*:\s*[\"'](\w+)[\"']", desc_block)
            )
            missing = desc_names - dict_keys
            if missing:
                issues.append(
                    f"{app_dir.name}: descriptions list {sorted(missing)} "
                    "without matching functions in the dict"
                )
    assert not issues, (
        "registerActions dict/descriptions mismatch:\n  " + "\n  ".join(issues)
    )


def test_registered_pages_have_description():
    """Every page calling EOS.registerActions should set config.description.

    The description is what the drawer shows the LLM about the page —
    skipping it defeats most of the value of registering on that page.
    """
    missing: list[str] = []
    for app_dir in _iter_app_dirs():
        index = app_dir / "pages" / "index.html"
        if not index.exists():
            continue
        html = index.read_text(encoding="utf-8")
        if "registerActions" not in html:
            continue
        if not re.search(r"description\s*:\s*[\"']", html):
            missing.append(app_dir.name)
    assert not missing, (
        f"Pages register actions but have no description: {missing}"
    )


# ── Layer 2: prompt contract (hits running daemon) ────────────────────


@pytest.fixture(scope="module")
def general_assistant_prompt():
    """Fetch the built system prompt for the default drawer agent."""
    url = f"{BASE_URL}/rooms/api/debug/system-prompt/general-assistant"
    try:
        resp = httpx.get(url, timeout=10)
    except httpx.HTTPError as e:
        pytest.skip(f"debug endpoint unreachable: {e}")
    if resp.status_code != 200 or not resp.content:
        pytest.skip(
            "rooms debug endpoint not available "
            f"(status {resp.status_code}) — daemon may need restart"
        )
    try:
        data = resp.json()
    except ValueError:
        pytest.skip("debug endpoint returned non-JSON — daemon needs restart")
    if data.get("error"):
        pytest.skip(f"general-assistant agent not available: {data['error']}")
    return data


def test_prompt_contains_every_allowlisted_method(general_assistant_prompt):
    """Every app.method in server_actions must show up in the system prompt."""
    prompt = general_assistant_prompt["prompt"]
    server_actions = general_assistant_prompt["server_actions"]
    missing = []
    for app_id, methods in server_actions.items():
        for method in methods:
            needle = f"{app_id}.{method}"
            if needle not in prompt:
                missing.append(needle)
    assert not missing, (
        "Server actions declared in allowlist but missing from prompt:\n  "
        + "\n  ".join(missing)
    )


def test_prompt_signatures_are_real(general_assistant_prompt):
    """Spot-check: methods that take arguments must expose them in the prompt.

    If every method shows `()`, runtime introspection failed and the LLM will
    hallucinate params (see the `filter=...` bug that kicked this off).
    """
    prompt = general_assistant_prompt["prompt"]
    # These methods are known to take arguments — if any of them appear with
    # empty parens, introspection is broken.
    known_arg_methods = [
        ("task", "complete"),   # (query)
        ("task", "add"),        # (text, file, due, project)
        ("task", "reopen"),     # (query)
        ("search", "_search"),  # (query, ...)
    ]
    empty_sig = []
    for app_id, method in known_arg_methods:
        pattern = rf"{re.escape(app_id)}\.{re.escape(method)}\(\s*\)"
        if re.search(pattern, prompt):
            empty_sig.append(f"{app_id}.{method}")
    assert not empty_sig, (
        "Methods shown with empty signature — introspection failed:\n  "
        + "\n  ".join(empty_sig)
    )


def test_prompt_warns_against_inventing_params(general_assistant_prompt):
    """Prompt should tell the LLM not to invent parameter names.

    Our defense in depth: signatures + explicit warning = fewer hallucinations.
    """
    prompt = general_assistant_prompt["prompt"].lower()
    assert "do not invent" in prompt or "only parameters" in prompt, (
        "System prompt lacks explicit guidance against inventing params"
    )


def test_prompt_size_within_budget(general_assistant_prompt):
    """Prompt must fit in reasonable token budget (~4k tokens ≈ 16k chars)."""
    size = len(general_assistant_prompt["prompt"])
    assert size < 16_000, (
        f"System prompt is {size} chars — too long for small-context models. "
        "Consider trimming or pruning the allowlist."
    )
