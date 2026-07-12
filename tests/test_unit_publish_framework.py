"""Unit tests for the publish app's per-site branding-framework evaluator.

No daemon — the framework functions are module-level, taking `self`, so a
SimpleNamespace over a tmp_path vault stands in for PublishApp. Covers:

- `_parse_dimensions`: parse a `## Dimensions` bullet list, strip separators.
- `_validate_scorecard`: clamp scores 1-5, fill missing dims, verdict logic,
  merge deterministic guardrail hits, tolerate non-dict model output.
- `_guardrail_hits_from_det`: redaction leak → high, high-severity prose → high.
- `_load_framework`: presence-gating (no note → default dims; note → parsed dims).
- `_deterministic_findings`: folds lint_prose + `_redaction_hits`, never raises.
- `_read_draft_body`: vault containment + frontmatter title.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.publish import framework as fw

SOURCE = "30_Resources/Published"


async def _fs_read(path):
    return Path(path).read_text(encoding="utf-8")


def make_self(tmp_path, redaction=None):
    return SimpleNamespace(
        _vault_dir=lambda: str(tmp_path),
        _source_folder=lambda site=None: SOURCE,
        read=_fs_read,
        _redaction_hits=lambda body, blurred="": list(redaction or []),
    )


# ── _parse_dimensions (pure) ─────────────────────────────────────────

def test_parse_dimensions_strips_separators():
    text = (
        "## Dimensions\n"
        "- Positioning fit — does it read as X?\n"
        "- Receipts-grade: a real demo?\n"
        "- Spine - reversibility present\n"
        "## Next section\n"
        "- ignored\n"
    )
    assert fw._parse_dimensions(text) == ["Positioning fit", "Receipts-grade", "Spine"]


def test_parse_dimensions_absent_returns_empty():
    assert fw._parse_dimensions("# Title\n\nno dimensions here") == []


# ── _guardrail_hits_from_det (pure) ──────────────────────────────────

def test_guardrail_hits_from_det():
    det = {
        "redaction": [{"match": "Secret St", "pattern": "addr"}],
        "prose": {"findings": [
            {"severity": "high", "line": 4, "rule": "banned-hype", "message": "m"},
            {"severity": "low", "line": 9, "rule": "em-dash-rate", "message": "x"},
        ]},
    }
    hits = fw._guardrail_hits_from_det(det)
    kinds = [(h["kind"], h["severity"]) for h in hits]
    assert ("redaction", "high") in kinds
    assert ("prose-tone", "high") in kinds
    # low-severity prose is NOT a guardrail hit
    assert all("em-dash" not in h["detail"] for h in hits)


# ── _validate_scorecard (pure) ───────────────────────────────────────

def _clean_det():
    return {"redaction": [], "prose": {"findings": []}}


def test_validate_clamps_and_fills():
    raw = {"overall": 9, "verdict": "ready",
           "dimensions": [{"name": "Positioning fit", "score": "7", "notes": "n"}],
           "fixes": ["f1"]}
    card = fw._validate_scorecard(raw, ["Positioning fit", "Receipts-grade"], _clean_det(), "default")
    assert card["overall"] == 5  # clamped from 9
    scores = {d["name"]: d["score"] for d in card["dimensions"]}
    assert scores["Positioning fit"] == 5  # "7" -> 5
    assert scores["Receipts-grade"] is None  # missing -> None
    assert card["fixes"] == ["f1"]


def test_validate_leak_forces_off_brand():
    det = {"redaction": [{"match": "X", "pattern": "p"}], "prose": {"findings": []}}
    card = fw._validate_scorecard({"overall": 5, "verdict": "ready", "dimensions": []},
                                  ["A"], det, "default")
    assert card["verdict"] == "off-brand"  # a high guardrail hit overrides the model's "ready"
    assert any(g["kind"] == "redaction" for g in card["guardrail_hits"])


def test_validate_tolerates_non_dict():
    card = fw._validate_scorecard("not json at all", ["A"], _clean_det(), "emptyos")
    assert card["site"] == "emptyos"
    assert card["overall"] is None
    assert card["verdict"] == "needs-polish"
    assert card["dimensions"][0]["name"] == "A"


def test_validate_derives_overall_and_verdict():
    raw = {"dimensions": [{"name": "A", "score": 4}, {"name": "B", "score": 5}]}
    card = fw._validate_scorecard(raw, ["A", "B"], _clean_det(), "default")
    assert card["overall"] in (4, 5)  # mean of 4,5
    assert card["verdict"] == "ready"


# ── _eval_user_message (pure) ────────────────────────────────────────

def test_eval_user_message_includes_parts():
    fwk = {"text": "FRAMEWORK-BODY", "dimensions": ["Positioning fit"]}
    det = {"prose": {"metrics": {"words": 5}, "findings": []}, "redaction": []}
    msg = fw._eval_user_message(fwk, "My Title", "the draft body", det)
    assert "FRAMEWORK-BODY" in msg
    assert "Positioning fit" in msg
    assert "My Title" in msg
    assert "the draft body" in msg


# ── _load_framework (async, presence-gated) ──────────────────────────

def _write_framework(tmp_path, body):
    src = tmp_path / SOURCE
    src.mkdir(parents=True, exist_ok=True)
    (src / "_framework.md").write_text(body, encoding="utf-8")


def test_load_framework_absent_defaults():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        fwk = asyncio.run(fw._load_framework(make_self(Path(d)), None))
        assert fwk["present"] is False
        assert fwk["dimensions"] == fw.DEFAULT_DIMENSIONS


def test_load_framework_parses_dimensions(tmp_path):
    _write_framework(tmp_path, "---\nx: 1\n---\n\n# F\n\n## Dimensions\n- Alpha — a\n- Beta: b\n")
    fwk = asyncio.run(fw._load_framework(make_self(tmp_path), None))
    assert fwk["present"] is True
    assert fwk["dimensions"] == ["Alpha", "Beta"]
    assert "x: 1" not in fwk["text"]  # frontmatter stripped


# ── _deterministic_findings (never raises) ───────────────────────────

def test_deterministic_findings_shape(tmp_path):
    s = make_self(tmp_path, redaction=[{"match": "Q", "pattern": "p"}])
    det = fw._deterministic_findings(s, "This is a plain sentence about a topic.")
    assert "prose" in det and "redaction" in det
    assert det["redaction"] == [{"match": "Q", "pattern": "p"}]
    assert isinstance(det["prose"].get("findings"), list)


# ── _read_draft_body (containment) ───────────────────────────────────

def test_read_draft_body_title_and_containment(tmp_path):
    src = tmp_path / SOURCE
    src.mkdir(parents=True, exist_ok=True)
    (src / "post.md").write_text("---\ntitle: Hello World\n---\n\nBody text here.\n", encoding="utf-8")
    s = make_self(tmp_path)
    abs_path, title, body = fw._read_draft_body(s, f"{SOURCE}/post.md")
    assert title == "Hello World"
    assert body == "Body text here."
    # escape attempt raises
    with pytest.raises(ValueError):
        fw._read_draft_body(s, "../../etc/passwd")


# ── api_evaluate end-to-end (offline, stubbed think) ─────────────────

def _make_eval_self(tmp_path, think_json, *, redaction=None, enabled=True):
    site = {"id": "default", "source_folder": SOURCE}

    async def _think(user, system=None, domain=None, temperature=None):
        return think_json

    async def _safe_json(req):
        return req._payload

    return SimpleNamespace(
        app_config=lambda k, d=None: (enabled if k == "feature.framework-eval.enabled" else d),
        safe_json=_safe_json,
        _vault_dir=lambda: str(tmp_path),
        _source_folder=lambda s=None: SOURCE,
        read=_fs_read,
        _redaction_hits=lambda body, blurred="": list(redaction or []),
        _get_site=lambda sid: site if sid == "default" else None,
        _active_site=lambda: site,
        think=_think,
        last_provenance=lambda: {"provider": "stub", "model": "m", "mode": "local"},
    )


def test_api_evaluate_end_to_end(tmp_path):
    src = tmp_path / SOURCE
    src.mkdir(parents=True, exist_ok=True)
    (src / "_framework.md").write_text(
        "# F\n\n## Dimensions\n- Positioning fit — x\n- Receipts-grade — y\n", encoding="utf-8"
    )
    (src / "draft.md").write_text(
        "---\ntitle: A Draft\n---\n\nA clear paragraph making a point.\n", encoding="utf-8"
    )
    think_json = (
        '{"overall": 4, "verdict": "ready", '
        '"dimensions": [{"name": "Positioning fit", "score": 4, "notes": "ok"}, '
        '{"name": "Receipts-grade", "score": 2, "notes": "argument-only"}], '
        '"guardrail_hits": [], "fixes": ["add a real demo"]}'
    )
    s = _make_eval_self(tmp_path, think_json)
    req = SimpleNamespace(_payload={"path": f"{SOURCE}/draft.md", "site": "default"})
    card = asyncio.run(fw.api_evaluate(s, req))
    assert card["ok"] is True
    assert card["framework_present"] is True
    assert card["site"] == "default"
    assert card["verdict"] == "ready"
    assert {d["name"] for d in card["dimensions"]} == {"Positioning fit", "Receipts-grade"}
    assert card["fixes"] == ["add a real demo"]
    assert card["provenance"]["provider"] == "stub"


def test_api_evaluate_disabled_flag(tmp_path):
    s = _make_eval_self(tmp_path, "{}", enabled=False)
    req = SimpleNamespace(_payload={"path": f"{SOURCE}/x.md", "site": "default"})
    card = asyncio.run(fw.api_evaluate(s, req))
    assert card["ok"] is False


def test_api_evaluate_leak_forces_off_brand(tmp_path):
    src = tmp_path / SOURCE
    src.mkdir(parents=True, exist_ok=True)
    (src / "draft.md").write_text("---\ntitle: T\n---\n\nBody.\n", encoding="utf-8")
    # model says ready, but a redaction leak is ground truth → off-brand
    s = _make_eval_self(
        tmp_path,
        '{"overall": 5, "verdict": "ready", "dimensions": [], "guardrail_hits": [], "fixes": []}',
        redaction=[{"match": "42 Secret St", "pattern": "addr"}],
    )
    req = SimpleNamespace(_payload={"path": f"{SOURCE}/draft.md"})
    card = asyncio.run(fw.api_evaluate(s, req))
    assert card["ok"] is True
    assert card["verdict"] == "off-brand"
    assert any(g["kind"] == "redaction" for g in card["guardrail_hits"])
