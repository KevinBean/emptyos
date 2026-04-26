"""Release — package EmptyOS for distribution."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from emptyos.sdk import BaseApp, on_event, web_route

try:
    import tomllib
except ImportError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_release() -> dict:
    p = ROOT / "release.toml"
    if not p.exists():
        return {}
    with open(p, "rb") as f:
        return tomllib.load(f)


def _resolve_tier(release: dict, tier_name: str) -> dict:
    tiers = release.get("tiers", {})
    tier = tiers.get(tier_name, {})
    result = {
        "apps": list(tier.get("apps", [])),
        "plugins": list(tier.get("plugins", [])),
        "skills": list(tier.get("skills", [])),
        "description": tier.get("description", ""),
    }
    parent = tier.get("extends")
    if parent and parent in tiers:
        p = _resolve_tier(release, parent)
        result["apps"] = p["apps"] + result["apps"]
        result["plugins"] = p["plugins"] + result["plugins"]
        result["skills"] = p["skills"] + result["skills"]
    return result


class ReleaseApp(BaseApp):

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        """Release config: tiers, version, last package info."""
        release = _load_release()
        version = release.get("release", {}).get("version", "0.0.0")
        tiers = {}
        for name in release.get("tiers", {}):
            resolved = _resolve_tier(release, name)
            tiers[name] = {
                "description": resolved["description"],
                "apps": len(resolved["apps"]),
                "plugins": len(resolved["plugins"]),
                "skills": len(resolved["skills"]),
            }

        # Check dist for existing packages
        dist = ROOT / "dist"
        packages = []
        if dist.exists():
            for d in sorted(dist.iterdir()):
                mf = d / "MANIFEST.json"
                if mf.exists():
                    m = json.loads(mf.read_text(encoding="utf-8"))
                    m["path"] = str(d)
                    m["size_mb"] = round(
                        sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1_048_576, 1
                    )
                    packages.append(m)

        return {
            "version": version,
            "tiers": tiers,
            "packages": packages,
        }

    @web_route("POST", "/api/check")
    async def api_check(self, request):
        """Run safety checks (personal data + branding)."""
        results = []
        for name, script in [
            ("personal", "scripts/check-personal.py"),
            ("branding", "scripts/check-branding.py"),
        ]:
            path = ROOT / script
            if not path.exists():
                results.append({"name": name, "ok": False, "output": f"{script} not found"})
                continue
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(path),
                cwd=str(ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            results.append({
                "name": name,
                "ok": proc.returncode == 0,
                "output": stdout.decode(errors="replace").strip(),
            })
        # Run test suite as a preflight check
        try:
            test_result = await self.call_app("tests", "run_all")
            s = test_result.get("summary", {})
            passed = s.get("passed", 0)
            failed = s.get("failed", 0)
            results.append({
                "name": "tests",
                "ok": failed == 0,
                "output": f"{passed} passed, {failed} failed",
            })
        except Exception as e:
            results.append({"name": "tests", "ok": False, "output": str(e)})

        return {"checks": results, "all_ok": all(r["ok"] for r in results)}

    @web_route("POST", "/api/package")
    async def api_package(self, request):
        """Package a tier. Body: {"tier": "core"|"standard"}"""
        data = await request.json()
        tier = data.get("tier", "")
        release = _load_release()
        if tier not in release.get("tiers", {}):
            return {"error": f"Unknown tier: {tier}"}

        script = ROOT / "scripts" / "package-release.py"
        if not script.exists():
            return {"error": "package-release.py not found"}

        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script), tier,
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode(errors="replace").strip()

        if proc.returncode != 0:
            return {"error": "Packaging failed", "output": output}

        # Read the generated manifest
        version = release.get("release", {}).get("version", "0.0.0")
        dist_dir = ROOT / "dist" / f"emptyos-{tier}-{version}"
        manifest = {}
        mf = dist_dir / "MANIFEST.json"
        if mf.exists():
            manifest = json.loads(mf.read_text(encoding="utf-8"))
            manifest["size_mb"] = round(
                sum(f.stat().st_size for f in dist_dir.rglob("*") if f.is_file()) / 1_048_576, 1
            )

        await self.emit("release:packaged", {"tier": tier, "version": version})
        return {"ok": True, "output": output, "manifest": manifest}

    # ── Demo seed orchestration ──────────────────────────────

    @on_event("kernel:started")
    async def on_kernel_started(self, event):
        """Run every app's `demo/seed.py` once on boot, when gated on."""
        if not self.kernel.config.get("demo.seed_on_boot", False):
            return
        await self._run_all_seeds()

    @web_route("POST", "/api/seed-demo")
    async def api_seed_demo(self, request):
        """Manually trigger the demo seeds. Idempotent — seeds self-skip."""
        return {"ok": True, "results": await self._run_all_seeds()}

    async def _run_all_seeds(self) -> list[dict]:
        """Scan every loaded app for `apps/<id>/demo/seed.py` and run it.

        Contract: each seed module exports `async def seed(app) -> dict`.
        Errors in one seed do not stop the others. Results are logged to
        the event bus so the orchestration is traceable.
        """
        results: list[dict] = []
        apps_dir = ROOT / "apps"
        personal_dir = apps_dir / "personal"
        for app_id, instance in list(self.kernel.apps.instances.items()):
            # Look in both core and personal app locations — both are loaded
            # at runtime, both may declare demo seeds.
            for base in (apps_dir, personal_dir):
                seed_path = base / app_id / "demo" / "seed.py"
                if seed_path.exists():
                    break
            else:
                continue
            mod_name = f"emptyos.apps.{app_id.replace('-', '_')}.demo.seed"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, str(seed_path))
                module = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                spec.loader.exec_module(module)
                if not hasattr(module, "seed"):
                    results.append({"app": app_id, "error": "no seed() function"})
                    continue
                outcome = await module.seed(instance)
                results.append({"app": app_id, "result": outcome})
            except Exception as e:
                results.append({"app": app_id, "error": str(e)})
        await self.emit("release:demo_seeded", {"count": len(results)})
        return results
