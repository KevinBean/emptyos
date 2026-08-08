"""Trust Loop — how an engineering calculator earns trust, demonstrated live.

Computes the three-phase prospective fault current at the LV terminals of an
MV/LV transformer from a pure in-app engine (`fault_current.py`), written
against `ALGORITHM.md`. The conformance gate is a published worked example
from an openly-downloadable source, so the anchor is a number the reader can
check without trusting this app.

The UI computes nothing; every number it shows came from the engine.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import PlainTextResponse

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.conformance import CalculatorRoutesMixin

from . import fault_current as fc

# The published worked example — ABB Technical Application Paper No. 2,
# clause 2.2. Inputs verbatim from the paper; the expected result is its
# printed answer. See ALGORITHM.md section 6.1.
PUBLISHED_CASE = {
    "u_net": 20000.0, "i_k_net": 14400.0, "c": 1.1,
    "r_mv": 0.360, "x_mv": 0.335,
    "s_n": 400000.0, "u_2n": 400.0, "vk_pct": 4.0, "pk_pct": 3.0,
    "r_lv": 0.000388, "x_lv": 0.000395,
}
PUBLISHED_RESULT_A = 14943.0   # "Ik3F = 14943 A = 14.95 kA"


class TrustLoopApp(CalculatorRoutesMixin, BaseApp):
    """Thin app over the pure engine. CalculatorRoutesMixin contributes
    /api/methods, /api/conformance and /api/conformance/run via the MRO walk
    in BaseApp._get_decorated."""

    # ── Web API ────────────────────────────────────────────────

    @web_route("POST", "/api/calc")
    async def api_calc(self, request):
        """Prospective fault current. Body: the inputs of ALGORITHM.md section 2."""
        body = await self.safe_json(request)
        try:
            payload = _coerce_inputs(body)
        except (TypeError, ValueError) as e:
            return {"error": str(e)}
        try:
            spec = self.resolve_method("fault", body.get("method"))
            result = await spec.run(self, payload)
        except ValueError as e:
            return {"error": str(e)}
        return {"result": result, "provenance": self.last_compute_provenance("fault")}

    @web_route("POST", "/api/sensitivity")
    async def api_sensitivity(self, request):
        """Sweep one element's impedance, for the chart.

        Same engine as the headline number, so the curve and the answer can
        never disagree.
        """
        body = await self.safe_json(request)
        element = str(body.get("element") or "transformer")
        try:
            payload = _coerce_inputs(body)
            points = fc.sensitivity(base=payload, element=element)
        except (TypeError, ValueError) as e:
            return {"error": str(e)}
        return {"element": element, "points": points}

    @web_route("GET", "/api/published-case")
    async def api_published_case(self, request):
        """The anchor case, so the page can load it in one click."""
        return {"inputs": dict(PUBLISHED_CASE), "published_result_a": PUBLISHED_RESULT_A}

    # Two handlers, not one with stacked decorators: `@web_route` stamps a
    # single `_eos_web` dict, so stacking silently registers only the outer.
    @web_route("GET", "/api/report")
    async def api_report_get(self, request):
        """The published case as a markdown calculation report."""
        return self._render_report(dict(PUBLISHED_CASE))

    @web_route("POST", "/api/report")
    async def api_report_post(self, request):
        """A markdown calculation report for the supplied inputs."""
        body = await self.safe_json(request)
        return self._render_report(body or dict(PUBLISHED_CASE))

    def _render_report(self, body: dict):
        """Inputs through to result, in a form that pastes into a document."""
        try:
            payload = _coerce_inputs(body)
            result = fc.prospective_fault_current(**payload)
        except (TypeError, ValueError) as e:
            return PlainTextResponse(f"cannot report: {e}", status_code=400)
        return PlainTextResponse(_report_markdown(result), media_type="text/markdown")

    @web_route("GET", "/api/algorithm")
    async def api_algorithm(self, request):
        """Serve ALGORITHM.md — stage 2 of the loop, as a live artifact.

        The loop strip links here, so the specification the engine was written
        against is one click away rather than a claim on a card.
        """
        doc = Path(__file__).parent / "ALGORITHM.md"
        if not doc.is_file():
            return PlainTextResponse("algorithm document not found", status_code=404)
        return PlainTextResponse(doc.read_text(encoding="utf-8"), media_type="text/markdown")

    # ── Method fn (dispatched via the method registry) ─────────
    async def _calc_fault(self, payload: dict) -> dict:
        return fc.prospective_fault_current(**payload)

    # ── Conformance (declared in manifest) ─────────────────────
    async def _conf_published_inputs(self) -> dict:
        return dict(PUBLISHED_CASE)

    async def _conf_published_expected(self) -> dict:
        return {"i_k3_a": PUBLISHED_RESULT_A}

    # ── CLI ────────────────────────────────────────────────────
    @cli_command("trust-loop", help="Prospective fault current at MV/LV transformer terminals")
    async def cli_calc(self, published: bool = True):
        """Run the published worked example and show the working."""
        r = fc.prospective_fault_current(**PUBLISHED_CASE)
        dev = abs(r["i_k3_a"] - PUBLISHED_RESULT_A) / PUBLISHED_RESULT_A * 100
        print(f"I_k3 = {r['i_k3_ka']} kA   (published {PUBLISHED_RESULT_A / 1000:.2f} kA, "
              f"{dev:.3f}% deviation)")
        print(f"Z = {r['z_total_ohm']:.6f} ohm  (R {r['r_total_ohm']:.6f}, "
              f"X {r['x_total_ohm']:.6f})")
        for c in r["contributions"]:
            print(f"   {c['name']:<16} Z={c['z_ohm']:.6f}  {c['share_pct']:>5.1f}%")


def _report_markdown(result: dict) -> str:
    """Render the engine's steps as a checkable calculation sheet."""
    lines = [
        "# Calculation report — three-phase prospective fault current",
        "",
        "Method: impedance referral, per `ALGORITHM.md` (served at "
        "`/trust-loop/api/algorithm`).",
        "Source of method and anchor case: ABB Technical Application Paper No. 2, "
        "cl. 2.2 — `1SDC007101G0202`.",
        "",
    ]
    group = None
    for s in result.get("steps", []):
        if s["group"] != group:
            group = s["group"]
            lines += ["", f"## {group}", "",
                      "| Symbol | Formula | Substitution | Value |",
                      "|---|---|---|---:|"]
        sub = "*given*" if s["substitution"] == "given" else f"`{s['substitution']}`"
        unit = f" {s['unit']}" if s["unit"] else ""
        lines.append(f"| `{s['symbol']}` | {s['formula']} | {sub} | **{s['value_str']}{unit}** |")
        if s["note"]:
            lines.append(f"| | | | <sub>{s['note']}</sub> |")
    lines += [
        "",
        "## Result",
        "",
        f"**I_k3 = {result['i_k3_ka']} kA** ({result['i_k3_a']} A)",
        "",
        f"Dominant element: {result['dominant']} "
        f"({max(c['share_pct'] for c in result['contributions'])}% of total impedance).",
        "",
        "Limits of this calculation are stated in ALGORITHM.md section 5 — it is the "
        "paper's simplified method, not a full IEC 60909 study.",
    ]
    return "\n".join(lines)


def _coerce_inputs(body: dict) -> dict:
    """Normalize the request body at the write boundary; engine re-validates."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    out = {}
    for key, default in (
        ("u_net", 0.0), ("i_k_net", 0.0), ("c", 1.1),
        ("r_mv", 0.0), ("x_mv", 0.0),
        ("s_n", 0.0), ("u_2n", 0.0), ("vk_pct", 0.0), ("pk_pct", 0.0),
        ("r_lv", 0.0), ("x_lv", 0.0),
    ):
        raw = body.get(key)
        out[key] = float(default if raw in (None, "") else raw)
    return out
