"""Unit tests for the sim app's vault-frontmatter numeric coercion.

Regression pin for the `eos sim` CLI crash (usecase-audit 2026-07-18): vault
frontmatter values are strings, so `runtime_s` / `kcl_residual_max` arrived as
e.g. "1.46e-11"; the CLI formatted them with a numeric code (`:.2e` / `:.2f`)
and raised `Unknown format code 'e' for object of type 'str'`. The fix coerces
them to float at the `_list_runs` read boundary via `_num`. No daemon needed —
`_num` is a module-level pure function.
"""

import pytest

from helpers import load_app_module  # noqa: E402

# The sim app hard-imports numpy at module scope
# (apps/extension/engineering/sim/app.py), so loading it here fails collection
# wherever numpy is absent -- and numpy is not a base dependency, so that
# includes CI. Guard before the load, not after: the ModuleNotFoundError is
# raised by load_app_module itself.
pytest.importorskip("numpy")

sim = load_app_module("sim", "app")
_num = sim._num


def test_num_coerces_scientific_string():
    assert _num("1.46e-11") == 1.46e-11


def test_num_passes_through_float():
    assert _num(0.04) == 0.04


def test_num_handles_none_and_empty():
    assert _num(None) is None
    assert _num("") is None


def test_num_rejects_garbage_without_raising():
    assert _num("not-a-number") is None
    assert _num("N/A") is None


def test_num_coerces_integerish_string():
    assert _num("3201") == 3201.0


def test_coerced_values_survive_the_cli_format():
    """The exact format lines from cmd_sim that used to crash on a str."""
    kcl = _num("1.46e-11")
    runtime = _num("0.13")
    line = "run"
    if kcl is not None:
        line += f"  kcl={kcl:.2e}"       # was: raised on the raw string
    if runtime:
        line += f"  runtime={runtime:.2f}s"
    assert line == "run  kcl=1.46e-11  runtime=0.13s"
