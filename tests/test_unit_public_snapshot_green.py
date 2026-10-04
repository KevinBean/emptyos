"""Pins for what kept the public snapshot's own suite red, or thin.

Measured on the public v0.7.0 snapshot, 2026-09-26: 30 collection errors
aborted every run, because the release scrub deleted `tests/conftest.py`, and
`preflight --scope apps` failed on a row whose app the snapshot drops. Then,
2026-09-27: the pruned-service rule dropped any test that named a held service
in prose, including the regression test for v0.7.1's own fix.

Lines that name a held service as fixture data carry `release-filter: optional`
so this file itself ships.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PUBLIC = ROOT / "scripts" / "release-public.py"
CROSSWALK = ROOT / "scripts" / "gen_vowel_crosswalk_artifact.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not RELEASE_PUBLIC.exists(), reason="release-public.py not in this tree")
def test_conftest_names_no_held_app():
    """`scrub_prose_held_refs` deletes any test file naming a held app. conftest
    carries the `apps.<id>` import finder and every shared fixture, so losing it
    broke collection for core apps too (task, hub, reactor). Held-app cleanup
    lives in tests/cleanup_engineering.py, which the scrub is free to drop."""
    tokens = _load("release_public_for_conftest_pin", RELEASE_PUBLIC).PUBLIC_DOC_HELD_TOKENS
    text = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert tokens, "token list came back empty; the pin would pass vacuously"
    assert [t for t in tokens if t in text] == []


@pytest.mark.skipif(not RELEASE_PUBLIC.exists(), reason="release-public.py not in this tree")
def test_the_scrub_runs_before_the_tier_filter():
    """The tier filter's test-drop pass drops a Python bridge whose node test is
    gone. It can only see what the scrub removed if the scrub ran first; after
    it, two CAD bridges shipped and failed asserting their `.mjs` exists."""
    src = RELEASE_PUBLIC.read_text(encoding="utf-8")
    docs = src.index("filter_docs(temp_dir)")
    scrub = src.index("scrub_prose_held_refs(temp_dir)")
    tiers = src.index("filter_to_tiers(temp_dir, PUBLIC_TIERS)")
    assert src.count("scrub_prose_held_refs(temp_dir)") == 1
    assert src.count("filter_docs(temp_dir)") == 1
    # filter_docs strips `public-exclude` blocks first, so a held name inside
    # one does not make the scrub drop a whole rule file.
    assert docs < scrub < tiers


@pytest.mark.skipif(not RELEASE_PUBLIC.exists(), reason="release-public.py not in this tree")
def test_a_pruned_service_binds_a_test_only_through_a_quoted_path(tmp_path):
    """A substring match dropped any test that named a held service in prose,
    which would have shipped v0.7.1 without the regression test for its own fix."""
    rp = _load("release_public_for_service_pin", RELEASE_PUBLIC)
    (tmp_path / "englishos-cloud").mkdir()  # release-filter: optional
    (tmp_path / "englishos-cloud" / "Dockerfile").write_text("FROM x\n", encoding="utf-8")  # release-filter: optional
    tests = tmp_path / "tests"
    tests.mkdir()
    prose = tests / "test_prose.py"
    prose.write_text('"""The englishos-cloud image shipped without it."""\n', encoding="utf-8")
    bound = tests / "test_bound.py"
    bound.write_text('P = ROOT / "englishos-cloud" / "Dockerfile"\n', encoding="utf-8")  # release-filter: optional
    marked = tests / "test_marked.py"
    marked.write_text(
        'P = "englishos-cloud/definitions.sqlite"  # release-filter: optional\n', encoding="utf-8"
    )
    # The shapes a substring match used to catch, which a quote-only rule missed:
    # a backtick citation in a docstring (test_unit_generate_tiers_doc.py names
    # held tiers and fails publicly), an f-string path, and a split path.
    cited = tests / "test_cited.py"
    cited.write_text('"""Reads `englishos-cloud` tier data."""\n', encoding="utf-8")  # release-filter: optional
    fstr = tests / "test_fstr.py"
    fstr.write_text('P = f"{ROOT}/englishos-cloud/Dockerfile"\n', encoding="utf-8")  # release-filter: optional
    split = tests / "test_split.py"
    split.write_text('P = ROOT / "services" / "earthing-calc"\n', encoding="utf-8")  # release-filter: optional
    (tmp_path / "services" / "earthing-calc").mkdir(parents=True)  # release-filter: optional

    rp.filter_commercial_services(tmp_path)

    assert not (tmp_path / "englishos-cloud").exists(), "the service itself must be pruned"  # release-filter: optional
    assert not bound.exists(), "a quoted path to a pruned service binds"
    assert not cited.exists(), "a backtick citation binds"
    assert not fstr.exists(), "an f-string path binds"
    assert not split.exists(), "a split quoted path binds"
    assert prose.exists(), "prose naming the service does not bind"
    assert marked.exists(), "a marked line does not bind"


def _crosswalk_tree(tmp_path: Path, with_app: bool) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(CROSSWALK, scripts / CROSSWALK.name)
    shutil.copy2(ROOT / "scripts" / "scanner_lib.py", scripts / "scanner_lib.py")
    if with_app:
        module = _load("vowel_crosswalk_for_pin", CROSSWALK)
        app_rel = module.ARTIFACT.relative_to(module.ROOT).parents[1]
        (tmp_path / app_rel).mkdir(parents=True)
    return scripts / CROSSWALK.name


def _run_check(script: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True, text=True, timeout=60,
    )


@pytest.mark.skipif(not CROSSWALK.exists(), reason="crosswalk generator not in this tree")
def test_crosswalk_check_skips_when_the_app_is_absent(tmp_path):
    result = _run_check(_crosswalk_tree(tmp_path, with_app=False))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("skip"), result.stdout


@pytest.mark.skipif(not CROSSWALK.exists(), reason="crosswalk generator not in this tree")
def test_crosswalk_check_still_fails_when_only_the_artifact_is_missing(tmp_path):
    """The skip keys on the APP, not the artifact: a present app that lost its
    artifact is real drift."""
    result = _run_check(_crosswalk_tree(tmp_path, with_app=True))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "artifact source missing" in result.stdout
