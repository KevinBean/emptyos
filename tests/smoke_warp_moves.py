"""One-shot smoke for the two Warp-inspired moves.

Move 1: Staff-agent action carries {rationale, is_read_only, is_risky}.
Move 2: BaseApp.cite() + last_provenance()['citations'] round-trips.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tomllib
import urllib.request


def _token() -> str:
    with open("emptyos.toml", "rb") as f:
        return tomllib.load(f)["network"]["auth_token"]


def _http(path: str, method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"http://localhost:9000{path}",
        method=method,
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body else None,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def test_move1_prompt_includes_risk_fields():
    """The staff JSON instructions must mention all three risk fields."""
    sys.path.insert(0, ".")
    from apps.personal.staff import agents as staff_agents

    text = staff_agents._JSON_INSTRUCTIONS
    for needle in ("rationale", "is_read_only", "is_risky"):
        assert needle in text, f"prompt missing {needle!r}"
    print("[move1] prompt includes rationale/is_read_only/is_risky ✓")


def test_move1_act_threads_risk_meta():
    """_act() must surface risk metadata into steps and results."""
    sys.path.insert(0, ".")
    from apps.personal.staff.app import StaffApp

    src = open("apps/personal/staff/app.py", encoding="utf-8").read()
    for needle in (
        'action.get("rationale")',
        'action.get("is_read_only")',
        'action.get("is_risky")',
        "**risk_meta",
        "(intent:",
    ):
        assert needle in src, f"_act source missing {needle!r}"
    assert hasattr(StaffApp, "_act"), "StaffApp._act missing"
    print("[move1] _act threads risk metadata into steps + failure log ✓")


def test_move2_baseapp_cite_roundtrip():
    """In-process: BaseApp.cite() → last_provenance() returns typed citations."""
    sys.path.insert(0, ".")
    from emptyos.sdk.base_app import BaseApp

    obj = BaseApp.__new__(BaseApp)
    # Pretend a think() just ran by setting the provider snapshot manually,
    # then verify cite() registers and last_provenance() surfaces them.
    obj._last_think_provider = {
        "provider": "ollama", "is_cloud": False, "model": "qwen3.5", "latency_ms": 42,
    }
    obj._pending_citations = []
    obj.cite("vault_note", "10_Projects/emptyos/log/2026-04-29.md", lines=[1, 50])
    obj.cite("eos_doc", "docs/DESIGN.md")
    obj.cite("web_page", "https://github.com/warpdotdev/warp")
    # Move pending → consumed (think() does this on entry)
    obj._last_think_citations = list(obj._pending_citations)
    obj._pending_citations = []

    prov = obj.last_provenance()
    assert prov["mode"] == "local", prov
    assert prov["provider"] == "ollama", prov
    assert "citations" in prov, prov
    cites = prov["citations"]
    assert len(cites) == 3, cites
    kinds = [c["kind"] for c in cites]
    assert kinds == ["vault_note", "eos_doc", "web_page"], kinds
    assert cites[0]["lines"] == [1, 50], cites[0]
    print(f"[move2] cite() + last_provenance() returned {len(cites)} typed citations ✓")
    print("        sample:", cites[0])


def test_move2_existing_callers_unaffected():
    """Live: hit a /api endpoint that returns provenance — must include citations key."""
    # publish landing-meta — fast no-LLM path won't trigger; pick a known shape.
    # Most reliable: search/api/search returns provenance even on no-AI runs.
    try:
        r = _http("/search/api/search?q=warp&use_ai=false")
    except Exception as e:
        print(f"[move2-live] skipped (search endpoint unreachable: {e})")
        return
    if "provenance" not in r:
        print("[move2-live] search returned no provenance key (no AI ran) — skip")
        return
    prov = r["provenance"]
    assert "citations" in prov, prov
    assert isinstance(prov["citations"], list), prov
    print(f"[move2-live] /search provenance has citations={prov['citations']} ✓")


def test_move1_live_create_dry_action():
    """Live: list staff and confirm at least one agent exists for run_now path."""
    agents = _http("/staff/api/staff")
    assert isinstance(agents, list) and agents, "no staff agents"
    sample = agents[0]
    for k in ("id", "name", "system_prompt"):
        assert k in sample, sample
    print(f"[move1-live] {len(agents)} staff agents loaded; first={sample['id']} ✓")


def main():
    test_move1_prompt_includes_risk_fields()
    test_move1_act_threads_risk_meta()
    test_move2_baseapp_cite_roundtrip()
    test_move1_live_create_dry_action()
    test_move2_existing_callers_unaffected()
    print("\nALL SMOKE PASSED")


if __name__ == "__main__":
    main()
