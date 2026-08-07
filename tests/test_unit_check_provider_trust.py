"""Pins both directions of scripts/check_provider_trust.py.

Both matter. A checker that fires on a healthy config gets ignored within a
month (`.claude/rules/audits.md`); one that stays quiet on a rented GPU is
worse than nothing. So: silent on the real-world shapes, loud on the ambiguous
ones.

The calibration that matters is the *public* row. An earlier version flagged
every `api.openai.com` endpoint — 5 findings on a healthy config, all noise,
because address inference already classifies those correctly as cloud. Only the
private/tailnet zone is genuinely ambiguous.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "check_provider_trust", REPO / "scripts" / "check_provider_trust.py")
CHK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CHK)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "t.toml"
    p.write_text(body, encoding="utf-8")
    return p


# --- quiet on everything a healthy machine really has --------------------

@pytest.mark.parametrize("host", [
    "http://localhost:11434",
    "http://127.0.0.1:8188",
    "http://localhost:8188",
])
def test_loopback_is_never_flagged(tmp_path, host):
    cfg = _write(tmp_path, f'[plugins.x]\nhost = "{host}"\n')
    assert CHK.scan(cfg) == []


@pytest.mark.parametrize("host", [
    "https://api.openai.com",
    "https://openrouter.ai/api",
    "smtp.gmail.com",
    "https://api.pexels.com",
])
def test_public_hosts_are_not_flagged(tmp_path, host):
    """Inference already calls these cloud and the gate already fires.
    Demanding a declaration here is noise — this is the calibration row."""
    cfg = _write(tmp_path, f'[capabilities.think.y]\nhost = "{host}"\n')
    assert CHK.scan(cfg) == []


def test_declared_trust_silences_an_ambiguous_host(tmp_path):
    cfg = _write(tmp_path,
                 '[plugins.comfyui]\nhost = "http://gpu.ts.net:8188"\ntrust = "rented"\n')
    assert CHK.scan(cfg) == []


def test_missing_config_is_not_an_error(tmp_path):
    assert CHK.scan(tmp_path / "nope.toml") == []


# --- loud on exactly the gap ---------------------------------------------

@pytest.mark.parametrize("host", [
    "http://gpu-rental.ts.net:8188",      # Tailscale MagicDNS
    "http://100.64.0.10:8188",          # Tailscale CGNAT
    "http://192.168.1.50:8188",           # private LAN
    "http://10.0.0.5:8188",
    "http://box.local:8188",              # mDNS
])
def test_ambiguous_hosts_are_flagged(tmp_path, host):
    """These read as LOCAL to the consent gate but could be someone else's box."""
    cfg = _write(tmp_path, f'[plugins.comfyui]\nhost = "{host}"\n')
    found = CHK.scan(cfg)
    assert len(found) == 1
    assert found[0]["issue"] == "undeclared_trust"
    assert found[0]["where"] == "plugins.comfyui"


@pytest.mark.parametrize("bad", ["ownd", "local", "mine", "yes"])
def test_invalid_trust_value_is_flagged(tmp_path, bad):
    cfg = _write(tmp_path, f'[plugins.x]\nhost = "http://10.0.0.5:1"\ntrust = "{bad}"\n')
    found = CHK.scan(cfg)
    assert len(found) == 1
    assert found[0]["issue"] == "invalid_trust"


def test_nested_tables_are_walked(tmp_path):
    cfg = _write(tmp_path, '[capabilities.think.remote]\nhost = "http://gpu.ts.net:1"\n')
    assert [f["where"] for f in CHK.scan(cfg)] == ["capabilities.think.remote"]


def test_mixed_config_reports_only_the_real_problems(tmp_path):
    cfg = _write(tmp_path, """
[plugins.ollama]
host = "http://localhost:11434"

[plugins.comfyui]
host = "http://gpu-rental.ts.net:8188"

[capabilities.think.openai]
host = "https://api.openai.com"

[capabilities.think.owned-vps]
host = "http://100.64.0.9:9000"
trust = "owned"
""")
    found = CHK.scan(cfg)
    assert [f["where"] for f in found] == ["plugins.comfyui"]


def test_exit_code_equals_finding_count(tmp_path, monkeypatch, capsys):
    cfg = _write(tmp_path, '[plugins.a]\nhost = "http://10.0.0.1:1"\n'
                           '[plugins.b]\nhost = "http://10.0.0.2:1"\n')
    monkeypatch.setattr(CHK.sys, "argv", ["check", "--config", str(cfg)])
    assert CHK.main() == 2
    capsys.readouterr()
