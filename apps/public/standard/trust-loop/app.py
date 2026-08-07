"""Trust Loop — trustworthy-engineering showcase over IEEE 80 tolerable voltages.

A public, self-contained demonstration of the seven-stage trust loop: the
calculator computes IEEE Std 80 tolerable touch/step voltages from a pure
in-app engine (ieee80.py), the manifest-declared conformance case pins the
standard's own published numbers, and the KB "?" popovers cite the formula
note the code implements. The UI computes nothing; every number comes from
the engine.
"""

from __future__ import annotations

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.conformance import CalculatorRoutesMixin

from . import ieee80


class TrustLoopApp(CalculatorRoutesMixin, BaseApp):
    """Thin app over the pure engine. CalculatorRoutesMixin contributes
    /api/methods, /api/conformance, /api/conformance/run via the MRO walk
    in BaseApp._get_decorated."""

    # ── Web API ────────────────────────────────────────────────

    @web_route("POST", "/api/calc")
    async def api_calc(self, request):
        """Compute tolerable voltages. Body: {rho, t_s, body_kg?, rho_s?, h_s?, method?}."""
        body = await self.safe_json(request)
        try:
            payload = _coerce_inputs(body)
        except (TypeError, ValueError) as e:
            return {"error": str(e)}
        try:
            spec = self.resolve_method("tolerable", body.get("method"))
            result = await spec.run(self, payload)
        except ValueError as e:
            return {"error": str(e)}
        return {
            "result": result,
            "provenance": self.last_compute_provenance("tolerable"),
        }

    # ── Method fn (dispatched via the method registry) ─────────
    async def _calc_tolerable(self, payload: dict) -> dict:
        return ieee80.tolerable_voltages(
            rho=payload["rho"],
            t_s=payload["t_s"],
            body_kg=payload.get("body_kg", 50),
            rho_s=payload.get("rho_s"),
            h_s=payload.get("h_s", 0.0),
        )

    # ── Conformance case (declared in manifest) ────────────────
    async def _conf_ieee80_inputs(self) -> dict:
        return {"rho": 100.0, "t_s": 0.5, "body_kg": 50}

    async def _conf_ieee80_expected(self) -> dict:
        # The standard's own resolved numbers for the case above.
        return {"e_touch_v": 188.65, "e_step_v": 262.48}

    # ── CLI ────────────────────────────────────────────────────
    @cli_command("trust-loop", help="Tolerable touch/step voltages (IEEE 80 closed forms)")
    async def cli_calc(self, rho: float = 100.0, t_s: float = 0.5, body_kg: int = 50):
        """Compute tolerable touch/step voltages (defaults = the anchor case)."""
        out = ieee80.tolerable_voltages(rho=rho, t_s=t_s, body_kg=body_kg)
        print(f"E_touch = {out['e_touch_v']} V   E_step = {out['e_step_v']} V")
        print(f"(C_s={out['c_s']}  I_B={out['i_b_a']} A  "
              f"R_touch={out['r_touch_ohm']} ohm  R_step={out['r_step_ohm']} ohm)")


def _coerce_inputs(body: dict) -> dict:
    """Normalize the request body at the write boundary; engine re-validates."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    payload: dict = {
        "rho": float(body.get("rho", 0) or 0),
        "t_s": float(body.get("t_s", 0) or 0),
        "body_kg": int(body.get("body_kg", 50) or 50),
    }
    if body.get("rho_s") not in (None, "", 0, "0"):
        payload["rho_s"] = float(body["rho_s"])
        payload["h_s"] = float(body.get("h_s", 0) or 0)
    return payload
