"""System tests for the `see` capability and webcam plugin.

Covers:
  - /api/capabilities exposes `see` with the expected provider order
  - Webcam plugin manifest is well-formed and class is importable
  - BaseApp exposes a `see()` coroutine that routes to the capability

These are infrastructure tests — no app-level UI yet. When a `camera` app
lands, add `test_sys_camera.py` for the app surface and keep this file
for capability/plugin plumbing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest


@pytest.mark.api
class TestSeeCapabilityEndpoint:
    def test_see_is_listed(self, http_client):
        data = http_client.get("/api/capabilities").json()
        assert "see" in data, f"see capability not registered; got {sorted(data)}"

    def test_human_fallback_is_present(self, http_client):
        providers = [p["name"] for p in http_client.get("/api/capabilities").json()["see"]]
        assert "human" in providers, (
            "see must have a human fallback per Dev Rule #2 "
            f"(got providers={providers})"
        )

    def test_webcam_provider_precedes_human(self, http_client):
        # Only meaningful when the webcam plugin is enabled. When it isn't,
        # the chain is ['human'] alone — still valid, just skip the order check.
        providers = [p["name"] for p in http_client.get("/api/capabilities").json()["see"]]
        if "webcam" not in providers:
            pytest.skip("webcam plugin not installed in this deployment")
        assert providers.index("webcam") < providers.index("human"), (
            f"webcam must be tried before human; got {providers}"
        )


@pytest.mark.api
class TestWebcamPluginManifest:
    def test_manifest_parses(self):
        path = Path(__file__).parent.parent / "plugins" / "webcam" / "manifest.toml"
        if not path.exists():
            pytest.skip("webcam plugin not present (shipped as optional)")
        with open(path, "rb") as f:
            manifest = tomllib.load(f)
        assert manifest["plugin"]["id"] == "webcam"
        assert manifest["plugin"]["entry"]["class"] == "WebcamPlugin"
        assert "webcam" in manifest["provides"]["services"]

    def test_plugin_class_importable(self):
        path = Path(__file__).parent.parent / "plugins" / "webcam" / "plugin.py"
        if not path.exists():
            pytest.skip("webcam plugin not present")
        import importlib.util
        spec = importlib.util.spec_from_file_location("_webcam_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "WebcamPlugin")
        assert hasattr(mod, "WebcamSeeProvider")
        # Provider's mode guard — webcam only does snapshots; other modes reject.
        assert mod.WebcamSeeProvider.name == "webcam"


@pytest.mark.api
class TestBaseAppSeeSurface:
    def test_base_app_exposes_see(self):
        import inspect
        from emptyos.sdk.base_app import BaseApp
        assert hasattr(BaseApp, "see"), "BaseApp.see() missing"
        assert inspect.iscoroutinefunction(BaseApp.see)
        sig = inspect.signature(BaseApp.see)
        assert "mode" in sig.parameters, f"see() should accept mode=; got {sig}"

    def test_see_capability_signature_matches_siblings(self):
        import inspect
        from emptyos.capabilities.types import SeeCapability
        sig = inspect.signature(SeeCapability.execute)
        assert "domain" in sig.parameters, (
            "SeeCapability.execute should accept domain= for provider-chain "
            "routing, consistent with Speak/Listen/Draw"
        )
        assert "mode" in sig.parameters
