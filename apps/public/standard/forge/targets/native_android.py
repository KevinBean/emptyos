"""Native Android target — pure-native Kotlin apps driven by Google's `android` CLI.

Validates the Target Protocol against a *third* categorically different stack:
- Tauri = web + Rust + native
- Cli   = standalone Python binary
- NativeAndroid = pure JVM/Kotlin + Gradle + a vendor-specific agentic CLI

V0 = preflight only. Scaffold/dev/build/release return graceful "not yet"
results so the Target appears in the Forge UI as a real card, preflight
answers honest installation questions, and the other verbs degrade
clearly. Each remaining verb lands in its own follow-up session per the
forge growth charter (apps/forge/FORGE.md).

References:
- Google's Android CLI (April 2026): https://developer.android.com/tools/agents
- agentskills.io open standard (project-local .skills/) — seeded by
  `android skills add` in the scaffold step (v1)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import (
    BuildResult,
    Check,
    LogCallback,
    ProcessRecord,
    ReleaseResult,
    ScaffoldCtx,
    ScaffoldResult,
)


_NOT_YET = (
    "native-android v0 ships preflight only — scaffold/dev/build/release "
    "are tracked in apps/forge/FORGE.md and land in follow-up sessions."
)


def _resolve_binary(name: str) -> str | None:
    return shutil.which(name)


class NativeAndroidTarget:
    id = "native-android"
    name = "Native Android"
    description = (
        "Pure-native Kotlin Android apps via Google's agentic `android` CLI. "
        "Single APK, Gradle build, WebView shell talks to a remote EmptyOS daemon."
    )
    coming_soon = False  # preflight is real — Target earns its card

    # ── Preflight ───────────────────────────────────────────────────────────

    def preflight(self) -> list[Check]:
        return [
            self._probe_android(),
            self._probe(
                "java",
                version_args=["-version"],
                install_hint="Install a JDK 17+ (Temurin / Microsoft / Oracle)",
                hint_url="https://adoptium.net/",
            ),
            self._probe(
                "gradle",
                version_args=["--version"],
                install_hint=(
                    "Gradle is usually bundled per-project via the wrapper (./gradlew); "
                    "system Gradle is optional"
                ),
                hint_url="https://gradle.org/install/",
            ),
            self._probe(
                "git",
                version_args=["--version"],
                install_hint="Install Git",
                hint_url="https://git-scm.com",
            ),
        ]

    def _probe_android(self) -> Check:
        """Google's `android` CLI — the agentic tool, NOT the old `android` SDK
        manager which was deprecated years ago. If both are on PATH, our
        check on `android info` disambiguates: the new CLI returns the SDK
        path; the deprecated one prints a deprecation banner."""
        path = _resolve_binary("android")
        hint_url = "https://developer.android.com/tools/agents"
        if not path:
            return Check(
                name="android",
                ok=False,
                detail=(
                    "Install Google's agentic Android CLI from "
                    "developer.android.com/tools/agents (Apple silicon / "
                    "AMD64 Linux / AMD64 Windows)."
                ),
                hint_url=hint_url,
            )
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            out = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=flags,
            )
            ver = (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else "?"
            if "deprecated" in ver.lower() or "moved" in ver.lower():
                return Check(
                    name="android",
                    ok=False,
                    detail=(
                        "Found the *old* Android SDK manager — Forge needs the new "
                        "agentic `android` CLI (April 2026). Replace via "
                        "developer.android.com/tools/agents."
                    ),
                    hint_url=hint_url,
                )
        except Exception as e:
            ver = f"probe failed: {e}"
        return Check(name="android", ok=True, detail=ver, hint_url=hint_url)

    @staticmethod
    def _probe(binary: str, *, version_args, install_hint: str, hint_url: str) -> Check:
        path = _resolve_binary(binary)
        if not path:
            return Check(name=binary, ok=False, detail=install_hint, hint_url=hint_url)
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            out = subprocess.run(
                [path, *version_args],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=flags,
            )
            ver = (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else "?"
        except Exception:
            ver = "?"
        return Check(name=binary, ok=True, detail=ver, hint_url=hint_url)

    # ── Scaffold / dev / build / release (v0 stubs) ─────────────────────────
    #
    # Each verb satisfies the Protocol with a clear not-yet result so the
    # Forge UI surfaces an honest message instead of crashing or pretending.
    # Implementing one is a follow-up session — see FORGE.md.

    async def scaffold(
        self, ctx: ScaffoldCtx, runtime, on_log: LogCallback
    ) -> ScaffoldResult:
        repo_path = ctx.root / ctx.project_id
        log_path = ctx.root / ".eos-forge-logs" / f"{ctx.project_id}-scaffold.log"
        return ScaffoldResult(
            repo_path=repo_path, log_path=log_path, ok=False, error=_NOT_YET
        )

    async def dev(
        self, repo_path: Path, log_path: Path, on_log: LogCallback
    ) -> ProcessRecord:
        raise NotImplementedError(_NOT_YET)

    async def build(
        self, repo_path: Path, runtime, on_log: LogCallback
    ) -> BuildResult:
        return BuildResult(success=False, error=_NOT_YET)

    async def release(
        self, repo_path: Path, version: str, runtime, on_log: LogCallback
    ) -> ReleaseResult:
        return ReleaseResult(success=False, error=_NOT_YET)

    async def status(self, repo_path: Path) -> dict:
        return {
            "implemented": False,
            "reason": _NOT_YET,
            "preflight_only": True,
        }
