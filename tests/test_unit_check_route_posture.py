"""Both directions for scripts/check_route_posture.py — it fires on a new
unclassified core route and on a hosted config that resolves to operator, and
is silent on a healthy tree. A green checker that has never been red pins
nothing (audits.md § Failure mode 3).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_route_posture.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_route_posture", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_route_posture"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


# ── Route classification ─────────────────────────────────────────────────────

def test_healthy_tree_is_clean():
    # The live tree must classify every core route.
    assert mod.scan_routes(ROOT) == []


def test_new_unclassified_route_is_flagged(tmp_path):
    web = tmp_path / "emptyos" / "web"
    web.mkdir(parents=True)
    (web / "x.py").write_text(
        '@server.get("/api/brand-new-thing")\n'
        "async def x(request):\n    return {}\n",
        encoding="utf-8",
    )
    findings = mod.scan_routes(tmp_path)
    assert any("brand-new-thing" in f for f in findings), findings


def test_operator_route_is_accepted(tmp_path):
    # A route matching OPERATOR_ROUTES needs no USER_ROUTES entry.
    web = tmp_path / "emptyos" / "web"
    web.mkdir(parents=True)
    (web / "x.py").write_text(
        '@server.post("/api/cli")\nasync def x(request):\n    return {}\n',
        encoding="utf-8",
    )
    assert mod.scan_routes(tmp_path) == []


def test_reviewed_user_route_is_accepted(tmp_path):
    web = tmp_path / "emptyos" / "web"
    web.mkdir(parents=True)
    (web / "x.py").write_text(
        '@server.get("/api/health")\nasync def x(request):\n    return {}\n',
        encoding="utf-8",
    )
    assert mod.scan_routes(tmp_path) == []


# ── Config posture resolution ────────────────────────────────────────────────

@pytest.mark.parametrize("data,expected", [
    ({"trust": {"web": "user"}, "network": {"mode": "local"}}, "user"),
    ({"trust": {"web": "operator"}, "demo": {"enabled": True}}, "operator"),
    ({"trust": {"web": "typo"}}, "user"),                       # fail closed
    ({"demo": {"enabled": True}, "network": {"mode": "public"}}, "user"),
    ({"cloud": {"locked": True}, "network": {"mode": "private"}}, "user"),
    ({"network": {"mode": "local"}}, "operator"),
    ({"network": {"mode": "private"}}, "operator"),
    ({"network": {"mode": "public"}}, ""),                      # unresolved
    ({}, "operator"),
])
def test_resolve_trust_web_matches_config(data, expected):
    assert mod.resolve_trust_web(data) == expected


def test_resolve_matches_the_daemons_own_resolution():
    # The checker reimplements Config.trust_web (no daemon import). Pin that the
    # two agree, so the reimplementation can't drift silently.
    import tempfile
    from emptyos.kernel.config import Config

    for data in [
        {"trust": {"web": "user"}},
        {"demo": {"enabled": True}},
        {"cloud": {"locked": True}},
        {"network": {"mode": "public"}},
        {"network": {"mode": "local"}},
        {},
    ]:
        d = tempfile.mkdtemp()
        p = Path(d) / "emptyos.toml"
        p.write_text("")
        c = Config(str(p))
        c._data = data
        assert mod.resolve_trust_web(data) == c.trust_web, data


def test_healthy_hosted_configs_are_user_posture():
    assert mod.scan_configs(ROOT) == []


def test_operator_hosted_config_is_flagged(tmp_path):
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "emptyos.toml").write_text(
        '[network]\nmode = "public"\n', encoding="utf-8"
    )
    findings = mod.scan_configs(tmp_path)
    assert any("demo/emptyos.toml" in f.replace("\\", "/") for f in findings), findings
