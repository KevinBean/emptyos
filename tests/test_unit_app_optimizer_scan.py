"""Both-direction pins for scripts/app_optimizer_scan.py.

Per .claude/skills/eos-graduate-audit: a checker ships with tests pinning BOTH
"fires on the real signal" and "silent on healthy code".

The two defects these guard were found by the 2026-07-15 real-run triangulation
(see 30_Resources/EmptyOS/app-optimizer/audit-log.md), and both were invisible to
every existing test because none existed:

  1. Signals were grepped as `self.think(` only, and only in top-level *.py. Apps
     decomposed per .claude/rules/multi-module-apps.md put handlers in module-level
     functions taking `app` (bound onto the class in app.py), sometimes nested. Those
     apps scored a PHANTOM zero on dims they genuinely fill — and `zeros` is the
     primary sort key of the worst-balanced list, so the phantoms sorted to the top of
     the one output a human acts on. (music-studio: 9 think calls, scored ai=0.)
  2. Test fixtures (`test-app`, `test-app-wiring`) were scored like real apps and
     permanently occupied worst-balanced slots.

Pure — builds synthetic app trees in tmp_path, never reads the real apps/ or vault.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "app_optimizer_scan.py"
_spec = importlib.util.spec_from_file_location("app_optimizer_scan", SCRIPT)
aos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aos)


def _mkapp(root: Path, aid: str, *, manifest: str = "", files: dict[str, str] | None = None) -> Path:
    """Build a synthetic app dir; returns it. files maps relative path -> source."""
    d = root / aid
    (d / "pages").mkdir(parents=True, exist_ok=True)
    d.joinpath("manifest.toml").write_text(
        manifest or f'[app]\nid = "{aid}"\nname = "{aid}"\n', encoding="utf-8"
    )
    for rel, src in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    return d


# A spine that calls think/vault via `self` — the classic single-module shape.
SELF_APP = """
from emptyos.sdk import BaseApp, web_route

class A(BaseApp):
    @web_route("GET", "/api/x")
    async def api_x(self, request):
        out = await self.think("hi")
        await self.vault_write("n.md", out)
        await self.call_app("task", "add")
        await self.emit("a:done", {})
        return {"ok": True}
"""

# The SAME work, decomposed per multi-module-apps.md: a module-level helper taking
# `app`, re-bound onto the class in app.py. Must score identically.
HELPER_APP_SPINE = """
from emptyos.sdk import BaseApp
from . import digest as _digest

class A(BaseApp):
    api_x = _digest.api_x
"""
HELPER_APP_MOD = """
from emptyos.sdk import web_route

@web_route("GET", "/api/x")
async def api_x(app, request):
    out = await app.think("hi")
    await app.vault_write("n.md", out)
    await app.call_app("task", "add")
    await app.emit("a:done", {})
    return {"ok": True}
"""


class TestMultiModuleBlindSpot:
    """Defect 1 — an `app.`-receiver helper must score like its `self.` twin."""

    def test_app_receiver_scores_ai_and_vault(self, tmp_path):
        d = _mkapp(tmp_path, "helper-app", files={
            "app.py": HELPER_APP_SPINE, "digest.py": HELPER_APP_MOD,
        })
        r = aos.score_app(d)
        assert r["d_ai"] > 0, "app.think() must count toward ai (was a phantom zero)"
        assert r["d_vault"] > 0, "app.vault_write() must count toward vault"
        assert r["think"] == 1
        assert r["call_app"] == 1 and r["emit"] == 1

    def test_self_and_app_receivers_score_identically(self, tmp_path):
        a = _mkapp(tmp_path / "s", "self-app", files={"app.py": SELF_APP})
        b = _mkapp(tmp_path / "h", "helper-app", files={
            "app.py": HELPER_APP_SPINE, "digest.py": HELPER_APP_MOD,
        })
        ra, rb = aos.score_app(a), aos.score_app(b)
        for dim in aos.DIMS:
            assert ra[f"d_{dim}"] == rb[f"d_{dim}"], (
                f"decomposition changed {dim}: self={ra[f'd_{dim}']} app={rb[f'd_{dim}']} "
                "— the scorer must not penalise following multi-module-apps.md"
            )

    def test_nested_helper_module_is_read(self, tmp_path):
        """work/ nests its helpers a package deep; top-level-only glob missed all 10."""
        d = _mkapp(tmp_path, "nested-app", files={
            "app.py": "from .lib import digest\n",
            "lib/__init__.py": "",
            "lib/digest.py": HELPER_APP_MOD,
        })
        r = aos.score_app(d)
        assert r["d_ai"] > 0, "a nested helper's app.think() must still be seen"
        assert r["think"] == 1


class TestSilentOnHealthyCode:
    """The fix must not inflate anything that was already scored correctly."""

    def test_plain_self_app_unchanged(self, tmp_path):
        d = _mkapp(tmp_path, "self-app", files={"app.py": SELF_APP})
        r = aos.score_app(d)
        assert r["think"] == 1 and r["vault"] == 1
        assert r["d_ai"] == 6  # bucket: 1 think -> 6

    def test_no_ai_signal_still_scores_zero(self, tmp_path):
        """AI=0 is CORRECT for a deterministic calculator — don't manufacture a score."""
        d = _mkapp(tmp_path, "calc", files={"app.py": (
            "from emptyos.sdk import BaseApp, web_route\n"
            "class C(BaseApp):\n"
            "    @web_route('POST', '/api/calc')\n"
            "    async def api_calc(self, request):\n"
            "        return {'amps': 42}\n"
        )})
        r = aos.score_app(d)
        assert r["think"] == 0 and r["d_ai"] == 0

    def test_bare_word_app_is_not_a_receiver(self, tmp_path):
        """A receiver merely *ending* in `app` must not count — `myapp.` contains the
        substring `app.`, so this pins the \\b in RECV, not just the alternation."""
        d = _mkapp(tmp_path, "decoy", files={"app.py": (
            "result = myapp.think('x')\n"
            "webapp.emit('y')\n"
            "other_thing.emit('z')\n"
        )})
        r = aos.score_app(d)
        assert r["think"] == 0 and r["emit"] == 0


def _manifest_id(d: Path) -> str:
    # Deliberately independent of app_layout._manifest_id: the fixture-exclusion
    # tests need an oracle that doesn't share the production id-reading code path,
    # or a bug there would break both sides identically and still pass.
    try:
        return tomllib.loads((d / "manifest.toml").read_text(encoding="utf-8")).get(
            "app", {}).get("id", d.name)
    except Exception:
        return d.name


class TestFixtureExclusion:
    """Defect 2 — test fixtures are not apps and must not be scored."""

    def test_fixture_ids_are_declared(self):
        assert aos.FIXTURE_IDS == {"test-app", "test-app-wiring"}

    def test_app_dirs_excludes_fixtures_from_the_real_tree(self):
        ids = {_manifest_id(d) for d in aos.app_dirs()}
        assert "test-app" not in ids
        assert "test-app-wiring" not in ids

    def test_app_dirs_still_excludes_retired_and_example(self):
        dirs = aos.app_dirs()
        assert dirs, "discovery returned nothing — the walker is broken"
        for d in dirs:
            assert not any(x in d.parts for x in ("_retired", "_example", "_catalog"))


class TestPureHelpers:
    def test_bucket_picks_largest_threshold_at_or_below(self):
        t = [(0, 0), (1, 4), (3, 7), (5, 10)]
        assert aos._bucket(0, t) == 0
        assert aos._bucket(2, t) == 4
        assert aos._bucket(3, t) == 7
        assert aos._bucket(99, t) == 10

    def test_grade_boundaries(self):
        assert aos.grade(96) == "A"
        assert aos.grade(95) == "A-"
        assert aos.grade(84) == "A-"
        assert aos.grade(60) == "B"
        assert aos.grade(40) == "C"
        assert aos.grade(20) == "D"
        assert aos.grade(19) == "F"

    def test_count_is_a_regex_count(self):
        assert aos._count(r"x\(", "x( y( x(") == 2


class TestCliContract:
    """The agent-cli envelope + exit-code-as-signal contract (.claude/rules/agent-cli.md)."""

    def test_json_is_one_envelope_line_and_exit_equals_f_count(self):
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--json"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
        assert len(lines) == 1, f"--json must emit exactly one line, got {len(lines)}"
        env = json.loads(lines[0])
        assert set(env) == {"ok", "code", "message", "data"}
        assert env["ok"] is True and env["code"] == "ok"
        assert p.returncode == env["data"]["summary"]["f_count"], (
            "exit code must equal the F-grade count (exit-code-as-signal)"
        )

    def test_check_mode_writes_nothing(self, tmp_path):
        """--check is the preflight seam: summary only, no vault writes."""
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", "--out", str(tmp_path)],
            capture_output=True, text=True, cwd=str(REPO),
        )
        assert "App Optimizer scan" in p.stdout
        assert list(tmp_path.iterdir()) == [], "--check must not write"
