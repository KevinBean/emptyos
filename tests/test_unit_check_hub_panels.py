"""Unit tests for scripts/check_hub_panels.py.

Pins BOTH directions, per `.claude/skills/eos-graduate-audit`:

- it fires on the two gating classes (unknown renderer, duplicate panel id);
- it is silent on a healthy tree AND on every noise class dismissed while
  measuring the false-positive rate (2026-07-10):
    * `_retired/` apps — the loader never sees them, so the two duplicate ids
      found on the live tree (`guideline-daily`, `birthdays`) are not defects;
    * `[[contributes.hub-life.panel]]` — a different namespace targeting a
      gitignored personal app, whose renderers hub.js legitimately lacks;
    * `priority >= 150` — intentional (hub.js:744 drops the ambient band from
      Explore). 63 of 102 live panels sit there; gating it would be noise.

The live-tree test asserts only the *gating* class, never the advisory list, so
another session adding a panel cannot redden this suite.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_hub_panels.py"


def _load():
    if "check_hub_panels" in sys.modules:
        return sys.modules["check_hub_panels"]
    spec = importlib.util.spec_from_file_location("check_hub_panels", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_hub_panels"] = mod
    spec.loader.exec_module(mod)
    return mod


chk = _load()

RENDERERS = {"stat-tile", "plain-list", "bar"}


def _app(root: Path, rel: str, app_id: str, body: str) -> None:
    d = root / rel / app_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.toml").write_text(
        f'[app]\nid = "{app_id}"\nname = "{app_id}"\n\n{body}\n', encoding="utf-8"
    )


def _panel(pid: str, renderer: str, priority: int = 100, ns: str = "hub") -> str:
    return (
        f"[[contributes.{ns}.panel]]\n"
        f'id = "{pid}"\nmethod = "panel_x"\nrenderer = "{renderer}"\n'
        f"priority = {priority}\n"
    )


def _run(root: Path):
    panels = chk.collect(root)
    return chk.analyze(panels, RENDERERS)


class TestFires:
    def test_unknown_renderer_is_a_violation(self, tmp_path):
        _app(tmp_path, "public/core", "alpha", _panel("a-tile", "nonexistent-thing"))
        violations, _ = _run(tmp_path)
        assert [v["kind"] for v in violations] == ["unknown_renderer"]
        assert violations[0]["renderer"] == "nonexistent-thing"
        assert "hub-life" in violations[0]["fix"]  # points at the real escape hatch

    def test_duplicate_panel_id_across_apps_is_a_violation(self, tmp_path):
        _app(tmp_path, "public/core", "alpha", _panel("shared", "stat-tile"))
        _app(tmp_path, "public/standard", "beta", _panel("shared", "stat-tile"))
        violations, _ = _run(tmp_path)
        assert [v["kind"] for v in violations] == ["duplicate_id"]
        assert violations[0]["apps"] == ["alpha", "beta"]

    def test_both_classes_reported_together(self, tmp_path):
        _app(tmp_path, "public/core", "alpha", _panel("dup", "stat-tile"))
        _app(tmp_path, "public/standard", "beta", _panel("dup", "bogus-renderer"))
        violations, _ = _run(tmp_path)
        assert {v["kind"] for v in violations} == {"unknown_renderer", "duplicate_id"}


class TestSilentOnHealthy:
    def test_healthy_tree_is_clean(self, tmp_path):
        _app(tmp_path, "public/core", "alpha", _panel("a", "stat-tile"))
        _app(tmp_path, "public/standard", "beta", _panel("b", "plain-list", priority=300))
        violations, advisory = _run(tmp_path)
        assert violations == []
        assert advisory and advisory[0]["count"] == 1  # the 300 one, advisory only

    def test_retired_apps_are_invisible(self, tmp_path):
        """The live tree's two duplicate ids both come from `_retired/`.

        `iter_app_dirs` skips them because the loader does. Regressing this
        would resurrect a 100%-false-positive class.
        """
        _app(tmp_path, "public/standard", "kb", _panel("guideline-daily", "stat-tile"))
        _app(tmp_path, "_retired", "guideline", _panel("guideline-daily", "stat-tile"))
        violations, _ = _run(tmp_path)
        assert violations == []

    def test_personal_retired_apps_are_invisible(self, tmp_path):
        _app(tmp_path, "public/standard", "people", _panel("birthdays", "plain-list"))
        _app(tmp_path, "personal/_retired", "contacts", _panel("birthdays", "plain-list"))
        violations, _ = _run(tmp_path)
        assert violations == []

    def test_hub_life_namespace_is_not_checked(self, tmp_path):
        """`[[contributes.hub-life.panel]]` targets a gitignored personal app.

        Declaring it IS the opt-out for a life-only renderer, so a renderer
        hub.js has never heard of must not fire here.
        """
        _app(tmp_path, "public/standard", "alpha", _panel("x", "hero-alert", ns="hub-life"))
        violations, advisory = _run(tmp_path)
        assert violations == []
        assert advisory == []  # hub-life panels aren't counted in the band note either

    def test_ambient_band_never_gates(self, tmp_path):
        for i in range(5):
            _app(tmp_path, "public/standard", f"app{i}", _panel(f"p{i}", "stat-tile", priority=220))
        violations, advisory = _run(tmp_path)
        assert violations == []
        assert advisory[0]["count"] == 5

    def test_inline_ignore_marker_suppresses_unknown_renderer(self, tmp_path):
        d = tmp_path / "public" / "core" / "alpha"
        d.mkdir(parents=True)
        (d / "manifest.toml").write_text(
            '[app]\nid = "alpha"\nname = "alpha"\n\n'
            "# hub-panel-check: ignore renderer custom-thing\n"
            + _panel("a", "custom-thing"),
            encoding="utf-8",
        )
        violations, _ = _run(tmp_path)
        assert violations == []

    def test_panel_without_renderer_is_skipped_not_flagged(self, tmp_path):
        d = tmp_path / "public" / "core" / "alpha"
        d.mkdir(parents=True)
        (d / "manifest.toml").write_text(
            '[app]\nid = "alpha"\nname = "alpha"\n\n'
            '[[contributes.hub.panel]]\nid = "a"\nmethod = "panel_x"\n',
            encoding="utf-8",
        )
        violations, _ = _run(tmp_path)
        assert violations == []

    def test_malformed_manifest_does_not_crash(self, tmp_path):
        d = tmp_path / "public" / "core" / "broken"
        d.mkdir(parents=True)
        (d / "manifest.toml").write_text("[app\nid = broken", encoding="utf-8")
        assert _run(tmp_path) == ([], [])


class TestRendererMapParsing:
    def test_reads_the_real_hub_js(self):
        found = chk.hub_renderers()
        assert "stat-tile" in found and "plain-list" in found
        assert len(found) > 10, "RENDERERS regex stopped matching — the map moved"

    def test_missing_file_returns_empty_set(self, tmp_path):
        assert chk.hub_renderers(tmp_path / "nope.js") == set()


class TestHubLifeCrossCheck:
    """The 2026-08-05 blind spot: a `hub.panel` renderer that core hub.js
    knows but hub-life's own RENDERERS map doesn't paints a red box on
    hub-life while passing a checker that only ever looked at hub.js."""

    def test_missing_from_hub_life_is_a_violation_when_resolvable(self, tmp_path):
        _app(tmp_path, "public/core", "alpha", _panel("a", "stat-tile"))
        violations, _ = chk.analyze(
            chk.collect(tmp_path), RENDERERS, hub_life_renderers_=set()  # resolvable, empty
        )
        assert [v["kind"] for v in violations] == ["unknown_renderer_hub_life"]
        assert "hub-life" in violations[0]["fix"]

    def test_present_in_both_maps_is_silent(self, tmp_path):
        _app(tmp_path, "public/core", "alpha", _panel("a", "stat-tile"))
        violations, _ = chk.analyze(
            chk.collect(tmp_path), RENDERERS, hub_life_renderers_={"stat-tile"}
        )
        assert violations == []

    def test_unresolvable_hub_life_skips_the_check_entirely(self, tmp_path):
        """`None` (gitignored/absent, e.g. a fresh clone) must degrade to
        unchecked, never to a silent pass counted as a real verification."""
        _app(tmp_path, "public/core", "alpha", _panel("a", "stat-tile"))
        violations, _ = chk.analyze(
            chk.collect(tmp_path), RENDERERS, hub_life_renderers_=None
        )
        assert violations == []

    def test_hub_life_panel_namespace_never_cross_checked(self, tmp_path):
        """`[[contributes.hub-life.panel]]` is its own opt-out (TestSilentOnHealthy
        already pins this against hub.js); confirm it holds against a
        non-None hub-life map too — the namespace is what exempts it, not
        just an unresolvable map."""
        _app(tmp_path, "public/standard", "alpha", _panel("x", "hero-alert", ns="hub-life"))
        violations, _ = chk.analyze(
            chk.collect(tmp_path), RENDERERS, hub_life_renderers_=set()
        )
        assert violations == []


class TestHubLifeRendererMapParsing:
    def test_reads_the_real_hub_life_index(self):
        found = chk.hub_life_renderers()
        if found is None:
            pytest.skip("apps/personal/hub-life/ absent — personal, gitignored")
        assert "stat-tile" in found
        assert len(found) > 10, "RENDERERS regex stopped matching — the map moved"

    def test_missing_file_returns_none_not_empty_set(self, tmp_path):
        assert chk.hub_life_renderers(tmp_path / "nope.html") is None

    def test_unparseable_file_returns_none(self, tmp_path):
        p = tmp_path / "index.html"
        p.write_text("<html>no RENDERERS map here</html>", encoding="utf-8")
        assert chk.hub_life_renderers(p) is None


class TestLiveTree:
    def test_live_tree_has_no_gating_violations(self):
        """Asserts the gating class only — never the advisory count.

        Passes ``hub_life_renderers()`` too (``None`` on a fresh clone
        without the personal app degrades to unchecked, never a false
        pass) so this test can't go green while a `hub.panel` renderer
        is missing from hub-life's own map — the exact 2026-08-05 gap.
        """
        panels = chk.collect(REPO / "apps")
        violations, _ = chk.analyze(panels, chk.hub_renderers(), chk.hub_life_renderers())
        assert violations == [], f"real hub.panel violations: {violations}"

    def test_live_tree_actually_has_panels(self):
        """Guards against the scan silently matching nothing and 'passing'."""
        assert len(chk.collect(REPO / "apps")) > 50
