"""Tauri target — Rust + JS desktop apps via `npm create tauri-app`.

Scaffold strategy: shell out to the upstream `npm create tauri-app@latest`
each time so the generated tree tracks Tauri's evolution. Forge layers a
README + .gitignore patches on top.

Preflight checks: cargo + rustc (Rust toolchain) + node + npm (JS side).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime
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


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z0-9.-]+)?$")


def _validate_semver(version: str) -> str | None:
    """Return None if version is a valid semver string, else an error message.

    Accepts X.Y.Z and X.Y.Z-prerelease. Leading 'v' is rejected — callers
    pass the bare number, we add the 'v' for the git tag ourselves.
    """
    if not version:
        return "version is empty"
    if version.startswith("v"):
        return f"version must not start with 'v' (got {version!r}); pass e.g. '1.2.3'"
    if not _SEMVER_RE.match(version):
        return f"version {version!r} is not semver (expected X.Y.Z or X.Y.Z-prerelease)"
    return None

# Tauri 2's create-tauri-app needs a reverse-DNS identifier. Hyphens are
# allowed in segments but underscores are safer; we strip both and join.
_IDENT_SUFFIX_RE = re.compile(r"[^a-zA-Z0-9]+")


def _identifier_for(project_id: str, prefix: str = "com.eos") -> str:
    """Build a Tauri-compatible reverse-DNS identifier.

    Tauri 2 requires each segment to match `[a-zA-Z][a-zA-Z0-9_-]*` — leading
    digit fails. We strip non-alphanumerics and prefix `app` if the suffix
    would otherwise lead with a digit. The user can edit `tauri.conf.json`
    afterwards to set a real publisher prefix.
    """
    suffix = _IDENT_SUFFIX_RE.sub("", project_id) or "app"
    if suffix[0].isdigit():
        suffix = "app" + suffix
    return f"{prefix}.{suffix}"


def _resolve_binary(name: str) -> str | None:
    """Resolve a CLI name to its full path. On Windows `npm` is `npm.cmd`;
    asyncio.create_subprocess_exec needs the resolved path."""
    return shutil.which(name)


def _tauri_cli_binding_for_host() -> str | None:
    """Pick the `@tauri-apps/cli-<os>-<arch>-<abi>` package name for this
    host. Returns None if the platform isn't recognised — the caller skips
    the explicit install in that case.

    Covers the platforms Tauri 2 currently publishes native bindings for.
    Add new ones as Tauri adds them; the exact name has to match the
    package on npm.
    """
    import platform
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        if machine in ("amd64", "x86_64"):
            return "@tauri-apps/cli-win32-x64-msvc"
        if machine == "arm64":
            return "@tauri-apps/cli-win32-arm64-msvc"
    elif system == "Darwin":
        if machine in ("x86_64", "amd64"):
            return "@tauri-apps/cli-darwin-x64"
        if machine in ("arm64", "aarch64"):
            return "@tauri-apps/cli-darwin-arm64"
    elif system == "Linux":
        if machine in ("x86_64", "amd64"):
            return "@tauri-apps/cli-linux-x64-gnu"
        if machine in ("aarch64", "arm64"):
            return "@tauri-apps/cli-linux-arm64-gnu"
    return None


# Patches layered on top of the generated tree after `npm create tauri-app`
# returns. Keep small — heavy customisation belongs in user code, not in the
# forge template.
GITIGNORE_ADDITIONS = """
# EmptyOS Forge — local build / dev cruft
.eos-forge/
*.eos-tmp
"""

README_TEMPLATE = """# {name}

Scaffolded by [EmptyOS Forge](https://emptyos.dev/forge) on {date}.

## Develop

```bash
npm install
npm run tauri dev
```

## Build

```bash
npm run tauri build
```

Artifacts land under `src-tauri/target/release/bundle/`.
"""


class TauriTarget:
    id = "tauri"
    name = "Tauri"
    description = "Desktop apps (Win/Mac/Linux) — Rust backend + HTML/JS frontend."
    coming_soon = False

    # ── Preflight ───────────────────────────────────────────────────────────

    def preflight(self) -> list[Check]:
        """Probe the four toolchain binaries Tauri needs.

        Mirrors the `shutil.which` + version-probe pattern from
        plugins/blender/plugin.py:77. Returns one Check per binary so the UI
        can render each row with its own install hint.
        """
        checks = [
            self._probe(
                "cargo",
                version_args=["--version"],
                install_hint="Install Rust via rustup: https://rustup.rs",
                hint_url="https://rustup.rs",
            ),
            self._probe(
                "rustc",
                version_args=["--version"],
                install_hint="Install Rust via rustup: https://rustup.rs",
                hint_url="https://rustup.rs",
            ),
            self._probe(
                "node",
                version_args=["--version"],
                install_hint="Install Node 20+ via nvm or nodejs.org",
                hint_url="https://nodejs.org",
            ),
            self._probe(
                "npm",
                version_args=["--version"],
                install_hint="Install Node (ships with npm)",
                hint_url="https://nodejs.org",
            ),
        ]
        return checks

    @staticmethod
    def _probe(binary: str, *, version_args: list[str], install_hint: str, hint_url: str) -> Check:
        path = shutil.which(binary)
        if not path:
            return Check(name=binary, ok=False, detail=install_hint, hint_url=hint_url)
        # Quick non-async version probe — Phase 1 is fine with a blocking
        # subprocess here because preflight runs only on demand from the UI.
        import subprocess

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

    # ── Scaffold ────────────────────────────────────────────────────────────

    async def scaffold(
        self, ctx: ScaffoldCtx, runtime, on_log: LogCallback
    ) -> ScaffoldResult:
        """Run `npm create tauri-app@latest -- <id> --template vanilla --manager npm --ci`
        from the parent root dir, then layer EmptyOS patches on the result.

        The npm-create command downloads create-tauri-app on first run and
        builds the project tree under `<root>/<project_id>/`. Total time ~2 min
        on first scaffold; subsequent scaffolds reuse npm's cache and run in
        ~30-60s.
        """
        log_path = ctx.root / ".eos-forge-logs" / f"{ctx.project_id}-scaffold.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate any prior log.
        log_path.write_bytes(b"")

        repo_path = ctx.root / ctx.project_id

        # We invoke `cargo create-tauri-app` rather than `npm create tauri-app`.
        # Why: the npm path goes through npx, which hits a well-known
        # optional-dependencies bug where the platform-specific Rust binary
        # (`create-tauri-app-win32-x64-msvc` on Windows) doesn't get
        # installed. Result: MODULE_NOT_FOUND at runtime. The cargo path is
        # a single self-contained Rust binary — no platform soup.
        cargo = _resolve_binary("cargo")
        if not cargo:
            return ScaffoldResult(
                repo_path=repo_path,
                log_path=log_path,
                ok=False,
                error="cargo not found on PATH — install Rust via https://rustup.rs",
            )
        if runtime is None:
            return ScaffoldResult(
                repo_path=repo_path,
                log_path=log_path,
                ok=False,
                error="agent-runtime service unavailable",
            )

        # Ensure cargo-create-tauri-app is installed. Cargo install returns
        # a non-zero exit code when *all* requested packages are already
        # installed (printing "Ignored package… already installed") — we
        # can't tell "no-op" from "real failure" by rc alone. So: check for
        # the binary in cargo's standard install dir first; only invoke
        # cargo install when missing. ~30s on first install, instant skip
        # otherwise.
        cargo_bin = Path.home() / ".cargo" / "bin"
        bin_name = "cargo-create-tauri-app.exe" if os.name == "nt" else "cargo-create-tauri-app"
        cta_path = cargo_bin / bin_name
        if not cta_path.exists():
            install_result = await runtime.run(
                [cargo, "install", "create-tauri-app", "--locked"],
                cwd=str(ctx.root),
                stdout_path=log_path,
                stderr_path=log_path,
                on_stdout_line=on_log,
                timeout_s=300.0,
                idle_timeout_s=120.0,
            )
            if install_result.get("returncode") != 0 or not cta_path.exists():
                return ScaffoldResult(
                    repo_path=repo_path,
                    log_path=log_path,
                    ok=False,
                    error=(
                        f"`cargo install create-tauri-app` failed "
                        f"(rc={install_result.get('returncode')})"
                        + (" — timed out" if install_result.get("timeout") else "")
                    ),
                )

        identifier = _identifier_for(ctx.project_id)
        cmd = [
            cargo,
            "create-tauri-app",
            ctx.project_id,
            "--template",
            "vanilla",
            "--manager",
            "npm",
            "--identifier",
            identifier,
            "--yes",
        ]
        env = {**os.environ, "CI": "1"}

        # 10-minute hard ceiling.
        result = await runtime.run(
            cmd,
            cwd=str(ctx.root),
            env=env,
            stdout_path=log_path,
            stderr_path=log_path,
            on_stdout_line=on_log,
            timeout_s=600.0,
            idle_timeout_s=240.0,
        )
        if result.get("returncode") != 0:
            return ScaffoldResult(
                repo_path=repo_path,
                log_path=log_path,
                ok=False,
                error=(
                    f"npm create tauri-app failed (rc={result.get('returncode')})"
                    + (" — timed out" if result.get("timeout") else "")
                    + (" — idle" if result.get("idle_timeout") else "")
                ),
            )

        if not repo_path.exists() or not (repo_path / "src-tauri").exists():
            return ScaffoldResult(
                repo_path=repo_path,
                log_path=log_path,
                ok=False,
                error=f"scaffold produced no src-tauri dir at {repo_path}",
            )

        # Layer EmptyOS patches.
        try:
            self._apply_patches(repo_path, ctx)
        except Exception as e:
            # Patches are non-fatal — the project works without them. Log and
            # report success with a soft warning embedded in the result.
            return ScaffoldResult(
                repo_path=repo_path,
                log_path=log_path,
                ok=True,
                error=f"scaffolded but post-patches failed: {e}",
            )

        # npm bug workaround #4828: install the platform-specific
        # @tauri-apps/cli native binding explicitly. Without this the user's
        # first `npm run tauri dev` fails with "Cannot find native binding"
        # because npm's optionalDependencies mechanism silently skips them
        # on Windows. See project_forge_tauri_npm_bug.md.
        npm = _resolve_binary("npm")
        binding = _tauri_cli_binding_for_host()
        if npm and binding:
            await runtime.run(
                [npm, "install", "--save-dev", binding],
                cwd=str(repo_path),
                stdout_path=log_path,
                stderr_path=log_path,
                on_stdout_line=on_log,
                timeout_s=120.0,
                idle_timeout_s=60.0,
            )
            # Non-fatal: if this fails the project is still usable; user just
            # has to fix the npm install manually before `tauri dev`. We log
            # but don't bail.

        return ScaffoldResult(
            repo_path=repo_path, log_path=log_path, ok=True, error=""
        )

    @staticmethod
    def _apply_patches(repo_path: Path, ctx: ScaffoldCtx) -> None:
        """Layer EmptyOS-specific files on top of the npm-create output.

        Idempotent — safe to re-run. README is overwritten; gitignore is
        appended only if our markers aren't already present; GH Actions
        workflow is overwritten so users get the latest forge-shipped
        version on re-scaffold.
        """
        readme = repo_path / "README.md"
        readme.write_text(
            README_TEMPLATE.format(
                name=ctx.name or ctx.project_id,
                date=datetime.now().strftime("%Y-%m-%d"),
            ),
            encoding="utf-8",
        )
        gitignore = repo_path / ".gitignore"
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if ".eos-forge/" not in existing:
            with gitignore.open("a", encoding="utf-8") as f:
                f.write(GITIGNORE_ADDITIONS)

        # GH Actions release workflow — fires on `v*.*.*` tags (which
        # `forge release` produces) and publishes a cross-platform installer
        # set as a GitHub Release.
        wf_src = Path(__file__).parent.parent / "templates" / "tauri" / "release.yml"
        if wf_src.exists():
            wf_dst = repo_path / ".github" / "workflows" / "release.yml"
            wf_dst.parent.mkdir(parents=True, exist_ok=True)
            wf_text = wf_src.read_text(encoding="utf-8").replace(
                "{{name}}", ctx.name or ctx.project_id
            )
            wf_dst.write_text(wf_text, encoding="utf-8")

    # ── Dev (long-running) ──────────────────────────────────────────────────

    async def dev(
        self, repo_path: Path, log_path: Path, on_log: LogCallback
    ) -> ProcessRecord:
        """Spawn `npm run tauri dev` from the repo dir. Returns immediately
        with a ProcessRecord pointing at the live process + log file.

        Process-tree management on Windows: the spawn uses
        CREATE_NEW_PROCESS_GROUP so stop() can send CTRL_BREAK_EVENT to
        terminate the whole `npm → node → cargo → rustc` tree at once.
        Without the group flag, signals only reach npm, leaving cargo as an
        orphan that keeps the GPU/CPU busy.

        Log drain runs in a background asyncio task; lines are written to
        log_path and dispatched to on_log. The drain task exits when the
        process closes its stdout.
        """
        npm = _resolve_binary("npm")
        if not npm:
            raise RuntimeError("npm not found on PATH")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate prior log on fresh start so the UI tail starts at offset 0.
        log_path.write_bytes(b"")

        flags = 0
        if os.name == "nt":
            # CREATE_NEW_PROCESS_GROUP — required for CTRL_BREAK_EVENT
            # delivery to the whole tree on Windows.
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        proc = await asyncio.create_subprocess_exec(
            npm,
            "run",
            "tauri",
            "dev",
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=flags,
        )

        # Background drain — non-blocking; the caller gets back the record
        # immediately while output streams to the log file.
        asyncio.create_task(
            _drain_process(proc, log_path, on_log),
            name=f"forge-dev-drain-{proc.pid}",
        )

        return ProcessRecord(
            pid=proc.pid,
            started_at=time.time(),
            log_path=log_path,
            proc=proc,
        )

    # ── Build (one-shot) ────────────────────────────────────────────────────

    async def build(
        self, repo_path: Path, runtime, on_log: LogCallback
    ) -> BuildResult:
        """Run `npm run tauri build` and return artifact paths.

        Build is a one-shot command — agent-runtime.run() is the right
        driver. Tauri's build outputs land under
        `src-tauri/target/release/bundle/<bundle-type>/` — we glob for known
        artifact extensions (.exe, .msi, .dmg, .deb, .AppImage, .rpm).
        """
        npm = _resolve_binary("npm")
        if not npm:
            return BuildResult(success=False, error="npm not found on PATH")
        if runtime is None:
            return BuildResult(success=False, error="agent-runtime unavailable")

        log_path = repo_path / ".eos-forge" / f"build-{int(time.time())}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(b"")

        started = time.time()
        # 30-minute ceiling — Rust release builds are slow.
        result = await runtime.run(
            [npm, "run", "tauri", "build"],
            cwd=str(repo_path),
            stdout_path=log_path,
            stderr_path=log_path,
            on_stdout_line=on_log,
            timeout_s=1800.0,
            idle_timeout_s=600.0,
        )
        duration = time.time() - started

        if result.get("returncode") != 0:
            return BuildResult(
                success=False,
                log_path=log_path,
                duration_s=duration,
                error=(
                    f"build failed (rc={result.get('returncode')})"
                    + (" — timed out" if result.get("timeout") else "")
                    + (" — idle" if result.get("idle_timeout") else "")
                ),
            )

        artifacts = _scan_artifacts(repo_path)
        return BuildResult(
            success=True,
            artifacts=artifacts,
            log_path=log_path,
            duration_s=duration,
        )

    # ── Release (version bump + tag + optional push) ────────────────────────

    async def release(
        self, repo_path: Path, version: str, runtime, on_log: LogCallback
    ) -> ReleaseResult:
        """Bump `tauri.conf.json` + `Cargo.toml` + `package.json` versions in
        lockstep, commit, tag `vX.Y.Z`, and push if a `origin` remote exists.

        The cross-platform installer build itself is delegated to the
        GH Actions workflow (`templates/tauri/release.yml`) which fires on
        the pushed tag. Forge's job is just to cut a clean release commit
        — not to compile installers on the user's machine.
        """
        err = _validate_semver(version)
        if err:
            return ReleaseResult(success=False, version=version, error=err)

        git = _resolve_binary("git")
        if not git:
            return ReleaseResult(success=False, version=version, error="git not found on PATH")
        if runtime is None:
            return ReleaseResult(success=False, version=version, error="agent-runtime unavailable")
        if not repo_path.exists():
            return ReleaseResult(
                success=False, version=version, error=f"repo missing at {repo_path}"
            )

        # Bump the three version files in place. _bump_* helpers return True
        # when they actually changed a file, False when the field was absent
        # or already correct. We track which files were touched for the
        # commit message + the response.
        bumped: list[str] = []
        try:
            for rel, bumper in (
                ("src-tauri/tauri.conf.json", _bump_json_version),
                ("package.json", _bump_json_version),
                ("src-tauri/Cargo.toml", _bump_cargo_toml_version),
            ):
                p = repo_path / rel
                if p.exists():
                    if bumper(p, version):
                        bumped.append(rel)
        except Exception as e:
            return ReleaseResult(
                success=False, version=version, error=f"version-bump failed: {e}"
            )

        if not bumped:
            return ReleaseResult(
                success=False,
                version=version,
                error="no version files found — is this a Tauri project?",
            )

        log_path = repo_path / ".eos-forge" / f"release-{version}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(b"")

        tag = f"v{version}"
        commit_msg = f"chore: release {tag}"

        # git add -A + commit + tag. Each is a fast one-shot; collect any
        # non-zero rc into a short error.
        for cmd in (
            [git, "add", "-A"],
            [git, "commit", "-m", commit_msg],
            [git, "tag", tag],
        ):
            r = await runtime.run(
                cmd,
                cwd=str(repo_path),
                stdout_path=log_path,
                stderr_path=log_path,
                on_stdout_line=on_log,
                timeout_s=60.0,
            )
            if r.get("returncode") != 0:
                return ReleaseResult(
                    success=False,
                    version=version,
                    tag=tag,
                    files_bumped=bumped,
                    error=(
                        f"`{' '.join(cmd[1:])}` failed "
                        f"(rc={r.get('returncode')}); see {log_path.name}"
                    ),
                )

        # Capture the commit SHA we just made.
        sha_proc = await asyncio.create_subprocess_exec(
            git, "rev-parse", "HEAD",
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        sha_out, _ = await sha_proc.communicate()
        commit_sha = sha_out.decode("utf-8", errors="replace").strip()

        # Push only if an `origin` remote is configured. No remote = local
        # tag only; UI surfaces this so the user knows nothing reached
        # GitHub.
        pushed = False
        if await _git_has_origin(git, repo_path):
            for cmd in (
                [git, "push", "origin", "HEAD"],
                [git, "push", "origin", tag],
            ):
                r = await runtime.run(
                    cmd,
                    cwd=str(repo_path),
                    stdout_path=log_path,
                    stderr_path=log_path,
                    on_stdout_line=on_log,
                    timeout_s=120.0,
                )
                if r.get("returncode") != 0:
                    # Tag is local, just couldn't push. Soft-fail with detail.
                    return ReleaseResult(
                        success=False,
                        version=version,
                        tag=tag,
                        commit_sha=commit_sha,
                        pushed=False,
                        files_bumped=bumped,
                        error=(
                            f"committed + tagged locally, but `{' '.join(cmd[1:])}` "
                            f"failed (rc={r.get('returncode')}); see {log_path.name}"
                        ),
                    )
            pushed = True

        return ReleaseResult(
            success=True,
            version=version,
            tag=tag,
            commit_sha=commit_sha,
            pushed=pushed,
            files_bumped=bumped,
        )

    # ── Status ──────────────────────────────────────────────────────────────

    async def status(self, repo_path: Path) -> dict:
        """Report on-disk state. Cheap; UI calls this per project on detail open."""
        if not repo_path.exists():
            return {"exists": False}
        conf_path = repo_path / "src-tauri" / "tauri.conf.json"
        version = ""
        product_name = ""
        if conf_path.exists():
            try:
                conf = json.loads(conf_path.read_text(encoding="utf-8"))
                version = conf.get("version") or ""
                product_name = conf.get("productName") or ""
            except Exception:
                pass
        target_release = repo_path / "src-tauri" / "target" / "release"
        last_built = ""
        if target_release.exists():
            try:
                last_built = max((p.stat().st_mtime for p in target_release.iterdir()), default=0)
            except Exception:
                last_built = 0
        return {
            "exists": True,
            "version": version,
            "product_name": product_name,
            "last_built_mtime": last_built,
        }


# ── Module helpers ──────────────────────────────────────────────────────────

# Suffixes Tauri 2's bundler emits — extend as new bundle types appear.
_ARTIFACT_SUFFIXES = (".exe", ".msi", ".dmg", ".deb", ".AppImage", ".rpm")


def _bump_json_version(path: Path, new_version: str) -> bool:
    """Rewrite the top-level `"version"` field of a JSON file. Returns True
    when the file was actually changed.

    Preserves the on-disk indent style by reading + dumping with indent=2
    (the standard for both tauri.conf.json and package.json). If a project
    uses a different indent style this would reflow it — accept that for
    Phase 1; promote to tokenized rewrite if a user complains.
    """
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    current = data.get("version")
    if current == new_version:
        return False
    data["version"] = new_version
    # `json.dumps` strips trailing newline — Tauri's generated files end with
    # one; preserve that to avoid noise in the release commit's diff.
    out = json.dumps(data, indent=2, ensure_ascii=False)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    path.write_text(out, encoding="utf-8")
    return True


# Cargo.toml uses TOML, not JSON. We bump only the `[package] version`
# field via a tight regex — avoids introducing a tomlkit dependency just
# for this and preserves all formatting/comments in the file.
_CARGO_VERSION_RE = re.compile(
    r"""(?msx)
    (^\[package\]\s*\n          # the [package] header on its own line
     (?:                         # …followed by any number of non-section lines
        (?!\[)                   #   (lookahead: not a new [section] header)
        .*\n
     )*?
     version\s*=\s*)             # …until we hit `version = `
    ("[^"]+"|'[^']+')           # the quoted current value (group 2)
    """,
)


def _bump_cargo_toml_version(path: Path, new_version: str) -> bool:
    """Replace `version = "X.Y.Z"` inside `[package]` in a Cargo.toml.

    Only touches the [package] section's `version` — won't accidentally bump
    a dependency's version pin. Returns True when the file changed.
    """
    text = path.read_text(encoding="utf-8")
    m = _CARGO_VERSION_RE.search(text)
    if not m:
        return False
    current = m.group(2)[1:-1]   # strip surrounding quotes
    if current == new_version:
        return False
    new_text = (
        text[: m.start(2)] + f'"{new_version}"' + text[m.end(2) :]
    )
    path.write_text(new_text, encoding="utf-8")
    return True


async def _git_has_origin(git: str, repo_path: Path) -> bool:
    """True iff `git remote get-url origin` succeeds with non-empty output.

    Quiet — stdout/stderr go to /dev/null. We only care about the return
    code + URL presence.
    """
    proc = await asyncio.create_subprocess_exec(
        git, "remote", "get-url", "origin",
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return proc.returncode == 0 and bool(out.strip())


def _scan_artifacts(repo_path: Path) -> list[Path]:
    """Collect installer files Tauri's bundler produced.

    Bundles land under `src-tauri/target/release/bundle/<type>/`. We walk
    the whole tree under `bundle/` because Tauri's per-platform output
    layout varies (`.deb` and `.AppImage` for Linux, `.dmg` and `.app.tar.gz`
    for macOS, `.msi` and `.exe` for Windows).
    """
    bundle_dir = repo_path / "src-tauri" / "target" / "release" / "bundle"
    if not bundle_dir.exists():
        return []
    out: list[Path] = []
    for p in bundle_dir.rglob("*"):
        if p.is_file() and p.suffix in _ARTIFACT_SUFFIXES:
            out.append(p)
    return sorted(out)


async def _drain_process(
    proc: asyncio.subprocess.Process,
    log_path: Path,
    on_log: LogCallback,
) -> None:
    """Read stdout one line at a time, append to log file, dispatch to callback.

    Callback errors are swallowed — we never let a UI bug kill the drain.
    Exits when stdout closes (process exits or pipe is severed). Closes the
    log file handle in `finally`.
    """
    if proc.stdout is None:
        return
    handle = log_path.open("ab")
    try:
        async for line in proc.stdout:
            try:
                handle.write(line)
                handle.flush()
            except Exception:
                pass
            try:
                on_log(line)
            except Exception:
                pass
    finally:
        try:
            handle.close()
        except Exception:
            pass
