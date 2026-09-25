"""The translate plugin's availability check must not import its heavy deps.

`import ctranslate2` pulls in torch with CUDA. The health watchdog calls
``available()`` on every provider once a minute, so an import inside that
check loaded torch into every daemon whether or not anything was ever
translated. In one sandbox measurement on the dev box (2026-09-24) that
roughly doubled a learner-profile daemon's working set.

Pinned here:
  - ``available()`` answers True without importing ctranslate2 or torch. Run
    in a fresh interpreter, because an import anywhere else in the pytest
    process would make the check vacuous.
  - either missing dependency still reports unavailable.
  - an installed package that raises ImportError makes the provider report
    itself unavailable, with the reason in ``health()``, and later calls fail
    fast instead of repeating the import.
  - an OSError during the import (can be transient on Windows) and any
    failure while building the model (out of memory, bad model_dir) are not
    sticky — the next call really retries.
  - a missing model_dir still reports unavailable.

No daemon; the plugin module is loaded by path.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import requires_dep

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "translate" / "plugin.py"


def _load():
    spec = importlib.util.spec_from_file_location("translate_plugin_under_test", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PROBE = r"""
import asyncio, importlib.util, json, sys
spec = importlib.util.spec_from_file_location("translate_probe", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
heavy = ("ctranslate2", "torch", "transformers")
before = {m: m in sys.modules for m in heavy}
p = mod.NLLBTranslateProvider(model_dir=sys.argv[2])
avail = asyncio.run(p.available())
health = asyncio.run(p.health())
after = {m: m in sys.modules for m in heavy}
print(json.dumps({"before": before, "after": after, "available": avail,
                  "health_available": health["available"]}))
"""


@requires_dep("ctranslate2", "transformers")
def test_available_does_not_import_heavy_deps(tmp_path):
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, str(PLUGIN), str(tmp_path)],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout.strip().splitlines()[-1])
    # The operands must exist: nothing imported them before the check...
    assert res["before"] == {"ctranslate2": False, "torch": False, "transformers": False}
    # ...the check still says yes, since the deps and a model dir are present...
    assert res["available"] is True
    assert res["health_available"] is True
    # ...and it got there without importing any of them.
    assert res["after"] == {"ctranslate2": False, "torch": False, "transformers": False}


@pytest.mark.parametrize("missing", ["ctranslate2", "transformers"])
def test_missing_dependency_is_unavailable(tmp_path, monkeypatch, missing):
    # Every other name is reported present, so `missing` alone decides the
    # outcome — otherwise ctranslate2 being absent in CI would satisfy the
    # transformers case too.
    mod = _load()
    monkeypatch.setattr(
        mod.importlib.util, "find_spec",
        lambda name, *a, **k: None if name == missing else object(),
    )
    p = mod.NLLBTranslateProvider(model_dir=str(tmp_path))
    assert asyncio.run(p.available()) is False
    assert "not installed" in asyncio.run(p.health())["reason"]


def _present_provider(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name, *a, **k: object())
    p = mod.NLLBTranslateProvider(model_dir=str(tmp_path))
    assert asyncio.run(p.available()) is True
    return p


def test_failed_import_makes_provider_unavailable(tmp_path, monkeypatch):
    mod = _load()
    p = _present_provider(mod, tmp_path, monkeypatch)
    # A None entry in sys.modules makes `import ctranslate2` raise ImportError —
    # the shape of an installed package that will not import.
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    with pytest.raises(ImportError):
        p._load()

    assert asyncio.run(p.available()) is False
    health = asyncio.run(p.health())
    assert health["available"] is False
    assert "ctranslate2" in health["reason"]
    # Later calls fail fast instead of repeating the import.
    with pytest.raises(RuntimeError, match="nllb-200 unavailable"):
        p._load()


def test_oserror_on_import_is_retried_not_sticky(tmp_path, monkeypatch):
    # Windows raises OSError (WinError 1455) from torch's DLL load when system
    # commit is exhausted — transient, so it must not disable NLLB for good.
    import builtins

    mod = _load()
    p = _present_provider(mod, tmp_path, monkeypatch)
    real_import = builtins.__import__
    calls = []

    def fake_import(name, *a, **k):
        if name == "ctranslate2":
            calls.append(name)
            raise OSError("[WinError 1455] The paging file is too small")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for _ in range(2):
        with pytest.raises(OSError, match="1455"):
            p._load()
    assert calls == ["ctranslate2", "ctranslate2"]  # really retried
    assert asyncio.run(p.available()) is True


@pytest.mark.parametrize("exc", [
    RuntimeError("CUDA failed with error out of memory"),
    OSError("Can't load tokenizer for the model dir"),  # from_pretrained's shape
])
def test_failed_build_is_retried_not_sticky(tmp_path, monkeypatch, exc):
    # A build failure comes after the imports succeeded: out of memory, or an
    # incomplete model_dir. It must not disable the provider until restart.
    mod = _load()
    p = _present_provider(mod, tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "ctranslate2", object())
    monkeypatch.setitem(sys.modules, "transformers", object())
    calls = []

    def boom(self, ct2, tf):
        calls.append(1)
        raise exc

    monkeypatch.setattr(mod.NLLBTranslateProvider, "_build", boom)
    for _ in range(2):
        with pytest.raises(type(exc)):
            p._load()
    assert len(calls) == 2  # the second call rebuilt, it did not fast-fail
    assert asyncio.run(p.available()) is True


def test_missing_model_dir_is_unavailable(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name, *a, **k: object())
    for model_dir in ("", str(tmp_path / "absent")):
        p = mod.NLLBTranslateProvider(model_dir=model_dir)
        assert asyncio.run(p.available()) is False
        assert "model_dir" in asyncio.run(p.health())["reason"]
