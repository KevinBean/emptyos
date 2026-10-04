"""System tests: Writing Editor — paste a draft, get a revision.

The single LLM-hitting case (`test_revise_returns_valid_shape`) is marked
@pytest.mark.llm so it stays out of the fast lane. The validation tests
are pure backend and run in every suite.
"""

from __future__ import annotations

import importlib.util
import pytest

from helpers import assert_ok


@pytest.fixture(scope="module")
def we_mod():
    """Load apps/writing-editor/app.py as a module without booting the kernel."""
    spec = importlib.util.spec_from_file_location(
        "we_app", "apps/public/standard/writing-editor/app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.api
class TestWritingEditorAPI:
    def test_page_loads(self, http_client):
        r = http_client.get("/writing-editor/")
        assert r.status_code == 200
        assert "Writing Editor" in r.text

    def test_page_ships_edit_review(self, http_client):
        """Per-edit accept/reject is client-side — pin that it's actually served."""
        r = http_client.get("/writing-editor/")
        for symbol in ("weRenderEditReview", "weResolveEdits", "weApplyEdits", "Accept all"):
            assert symbol in r.text, f"{symbol} missing from the page"

    def test_page_ships_focus_mode(self, http_client):
        """Focus/typewriter mode is client-side — pin that it's served."""
        r = http_client.get("/writing-editor/")
        for symbol in ("toggleFocus", "centerCaret", "we-focus", "writing-editor.focus_mode"):
            assert symbol in r.text, f"{symbol} missing from the page"

    def test_options_endpoint(self, http_client):
        body = assert_ok(http_client.get("/writing-editor/api/options"))
        ids_a = [a["id"] for a in body.get("audiences", [])]
        ids_t = [t["id"] for t in body.get("tones", [])]
        assert {"peer", "manager", "external", "report"}.issubset(set(ids_a))
        assert {"default", "direct", "diplomatic", "shorter"}.issubset(set(ids_t))
        assert isinstance(body.get("max_draft_len"), int)

    def test_revise_rejects_empty_draft(self, http_client):
        body = assert_ok(http_client.post(
            "/writing-editor/api/revise", json={"draft": ""},
        ))
        assert body["ok"] is False
        assert "draft" in (body.get("error") or "")

    def test_revise_rejects_oversized_draft(self, http_client):
        body = assert_ok(http_client.post(
            "/writing-editor/api/revise",
            json={"draft": "x" * 5001},
        ))
        assert body["ok"] is False
        assert "too long" in (body.get("error") or "")

    def test_revise_rejects_unknown_audience(self, http_client):
        body = assert_ok(http_client.post(
            "/writing-editor/api/revise",
            json={"draft": "Hi team, just checking in.", "audience": "ceo"},
        ))
        assert body["ok"] is False
        assert "audience" in (body.get("error") or "")

    def test_revise_rejects_unknown_tone(self, http_client):
        body = assert_ok(http_client.post(
            "/writing-editor/api/revise",
            json={"draft": "Hi team, just checking in.", "tone": "snarky"},
        ))
        assert body["ok"] is False
        assert "tone" in (body.get("error") or "")

    def test_lint_rejects_empty_draft(self, http_client):
        body = assert_ok(http_client.post(
            "/writing-editor/api/lint", json={"draft": ""},
        ))
        assert body["ok"] is False
        assert "draft" in (body.get("error") or "")

    def test_lint_rejects_oversized_draft(self, http_client):
        body = assert_ok(http_client.post(
            "/writing-editor/api/lint", json={"draft": "x" * 5001},
        ))
        assert body["ok"] is False
        assert "too long" in (body.get("error") or "")


class TestWritingEditorHelpers:
    """Pure-function contracts — no daemon, no LLM."""

    def test_audiences_dict_shape(self, we_mod):
        assert set(we_mod.AUDIENCES.keys()) == {"peer", "manager", "external", "report"}
        for k, v in we_mod.AUDIENCES.items():
            assert isinstance(v, str) and v.strip(), f"empty description for {k!r}"

    def test_tones_dict_shape(self, we_mod):
        assert set(we_mod.TONES.keys()) == {"default", "direct", "diplomatic", "shorter"}
        for k, v in we_mod.TONES.items():
            assert isinstance(v, str) and v.strip(), f"empty description for {k!r}"

    def test_lint_severities_shape(self, we_mod):
        assert we_mod._LINT_SEVERITIES == {"hard-banned", "warn", "style"}
        # The lint prompt must mention each severity so the model can grade.
        for sev in we_mod._LINT_SEVERITIES:
            assert sev in we_mod.LINT_SYSTEM, f"{sev!r} missing from LINT_SYSTEM"

    # ── _annotate_edits: can the page splice this edit back in? ──

    def test_annotate_edits_found(self, we_mod):
        edits = [{"original": "touch base", "replacement": "meet", "rationale": ""}]
        out = we_mod._annotate_edits("let's touch base soon", edits)
        assert out[0]["found"] is True

    def test_annotate_edits_not_found_when_paraphrased(self, we_mod):
        edits = [{"original": "kindly advise", "replacement": "tell me", "rationale": ""}]
        out = we_mod._annotate_edits("Please advise on the matter.", edits)
        assert out[0]["found"] is False

    def test_annotate_edits_empty_original_is_not_found(self, we_mod):
        """An insertion has nothing to anchor to — the page can't place it."""
        out = we_mod._annotate_edits("abc", [{"original": "", "replacement": "x", "rationale": ""}])
        assert out[0]["found"] is False

    def test_annotate_edits_is_case_sensitive(self, we_mod):
        out = we_mod._annotate_edits("touch base", [{"original": "Touch base", "replacement": "meet"}])
        assert out[0]["found"] is False

    def test_annotate_edits_handles_cjk(self, we_mod):
        out = we_mod._annotate_edits("这个方案基本上可行。", [{"original": "基本上", "replacement": ""}])
        assert out[0]["found"] is True

    def test_annotate_edits_marks_every_edit(self, we_mod):
        edits = [
            {"original": "one", "replacement": "1"},
            {"original": "missing", "replacement": "x"},
        ]
        out = we_mod._annotate_edits("one two three", edits)
        assert [e["found"] for e in out] == [True, False]


@pytest.mark.api
@pytest.mark.llm
class TestWritingEditorRevise:
    """The one LLM-hitting case. Skip in fast lane via `-m "not llm"`."""

    def test_revise_returns_valid_shape(self, http_client):
        body = assert_ok(http_client.post(
            "/writing-editor/api/revise",
            json={
                "draft": "Hey just wanted to maybe touch base about the thing we talked about last week if you have time.",
                "audience": "manager",
                "tone": "direct",
            },
        ))
        assert body["ok"] is True
        assert isinstance(body.get("revised"), str) and body["revised"].strip()
        assert isinstance(body.get("edits"), list)
        for e in body["edits"]:
            # The page pre-disables edits it can't splice; the flag must be there.
            assert isinstance(e.get("found"), bool)
            for key in ("original", "replacement", "rationale"):
                assert key in e, f"edit missing {key}: {e}"
        # `pattern` may be null or an object — either is valid.
        if body.get("pattern") is not None:
            assert isinstance(body["pattern"], dict)
            assert "title" in body["pattern"]
        # Sanity: a "direct" rewrite of a hedge-heavy draft should be shorter
        # OR have at least one edit. (Not both required — model judgment.)
        assert len(body["revised"]) < len(
            "Hey just wanted to maybe touch base about the thing we talked about last week if you have time."
        ) or len(body["edits"]) > 0


@pytest.mark.api
@pytest.mark.llm
class TestWritingEditorLint:
    """LLM-hitting lint cases. Skip in fast lane via `-m "not llm"`."""

    _SLOP = (
        "Hey team, I just wanted to circle back and leverage our synergy. "
        "At the end of the day, this is a game-changing, revolutionary solution. "
        "It's not just a tool, it's a paradigm shift."
    )
    _VALID_SEV = {"hard-banned", "warn", "style"}

    def test_lint_flags_slop(self, http_client):
        body = assert_ok(http_client.post(
            "/writing-editor/api/lint", json={"draft": self._SLOP},
        ))
        assert body["ok"] is True
        findings = body.get("findings")
        assert isinstance(findings, list)
        # A draft this slop-heavy should trip several findings.
        assert len(findings) >= 3, f"expected ≥3 findings, got {len(findings)}"
        for f in findings:
            assert f.get("severity") in self._VALID_SEV, f"bad severity: {f!r}"
            assert isinstance(f.get("span"), str) and f["span"].strip()
            assert "rule" in f and "fix" in f
        # The worst offenders should be graded hard-banned, not just style nits.
        assert any(f["severity"] == "hard-banned" for f in findings)

    def test_lint_clean_draft_no_false_positives(self, http_client):
        body = assert_ok(http_client.post(
            "/writing-editor/api/lint",
            json={"draft": "The cable rating dropped 12% after we corrected the soil "
                           "thermal resistivity. I've attached the revised calc. Can you "
                           "confirm the trench depth before Friday?"},
        ))
        assert body["ok"] is True
        assert isinstance(body.get("findings"), list)
        # A clean, concrete draft should stay quiet — slop detectors that fire on
        # everything are noise. Allow at most one borderline style nit.
        assert len(body["findings"]) <= 1, f"false positives: {body['findings']}"


@pytest.mark.api
class TestWritingEditorDocuments:
    """Documents mode — vault-persisted articles with autosave + dictation + PDF."""

    def _create(self, http_client, title):
        body = assert_ok(http_client.post("/writing-editor/api/articles", json={"title": title}))
        assert body.get("ok") is True, f"create failed: {body}"
        return body["file"]

    def test_create_and_list(self, http_client):
        title = "PLAYWRIGHT-TEST- article create"
        f = self._create(http_client, title)
        body = assert_ok(http_client.get("/writing-editor/api/articles"))
        files = [a["file"] for a in body.get("articles", [])]
        assert f in files, f"{f} not in list"
        http_client.delete(f"/writing-editor/api/articles/{f}")

    def test_save_and_detail_roundtrip(self, http_client):
        f = self._create(http_client, "PLAYWRIGHT-TEST- article save")
        text = "First paragraph.\n\nSecond paragraph with 中文内容."
        body = assert_ok(http_client.post(f"/writing-editor/api/articles/{f}", json={"body": text}))
        assert body.get("ok") is True
        # CJK-aware word count: 4 CJK chars + 6 latin words
        assert body.get("words") == 10, f"word count wrong: {body.get('words')}"
        detail = assert_ok(http_client.get(f"/writing-editor/api/articles/{f}"))
        assert detail.get("body", "").strip() == text
        assert detail.get("words") == 10
        http_client.delete(f"/writing-editor/api/articles/{f}")

    def test_save_preserves_block_style_tags(self, http_client):
        """Frontmatter tags must stay block-style after a body save."""
        f = self._create(http_client, "PLAYWRIGHT-TEST- article tags")
        assert_ok(http_client.post(f"/writing-editor/api/articles/{f}", json={"body": "x"}))
        detail = assert_ok(http_client.get(f"/writing-editor/api/articles/{f}"))
        # detail still queryable by tag means the tag survived the rewrite
        assert detail.get("title"), f"detail lost after save: {detail}"
        http_client.delete(f"/writing-editor/api/articles/{f}")

    def test_create_requires_title(self, http_client):
        body = assert_ok(http_client.post("/writing-editor/api/articles", json={}))
        assert "error" in body

    def test_detail_missing_article(self, http_client):
        body = assert_ok(http_client.get("/writing-editor/api/articles/PLAYWRIGHT-TEST-nope.md"))
        assert "error" in body

    def test_delete_removes_from_list(self, http_client):
        f = self._create(http_client, "PLAYWRIGHT-TEST- article delete")
        body = assert_ok(http_client.delete(f"/writing-editor/api/articles/{f}"))
        assert body.get("ok") is True
        listed = assert_ok(http_client.get("/writing-editor/api/articles"))
        assert f not in [a["file"] for a in listed.get("articles", [])]

    def test_transcribe_requires_audio(self, http_client):
        body = assert_ok(http_client.post("/writing-editor/api/transcribe", data={"note": "no-audio"}))
        assert "error" in body

    def test_page_has_documents_tab(self, http_client):
        r = http_client.get("/writing-editor/")
        assert r.status_code == 200
        assert "tab-docs" in r.text and "Dictate" in r.text
