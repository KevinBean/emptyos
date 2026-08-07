"""Unit tests for scripts/check_undefined_names.py.

Pins BOTH directions per `.claude/rules/audits.md`: the checker must catch the
real bug class, AND stay silent on healthy code. A gate that only ever passes is
indistinguishable from a broken gate — which matters here because the whole tree
currently reports zero findings.

The fixtures reproduce the two shapes that actually shipped in
`apps/personal/shadowing` (2026-08-07): a helper module missing an import the
spine held, and a `TYPE_CHECKING`-only name used at runtime.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_SCRIPT = REPO / "scripts" / "check_undefined_names.py"


def _load():
    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location("_cun", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cun = _load()


# ── the TYPE_CHECKING pass (the half pyflakes cannot do) ────────────────────

HEALTHY = '''
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import ShadowingApp  # noqa: F401


def helper(self: "ShadowingApp", x: int) -> ShadowingApp:
    """Annotation-only use — this is exactly what the guard is FOR."""
    return self.thing(x)
'''

LEAKED = '''
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import ShadowingApp  # noqa: F401


def helper(payload):
    # Runtime use of a guarded name — NameError the moment this line runs.
    return ShadowingApp._alignment_to_events(payload)
'''

OPTED_OUT = '''
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import ShadowingApp  # noqa: F401


def helper(payload):
    # undefined-names: ignore
    return ShadowingApp._alignment_to_events(payload)
'''

ALSO_RUNTIME_IMPORTED = '''
from __future__ import annotations
from typing import TYPE_CHECKING

from .app import ShadowingApp

if TYPE_CHECKING:
    from .app import ShadowingApp  # noqa: F401


def helper(payload):
    return ShadowingApp._alignment_to_events(payload)
'''


def _scan(tmp_path: Path, src: str) -> list[dict]:
    f = tmp_path / "helper.py"
    f.write_text(src, encoding="utf-8")
    # the checker reports paths relative to REPO; point it at tmp for the test
    orig = cun.REPO
    cun.REPO = tmp_path
    try:
        return cun.type_checking_leaks([f])
    finally:
        cun.REPO = orig


class TestTypeCheckingPass:
    def test_catches_runtime_use_of_guarded_name(self, tmp_path):
        """The bug that shipped in shadowing/passages.py."""
        found = _scan(tmp_path, LEAKED)
        assert len(found) == 1
        assert found[0]["kind"] == "type-checking-only"
        assert "ShadowingApp" in found[0]["detail"]

    def test_silent_on_annotation_only_use(self, tmp_path):
        """The healthy pattern every decomposed helper uses — must not fire.

        This is the false-positive floor: ~50 helper modules in the tree use a
        TYPE_CHECKING import purely for hints. Firing on those would make the
        gate unusable within a week.
        """
        assert _scan(tmp_path, HEALTHY) == []

    def test_silent_when_also_imported_at_runtime(self, tmp_path):
        """A guarded import that is ALSO a real import is fine."""
        assert _scan(tmp_path, ALSO_RUNTIME_IMPORTED) == []

    def test_inline_opt_out_respected(self, tmp_path):
        assert _scan(tmp_path, OPTED_OUT) == []

    def test_pyflakes_genuinely_misses_this(self, tmp_path):
        """Justifies the second pass existing at all.

        If pyflakes ever learns to catch this, the AST pass becomes redundant
        and this test tells us so by failing.
        """
        f = tmp_path / "leaked.py"
        f.write_text(LEAKED, encoding="utf-8")
        assert cun.pyflakes_undefined([f]) == [], (
            "pyflakes now catches TYPE_CHECKING leaks — the AST pass may be redundant"
        )


# ── the pyflakes pass ───────────────────────────────────────────────────────

MISSING_IMPORT = '''
from pathlib import Path


def serve(filepath):
    # `shutil` lives in the spine module, not here — exactly the shadowing bug.
    shutil.copy2(str(filepath), "/tmp/x")
'''


class TestPyflakesPass:
    def test_catches_missing_import(self, tmp_path):
        f = tmp_path / "audio.py"
        f.write_text(MISSING_IMPORT, encoding="utf-8")
        orig = cun.REPO
        cun.REPO = tmp_path
        try:
            found = cun.pyflakes_undefined([f])
        finally:
            cun.REPO = orig
        assert any(x["kind"] == "undefined-name" and "shutil" in x["detail"] for x in found)

    def test_silent_on_clean_file(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("import shutil\n\n\ndef f(p):\n    return shutil.copy2(p, p)\n",
                     encoding="utf-8")
        orig = cun.REPO
        cun.REPO = tmp_path
        try:
            assert cun.pyflakes_undefined([f]) == []
        finally:
            cun.REPO = orig


# ── the live tree ───────────────────────────────────────────────────────────

@pytest.mark.slow
def test_repo_is_clean():
    """The tree must stay at zero. This is the gate's real assertion."""
    files = cun._iter_py()
    assert files, "scan found no files — the walker is broken"
    findings = cun.pyflakes_undefined(files) + cun.type_checking_leaks(files)
    assert findings == [], f"undefined names in the tree: {findings[:5]}"
