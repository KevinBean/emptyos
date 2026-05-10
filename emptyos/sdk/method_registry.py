"""Method registry — uniform "this app exposes these algorithms" mechanism.

Reads `[[provides.methods.<endpoint>]]` blocks from app manifests, resolves
each to a bound async method on the app, and gates availability on declared
engine dependencies.

Apps with multiple algorithms for the same task (analytic/EMTP solvers,
sheath-voltage CIGRE/IEEE-575/IEC/explicit, future cable rating IEC/FEM)
declare them in their manifest.toml:

    [[provides.methods.solve]]
    id = "analytic"
    label = "Carson chain (analytic)"
    default = true
    fn = "_solve_analytic"
    input_schema = "solver.model:Network"
    output_schema = "solver.solve:SolveResult"
    version = "1.1.0"
    references = ["[[carson-earth-return]]"]
    requires_engines = []

    [[provides.methods.solve]]
    id = "emtp"
    label = "EMTP time-domain (sim engine)"
    fn = "_solve_emtp"
    requires_engines = ["sim"]
    ...

`endpoint` ("solve" above) is per-app — apps can have multiple endpoints
(e.g. cable's future `sheath_voltage`, `pulling_tension`, `cable_rating`).

Resolution flow at runtime:
    spec = app.method_registry.resolve("solve", "emtp")
    if spec is None:                        # missing or unavailable
        spec = app.method_registry.default("solve")
    result = await spec.run(app, payload)   # records provenance, calls fn
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


@dataclass
class MethodSpec:
    """One algorithm available at one endpoint of one app."""

    endpoint: str
    id: str
    label: str
    fn: str                     # name of the BaseApp method to call
    default: bool = False
    version: str = "0.0.0"
    description: str = ""
    references: list[str] = field(default_factory=list)
    requires_engines: list[str] = field(default_factory=list)
    input_schema: str = ""      # "module:Class" — informational; resolved lazily
    output_schema: str = ""     # ditto
    raw: dict = field(default_factory=dict)

    def is_available(self, app: BaseApp) -> tuple[bool, str]:
        """Return (available, disabled_reason). Empty reason when available."""
        if not hasattr(app, self.fn):
            return False, f"app missing method '{self.fn}'"
        for eid in self.requires_engines:
            if app.engine(eid) is None:
                return False, f"engine '{eid}' not available"
        return True, ""

    async def run(self, app: BaseApp, payload: Any) -> Any:
        """Invoke the underlying method, recording provenance on `app`.

        The bound `fn` receives `payload` as its single argument and may
        return any JSON-serializable value (dict typical). Provenance is
        recorded under `app._compute_provenance_by_endpoint[self.endpoint]`.
        """
        from emptyos.sdk.schema import inputs_hash

        ok, reason = self.is_available(app)
        if not ok:
            raise RuntimeError(f"method '{self.id}' unavailable: {reason}")

        bound = getattr(app, self.fn)
        t0 = time.monotonic()
        warnings: list[str] = []
        try:
            result = await bound(payload)
        finally:
            runtime_s = time.monotonic() - t0

        # Pull warnings + extras from result if it's a dict that follows the convention.
        extras: dict = {}
        if isinstance(result, dict):
            warnings = list(result.get("warnings") or [])
            for k in ("kcl_residual_max", "n_nodes", "n_steps", "energy_residual_pct"):
                if k in result:
                    extras[k] = result[k]

        prov = {
            "endpoint": self.endpoint,
            "method": self.id,
            "method_version": self.version,
            "inputs_hash": inputs_hash(payload),
            "runtime_s": round(runtime_s, 4),
            "runtime_ms": round(runtime_s * 1000, 1),
            "warnings": warnings,
            "extras": extras,
        }
        if not hasattr(app, "_compute_provenance_by_endpoint"):
            app._compute_provenance_by_endpoint = {}
        app._compute_provenance_by_endpoint[self.endpoint] = prov

        return result


class MethodRegistry:
    """Per-app registry of MethodSpecs, organized by endpoint."""

    def __init__(self):
        self._by_endpoint: dict[str, dict[str, MethodSpec]] = {}

    @classmethod
    def from_manifest(cls, manifest_provides: dict) -> MethodRegistry:
        """Parse `provides.methods.<endpoint>` blocks from a manifest.

        Accepts the parsed `manifest.provides` dict (already-loaded TOML).
        Tolerates missing / malformed entries by skipping them — load-time
        warnings happen at the BaseApp level.
        """
        reg = cls()
        methods_section = manifest_provides.get("methods") or {}
        if not isinstance(methods_section, dict):
            return reg
        for endpoint, items in methods_section.items():
            if not isinstance(items, list):
                continue
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                if not raw.get("id") or not raw.get("fn"):
                    continue
                spec = MethodSpec(
                    endpoint=endpoint,
                    id=raw["id"],
                    label=raw.get("label") or raw["id"],
                    fn=raw["fn"],
                    default=bool(raw.get("default", False)),
                    version=str(raw.get("version", "0.0.0")),
                    description=str(raw.get("description", "")),
                    references=list(raw.get("references") or []),
                    requires_engines=list(raw.get("requires_engines") or []),
                    input_schema=str(raw.get("input_schema", "")),
                    output_schema=str(raw.get("output_schema", "")),
                    raw=dict(raw),
                )
                reg._by_endpoint.setdefault(endpoint, {})[spec.id] = spec
        return reg

    def endpoints(self) -> list[str]:
        return list(self._by_endpoint.keys())

    def list(self, endpoint: str) -> list[MethodSpec]:
        return list((self._by_endpoint.get(endpoint) or {}).values())

    def get(self, endpoint: str, method_id: str) -> MethodSpec | None:
        return (self._by_endpoint.get(endpoint) or {}).get(method_id)

    def default(self, endpoint: str) -> MethodSpec | None:
        for spec in self.list(endpoint):
            if spec.default:
                return spec
        # No explicit default — first declared, by manifest order.
        items = self.list(endpoint)
        return items[0] if items else None

    def resolve(self, endpoint: str, method_id: str | None) -> MethodSpec | None:
        """Pick a method by id (or fall back to default). Returns None if
        neither resolves. Caller decides what to do with None."""
        if method_id:
            spec = self.get(endpoint, method_id)
            if spec is not None:
                return spec
        return self.default(endpoint)

    def to_listing(self, app: BaseApp, endpoint: str) -> list[dict]:
        """Render a JSON-friendly listing for `GET /api/methods` style routes."""
        out = []
        for spec in self.list(endpoint):
            ok, reason = spec.is_available(app)
            out.append({
                "id": spec.id,
                "label": spec.label,
                "default": spec.default,
                "version": spec.version,
                "description": spec.description,
                "references": list(spec.references),
                "requires_engines": list(spec.requires_engines),
                "input_schema": spec.input_schema,
                "output_schema": spec.output_schema,
                "available": ok,
                "disabled_reason": reason,
            })
        return out
