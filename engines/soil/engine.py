"""SoilEngine — kernel-loaded entry point for soil resistivity inversion.

Apps access via ``self.engine("soil")``. Methods:

  invert_wenner(spacings_m, rho_a, n_layers=2, **opts) -> dict
      Run LM inversion on a Wenner sounding. Returns layered soil model + RMS
      + per-point discrepancy + Jacobian SVD diagnostic (well-conditioned flag,
      condition number, unresolved-direction descriptions).

  forward_wenner(rhos, thicknesses, spacings_m) -> list[float]
      Forward kernel — predict apparent ρ at each Wenner spacing for a given
      layered model. Useful for plotting fit overlays.

  forward_general(rhos, thicknesses, electrodes_xyz) -> float
      Forward kernel for a general 4-electrode array.

  load_csv(path) -> list[Measurement]
      Wenner CSV loader (active flag, comment column, optional R or ρ_a).

  available() -> bool
      True iff numpy + scipy import successfully.
"""

from __future__ import annotations

from emptyos.sdk import BaseEngine

from .geometry import ElectrodeArray
from .inverse import InversionConfig, Measurement, invert
from .forward import forward_apparent_resistivity
from .soil_model import SoilModel
from .io_csv import load_wenner_csv, result_to_json


class SoilEngine(BaseEngine):
    name = "soil"

    async def init(self) -> None:
        return None

    async def available(self) -> bool:
        try:
            import numpy  # noqa: F401
            import scipy  # noqa: F401
        except ImportError:
            return False
        return True

    async def health_check(self) -> dict:
        ok = await self.available()
        return {
            "status": "ok" if ok else "degraded",
            "available": ok,
            "engine_version": self.manifest.raw.get("engine", {}).get("version", "?"),
            "forward_kernel": "stefanesco + guptasarma-singh 61pt J0",
            "inverter": "scipy.optimize.least_squares (trf), log-parameters",
            "diagnostics": ["jacobian_svd_condition", "unresolved_directions"],
            "validation": "RS_TUT1 reference case (RMS within 0.1% of CDEGS RESAP)",
        }

    # ── Inversion ──────────────────────────────────────────────────

    def invert_wenner(
        self,
        spacings_m: list[float],
        rho_a_ohm_m: list[float],
        *,
        n_layers: int = 2,
        active: list[bool] | None = None,
        comments: list[str] | None = None,
        target_accuracy_pct: float = 2.5,
        max_iter: int = 500,
        bounds_resistivity: tuple[float, float] = (1.0, 1e6),
        bounds_thickness: tuple[float, float] = (1e-2, 1e3),
        initial_model: dict | None = None,
        locked_resistivities: list[bool] | None = None,
        locked_thicknesses: list[bool] | None = None,
    ) -> dict:
        if len(spacings_m) != len(rho_a_ohm_m):
            raise ValueError("spacings_m and rho_a_ohm_m must have the same length")
        active = active or [True] * len(spacings_m)
        comments = comments or [""] * len(spacings_m)

        ms = [
            Measurement(
                array=ElectrodeArray("wenner", (float(s),)),
                apparent_resistivity=float(r),
                active=bool(a),
                comment=str(c),
            )
            for s, r, a, c in zip(spacings_m, rho_a_ohm_m, active, comments)
        ]

        init_model = None
        if initial_model is not None:
            init_model = SoilModel(
                resistivities=tuple(float(x) for x in initial_model["resistivities"]),
                thicknesses=tuple(float(x) for x in initial_model["thicknesses"]),
            )

        cfg = InversionConfig(
            n_layers=n_layers,
            target_accuracy_pct=target_accuracy_pct,
            max_iter=max_iter,
            bounds_resistivity=bounds_resistivity,
            bounds_thickness=bounds_thickness,
            initial_model=init_model,
            locked_resistivities=tuple(locked_resistivities) if locked_resistivities else None,
            locked_thicknesses=tuple(locked_thicknesses) if locked_thicknesses else None,
        )

        result = invert(ms, cfg)
        return result_to_json(result, measurements=ms)

    # ── Forward kernels ────────────────────────────────────────────

    def forward_wenner(
        self,
        resistivities_ohm_m: list[float],
        thicknesses_m: list[float],
        spacings_m: list[float],
    ) -> list[float]:
        model = SoilModel(
            resistivities=tuple(float(x) for x in resistivities_ohm_m),
            thicknesses=tuple(float(x) for x in thicknesses_m),
        )
        out = []
        for s in spacings_m:
            arr = ElectrodeArray("wenner", (float(s),))
            out.append(forward_apparent_resistivity(model, arr))
        return out

    # ── I/O passthrough ────────────────────────────────────────────

    def load_csv(self, path: str) -> dict:
        """Load a Wenner CSV; returns parallel arrays suitable for invert_wenner."""
        ms = load_wenner_csv(path)
        return {
            "spacings_m": [m.array.spacings[0] for m in ms],
            "rho_a_ohm_m": [m.apparent_resistivity for m in ms],
            "active": [m.active for m in ms],
            "comments": [m.comment for m in ms],
        }
