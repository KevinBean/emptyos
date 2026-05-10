"""Tests for the Jacobian SVD diagnostic."""

import numpy as np
import pytest
from engines.soil.diagnostics import (
    CONDITION_NUMBER_THRESHOLD,
    free_param_names,
    jacobian_diagnostic,
)


def test_well_conditioned_identity_jacobian():
    """Identity Jacobian: each parameter independently constrained."""
    J = np.eye(3)
    names = ["log(rho_1)", "log(rho_2)", "log(h_1)"]
    d = jacobian_diagnostic(J, names)
    assert d.is_well_conditioned
    assert d.condition_number == pytest.approx(1.0)
    assert d.unresolved_directions == ()


def test_singular_jacobian_flagged_unresolved():
    """If two columns are identical, that combination is unresolved."""
    # Two parameters that affect every measurement identically — only their sum is constrained
    J = np.array([
        [1.0, 1.0, 0.0],
        [1.0, 1.0, 1.0],
        [1.0, 1.0, 2.0],
    ])
    names = ["log(rho_1)", "log(rho_2)", "log(h_1)"]
    d = jacobian_diagnostic(J, names)
    assert not d.is_well_conditioned
    assert d.condition_number > CONDITION_NUMBER_THRESHOLD
    # The unresolved direction should involve rho_1 and rho_2 (their difference)
    assert len(d.unresolved_directions) >= 1
    desc = d.unresolved_directions[0].description
    assert "log(rho_1)" in desc and "log(rho_2)" in desc


def test_free_param_names_no_locks():
    names = free_param_names(2, (False, False), (False,))
    assert names == ["log(rho_1)", "log(rho_2)", "log(h_1)"]


def test_free_param_names_with_locks():
    names = free_param_names(3, (True, False, False), (False, True))
    # Locked: rho_1, h_2 → free: rho_2, rho_3, h_1
    assert names == ["log(rho_2)", "log(rho_3)", "log(h_1)"]


def test_empty_jacobian_returns_well_conditioned():
    d = jacobian_diagnostic(np.empty((0, 0)), [])
    assert d.is_well_conditioned
    assert d.condition_number == 1.0
    assert d.unresolved_directions == ()
