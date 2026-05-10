"""Steady-state 2D FEM heat solver — linear triangles.

Solves  ∇·(k ∇T) + Q = 0  on a triangular mesh with Dirichlet BC.

Element formulation (standard 3-node linear triangle):
    K_e = (k_e / 4A) · (b·bᵀ + c·cᵀ)        where b_i = y_j − y_k, c_i = x_k − x_j
    f_e = Q_e · A / 3 · [1, 1, 1]

Coordinates assumed in millimetres (matches gmsh output); converted to
metres internally so k [W/m·K] and Q [W/m² in 2D] keep their natural units.

Ported from cable-current-rating/src/fem/solver.py — transient stepping
and Robin BC trimmed; will return when a Phase B case needs them.
"""

from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

from .mesh import FEMMesh


def solve_thermal(
    mesh: FEMMesh,
    conductivity: np.ndarray,
    heat_source: np.ndarray,
    T_boundary: float = 20.0,
) -> np.ndarray:
    """Solve 2D steady-state heat equation on the triangular mesh.

    Args:
        mesh: FEM mesh; nodes in mm.
        conductivity: (M,) thermal conductivity per element, W/m·K.
        heat_source: (M,) volumetric source per element, W/m² (= W/m per metre depth).
        T_boundary: Dirichlet temperature at boundary nodes, °C.

    Returns:
        (N,) temperature at each node, °C.
    """
    N = mesh.n_nodes
    nodes_m = mesh.nodes * 1e-3

    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    rhs = np.zeros(N, dtype=np.float64)

    for e in range(mesh.n_elements):
        n0, n1, n2 = mesh.elements[e]
        x = nodes_m[[n0, n1, n2], 0]
        y = nodes_m[[n0, n1, n2], 1]
        area = 0.5 * abs((x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0]))
        if area < 1e-20:
            continue

        b = np.array([y[1] - y[2], y[2] - y[0], y[0] - y[1]])
        c = np.array([x[2] - x[1], x[0] - x[2], x[1] - x[0]])

        factor = conductivity[e] / (4.0 * area)
        K_local = factor * (np.outer(b, b) + np.outer(c, c))

        f_local = heat_source[e] * area / 3.0
        local = (n0, n1, n2)
        for i in range(3):
            rhs[local[i]] += f_local
            for j in range(3):
                rows.append(local[i])
                cols.append(local[j])
                vals.append(K_local[i, j])

    K = sparse.csr_matrix((vals, (rows, cols)), shape=(N, N), dtype=np.float64)

    K = K.tolil()
    for node in mesh.boundary_nodes:
        K[node, :] = 0
        K[node, node] = 1.0
        rhs[node] = T_boundary
    K = K.tocsr()

    return spsolve(K, rhs)


def element_average_temperature(
    T: np.ndarray, elements: np.ndarray, elem_indices: np.ndarray
) -> float:
    """Mean of element-averaged nodal temperatures over a selection."""
    if len(elem_indices) == 0:
        return 0.0
    sel = elements[elem_indices]
    T_elem = (T[sel[:, 0]] + T[sel[:, 1]] + T[sel[:, 2]]) / 3.0
    return float(np.mean(T_elem))
