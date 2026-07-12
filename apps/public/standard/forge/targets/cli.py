"""CLI target — Python click-based command-line apps packaged with pyinstaller.

Validates the Target Protocol by being deliberately *different* from Tauri:
- Python tooling, not Rust + JS
- Scaffold from a baked template, not an upstream creator
- Dev = run the CLI once (CLIs aren't long-running); the ProcessRecord
  returned to the caller is for a short-lived process
- Build = pyinstaller --onefile, not cargo+tauri-bundle
- Release = single pyproject.toml bump, no Cargo.toml soup

The whole point is to confirm the Target abstraction isn't shaped around
Tauri's quirks.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
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
_PYPROJECT_VERSION_RE = re.compile(
    r"""(?msx)
    (^\[project\]\s*\n
     (?:(?!\[).*\n)*?
     version\s*=\s*)
    ("[^"]+"|'[^']+')
    """,
)


def _resolve_binary(name: str) -> str | None:
    return shutil.which(name)


def _package_name(project_id: str) -> str:
    """Slug -> valid Python package name. Hyphens become underscores;
    runs collapse so `a--b` becomes `a_b`; an all-non-alnum input (e.g.
    `---`) collapses to `app`; leading digits get an `app_` prefix so
    `import` works at module-load time."""
    pkg = re.sub(r"[^a-zA-Z0-9_]+", "_", project_id).strip("_")
    if not pkg:
        pkg = "app"
    if pkg[0].isdigit():
        pkg = "app_" + pkg
    return pkg.lower()


class CliTarget:
    id = "cli"
    name = "CLI (Python)"
    description = "Standalone Python CLIs (click + pyinstaller). One file ship."
    coming_soon = False

    # ── Preflight ───────────────────────────────────────────────────────────

    def preflight(self) -> list[Check]:
        checks = [
            self._probe(
                "python",
                version_args=["--version"],
                install_hint="Install Python 3.10+ from python.org or via your OS package manager",
                hint_url="https://www.python.org/downloads/",
            ),
            self._probe(
                "pip",
                version_args=["--version"],
                install_hint="Ships with Python — run `python -m ensurepip` if missing",
                hint_url="https://pip.pypa.io",
            ),
            self._probe(
                "git",
                version_args=["--version"],
                install_hint="Install Git",
                hint_url="https://git-scm.com",
            ),
        ]
        return checks

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

    # ── Scaffold ────────────────────────────────────────────────────────────

    async def scaffold(
        self, ctx: ScaffoldCtx, runtime, on_log: LogCallback
    ) -> ScaffoldResult:
        """Lay down a minimal but real Python CLI structure:

            <id>/
              pyproject.toml      [project] + [project.scripts]
              README.md
              .gitignore
              src/<pkg>/__init__.py
              src/<pkg>/cli.py    Click command, importable as `<pkg>.cli:main`
              tests/test_cli.py   one passing smoke test
              .github/workflows/test.yml
        """
        log_path = ctx.root / ".eos-forge-logs" / f"{ctx.project_id}-scaffold.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(b"")

        repo_path = ctx.root / ctx.project_id
        if repo_path.exists() and any(repo_path.iterdir()):
            return ScaffoldResult(
                repo_path=repo_path,
                log_path=log_path,
                ok=False,
                error=f"target dir not empty: {repo_path}",
            )
        try:
            self._write_tree(repo_path, ctx)
        except Exception as e:
            return ScaffoldResult(
                repo_path=repo_path,
                log_path=log_path,
                ok=False,
                error=f"failed to write tree: {e}",
            )

        # git init so release verb has a repo to tag.
        git = _resolve_binary("git")
        if git and runtime is not None:
            for cmd in (
                [git, "init"],
                [git, "add", "-A"],
                [git, "commit", "-m", "chore: scaffold from forge"],
            ):
                await runtime.run(
                    cmd,
                    cwd=str(repo_path),
                    stdout_path=log_path,
                    stderr_path=log_path,
                    on_stdout_line=on_log,
                    timeout_s=30.0,
                )

        return ScaffoldResult(
            repo_path=repo_path, log_path=log_path, ok=True, error=""
        )

    def _write_tree(self, repo_path: Path, ctx: ScaffoldCtx) -> None:
        pkg = _package_name(ctx.project_id)
        title = ctx.name or ctx.project_id

        repo_path.mkdir(parents=True, exist_ok=True)
        (repo_path / "src" / pkg).mkdir(parents=True)
        (repo_path / "tests").mkdir()
        (repo_path / ".github" / "workflows").mkdir(parents=True)

        (repo_path / "pyproject.toml").write_text(
            _PYPROJECT_TMPL.format(id=ctx.project_id, pkg=pkg, title=title),
            encoding="utf-8",
        )
        (repo_path / "README.md").write_text(
            _README_TMPL.format(
                title=title,
                pkg=pkg,
                id=ctx.project_id,
                date=datetime.now().strftime("%Y-%m-%d"),
            ),
            encoding="utf-8",
        )
        (repo_path / ".gitignore").write_text(_GITIGNORE_TMPL, encoding="utf-8")
        (repo_path / "src" / pkg / "__init__.py").write_text(
            f'"""{title} — Python CLI scaffolded by EmptyOS Forge."""\n\n__version__ = "0.1.0"\n',
            encoding="utf-8",
        )
        (repo_path / "src" / pkg / "cli.py").write_text(
            _CLI_TMPL.format(title=title, pkg=pkg), encoding="utf-8"
        )
        (repo_path / "tests" / "test_cli.py").write_text(
            _TEST_TMPL.format(pkg=pkg), encoding="utf-8"
        )
        (repo_path / ".github" / "workflows" / "test.yml").write_text(
            _GH_TEST_TMPL, encoding="utf-8"
        )

    # ── Dev (short-lived run) ───────────────────────────────────────────────

    async def dev(
        self, repo_path: Path, log_path: Path, on_log: LogCallback
    ) -> ProcessRecord:
        """Run the CLI's `--help` so the user can see it's wired up.

        CLIs are one-shot — there is no analogue to `tauri dev`'s
        hot-reload window. For interactive Click apps a future verb could
        spawn `python -m <pkg>` in a terminal; V1 just demonstrates the
        binding by running help.
        """
        python = _resolve_binary("python") or _resolve_binary("python3")
        if not python:
            raise RuntimeError("python not found on PATH")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(b"")

        # `pip install -e .` first so the package is importable. Best-effort —
        # if it fails the user sees the error in the log.
        pip = _resolve_binary("pip") or python
        env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        install = await asyncio.create_subprocess_exec(
            python, "-m", "pip", "install", "-e", ".",
            cwd=str(repo_path),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for line in install.stdout:
            try:
                with log_path.open("ab") as f:
                    f.write(line)
            except Exception:
                pass
            try:
                on_log(line)
            except Exception:
                pass
        await install.wait()

        pkg = _package_name(repo_path.name)
        proc = await asyncio.create_subprocess_exec(
            python, "-m", f"{pkg}.cli", "--help",
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        asyncio.create_task(
            _drain_process(proc, log_path, on_log),
            name=f"forge-cli-dev-{proc.pid}",
        )
        return ProcessRecord(
            pid=proc.pid,
            started_at=time.time(),
            log_path=log_path,
            proc=proc,
        )

    # ── Build (pyinstaller --onefile) ───────────────────────────────────────

    async def build(
        self, repo_path: Path, runtime, on_log: LogCallback
    ) -> BuildResult:
        """Bundle the CLI into a single executable with pyinstaller.

        Installs pyinstaller into the project's venv-less env on demand —
        cheap and predictable for V1; users with strict env hygiene can run
        forge inside a virtualenv.
        """
        python = _resolve_binary("python") or _resolve_binary("python3")
        if not python:
            return BuildResult(success=False, error="python not found on PATH")
        if runtime is None:
            return BuildResult(success=False, error="agent-runtime unavailable")

        log_path = repo_path / ".eos-forge" / f"build-{int(time.time())}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(b"")

        started = time.time()
        # Ensure pyinstaller present. Idempotent — pip skips if installed.
        install = await runtime.run(
            [python, "-m", "pip", "install", "--quiet", "pyinstaller"],
            cwd=str(repo_path),
            stdout_path=log_path,
            stderr_path=log_path,
            on_stdout_line=on_log,
            timeout_s=180.0,
        )
        if install.get("returncode") != 0:
            return BuildResult(
                success=False,
                log_path=log_path,
                duration_s=time.time() - started,
                error=f"pip install pyinstaller failed (rc={install.get('returncode')})",
            )

        pkg = _package_name(repo_path.name)
        result = await runtime.run(
            [
                python, "-m", "PyInstaller",
                "--onefile",
                "--name", repo_path.name,
                "--distpath", "dist",
                "--workpath", "build",
                "--specpath", "build",
                f"src/{pkg}/cli.py",
            ],
            cwd=str(repo_path),
            stdout_path=log_path,
            stderr_path=log_path,
            on_stdout_line=on_log,
            timeout_s=600.0,
            idle_timeout_s=180.0,
        )
        duration = time.time() - started
        if result.get("returncode") != 0:
            return BuildResult(
                success=False,
                log_path=log_path,
                duration_s=duration,
                error=f"pyinstaller failed (rc={result.get('returncode')})",
            )

        dist = repo_path / "dist"
        artifacts: list[Path] = []
        if dist.exists():
            for p in dist.iterdir():
                if p.is_file() and p.suffix in ("", ".exe"):
                    artifacts.append(p)
        return BuildResult(
            success=True,
            artifacts=sorted(artifacts),
            log_path=log_path,
            duration_s=duration,
        )

    # ── Release (pyproject.toml bump + tag + push) ──────────────────────────

    async def release(
        self, repo_path: Path, version: str, runtime, on_log: LogCallback
    ) -> ReleaseResult:
        if not _SEMVER_RE.match(version or ""):
            return ReleaseResult(
                success=False,
                version=version,
                error=f"version {version!r} is not semver (X.Y.Z or X.Y.Z-prerelease)",
            )
        if version.startswith("v"):
            return ReleaseResult(
                success=False,
                version=version,
                error="version must not start with 'v' — pass e.g. '1.2.3'",
            )

        git = _resolve_binary("git")
        if not git:
            return ReleaseResult(success=False, version=version, error="git not found on PATH")
        if runtime is None:
            return ReleaseResult(
                success=False, version=version, error="agent-runtime unavailable"
            )
        if not repo_path.exists():
            return ReleaseResult(
                success=False, version=version, error=f"repo missing at {repo_path}"
            )

        pyproject = repo_path / "pyproject.toml"
        if not pyproject.exists():
            return ReleaseResult(
                success=False,
                version=version,
                error="pyproject.toml missing — is this a CLI project?",
            )

        try:
            changed = _bump_pyproject_version(pyproject, version)
        except Exception as e:
            return ReleaseResult(
                success=False, version=version, error=f"version-bump failed: {e}"
            )

        bumped = ["pyproject.toml"] if changed else []
        if not bumped:
            # Version already at requested — that's fine, still cut a tag.
            pass

        log_path = repo_path / ".eos-forge" / f"release-{version}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(b"")

        tag = f"v{version}"
        for cmd in (
            [git, "add", "-A"],
            [git, "commit", "--allow-empty", "-m", f"chore: release {tag}"],
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

        sha_proc = await asyncio.create_subprocess_exec(
            git, "rev-parse", "HEAD",
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        sha_out, _ = await sha_proc.communicate()
        commit_sha = sha_out.decode("utf-8", errors="replace").strip()

        pushed = False
        remote_proc = await asyncio.create_subprocess_exec(
            git, "remote", "get-url", "origin",
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rem_out, _ = await remote_proc.communicate()
        if remote_proc.returncode == 0 and rem_out.strip():
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
                    return ReleaseResult(
                        success=False,
                        version=version,
                        tag=tag,
                        commit_sha=commit_sha,
                        pushed=False,
                        files_bumped=bumped,
                        error=(
                            f"committed + tagged locally, but `{' '.join(cmd[1:])}` "
                            f"failed (rc={r.get('returncode')})"
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
        if not repo_path.exists():
            return {"exists": False}
        pyproject = repo_path / "pyproject.toml"
        version = ""
        if pyproject.exists():
            try:
                text = pyproject.read_text(encoding="utf-8")
                m = _PYPROJECT_VERSION_RE.search(text)
                if m:
                    version = m.group(2)[1:-1]
            except Exception:
                pass
        dist = repo_path / "dist"
        last_built = 0.0
        if dist.exists():
            try:
                last_built = max((p.stat().st_mtime for p in dist.iterdir()), default=0)
            except Exception:
                last_built = 0
        return {
            "exists": True,
            "version": version,
            "last_built_mtime": last_built,
        }


# ── Module helpers + templates ──────────────────────────────────────────────


def _bump_pyproject_version(path: Path, new_version: str) -> bool:
    text = path.read_text(encoding="utf-8")
    m = _PYPROJECT_VERSION_RE.search(text)
    if not m:
        raise ValueError("could not find [project] version in pyproject.toml")
    current = m.group(2)[1:-1]
    if current == new_version:
        return False
    new_text = text[: m.start(2)] + f'"{new_version}"' + text[m.end(2) :]
    path.write_text(new_text, encoding="utf-8")
    return True


async def _drain_process(
    proc: asyncio.subprocess.Process,
    log_path: Path,
    on_log: LogCallback,
) -> None:
    if proc.stdout is None:
        return
    handle = log_path.open("ab")
    try:
        async for line in proc.stdout:
            try:
                handle.write(line); handle.flush()
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


_PYPROJECT_TMPL = """\
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{id}"
version = "0.1.0"
description = "{title} — scaffolded by EmptyOS Forge"
requires-python = ">=3.10"
dependencies = [
    "click>=8.1",
]

[project.scripts]
{id} = "{pkg}.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
"""

_CLI_TMPL = '''\
"""{title} — command-line entry point.

Version is duplicated here from `__init__.py` so this file is bundleable
by pyinstaller as a standalone script (relative imports break when the
script is the entry, not a package member). Edit both when bumping.
"""

from __future__ import annotations

import click

__version__ = "0.1.0"


@click.group(context_settings={{"help_option_names": ["-h", "--help"]}})
@click.version_option(__version__)
def main() -> None:
    """{title} — placeholder. Add your verbs here."""


@main.command()
@click.argument("name", default="world")
def hello(name: str) -> None:
    """Print a friendly greeting."""
    click.echo(f"Hello, {{name}}!")


if __name__ == "__main__":
    main()
'''

_TEST_TMPL = '''\
"""Smoke test — proves the CLI is importable and runnable."""

from click.testing import CliRunner

from {pkg}.cli import main


def test_hello_default() -> None:
    result = CliRunner().invoke(main, ["hello"])
    assert result.exit_code == 0
    assert "Hello, world!" in result.output


def test_hello_named() -> None:
    result = CliRunner().invoke(main, ["hello", "Alice"])
    assert result.exit_code == 0
    assert "Hello, Alice!" in result.output
'''

_GITIGNORE_TMPL = """\
# Python
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
build/
dist/
*.spec

# Virtual envs
.venv/
venv/

# IDE
.vscode/
.idea/

# EmptyOS Forge
.eos-forge/
.eos-forge-logs/
"""

_GH_TEST_TMPL = """\
name: Test

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python: ['3.10', '3.11', '3.12']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: pip install -e . pytest
      - run: pytest -v
"""

_README_TMPL = """\
# {title}

Python CLI scaffolded by [EmptyOS Forge](https://emptyos.dev/forge) on {date}.

## Install (dev)

```bash
pip install -e .
{id} --help
```

## Test

```bash
pytest
```

## Build a single-file executable

```bash
pip install pyinstaller
pyinstaller --onefile --name {id} src/{pkg}/cli.py
```

The binary lands at `dist/{id}` (or `dist/{id}.exe` on Windows).
"""
