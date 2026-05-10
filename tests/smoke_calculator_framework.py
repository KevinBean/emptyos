"""End-to-end smoke — exercises the live calculator framework against
fault-distribution v0.3.0 + cable v1.2.0 without booting the daemon.

Not a pytest file (the apps' relative imports require kernel-style package
setup). Run as: `python tests/smoke_calculator_framework.py`. Exits non-zero
on any check failure so it can be wired into release gates.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "engines"))


# ── Test infrastructure — fake kernel that resolves engines + services ──


class _FakeServices:
    def __init__(self, engines: dict):
        self._engines = engines

    def get_optional(self, k):
        return self._engines.get(k)

    def get(self, k):
        return self._engines[k]


class _FakeEvents:
    async def emit(self, *a, **kw):
        pass


class _FakeVaultMap:
    """Minimal vault_map stub — returns the default path verbatim."""
    def get(self, app_id: str, key: str, default: str = "") -> str:
        return default


class _FakeKernel:
    def __init__(self, engines: dict):
        self.services = _FakeServices(engines)
        self.events = _FakeEvents()
        self.config = type("C", (), {"notes_path": None, "path": Path(".")})()
        self.vault_map = _FakeVaultMap()


def _load_app(app_dir: Path, package_name: str, class_name: str):
    """Load an app's app.py module under a synthetic package so its relative
    imports work outside the kernel."""
    pkg = types.ModuleType(package_name)
    pkg.__path__ = [str(app_dir)]
    pkg.__package__ = package_name
    sys.modules[package_name] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.app", app_dir / "app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = package_name
    sys.modules[f"{package_name}.app"] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, class_name)


# ── Boot the sim engine for fault-distribution ────────────────────────


def _boot_sim_engine():
    from sim.engine import SimEngine
    eng = SimEngine.__new__(SimEngine)
    eng._numpy = True
    eng._scipy = True
    eng.manifest = type("M", (), {"raw": {"engine": {"version": "0.1.0"}}})()
    return eng


# ── Boot the engineering engine for cable ─────────────────────────────


def _boot_engineering_engine():
    sys.path.insert(0, str(REPO / "engines" / "personal" / "engineering"))
    pkg_root = types.ModuleType("eos_engines_eng")
    pkg_root.__path__ = [str(REPO / "engines" / "personal" / "engineering")]
    sys.modules["eos_engines_eng"] = pkg_root
    spec = importlib.util.spec_from_file_location(
        "eos_engines_eng.engine",
        REPO / "engines" / "personal" / "engineering" / "engine.py",
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "eos_engines_eng"
    sys.modules["eos_engines_eng.engine"] = mod
    spec.loader.exec_module(mod)
    eng = mod.EngineeringEngine.__new__(mod.EngineeringEngine)
    eng._numpy = True
    # Try to detect opendssdirect
    try:
        import opendssdirect  # noqa: F401
        eng._opendss = True
    except ImportError:
        eng._opendss = False
    eng.manifest = type("M", (), {"raw": {"engine": {"version": "1.0.0"}}})()
    return eng


# ── Smoke checks ──────────────────────────────────────────────────────


PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []   # (name, status, detail)


def check(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    line = f"  [{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)


async def smoke_fault_distribution():
    print("\n=== fault-distribution v0.3.0 ===")
    from emptyos.kernel.app_loader import AppManifest

    sim = _boot_sim_engine()
    kernel = _FakeKernel({"engine:sim": sim})
    manifest = AppManifest.from_toml(REPO / "apps" / "personal" / "fault-distribution" / "manifest.toml")
    AppCls = _load_app(
        REPO / "apps" / "personal" / "fault-distribution",
        "fdapp_smoke",
        "FaultDistributionApp",
    )
    app = AppCls(kernel, manifest)

    # 1. Method registry
    methods = app.list_methods("solve")
    method_ids = {m["id"] for m in methods}
    check(
        "method registry surfaces analytic + emtp",
        method_ids == {"analytic", "emtp"},
        f"got {sorted(method_ids)}",
    )
    check(
        "analytic is default",
        any(m["id"] == "analytic" and m["default"] for m in methods),
    )
    check(
        "emtp requires sim engine and is available",
        any(m["id"] == "emtp" and m["available"] for m in methods),
    )

    # 2. Build RT-07 payload via the conformance loader
    ctx = await app._conformance_rt07_inputs()
    payload = ctx["payload"]

    # 3. Resolve & run analytic
    analytic_spec = app.resolve_method("solve", "analytic")
    t = time.monotonic()
    analytic_result = await analytic_spec.run(app, ctx)
    t_analytic = time.monotonic() - t
    epr_a = analytic_result["central_epr_v"]
    sf_a = analytic_result["split_factor"]
    err_epr_a = abs(epr_a - 2446.6) / 2446.6 * 100
    err_sf_a = abs(sf_a - 0.4919) / 0.4919 * 100
    check("analytic central EPR within 5% of CDEGS",
          err_epr_a < 5.0, f"{epr_a:.1f}V vs 2446.6V (err {err_epr_a:.2f}%)")
    check("analytic split factor within 2% of CDEGS",
          err_sf_a < 2.0, f"{sf_a:.4f} vs 0.4919 (err {err_sf_a:.2f}%)")
    print(f"      analytic runtime: {t_analytic:.3f}s")

    # 4. Resolve & run emtp (cold)
    emtp_spec = app.resolve_method("solve", "emtp")
    app.cache_clear()  # ensure cold
    t = time.monotonic()
    emtp_cold = await emtp_spec.run(app, ctx)
    t_emtp_cold = time.monotonic() - t
    epr_e = emtp_cold["central_epr_v"]
    sf_e = emtp_cold["split_factor"]
    err_epr_e = abs(epr_e - 2446.6) / 2446.6 * 100
    err_sf_e = abs(sf_e - 0.4919) / 0.4919 * 100
    check("emtp central EPR within 5% of CDEGS",
          err_epr_e < 5.0, f"{epr_e:.1f}V vs 2446.6V (err {err_epr_e:.2f}%)")
    check("emtp split factor within 2% of CDEGS",
          err_sf_e < 2.0, f"{sf_e:.4f} vs 0.4919 (err {err_sf_e:.2f}%)")
    check("emtp cache_hit=False on cold call", emtp_cold.get("cache_hit") is False)
    check("emtp KCL residual clean (<1e-6)",
          emtp_cold["kcl_residual_max"] < 1e-6,
          f"{emtp_cold['kcl_residual_max']:.2e}")
    print(f"      emtp cold runtime: {t_emtp_cold:.2f}s")

    # 5. Cache hit on warm call
    t = time.monotonic()
    emtp_warm = await emtp_spec.run(app, ctx)
    t_emtp_warm = time.monotonic() - t
    speedup = t_emtp_cold / max(t_emtp_warm, 1e-6)
    check("emtp cache_hit=True on warm call", emtp_warm.get("cache_hit") is True)
    check("emtp warm call >100x faster than cold",
          speedup > 100, f"{speedup:,.0f}× ({t_emtp_warm*1000:.2f}ms)")

    # 6. Provenance recorded
    prov = app.last_compute_provenance("solve")
    check("provenance records method id", prov.get("method") == "emtp")
    check("provenance records inputs_hash", isinstance(prov.get("inputs_hash"), str))
    check("provenance records runtime", prov.get("runtime_s") is not None)

    # 7. Compare endpoint — both methods + reference
    cmp = await app.compare_methods(
        "solve", ctx,
        reference={"central_epr_v": 2446.6, "split_factor": 0.4919},
    )
    cmp_fields = set(cmp["comparison"]["matched_fields"])
    check("compare aligns central_epr_v across all 3",
          "central_epr_v" in cmp_fields)
    check("compare aligns split_factor across all 3",
          "split_factor" in cmp_fields)
    epr_diff = next(d for d in cmp["comparison"]["diffs"] if d["field"] == "central_epr_v")
    check("compare reports max_rel_pct within 2%",
          epr_diff["max_rel_pct"] < 2.0,
          f"max_rel_pct={epr_diff['max_rel_pct']}%")

    # 8. Conformance suite
    conf_cases = app.list_conformance("solve")
    check("conformance lists rt07 case", len(conf_cases) == 1 and conf_cases[0]["case_id"] == "rt07")
    rt07_run = await app.run_conformance("solve", "rt07")
    check("conformance rt07 passes overall", rt07_run["passed"] is True)
    check("conformance rt07 — analytic passed",
          rt07_run["methods"]["analytic"]["passed"] is True)
    check("conformance rt07 — emtp passed",
          rt07_run["methods"]["emtp"]["passed"] is True)

    return True


async def smoke_cable():
    print("\n=== cable v1.2.0 ===")
    from emptyos.kernel.app_loader import AppManifest

    eng = _boot_engineering_engine()
    kernel = _FakeKernel({"engine:engineering": eng})
    manifest = AppManifest.from_toml(REPO / "apps" / "personal" / "cable" / "manifest.toml")
    AppCls = _load_app(
        REPO / "apps" / "personal" / "cable",
        "cableapp_smoke",
        "CableApp",
    )
    app = AppCls(kernel, manifest)

    methods = app.list_methods("sheath_voltage")
    ids = {m["id"] for m in methods}
    check(
        "cable sheath_voltage registry has 5 methods",
        ids == {"cigre", "ieee575", "iec", "explicit", "opendss"},
        f"got {sorted(ids)}",
    )
    check(
        "cigre is default",
        any(m["id"] == "cigre" and m["default"] for m in methods),
    )
    opendss_meta = next(m for m in methods if m["id"] == "opendss")
    check(
        "opendss method declares engineering requirement",
        "engineering" in opendss_meta["requires_engines"],
    )

    # Build a small payload that the engineering engine accepts
    payload = {
        "frequency_hz": 50.0,
        "load_current_a": 1000.0,
        "soil_resistivity_ohm_m": 100.0,
        "conductor_outer_radius_mm": 25.25,
        "sheath_inner_radius_mm": 46.0,
        "sheath_outer_radius_mm": 48.5,
        "ecc_size_mm2": 120.0,
        "ecc_material": "copper",
        "bonding_scheme": "single_point",
        "sections": [{
            "name": "S1", "length_m": 500.0, "num_circuits": 1,
            "formation_c1": "flat", "spacing_c1_mm": 300.0, "depth_c1_m": 1.0,
            "phase_seq_c1": "ABC",
        }],
    }

    # Run cigre via the registry
    spec = app.resolve_method("sheath_voltage", "cigre")
    try:
        result = await spec.run(app, payload)
        check(
            "cable cigre method returns K_ecc_max",
            isinstance(result.get("K_ecc_max"), (int, float)),
            f"K_ecc_max={result.get('K_ecc_max')}",
        )
        check(
            "cable cigre returns K_ecc_avg + n_cables",
            "K_ecc_avg" in result and result.get("n_cables") == 3,
        )
    except Exception as e:  # noqa: BLE001
        check(f"cable cigre method runs cleanly", False, str(e))

    # Compare cigre vs ieee575 vs iec
    try:
        cmp = await app.compare_methods(
            "sheath_voltage", payload,
            methods=["cigre", "ieee575", "iec"],
        )
        n_results = sum(1 for r in cmp["results"].values() if "result" in r)
        check(
            f"cable compare runs all 3 analytical methods",
            n_results == 3,
            f"got {n_results}/3 successful",
        )
        if "K_ecc_max" in cmp["comparison"]["matched_fields"]:
            d = next(x for x in cmp["comparison"]["diffs"] if x["field"] == "K_ecc_max")
            print(f"      K_ecc_max across cigre/ieee575/iec: {d['values']}")
    except Exception as e:  # noqa: BLE001
        check(f"cable compare runs cleanly", False, str(e))

    return True


# ── Main ──────────────────────────────────────────────────────────────


async def main():
    print("Calculator framework — end-to-end smoke")
    print("=" * 60)
    try:
        await smoke_fault_distribution()
    except Exception as e:  # noqa: BLE001
        check("fault-distribution smoke crashed", False, str(e))
    try:
        await smoke_cable()
    except Exception as e:  # noqa: BLE001
        check("cable smoke crashed", False, str(e))

    print("\n" + "=" * 60)
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    print(f"\nResult: {n_pass} passed, {n_fail} failed (total {len(results)})")
    if n_fail:
        print("\nFailures:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
