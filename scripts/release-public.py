#!/usr/bin/env python3
"""Snapshot the working tree and ship a clean single commit to the public repo.

Usage:
  python scripts/release-public.py v0.2.3
  python scripts/release-public.py v0.2.3 --message "Custom release note"
  python scripts/release-public.py v0.2.3 --dry-run    # do everything except the actual force-push
  python scripts/release-public.py v0.2.3 --no-tag-private   # skip tagging the private HEAD

What it does:
  1. Verify the working tree is clean (no unstaged or staged changes)
  2. Run check-personal.py + check-branding.py against the working tree
  3. git archive HEAD into a fresh temp dir (history-free snapshot of tracked files only)
  4. Defensive sweep: strip known-cruft (caddy.exe, results/, dist/, build/, *.pyc)
  5. Filter to public tiers (core + standard from release.toml) — drops dev/uncategorized
     apps + plugins. Use --all to skip and ship everything tracked.
  6. Re-run scans inside the snapshot to confirm clean state
  6. Init the snapshot as a fresh git repo, single commit
  7. Force-push to PUBLIC_REMOTE (default: github.com/KevinBean/emptyos)
  8. Tag the snapshot commit AND tag the private HEAD with the same version
  9. Cleanup temp dir

Required state:
  - D:/emptyos (or wherever this script lives) is a git repo with origin pointing at the
    PRIVATE working repo (where you commit freely)
  - The PUBLIC remote must exist on GitHub already (this script doesn't create repos)

Force-push warning:
  This force-pushes to public main and overwrites whatever was there. That's intentional
  (snapshot model — public history is regenerated from working tree at each release). The
  PRIVATE repo is never force-pushed and keeps full WIP history.

Environment overrides:
  EOS_PUBLIC_REMOTE   default: https://github.com/KevinBean/emptyos.git
  EOS_PUBLIC_BRANCH   default: main
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path


def rmtree_force(path: Path) -> None:
    """rmtree that handles Windows read-only file annoyance.

    Imported lazily: `emptyos.sdk.__init__` pulls heavy runtime deps, and this
    script must stay importable in a bare checkout until it needs them.
    """
    from emptyos.sdk.release_filter import rmtree_forgiving

    rmtree_forgiving(path)


# Windows consoles default to cp1252 which can't encode the unicode arrows /
# checkmarks used in status output. Reconfigure stdout/stderr to utf-8 so the
# script runs the same on Windows / macOS / Linux.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_REMOTE = os.environ.get("EOS_PUBLIC_REMOTE", "https://github.com/KevinBean/emptyos.git")
PUBLIC_BRANCH = os.environ.get("EOS_PUBLIC_BRANCH", "main")

# Defensive cruft sweep — files that should never be in a public release even if
# they slipped past .gitignore at some point. Add to this list when you find new
# offenders; the .gitignore is the primary defense, this is the safety net.
CRUFT_PATHS = [
    "caddy.exe",
    "results",
    "dist",
    "build",
    ".venv",
    # Sandbox pool runtime directories — auto-created by plugins/sandbox-pool/
    # on member spawn. Each holds machine-specific paths + isolated state.
    "sandbox",
    # Engineering-only utility script — sole public consumer of the held
    # engines/models (cable specs). Drop it so models stays private.
    "scripts/import_nexans_library.py",
    # The personal-pattern files. Each is by construction a list of the strings
    # it protects, so shipping it disclosed them. The snapshot scan below reads
    # the private copies instead (run_scans → --patterns).
    ".eos-personal",
    ".eos-personal-subs",
]
# Glob patterns (vs. exact paths) for cruft that varies by port/instance.
CRUFT_GLOBS = [
    "sandbox-*",
]

# Docs (relative to repo root) that describe HELD engineering/internal IP — dropped
# from the public snapshot the same way engineering engines/apps are. These cover
# the cable/earthing/CAD/EM detail that the engine-tier hold removes from code; the
# docs must follow or the IP leaks via prose. Add to this list when a new
# Commercial service IMPLEMENTATIONS held out of the public snapshot (decided
# 2026-07-12). These shipped only because nothing pruned them — `git archive` takes
# the whole tracked tree and the tier filter only touches apps/plugins/engines.
#
# The line is code, not prose: the ARCHITECTURE stays documented (README already
# advertises EnglishOS + Engineer; docs/DESIGN.md + AUTH.md explain how
# multi-tenancy works, which is consistent with building in public). What must not
# ship is the implementation a competitor can lift and run.
#
# Kept public deliberately: emptyos-codoc + emptyos-commons (their apps ARE public),
# voice-api + blender-mcp + pronounce (support public plugins), chatbot (serves
# publish-built sites). Those are the OSS deployment story.
PUBLIC_SERVICE_DROP = [
    "services/earthing-calc",           # paid engineering-calculator SaaS
    "services/englishos-control-plane", # commercial multi-tenant identity/routing (53 files)
    "englishos-cloud",                  # hosted learner container + curriculum content
]

# engineering/internal doc lands.
PUBLIC_DOC_DROP = [
    "docs/EMPTYOS-EM-ROADMAP.md",
    "docs/cable-rating-em-engine-integration.md",
    "docs/CABLE-REPORT-LIMITS.md",
    "docs/CAD-ROADMAP.md",
    "docs/ENGINEERING-APP-WORKFLOW.md",
    "docs/ENGINEERING-WORK-LOOP.md",
    "docs/KB-APP-ALIGNMENT-AUDIT.md",
    "docs/ENGINEERING-APPS-AUDIT-2026-07-17.md",
    "docs/POWERFACTORY-APP-OPTIMIZER-2026-07-15.md",
    # Not a doc, but the same unlink applies: this CI workflow runs the held
    # engineering engines' conformance suites, which the public snapshot drops —
    # shipping it would break public CI and name a held engine path.
    ".github/workflows/engine-conformance.yml",
    # Internal planning / strategy / roadmap docs — not contributor/user docs, and
    # they reference held engineering apps as examples. Public OSS cloners don't
    # need EmptyOS's private backlog or borrow-verdict log.
    "docs/DEFERRED-WORK.md",
    "docs/HOME-COMPANION-REDESIGN.md",
    "docs/OPEN-SOURCE-BORROWING-PLAN.md",
    "docs/WORK-SURFACE-PIVOT.md",
    # Internal triage over a scraped feed — names a private individual and cites
    # held engines. Never a contributor/user doc.
    "docs/MAX-OLIVER-REPO-CHECK-2026-06-28.md",
    # Not a doc: a one-off 2026-04 migration of the maintainer's own vault. Its
    # rename table names personal notes (visa, medical, relocation, a former
    # employer's review), so it is personal data, not a contributor tool.
    "scripts/standardize_projects.py",
    # Dated internal audit reports (2026-08/09). Each is a findings ledger over
    # the WHOLE tree — engineering apps and engines included, by path — written
    # for the maintainer, not a contributor. First reached this gate at v0.7.0,
    # the first release since they were written. Their only inbound links are
    # each other, two `.claude/rules` mentions and a held engine's test, so
    # dropping them strands no shipped user/contributor doc.
    "docs/APPS-MATRIX-AUDIT-2026-08-28.md",
    "docs/DESIGN-SYSTEM-AUDIT-2026-09-12.md",
    "docs/FRONTEND-AUDIT-2026-09-03.md",
    "docs/SYSTEM-AUDIT-2026-09-05.md",
    # The generated skill matrix documents EVERY skill, including the private
    # engineering ones (their names AND descriptions). Unlike APPS.md there is no
    # --public-only mode on generate_skills_doc.py, so the snapshot cannot filter
    # it and the whole matrix would ship. Drop it until the generator learns to
    # emit a public-only catalog, then serve the public subset instead.
    "docs/SKILLS.md",
    # docs/TIERS.md is NO LONGER dropped (2026-08-30). generate_tiers_doc.py
    # grew a --public-only mode and the snapshot regenerates it on the normal
    # path, the same way APPS.md is handled — so the public repo gets a real,
    # filtered tier matrix instead of nothing. It is NOT added to the held-IP
    # gate's skip list, so it is scanned like any other file.
    #
    # Two honest limits on that, because the scan is weaker than it looks:
    #   * PUBLIC_DOC_HELD_TOKENS holds engine paths and ENGINEERING app ids. It
    #     contains no tier names or product brands, so it cannot catch a slip on
    #     `plekto` / `plus` / `english-learning` / `englishos-cloud`. For those,
    #     `private = true` is the ONLY defence — there is one, not two.
    #   * Under `--all`, neither this regeneration nor filter_docs runs, so the
    #     unfiltered private matrix ships. That was equally true when the file
    #     was on the drop list (filter_docs is inside the same branch); `--all`
    #     is the deliberate "ship everything" escape hatch, not a public path.
]
# Section-level public hold: wrap engineering/internal prose in a shared doc (one
# that legitimately stays public) between these markers; the release strips the
# block from the snapshot while the private source keeps it. Invisible in rendered
# markdown, so it's safe to leave in the tracked source.
_PUBLIC_EXCLUDE_RE = re.compile(
    r"[ \t]*<!--\s*public-exclude\s*-->.*?<!--\s*/public-exclude\s*-->[ \t]*\n?",
    re.DOTALL,
)
# High-signal held-IP tokens the doc-consistency gate forbids in shipped docs.
# Engine paths (unambiguous) + distinctive engineering app ids. Generic words
# (bare "cad", public standard names like "IEC 60287") are deliberately excluded
# to avoid false positives — see .claude/rules/audits.md.
PUBLIC_DOC_HELD_TOKENS = [
    "engines/cables", "engines/cable_corridor", "engines/cad", "engines/earthing",
    "engines/overhead_line", "engines/soil", "engines/thermal", "engines/em",
    "engines/sc_force", "engines/reticulation", "engines/as2067_clearance",
    "engines/tree_protection", "engines.cables", "engines.cad", "engines.earthing",
    "engines.overhead_line", "engines.soil", "engines.thermal", "engines.em",
    "engines.models", "engines.reticulation",
    "cable-network", "cable-library", "cable-stress", "overhead-line", "power-study",
    "sc-force", "short-circuit", "geo-cad", "design-package", "engineering-scene",
]

VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-[a-z0-9]+)?$")

# Public tier filter — only apps + plugins listed in these release.toml tiers
# (and their `extends` chain) ship to the public repo. Dev/personal/uncategorized
# apps stay private. Override per-invocation with --all.
PUBLIC_TIERS = ("core", "standard")

# Whole `apps/<track>/` subtrees `prune_snapshot` removes from every public
# snapshot. Named here rather than inline at the call site because the
# working-tree pre-checks need the same answer: a manifest under one of these
# cannot reach the public repo, so flagging it pre-emptively blocks a release
# over a file the snapshot would never carry. `check_no_private_apps`'s own
# docstring already states that principle for UNTRACKED paths; a dropped track
# is the same argument one level up.
PUBLIC_DROP_TRACKS = ("extension", "personal", "installed", "private", "_catalog")

# Gates that degraded to a warning instead of running. Two of them skip when the
# machine lacks a live daemon / mounted vault, which means a release can ship
# with them never having run. Silence made that invisible; the end-of-run
# summary makes it loud, and --strict-gates makes it fatal.
SKIPPED_GATES: list[tuple[str, str]] = []


def skip_gate(name: str, reason: str) -> None:
    SKIPPED_GATES.append((name, reason))


def fail(msg: str, code: int = 1) -> None:
    print(f"\n  ✗ {msg}\n", file=sys.stderr)
    sys.exit(code)


def step(label: str) -> None:
    print(f"\n  → {label}")


def run(
    cmd: list[str],
    cwd: Path | None = None,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> str:
    """Run a command, fail loud on non-zero exit, optionally capture stdout."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=False,
        env={**os.environ, **env} if env else None,
    )
    if result.returncode != 0:
        out = (result.stdout or "") + (result.stderr or "")
        fail(f"command failed: {' '.join(cmd)}\n{out}")
    return (result.stdout or "").strip() if capture else ""


def verify_clean_tree() -> None:
    step("Verify working tree is clean")
    status = run(["git", "status", "--porcelain"], cwd=ROOT, capture=True)
    if status:
        print(status)
        fail(
            "working tree has uncommitted changes. Commit or stash before releasing — "
            "the public snapshot must reflect a stable state."
        )
    print("    OK: clean tree")


def run_scans(target_dir: Path | None = None) -> None:
    """Run check-personal + check-branding. If target_dir is given, run them
    inside that dir (the snapshot); otherwise run against working tree."""
    cwd = target_dir or ROOT
    label = "snapshot" if target_dir else "working tree"
    step(f"Run safety scans against {label}")
    for script in ("check-personal.py", "check-branding.py"):
        # Scripts live in ROOT/scripts/ — invoke with absolute path so they
        # can run inside the snapshot dir which has its own copy.
        path = ROOT / "scripts" / script
        cmd = [sys.executable, str(path)]
        if target_dir is not None and script == "check-personal.py":
            # The snapshot's own `.eos-personal` was swept as cruft, so point the
            # scan at the private copy and ignore the allowlist: no file in a
            # public snapshot may carry personal data.
            cmd += ["--patterns", str(ROOT / ".eos-personal"), "--no-allowlist"]
        run(cmd, cwd=cwd)
    check_no_private_apps(cwd)
    if target_dir is None:
        # Static dispatch audit — working tree, public-source scope. Catches
        # call_app / manifest-method= / requires.apps that resolve to a missing
        # app or (existing-app, missing-method), which fails silently at
        # runtime. Run on the WORKING TREE (not the snapshot) so cross-tier
        # targets resolve — a public app legitimately calling a personal app
        # must not read as "app missing". --public-only restricts findings to
        # apps that actually ship; personal-app dispatch bugs don't block a
        # release. Exit code = HIGH count, so `run` aborts on any HIGH.
        ca = ROOT / "scripts" / "code_archaeology.py"
        run([sys.executable, str(ca), "--public-only", "--tier", "high"], cwd=ROOT)
        # Attribute-escaper audit — fails on any attribute-context esc() (the
        # wrong-escaper XSS class): plain-attribute sites AND inline event handlers
        # ("js-context"). Both are 0 after the --fix / --fix-js codemods; a new
        # one (or a js-context shape --fix-js can't auto-rewrite) blocks the release.
        ae = ROOT / "scripts" / "check-attr-escaper.py"
        run([sys.executable, str(ae)], cwd=ROOT)
        # Generated-doc drift gate — APPS.md / TIERS.md must match the live app
        # tree + release.toml (reproducible from the tracked docs/doc-summaries.json
        # cache). Run on the WORKING TREE so the generators see the full app set.
        for gen in ("generate_apps_doc.py", "generate_tiers_doc.py"):
            run([sys.executable, str(ROOT / "scripts" / gen), "--check"], cwd=ROOT)
    print(f"    OK: {label} scans clean")


def scan_demo_vault(temp_dir: Path) -> None:
    """Second-pass scan over `demo/vault/**` using the outbound_scan engine.

    check-personal.py already scans demo/vault content for `.eos-personal`
    matches, but outbound_scan adds the high-confidence SECRET patterns
    (OpenAI/AWS/GH/JWT/Bearer tokens, private-key blocks) on top. Together
    they close the "someone accidentally pasted a real key into a seed note"
    risk.

    Aborts the release on any finding — same shape as check-personal.
    """
    vault_dir = temp_dir / "demo" / "vault"
    if not vault_dir.is_dir():
        return
    step("Scan demo/vault/ with outbound_scan (secrets + personal patterns)")
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    try:
        from emptyos.capabilities.outbound_scan import scan_outbound
    except Exception as e:
        # outbound_scan must always be importable from the snapshot tree;
        # if it isn't, treat that as a hard error rather than silently skipping.
        fail(f"could not import outbound_scan: {e}")
    findings: list[tuple[str, str, str]] = []
    file_count = 0
    for f in vault_dir.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".mp3", ".mp4", ".wav"}:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        file_count += 1
        for hit in scan_outbound(text):
            rel = f.relative_to(temp_dir).as_posix()
            findings.append((rel, hit.pattern_name, hit.preview))
    if findings:
        print(f"    FAIL: {len(findings)} finding(s) in demo/vault/:")
        for rel, pat, preview in findings[:25]:
            print(f"      {rel} :: {pat} :: {preview}")
        if len(findings) > 25:
            print(f"      ... and {len(findings) - 25} more")
        fail("demo/vault contains personal / secret patterns — fix before release")
    print(f"    OK: {file_count} demo/vault files clean")


def run_clickable_audit() -> None:
    """Run scripts/check-clickable.py against the local daemon.

    Requires the user's :9000 daemon to be running with the full app set
    installed. If unreachable (exit 2), prints a warning and continues — we
    don't block releases on a daemon that isn't running. If real intercepts
    are found (exit 1), the release aborts.
    """
    step("Click-target audit (primary CTAs not intercepted by FAB / overlays)")
    path = ROOT / "scripts" / "check-clickable.py"
    import subprocess
    proc = subprocess.run([sys.executable, str(path)], cwd=ROOT)
    if proc.returncode == 2:
        print("    WARNING: daemon at :9000 unreachable — click audit skipped.")
        print("    Start the daemon and re-run release-public.py to enforce.")
        skip_gate("click-target audit", "daemon at :9000 unreachable")
    elif proc.returncode != 0:
        raise SystemExit("Click-target audit failed — fix the intercepts above and try again.")
    else:
        print("    OK: no primary-CTA intercepts")


def run_kb_alignment_audit() -> None:
    """Run scripts/check-kb-alignment.py against the working-tree app set + vault.

    KB notes live in the user's mounted vault; `implemented_in` paths and app
    manifests live in the repo. The gate checks they stay aligned. If the vault
    isn't configured (exit 2) — e.g. a machine without the vault mounted — it
    warns and continues, same as the click audit when the daemon is down.
    Integrity-breaking findings (exit 1) abort the release.
    """
    step("KB/app alignment audit (implemented_in paths, app KB refs, slugs)")
    path = ROOT / "scripts" / "check-kb-alignment.py"
    proc = subprocess.run([sys.executable, str(path)], cwd=ROOT)
    if proc.returncode == 2:
        print("    WARNING: vault not configured — KB alignment gate skipped.")
        print("    Mount the vault and re-run release-public.py to enforce.")
        skip_gate("KB/app alignment audit", "vault not configured")
    elif proc.returncode != 0:
        raise SystemExit("KB/app alignment audit failed — fix the findings above and try again.")
    else:
        print("    OK: KB notes aligned with the app/engine tree")


def run_boot_smoke(target_dir: Path, timeout: int) -> None:
    """Compile + actually boot the snapshot before it ships.

    Every other gate reads files. None of them runs the code, which is how
    v0.2.7 → v0.2.10 each shipped a snapshot that failed to boot on the VPS:
    the edited module compiled fine inside the already-running daemon (its
    modules were cached), and nothing here ever imported it fresh.

    Unlike the click / KB gates, this one never soft-skips: a boot smoke that
    cannot run is a release that cannot be trusted.
    """
    step("Boot smoke (compile every module, then boot the snapshot)")
    script = ROOT / "scripts" / "check_snapshot_boot.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(target_dir), "--timeout", str(timeout)],
        cwd=ROOT,
    )
    if proc.returncode != 0:
        fail(
            "the snapshot does not boot — it would fail on the VPS exactly as "
            "v0.2.7-v0.2.10 did. Fix the failure above and re-release."
        )


def report_skipped_gates() -> None:
    if not SKIPPED_GATES:
        return
    print("\n  " + "=" * 66)
    print("  ⚠  SKIPPED GATES — this release was NOT fully verified")
    print("  " + "=" * 66)
    for name, reason in SKIPPED_GATES:
        print(f"    - {name}: {reason}")
    print("\n    Re-run with the daemon up / vault mounted to enforce them,")
    print("    or pass --strict-gates to make a skip abort the release.")
    print("  " + "=" * 66)


def _in_dropped_track(rel_posix: str) -> bool:
    """True for a path under an `apps/<track>/` subtree the snapshot drops."""
    parts = rel_posix.split("/")
    return len(parts) > 2 and parts[0] == "apps" and parts[1] in PUBLIC_DROP_TRACKS


def check_no_private_apps(cwd: Path) -> None:
    """Refuse the release if any tracked app under apps/ declares `[app] private = true`.

    Mirrors the spirit of `.eos-personal`: things flagged private must never
    reach the public snapshot. The release script is the load-bearing gate;
    `app_loader` honours the same flag at runtime to hide the app from demo
    deployments. Both layers read the same manifest field — one source of
    truth.

    Only consults git-tracked manifests — untracked paths never enter the
    snapshot, so flagging them here is a false positive that blocks releases the
    snapshot itself would never carry. Inside a snapshot dir (no `.git`), falls
    back to filesystem glob because every file there is by definition tracked.

    A whole dropped TRACK is the same argument one level up, and is why this
    gate first fired on 2026-09-20 against `apps/extension/english-learning/
    speaking-practice` — tracked, `private = true` since 2026-08-30, and held
    three times over (the `extension` track is in `PUBLIC_DROP_TRACKS`, its
    tier is private, and the tier is not in `PUBLIC_TIERS`). The flag is
    correct there and not redundant: its OTHER enforcement point is runtime,
    where `app_loader` hides it from any demo deployment that has the code
    (`.claude/rules/demo-mode.md`). So the exemption is the fix, not the flag.
    It applies only in a working tree — inside a snapshot the tracks are
    already gone, so a manifest still present IS the leak, and skipping by
    track name there would disarm the gate.

    The same reasoning now has to cover paths that ARE tracked but are pruned
    from every snapshot: `apps/personal/` became tracked on 2026-08-16 and 15 of
    its manifests declare `private = true`, which would abort every release for
    apps the snapshot never carries. `is_never_published` is that exemption, and
    `assert_never_published_absent` is what keeps it honest.
    """
    import tomllib

    apps_dir = cwd / "apps"
    if not apps_dir.is_dir():
        return

    # In a git working tree, restrict to tracked manifests. In a snapshot
    # (no .git), every file present is part of the snapshot, so glob it.
    in_working_tree = (cwd / ".git").exists()
    if in_working_tree:
        try:
            out = subprocess.run(
                ["git", "ls-files", "apps/**/manifest.toml"],
                cwd=cwd, capture_output=True, text=True, check=True,
            ).stdout
            manifests = [cwd / line for line in out.splitlines() if line.strip()]
        except (subprocess.CalledProcessError, FileNotFoundError):
            manifests = list(apps_dir.rglob("manifest.toml"))
    else:
        manifests = list(apps_dir.rglob("manifest.toml"))

    from emptyos.sdk.release_filter import is_never_published

    offenders: list[str] = []
    for manifest_path in manifests:
        try:
            rel = manifest_path.relative_to(cwd)
        except ValueError:
            continue
        # Tracked, but pruned from every snapshot — see the docstring.
        if is_never_published(rel.as_posix()):
            continue
        # Same exemption, one level up: a whole track `prune_snapshot` drops.
        # Applied ONLY in the working tree. Inside a snapshot the tracks are
        # already gone, so a manifest still present there IS the leak this gate
        # exists to catch — skipping by track name there would disarm it.
        if in_working_tree and _in_dropped_track(rel.as_posix()):
            continue
        try:
            with open(manifest_path, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if data.get("app", {}).get("private", False):
            offenders.append(str(rel))
    if offenders:
        joined = "\n      ".join(offenders)
        fail(
            f"private apps must not be in the public snapshot:\n      {joined}\n"
            f"Either remove them from apps/, gitignore the dir, or drop the "
            f"`private = true` flag if they're meant to ship publicly."
        )


def snapshot_to(temp_dir: Path) -> None:
    """Snapshot HEAD into temp_dir.

    Uses Python's tarfile module (not the system `tar`) for extraction,
    because Windows' Git-Bash `tar` mangles filenames containing special
    chars (e.g. multi-line TOML inline fields parsed as paths).
    """
    import io
    import tarfile

    step(f"Snapshot HEAD via git archive → {temp_dir}")
    result = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        fail(f"git archive failed: {result.stderr.decode(errors='replace')}")
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r|") as tar:
        # filter='data' (Python 3.12+) strips dangerous attributes (abs paths,
        # symlinks pointing outside the extraction root). Falls back to the
        # default extraction filter on older Python.
        try:
            tar.extractall(path=temp_dir, filter="data")
        except TypeError:
            tar.extractall(path=temp_dir)
    file_count = sum(1 for _ in temp_dir.rglob("*") if _.is_file())
    print(f"    OK: extracted {file_count} files")


def _tier_union(
    release_toml: Path, tier_names: tuple[str, ...]
) -> tuple[set[str], set[str], set[str]]:
    """Resolve `tier_names` (+ `extends` chains) into (apps, plugins, engines) sets."""
    import tomllib

    from emptyos.sdk.release_tiers import tier_union

    with open(release_toml, "rb") as f:
        data = tomllib.load(f)
    tiers = data.get("tiers", {}) or {}
    return (
        tier_union(tiers, tier_names, "apps"),
        tier_union(tiers, tier_names, "plugins"),
        tier_union(tiers, tier_names, "engines"),
    )


def filter_to_tiers(temp_dir: Path, tier_names: tuple[str, ...]) -> None:
    """Drop apps/<id>/ and plugins/<id>/ not in the union of `tier_names`.

    Source of truth is `release.toml` in the snapshot. Anything not declared in
    a public tier — dev tooling, uncategorized work-in-progress, scaffolding —
    stays in the private repo only. Tests in `tests/` that hard-bind to dropped
    apps are dropped alongside, so the public test suite collects clean.
    """
    release_toml = temp_dir / "release.toml"
    if not release_toml.is_file():
        fail("release.toml missing from snapshot — cannot resolve tier filter")
    allowed_apps, allowed_plugins, allowed_engines = _tier_union(release_toml, tier_names)
    step(
        f"Filter to tiers {list(tier_names)}: {len(allowed_apps)} apps, "
        f"{len(allowed_plugins)} plugins, {len(allowed_engines)} engines"
    )

    from emptyos.sdk.release_filter import (
        assert_never_published_absent,
        drop_tests_bound_to,
        prune_snapshot,
    )

    report = prune_snapshot(
        temp_dir,
        allowed_apps=allowed_apps,
        allowed_plugins=allowed_plugins,
        allowed_engines=allowed_engines,
        drop_tracks=PUBLIC_DROP_TRACKS,
        # `tests/personal/` became git-tracked in the private repo on 2026-08-16,
        # so `git archive` now collects it. `drop_tests_bound_to` globs
        # `tests/test_*.py` non-recursively and cannot see it.
        drop_test_dirs=("personal",),
    )

    if report.dropped_apps:
        print(f"    dropped apps: {', '.join(report.dropped_apps)}")
    if report.dropped_plugins:
        print(f"    dropped plugins: {', '.join(report.dropped_plugins)}")
    if report.dropped_engines:
        print(f"    dropped engines: {', '.join(report.dropped_engines)}")
    if not any((report.dropped_apps, report.dropped_plugins, report.dropped_engines)):
        print("    nothing to drop")

    dropped = drop_tests_bound_to(temp_dir, allowed_apps, allowed_engines)
    if dropped:
        step(f"Drop tests bound to dropped apps: {len(dropped)}")
        for d in dropped:
            print(f"    {d}")

    # Receipt, not an inference. The personal-data and branding scanners now
    # SKIP these paths (they are tracked-but-never-published), so their absence
    # here is what makes that exemption safe — prove it rather than trusting the
    # prune arguments above to have been passed correctly.
    assert_never_published_absent(temp_dir)
    step("Verified: no never-published subtree survived into the snapshot")


def filter_release_toml(temp_dir: Path) -> None:
    """Strip private tiers (+ the targets that reference them) from the shipped release.toml.

    release.toml ships in the public snapshot, and it is the SOURCE that
    docs/TIERS.md is generated from. Dropping the generated doc while shipping
    its source is cosmetic — the private tier inventory (audiences, descriptions,
    app counts, and the held ENGINEERING app names: earthing, short-circuit,
    power-study, geo-cad …) stays public either way.

    A tier marked `private = true` is by definition not for the public snapshot,
    so remove it here, along with any [targets.*] that packages it. Comments
    immediately above a dropped section are dropped with it — they carry the
    rationale prose (premium/commercial plans) that is the point of removing it.

    NOT SUFFICIENT ON ITS OWN. `private` is a flag someone remembers to set;
    `PUBLIC_TIERS` is what actually ships. Six tiers are non-private and still
    ship nothing, so this pass leaves their app lists intact and naming software
    the snapshot no longer holds. `prune_release_toml_to_snapshot` runs straight
    after and closes that; keep them paired.
    """
    path = temp_dir / "release.toml"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    private = {n for n, t in (data.get("tiers") or {}).items() if t.get("private")}
    dead_targets = {
        n for n, t in (data.get("targets") or {}).items() if t.get("tier") in private
    }
    if not private and not dead_targets:
        return

    step("Filter release.toml (drop private tiers + their targets)")
    drop = {f"[tiers.{n}]" for n in private} | {f"[targets.{n}]" for n in dead_targets}
    out: list[str] = []
    pending: list[str] = []  # comment/blank run — belongs to whatever section comes NEXT
    skipping = False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            # A section header decides the fate of the comment block above it.
            skipping = s in drop
            if not skipping:
                out.extend(pending)
                out.append(line)
            pending = []
        elif not s or s.startswith("#"):
            # Hold it — we don't yet know which section it introduces.
            pending.append(line)
        else:
            if not skipping:
                out.extend(pending)
                out.append(line)
            pending = []
    if not skipping:
        out.extend(pending)  # trailing comments after a kept section
    path.write_text("".join(out), encoding="utf-8")
    print(f"    dropped {len(private)} private tier(s): {', '.join(sorted(private))}")
    if dead_targets:
        print(f"    dropped {len(dead_targets)} target(s): {', '.join(sorted(dead_targets))}")


# Every array a tier can declare. `services` is here because
# package-release.py resolves ("apps", "plugins", "skills", "services") and
# `engines` because release_filter prunes engines/<name>/ — between them that is
# every id a tier names. The regexes are BUILT from this tuple so a key can
# never be added to one and missed by the other.
_ARRAY_KEYS = ("apps", "plugins", "skills", "engines", "services")
_KEY_ALT = "|".join(_ARRAY_KEYS)
_INLINE_ARRAY = re.compile(rf"^(\s*)({_KEY_ALT})\s*=\s*\[(.*)\]\s*$")
_OPEN_ARRAY = re.compile(rf"^(\s*)({_KEY_ALT})\s*=\s*\[\s*$")
_QUOTED_ID = re.compile(r'"([^"]+)"')
_SECTION = re.compile(r"^\s*(\[[^\]]+\])\s*$")


def _snapshot_inventory(temp_dir: Path) -> dict[str, set[str]]:
    """What the snapshot actually contains, by tier-array kind."""
    app_ids: set[str] = set()
    apps_dir = temp_dir / "apps"
    if apps_dir.is_dir():
        for mf in apps_dir.rglob("manifest.toml"):
            try:
                aid = (tomllib.loads(mf.read_text(encoding="utf-8")).get("app") or {}).get("id")
            except Exception:
                continue
            if aid:
                app_ids.add(aid)
    plugins = {
        mf.parent.name for mf in (temp_dir / "plugins").glob("*/manifest.toml")
    } if (temp_dir / "plugins").is_dir() else set()
    skills: set[str] = set()
    for base in (temp_dir / "skills", temp_dir / ".agents" / "skills"):
        if base.is_dir():
            skills |= {d.name for d in base.iterdir() if d.is_dir()}
    engines = {
        d.name for d in (temp_dir / "engines").iterdir() if d.is_dir()
    } if (temp_dir / "engines").is_dir() else set()
    # `filter_commercial_services` runs AFTER this pass, so the held commercial
    # services are still on disk right now. Reading the directory alone would
    # therefore report `earthing-calc` (paid calc SaaS) and
    # `englishos-control-plane` as present, keep them in a surviving tier's
    # `services` array, and pass the post-write ghost check — which validates
    # against this same dict. Subtract what is already condemned.
    #
    # The docstring's "the tree on disk is the authority" holds for apps,
    # plugins and engines, whose deletions all precede this pass. It does not
    # hold for services, and `skills` are never pruned from the tree at all.
    held_services = {
        rel.split("/", 1)[1] for rel in PUBLIC_SERVICE_DROP if rel.startswith("services/")
    }
    services = ({
        d.name for d in (temp_dir / "services").iterdir() if d.is_dir()
    } - held_services) if (temp_dir / "services").is_dir() else set()
    inv = {
        "apps": app_ids,
        "plugins": plugins,
        "skills": skills,
        "engines": engines,
        "services": services,
    }
    # A key here but not in _ARRAY_KEYS (or vice versa) would KeyError deep in
    # the rewrite loop on a release run. Fail at the top instead — as a real
    # check, not an `assert`, which `python -O` / PYTHONOPTIMIZE strips.
    if set(inv) != set(_ARRAY_KEYS):
        fail(f"inventory/key drift: {sorted(set(inv) ^ set(_ARRAY_KEYS))}")
    return inv


def _assert_prunable_shape(text: str) -> None:
    """Refuse to rewrite a release.toml whose syntax this pass cannot handle.

    The rewrite below is line-based, not a TOML parser — deliberately, because
    it must preserve comments and `tomlkit` is not a declared dependency. That
    trade is only safe if the shapes it cannot handle ABORT rather than being
    silently mis-edited. Each check below corresponds to a demonstrated
    corruption:

      * a section header with a trailing comment is not recognised as a header,
        so a preceding dropped section keeps swallowing lines — measured to
        delete every remaining tier;
      * a nested `[tiers.x.sub]` table is not in the drop set, so it resurrects
        a tier that was meant to disappear;
      * an inline array with a trailing comment matches neither array regex and
        passes through UNPRUNED, while the receipt still reports success — the
        exact leak this function exists to prevent.

    A comment line INSIDE a multi-line array is deliberately NOT refused: the
    real release.toml already uses that style four times (`# End-user
    calculators`, `# Flagship surface`), so rejecting it would abort every
    release over the file's own established convention — a checker that fires on
    a healthy target, which `.claude/rules/audits.md` calls a checker bug. The
    rewrite handles it instead, holding the comment until an entry it annotates
    survives.

    `.claude/rules/audits.md`: assert the shape of the input up front and let an
    unexpected one fail loudly, rather than reporting a clean run over input the
    scanner did not understand.
    """
    problems: list[str] = []
    in_array = False
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if in_array:
            if s.startswith("]"):
                in_array = False
            continue
        if s.startswith("[") and not _SECTION.match(line):
            problems.append(f"line {n}: section header with trailing content — {s[:60]}")
            continue
        m = _SECTION.match(line)
        if m:
            head = m.group(1)
            if head.startswith("[tiers.") and head.count(".") > 1:
                problems.append(f"line {n}: nested tier sub-table — {head}")
            continue
        if _OPEN_ARRAY.match(line):
            in_array = True
            continue
        # An array key whose line is neither a clean inline array nor a clean
        # opener: e.g. `apps = ["a", "b"]  # note`, which would ship unpruned.
        if re.match(rf"^\s*({_KEY_ALT})\s*=", line) and not _INLINE_ARRAY.match(line):
            problems.append(f"line {n}: array this pass cannot rewrite — {s[:60]}")
    if problems:
        fail(
            "release.toml uses TOML shapes prune_release_toml_to_snapshot cannot "
            "rewrite safely:\n    " + "\n    ".join(problems)
            + "\n  Reformat those lines, or teach the rewriter the shape. Refusing "
            "to edit rather than silently corrupt the shipped file."
        )


def prune_release_toml_to_snapshot(temp_dir: Path) -> None:
    """Prune surviving tiers' id lists to what the snapshot actually contains.

    `filter_release_toml` drops tiers marked `private = true`. That is the wrong
    question to ask on its own, because `PUBLIC_TIERS = ("core", "standard")` —
    a tier can be perfectly non-private and still ship nothing. Measured on
    2026-08-30, the public release.toml carried `[tiers.dev]`, `[tiers.labs]`,
    `[tiers.macro-studio]` and `[tiers.demo]`, between them naming **46 apps
    that filter_to_tiers had already deleted from the tree**.

    This is the same defect shape as the TIERS.md leak of v0.4.5-v0.5.0: a file
    was held while the SOURCE it derives from shipped. The rule earned there was
    "when holding a generated doc, check whether its source ships"; the rule
    here is its sibling — the criterion must be *does this ship*, not *is this
    flagged*, because only the first one stays true when someone adds a tier.

    Runs AFTER filter_to_tiers (which does the actual deletion), so the tree on
    disk is the authority. A tier that had apps and now has none is dropped
    whole, along with any [targets.*] that packages it.
    """
    path = temp_dir / "release.toml"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    tiers = data.get("tiers") or {}
    if not tiers:
        return

    # Order dependency, enforced rather than merely documented: this must run
    # AFTER filter_to_tiers, whose deletions are the authority for what "ships".
    # Reordered, the tree is still complete, nothing looks absent, and the pass
    # prunes zero ids while printing a success line — a silent no-op that reads
    # exactly like a healthy run.
    if (temp_dir / "apps" / "extension").exists():
        fail(
            "prune_release_toml_to_snapshot ran before filter_to_tiers "
            "(apps/extension/ is still present) — it would prune nothing and "
            "report success. Fix the call order in main()."
        )
    _assert_prunable_shape(text)
    present = _snapshot_inventory(temp_dir)

    # A tier that declared apps but retains none of them ships nothing.
    emptied = {
        n
        for n, t in tiers.items()
        if (t.get("apps") or []) and not (set(t["apps"]) & present["apps"])
    }
    dead_targets = {
        n for n, t in (data.get("targets") or {}).items() if t.get("tier") in emptied
    }
    drop = {f"[tiers.{n}]" for n in emptied} | {f"[targets.{n}]" for n in dead_targets}

    step("Prune release.toml to the shipped set")
    out: list[str] = []
    pending: list[str] = []
    skipping = False
    in_tier = False
    array_key: str | None = None
    removed = 0

    def keep_ids(raw: str, kind: str) -> list[str]:
        return [i for i in _QUOTED_ID.findall(raw) if i in present[kind]]

    def pending_starts_next_block() -> bool:
        # Mirrors filter_suites_toml: a run that opens with a blank line is
        # section separation and belongs to what follows; a comment butted
        # straight against the previous key is that section's own prose.
        return bool(pending and not pending[0].strip())

    for line in text.splitlines(keepends=True):
        s = line.strip()
        # Reuse the guard's own definition of a header rather than a looser
        # startswith/endswith test — two definitions of "section" is how a line
        # like `["a", "b"]` gets read as a header inside an array.
        if _SECTION.match(line):
            was_skipping = skipping
            skipping = s in drop
            in_tier = s.startswith("[tiers.")
            array_key = None
            if not skipping:
                # Carry the buffer across only if it is separation, or if the
                # section it trailed was itself kept.
                if not was_skipping or pending_starts_next_block():
                    out.extend(pending)
                out.append(line)
            pending = []
            continue
        if skipping:
            # Inside a dropped section: swallow its body. A comment/blank run may
            # still belong to whatever comes NEXT — clearing it unconditionally
            # destroyed the 11-line `[targets.*]` banner in the shipped file,
            # because that banner follows a tier this pass drops.
            #
            # But buffering it unconditionally leaks the other way: a comment run
            # sitting immediately after a dropped tier's last key is THAT tier's
            # rationale prose, and re-emitting it attributes commercial notes to
            # a surviving section. filter_suites_toml already settled this —
            # a run STARTING WITH A BLANK LINE is separation belonging to the
            # next block; a comment butted straight against a key rides with the
            # current one. Same rule here rather than a third variant.
            if not s or s.startswith("#"):
                pending.append(line)
            else:
                pending = []
            continue
        if array_key is not None:
            # Inside a multi-line array belonging to a tier. A comment here is a
            # grouping header ("# End-user calculators") annotating the entries
            # below it, so hold it and emit it only once one of those entries
            # survives — otherwise the prose naming a held app ships while the
            # id it names is pruned. Comments still pending when the array closes
            # annotated only pruned entries, so they go with them.
            if s.startswith("]"):
                array_key = None
                out.append(line)
                pending = []
                continue
            if not s or s.startswith("#"):
                pending.append(line)
                continue
            found = _QUOTED_ID.findall(s)
            if found and found[0] not in present[array_key]:
                removed += 1
                continue
            out.extend(pending)
            out.append(line)
            pending = []
            continue
        if in_tier:
            m = _INLINE_ARRAY.match(line)
            if m:
                indent, key, body = m.groups()
                kept = keep_ids(body, key)
                removed += len(_QUOTED_ID.findall(body)) - len(kept)
                rendered = ", ".join(f'"{i}"' for i in kept)
                out.extend(pending)
                out.append(f"{indent}{key} = [{rendered}]\n")
                pending = []
                continue
            m = _OPEN_ARRAY.match(line)
            if m:
                array_key = m.group(2)
                out.extend(pending)
                out.append(line)
                pending = []
                continue
        if not s or s.startswith("#"):
            pending.append(line)
            continue
        out.extend(pending)
        out.append(line)
        pending = []
    out.extend(pending)  # trailing comments after the last kept section
    rewritten = "".join(out)

    # Re-read what we just produced and assert the contract. The rewrite is
    # line-based; this is the only step that checks it actually did what the
    # receipt is about to claim. Without it, every failure mode above is a
    # silent success message — the shape audits.md calls "green because it
    # checks nothing".
    try:
        after = tomllib.loads(rewritten)
    except tomllib.TOMLDecodeError as e:
        fail(f"prune produced invalid TOML ({e}) — refusing to ship it")
    expected = set(tiers) - emptied
    if set(after.get("tiers") or {}) != expected:
        got = set(after.get("tiers") or {})
        fail(
            "prune changed the tier set unexpectedly: "
            f"lost {sorted(expected - got)}, gained {sorted(got - expected)}"
        )
    ghosts = {
        f"[tiers.{n}].{kind}: {ident}"
        for n, t in (after.get("tiers") or {}).items()
        for kind in _ARRAY_KEYS
        for ident in (t.get(kind) or [])
        if ident not in present[kind]
    }
    if ghosts:
        fail("prune left ids naming absent software: " + ", ".join(sorted(ghosts)))
    # A surviving tier must not `extends` one we just dropped: both resolvers
    # return an empty set for a missing parent rather than raising, so the
    # result would be a silently under-populated bundle.
    orphaned = {
        n
        for n, t in (after.get("tiers") or {}).items()
        if t.get("extends") and t["extends"] not in after["tiers"]
    }
    if orphaned:
        fail(f"prune orphaned `extends` for tier(s): {sorted(orphaned)}")

    path.write_text(rewritten, encoding="utf-8")
    print(f"    pruned {removed} id(s) naming software absent from the snapshot")
    if emptied:
        print(f"    dropped {len(emptied)} tier(s) that ship nothing: {', '.join(sorted(emptied))}")
    if dead_targets:
        print(f"    dropped {len(dead_targets)} target(s): {', '.join(sorted(dead_targets))}")


def filter_docs(temp_dir: Path) -> None:
    """Drop held-IP docs + strip public-exclude marker blocks from shipped .md.

    The engine-tier filter holds the engineering CODE; this holds the engineering
    DOCS so the IP doesn't leak via prose. Whole engineering/internal docs are in
    PUBLIC_DOC_DROP; section-level holds inside otherwise-public docs use
    <!-- public-exclude -->…<!-- /public-exclude --> (kept in private source,
    stripped here).
    """
    step("Filter public docs (drop engineering/internal + strip public-exclude markers)")
    dropped = []
    for rel in PUBLIC_DOC_DROP:
        p = temp_dir / rel
        if p.is_file():
            p.unlink()
            dropped.append(rel)
    stripped = 0
    for md in temp_dir.rglob("*.md"):
        try:
            t = md.read_text(encoding="utf-8")
        except Exception:
            continue
        nt = _PUBLIC_EXCLUDE_RE.sub("", t)
        if nt != t:
            md.write_text(nt, encoding="utf-8")
            stripped += 1
    print(f"    dropped {len(dropped)} doc(s); stripped public-exclude blocks in {stripped} file(s)")
    if dropped:
        print(f"      dropped: {', '.join(d.replace('docs/', '') for d in dropped)}")


# Paths where a held name is STRUCTURALLY REQUIRED — the code cannot work
# without it, so it can never be scrubbed. Each entry is a standing decision that
# this exposure is accepted, not an oversight. Per feedback_skiplist_is_a_promise:
# an allowlist entry is a claim that the name is load-bearing. Justify or remove.
#
# NOTE: these mean the public tree DOES disclose that these engineering apps
# exist. Only their implementations are held. Closing that fully would require
# renaming the apps + their live API routes — a cross-repo refactor, not a gate.
# EXACT files permitted to carry a held app name — a PIN, not a blanket.
#
# This was a set of directory prefixes (services/, emptyos/, apps/public/, scripts/).
# That made "59 load-bearing files" a policy assertion the scanner never checked: any
# NEW file — or a stray prose fragment — anywhere under those trees would have passed
# automatically. That is precisely the shape feedback_skiplist_is_a_promise warns
# about, and it is what let docs/TIERS.md ship for three releases.
#
# Pinning the exact list means a new held reference FAILS the gate and a human decides:
# scrub it, or add it here with a reason. The names below are load-bearing —
# /geo-cad/ API routes in the shared bundle, CAD view modules named for their apps,
# public apps integrating with held app ids by id, and the release tooling that holds
# them (release-public.py literally IS the token list).
#
# HONEST SCOPE: the public tree therefore DISCLOSES THAT THESE APPS EXIST. Only their
# implementations are held (engines/ = articulated + sheet; apps/extension/ absent).
# Closing the disclosure fully requires renaming the apps + their live API routes.
HELD_REF_CODE_ALLOWLIST = {
    "apps/public/standard/boards/presets.py",
    "apps/public/standard/kb/source_pdf.py",
    "apps/public/standard/projects/reading.py",
    "apps/public/standard/publish/framework.py",
    "apps/public/standard/reactor/manifest.toml",
    "apps/public/standard/reactor/reactions_work.py",
    "apps/public/standard/rooms/pages/rooms-chat.js",
    "apps/public/standard/viz/embeds.py",
    "apps/public/standard/voice-assistant/app.py",
    "apps/public/standard/workspaces/app.py",
    "docs/doc-summaries.json",
    "emptyos/capabilities/tool_consent.py",
    "emptyos/kernel/app_loader.py",
    "emptyos/runtime/vault_map.py",
    "emptyos/sdk/app_layout.py",
    "emptyos/sdk/base_app.py",
    "emptyos/sdk/base_app_context.py",
    "emptyos/sdk/base_app_media.py",
    "emptyos/sdk/base_app_think.py",
    "emptyos/sdk/clustering.py",
    "emptyos/sdk/conformance.py",
    "emptyos/sdk/deep_loop.py",
    # Names design-package in its docstring because the module IS the
    # [[contributes.design-package.section]] roster-discovery contract.
    "emptyos/sdk/discipline_roster.py",
    "emptyos/sdk/dxf_read.py",
    "emptyos/sdk/external_service.py",
    "emptyos/sdk/georef.py",
    "emptyos/sdk/html_artifact.py",
    "emptyos/sdk/intents.py",
    "emptyos/sdk/json_library.py",
    "emptyos/sdk/kb_refs.py",
    "emptyos/sdk/model_note.py",
    # Names cable-network in a comment — it IS the release filter and must know
    # the held apps it prunes.
    "emptyos/sdk/release_filter.py",
    "emptyos/sdk/utils.py",
    "emptyos/web/routes_auth.py",
    "emptyos/web/static/eos-cable-section.js",
    # "short-circuit force" is the physical quantity the shipped check message
    # names — domain vocabulary, not the held app id.
    "emptyos/web/static/eos-cad-checks-core.js",
    "emptyos/web/static/eos-cad-corridor.js",
    # The checks view dispatches to engineering-scene's live API by route
    # prefix — same accepted app-id exposure as the other cad-views files.
    "emptyos/web/static/eos-cad-views/checks.js",
    # Same accepted exposure as checks.js: the substation views read the
    # document extension namespace `doc.ext['engineering-scene']`, keyed by the
    # app id that writes it. substation-common.js names it only in its header
    # comment, but is the shared helper of the two that read the key.
    "emptyos/web/static/eos-cad-views/substation-common.js",
    "emptyos/web/static/eos-cad-views/substation-elevation.js",
    "emptyos/web/static/eos-cad-views/substation-plan.js",
    # The trusted app-icon sprite registry, mirrored front (EOS.APP_ICON_IDS)
    # and back (app_icons.py). A manifest may only SELECT an id from this list,
    # so an id absent here has its icon refused; `cable-network` is one entry.
    "emptyos/web/static/eos.js",
    "emptyos/sdk/app_icons.py",
    # "short-circuit calculation" is the published title of the ABB technical
    # paper this source pack cites, and the KB slugs built from it — domain
    # vocabulary, the same basis as eos-cad-checks-core.js above.
    "apps/public/standard/trust-loop/SOURCE-PACK.md",
    # "short-circuit" is a domain keyword in the T2 queue classifier's
    # vocabulary list — data, not a reference to the held app.
    "scripts/ingest_build_t2_queue.py",
    "emptyos/web/static/eos-cad-viewport.js",
    "emptyos/web/static/eos-cad-views/corridor-inspector.js",
    "emptyos/web/static/eos-cad-views/cross-section.js",
    "emptyos/web/static/eos-cad-views/line-profile.js",
    "emptyos/web/static/eos-cad-views/plan.js",
    "emptyos/web/static/eos-cad-views/scene-editor.js",
    "emptyos/web/static/eos-components.js",
    "emptyos/web/static/eos-geocad.js",
    "emptyos/web/static/eos-hands-free.js",
    "emptyos/web/static/eos-library-picker.js",
    "emptyos/web/static/eos-sld.js",
    "pyproject.toml",
    "scripts/check-test-app-paths.py",
    "scripts/check_ai_native.py",
    "scripts/check_vault_test_leak.py",
    "scripts/eos-agent.py",
    "scripts/fix_kb_app_alignment_vault.py",
    "scripts/import_prysmian_conductors.py",
    "scripts/migrate_apps_tree.py",
    "scripts/release-public.py",
    "scripts/seed_demo_windfarm.py",
    "scripts/seed_reports_demo.py",
    "scripts/split_page_js.py",
    "services/chatbot/corpus.py",
    "services/chatbot/main.py",
}


def filter_suites_toml(temp_dir: Path) -> None:
    """Drop private suites from the shipped suites.toml.

    Sibling of filter_release_toml, for the same reason: suites.toml ships, and
    the Engineer suite is a plain list of every held engineering app id. Holding
    the apps while shipping a catalog that names them is cosmetic — and it reads
    as a menu of software the public snapshot cannot install.

    Keyed on `private = true` rather than the suite id so a second held suite is
    a one-line edit in suites.toml, not a change here.
    """
    path = temp_dir / "suites.toml"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    private_ids = {
        s.get("id") for s in (tomllib.loads(text).get("suite") or []) if s.get("private")
    }
    private_ids.discard(None)
    if not private_ids:
        return

    step("Filter suites.toml (drop private suites)")
    # Unlike release.toml, identity is not in the header — every block opens with
    # a bare [[suite]] and names itself on an inner `id =`. So a block has to be
    # buffered until its id appears, rather than decided at the header.
    out: list[str] = []
    block: list[str] = []      # current [[suite]] block, emitted once its id is known
    pending: list[str] = []    # comment/blank run — belongs to whatever comes NEXT
    block_id: str | None = None
    started = False

    def pending_starts_next_block() -> bool:
        # A separated run (starts with a blank line) is suite-to-suite spacing or
        # an intro comment for the NEXT suite. A comment immediately after a key
        # belongs to the current suite and must ride with it.
        return bool(pending and not pending[0].strip())

    def flush() -> None:
        if block and block_id not in private_ids:
            out.extend(block)

    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s == "[[suite]]":
            if pending and not pending_starts_next_block():
                block.extend(pending)
                pending = []
            flush()
            # The comment run above a suite introduces it, so it shares its fate.
            # Emitting it separately would ship the held suite's rationale prose
            # while dropping the suite — the exact leak this filter exists to stop.
            block, pending, block_id, started = pending + [line], [], None, True
            continue
        if not started:            # preamble before the first suite — always keep
            out.extend(pending)
            pending = []
            out.append(line)
            continue
        if not s or s.startswith("#"):
            pending.append(line)   # hold it: may introduce the NEXT suite
            continue
        if block_id is None and s.startswith("id ="):
            block_id = s.split("=", 1)[1].strip().strip('"').strip("'")
        block.extend(pending)      # a key line proves the run belonged to THIS suite
        pending = []
        block.append(line)
    if pending and not pending_starts_next_block():
        block.extend(pending)
        pending = []
    flush()
    if not started or block_id not in private_ids:
        out.extend(pending)        # trailing run rides along only with a kept block

    path.write_text("".join(out), encoding="utf-8")
    print(f"    dropped {len(private_ids)} private suite(s): {', '.join(sorted(private_ids))}")


def filter_commercial_services(temp_dir: Path) -> None:
    """Drop commercial service implementations from the public snapshot.

    `git archive` snapshots the whole tracked tree and the tier filter only prunes
    apps/plugins/engines — so services/ and englishos-cloud/ shipped publicly for
    every release simply because no rule removed them. That was inheritance, not a
    decision. See PUBLIC_SERVICE_DROP for what is held and what is deliberately kept.
    """
    step("Filter commercial services (hold paid/multi-tenant implementations)")
    dropped = []
    for rel in PUBLIC_SERVICE_DROP:
        target = temp_dir / rel
        if target.is_dir():
            rmtree_force(target)
            dropped.append(rel)
        elif target.is_file():
            target.unlink()
            dropped.append(rel)
    if dropped:
        print(f"    dropped {len(dropped)}: {', '.join(dropped)}")
    else:
        print("    nothing to drop")

    # Drop tests BOUND to a pruned service, exactly as app/engine pruning drops its
    # bound tests. v0.5.4 shipped tests/test_unit_earthing_calc_ledger.py, which loads
    # services/earthing-calc/ledger.py by path — the service was gone, so the test
    # could only FileNotFoundError. Pruning a thing means pruning what tests it.
    tests_dir = temp_dir / "tests"
    orphaned = []
    if dropped and tests_dir.is_dir():
        for t in sorted(tests_dir.rglob("*.py")):
            try:
                text = t.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if any(rel in text for rel in dropped):
                t.unlink()
                orphaned.append(t.name)
    if orphaned:
        print(f"    dropped {len(orphaned)} test(s) bound to a pruned service: "
              f"{', '.join(orphaned)}")

    kept = sorted(p.name for p in (temp_dir / "services").iterdir()) if (
        temp_dir / "services"
    ).is_dir() else []
    if kept:
        print(f"    kept public (support public apps/plugins): {', '.join(kept)}")


def scrub_prose_held_refs(temp_dir: Path) -> None:
    """Drop prose that names held engineering apps from the public snapshot.

    Held app NAMES are secret (decided 2026-07-12), but they cannot be removed
    from functional code — the shared geo-cad bundle calls /geo-cad/ endpoints,
    public apps integrate with held app ids by name. So the scrub is prose-only:
    it drops the internal dev docs / rules / skills / tests that merely *mention*
    a held app. That REDUCES the disclosure; it does not close it. The residue is
    HELD_REF_CODE_ALLOWLIST, which is an explicit accepted-exposure list.

    Prose = the agent-facing rule/skill trees and tests. These are internal
    developer context; a public OSS cloner loses nothing operational.

    `tools/` is deliberately NOT a prose root: it holds shipped product code (the
    Chrome extension), and unlinking code ships a broken artifact rather than a
    reduced disclosure. v0.5.x shipped a public extension with no sidepanel.js
    because a comment used the words "short-circuit". Held tokens appearing in
    tools/ are caught loudly by check_docs_no_held_refs instead — fix the source.
    """
    step("Scrub prose naming held engineering apps (dev rules / skills / tests)")
    prose_roots = (".claude", ".agent-bus", ".agents", "skills", "tests")
    dropped = 0
    for root in prose_roots:
        base = temp_dir / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if any(tok in text for tok in PUBLIC_DOC_HELD_TOKENS):
                p.unlink()
                dropped += 1
    print(f"    dropped {dropped} prose file(s) naming held apps")


def check_docs_no_held_refs(temp_dir: Path) -> None:
    """Gate: the shipped SNAPSHOT must not reference held engineering apps.

    Scans every text file in the snapshot — not just docs/*.md, which was the
    blind spot that let docs/TIERS.md ship the private tier inventory from
    v0.4.5 through v0.5.0 while this gate printed "41 shipped docs clean".
    (docs/doc-summaries.json was invisible to it for the same reason: not .md.)

    Files in HELD_REF_CODE_ALLOWLIST are structurally required to carry the name
    and are reported, not failed — an accepted exposure, reviewed each release.
    """
    step("Snapshot-consistency: no held engineering IP outside the code allowlist")
    SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
                     ".ttf", ".pdf", ".zip", ".mp3", ".mp4", ".wav", ".pyc"}
    hits: list[tuple[str, str]] = []
    accepted: list[tuple[str, str]] = []
    for p in sorted(temp_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() in SKIP_SUFFIXES:
            continue
        rel = str(p.relative_to(temp_dir)).replace("\\", "/")
        if rel.startswith(".git/"):
            continue
        # docs/APPS.md is the ONE justified skip: the snapshot regenerates it with
        # --public-only, so it is filtered by construction. docs/TIERS.md used to
        # be skipped on the same "generated, trusted" reasoning — but nothing
        # regenerated it, so the private tier inventory shipped v0.4.5 → v0.5.0
        # while this gate printed "all clean". A skip is a promise that something
        # else filtered the file. Never add one without that filter.
        if rel == "docs/APPS.md":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        found = [tok for tok in PUBLIC_DOC_HELD_TOKENS if tok in text]
        if not found:
            continue
        # Exact-file pin. A directory prefix would auto-accept any NEW held
        # reference under that tree — the blanket-promise failure mode.
        (accepted if rel in HELD_REF_CODE_ALLOWLIST else hits).append((rel, found[0]))
    if accepted:
        print(f"    accepted exposure (name is load-bearing in code): {len(accepted)} file(s)")
        for rel, tok in accepted[:10]:
            print(f"      · {rel}: {tok}")
    if hits:
        print("  ✗ snapshot references held engineering IP outside the allowlist:")
        for rel, tok in hits[:40]:
            print(f"      {rel}: {tok}")
        fail(f"{len(hits)} held-IP reference(s) outside the allowlist — drop the file "
             "(PUBLIC_DOC_DROP / scrub_prose_held_refs), wrap the section in "
             "<!-- public-exclude --> markers, or — if the name is genuinely "
             "load-bearing in code — add it to HELD_REF_CODE_ALLOWLIST with a reason")
    # Deliberately NOT phrased as "clean of held-IP": the allowlist above is a real,
    # accepted exposure. Claiming clean while carrying it is exactly the overstatement
    # that let docs/TIERS.md ship for three releases.
    print(f"    OK: no held-IP outside the allowlist ({len(accepted)} accepted exposure(s))")


def check_extension_ships(temp_dir: Path) -> None:
    """Gate: every tracked Chrome-extension file survives every filter.

    The extension is shipped product code that no other gate can see — the boot
    smoke never imports it, and a missing file breaks the artifact silently in
    the user's browser, not in this script. v0.5.x shipped a public extension
    with sidepanel.js unlinked by the prose scrub (a comment said "short-circuit")
    and nothing noticed. This gate fails on ANY future filter that eats it.
    """
    step("Snapshot-consistency: chrome extension ships complete")
    tracked = [
        f for f in run(
            ["git", "ls-files", "tools/chrome-extension"], cwd=ROOT, capture=True
        ).splitlines() if f.strip()
    ]
    if not tracked:
        fail("no tracked files under tools/chrome-extension — did the path move? "
             "Update this gate or remove it deliberately; a silent pass here means "
             "the extension is no longer verified to ship.")
    missing = [f for f in tracked if not (temp_dir / f).is_file()]
    if missing:
        print("  ✗ extension files lost from the public snapshot:")
        for rel in missing[:40]:
            print(f"      {rel}")
        fail(f"{len(missing)} of {len(tracked)} extension file(s) were removed by a "
             "filter (prose scrub / cruft sweep / doc drop). The public extension "
             "would be broken — fix the source file or narrow the filter.")
    print(f"    OK: {len(tracked)} extension files present")


def sweep_cruft(temp_dir: Path) -> None:
    step("Defensive cruft sweep")
    removed = []
    for name in CRUFT_PATHS:
        path = temp_dir / name
        if path.is_file():
            path.unlink()
            removed.append(name)
        elif path.is_dir():
            shutil.rmtree(path)
            removed.append(name + "/")
    # Glob-style cruft (sandbox-9002, sandbox-9003, …) — anything matching
    # one of CRUFT_GLOBS in the temp dir root.
    for pattern in CRUFT_GLOBS:
        for path in temp_dir.glob(pattern):
            if path.is_file():
                path.unlink()
                removed.append(path.name)
            elif path.is_dir():
                shutil.rmtree(path)
                removed.append(path.name + "/")
    # Also strip pycache + pyc anywhere
    for pyc in list(temp_dir.rglob("*.pyc")):
        pyc.unlink()
    for cache in list(temp_dir.rglob("__pycache__")):
        if cache.is_dir():
            shutil.rmtree(cache)
    print(f"    OK: removed {removed or 'no cruft found'}")


def commit_and_push(
    snapshot_src: Path, version: str, message: str, dry_run: bool, reset_history: bool = False
) -> None:
    """Release the snapshot to the public repo.

    Default (append-mode): clones the existing public repo, wipes its tracked
    files (preserving .git), copies the fresh snapshot in, commits as a single
    squashed commit on top of existing history, tags it, and pushes. Public
    history accumulates one commit per release.

    With `reset_history=True` (orphan purge): skips the clone, inits a fresh repo,
    makes ONE commit from the snapshot, and FORCE-pushes `main` — wiping all prior
    public history/tags. Used when sensitive content must not remain reachable at
    an older commit/tag. The private repo keeps full history regardless.

    For the very first release into an empty repo, append-mode falls through to a
    plain `git init` + first commit.
    """
    work = snapshot_src.parent / "release-work"
    if work.exists():
        rmtree_force(work)
    try:
        _commit_and_push(work, snapshot_src, version, message, dry_run, reset_history)
    finally:
        # `work` lives in %TEMP%, NOT inside the caller's TemporaryDirectory, so
        # nothing else cleans it. Only the full-success path used to — a dry run,
        # an identical snapshot, or any abort left a full repo clone behind.
        rmtree_force(work)


def _commit_and_push(
    work: Path,
    snapshot_src: Path,
    version: str,
    message: str,
    dry_run: bool,
    reset_history: bool,
) -> None:
    if reset_history:
        step("Reset public history (orphan) — fresh single-commit repo (will force-push)")
        work.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "-q", "-b", PUBLIC_BRANCH], cwd=work)
        is_first_release = True  # copy snapshot directly (no git rm of prior tree)
    else:
        step(f"Clone existing public repo from {PUBLIC_REMOTE}")
        clone_result = subprocess.run(
            ["git", "clone", "--depth", "50", PUBLIC_REMOTE, str(work)],
            capture_output=True,
            text=True,
        )
        is_first_release = clone_result.returncode != 0 or not (work / ".git").exists()
        if is_first_release:
            print("    Public repo is empty or unreachable — initializing first commit")
            if work.exists():
                rmtree_force(work)
            work.mkdir(parents=True, exist_ok=True)
            run(["git", "init", "-q", "-b", PUBLIC_BRANCH], cwd=work)
        else:
            # Switch to the right branch (remote may use a different default)
            try:
                run(["git", "checkout", PUBLIC_BRANCH], cwd=work)
            except SystemExit:
                run(["git", "checkout", "-b", PUBLIC_BRANCH], cwd=work)
            print("    OK: cloned (history depth ~50 commits)")

    # Configure committer identity from the private repo's last committer
    name = run(["git", "log", "-1", "--format=%an"], cwd=ROOT, capture=True)
    email = run(["git", "log", "-1", "--format=%ae"], cwd=ROOT, capture=True)
    run(["git", "config", "user.name", name], cwd=work)
    run(["git", "config", "user.email", email], cwd=work)

    step("Replace tracked files with fresh snapshot")
    # Remove every tracked file (and stage the deletions). .git/ is preserved
    # because it isn't tracked.
    if not is_first_release:
        # `git rm -rf .` only removes tracked files; untracked stay (we have none).
        run(["git", "rm", "-rfq", "."], cwd=work)
    # Now overlay the snapshot
    for item in snapshot_src.iterdir():
        if item.name == ".git":
            continue
        dest = work / item.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    run(["git", "add", "-A"], cwd=work)

    # Check if there's actually a diff (snapshot may be identical to last release)
    diff_check = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=work,
        capture_output=True,
    )
    if diff_check.returncode == 0:
        print("    NOTE: snapshot is identical to current public HEAD — skipping commit")
        if dry_run:
            print(f"\n  [DRY RUN] Nothing to push; tag {version} would still be created if missing")
        return

    # Pin author/committer/tagger dates to date-only midnight: the public repo
    # is the one open surface a release run touches, so a normal timestamp would
    # leak the exact clock-time of the release (i.e. could reveal work during
    # employer hours). Date-only matches the public site's discipline.
    today = datetime.now().strftime("%Y-%m-%d")
    date_env = {
        "GIT_AUTHOR_DATE": f"{today} 00:00:00",
        "GIT_COMMITTER_DATE": f"{today} 00:00:00",
    }

    step(f"Commit snapshot as {version}")
    run(["git", "commit", "-q", "-m", message], cwd=work, env=date_env)

    step(f"Tag snapshot {version}")
    # If the tag already exists locally (from a previous attempt), force-replace
    existing_tag = subprocess.run(
        ["git", "tag", "--list", version], cwd=work, capture_output=True, text=True
    )
    if existing_tag.stdout.strip():
        run(["git", "tag", "-d", version], cwd=work)
    run(["git", "tag", "-a", version, "-m", f"EmptyOS {version}"], cwd=work, env=date_env)

    if dry_run:
        sha = run(["git", "rev-parse", "HEAD"], cwd=work, capture=True)
        print(
            f"\n  [DRY RUN] Would push commit {sha[:8]} to {PUBLIC_REMOTE} {PUBLIC_BRANCH} (fast-forward)"
        )
        print(f"  [DRY RUN] Would push tag {version}")
        return

    step(f"Push to {PUBLIC_REMOTE}")
    # First-release / orphan paths needed an `origin` remote added; clone has it.
    if reset_history:
        run(["git", "remote", "add", "origin", PUBLIC_REMOTE], cwd=work)
        # Orphan purge — overwrite public main history wholesale.
        run(["git", "push", "-f", "origin", PUBLIC_BRANCH], cwd=work)
        # Force-pushing a BRANCH does not remove TAGS: every prior release tag
        # still points at its old snapshot commit, keeping the very content we
        # are purging fetchable via `git fetch --tags`. An orphan purge that
        # leaves the tags behind is theatre. Delete every remote tag except the
        # one we are about to push.
        remote_tags = run(
            ["git", "ls-remote", "--tags", "--refs", "origin"], cwd=work, capture=True
        )
        stale = [
            line.split("refs/tags/", 1)[1]
            for line in remote_tags.splitlines()
            if "refs/tags/" in line
        ]
        stale = [t for t in stale if t != version]
        if stale:
            step(f"Purge {len(stale)} stale public tag(s) — they still reach the old history")
            for tag in stale:
                run(["git", "push", "origin", "--delete", tag], cwd=work)
            print(f"    OK: deleted {', '.join(stale)}")
    elif is_first_release:
        run(["git", "remote", "add", "origin", PUBLIC_REMOTE], cwd=work)
        run(["git", "push", "-u", "origin", PUBLIC_BRANCH], cwd=work)
    else:
        run(["git", "push", "origin", PUBLIC_BRANCH], cwd=work)
    # Tags can collide with previous attempts — force-push tag refs only
    run(["git", "push", "-f", "origin", version], cwd=work)
    print(f"    OK: pushed commit + tag {version}")


def tag_private(version: str) -> None:
    step(f"Tag private HEAD with {version}")
    # Check if tag exists; if so, force-replace
    existing = run(["git", "tag", "--list", version], cwd=ROOT, capture=True)
    if existing:
        print(f"    NOTE: tag {version} already exists in private repo, replacing")
        run(["git", "tag", "-d", version], cwd=ROOT)
    run(
        ["git", "tag", "-a", version, "-m", f"EmptyOS {version} (private HEAD at release)"],
        cwd=ROOT,
    )
    # Push to private origin
    run(["git", "push", "origin", "-f", version], cwd=ROOT)
    print(f"    OK: tagged + pushed {version} to private origin")


def default_message(version: str) -> str:
    return f"""EmptyOS {version}

Snapshot release from working tree. See full changelog and history in the
private dev repo (not public). Each public release is a single squashed
commit; intermediate WIP is not part of public history.

Tag: {version}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Ship a clean snapshot release to public.")
    parser.add_argument("version", help="Version tag, e.g. v0.2.3")
    parser.add_argument("--message", "-m", default=None, help="Override the default commit message")
    parser.add_argument(
        "--dry-run", action="store_true", help="Do everything except the force-push"
    )
    parser.add_argument(
        "--no-tag-private", action="store_true", help="Skip tagging the private HEAD"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=f"Skip the public-tier filter (default: only ship apps+plugins in tiers {list(PUBLIC_TIERS)})",
    )
    parser.add_argument(
        "--reset-history",
        action="store_true",
        help="Orphan purge: force-push a single fresh commit, wiping all prior public history/tags "
        "(use when sensitive content must not remain reachable at an older commit).",
    )
    parser.add_argument(
        "--strict-gates",
        action="store_true",
        help="Abort if any gate would be skipped (daemon down, vault unmounted, boot smoke off)",
    )
    parser.add_argument(
        "--skip-boot-smoke",
        action="store_true",
        help="Skip the snapshot boot smoke (escape hatch — recorded as a skipped gate)",
    )
    parser.add_argument(
        "--boot-timeout", type=int, default=90, help="Seconds to wait for the snapshot to boot"
    )
    args = parser.parse_args()

    if not VERSION_RE.match(args.version):
        fail(f"version must look like v0.2.3 (got: {args.version})")

    print(f"\n  Releasing {args.version} → {PUBLIC_REMOTE} ({PUBLIC_BRANCH})")
    if args.dry_run:
        print("  [DRY RUN — no push will happen]")

    verify_clean_tree()
    run_scans()  # working tree
    run_clickable_audit()  # only against live daemon — skipped if unreachable
    run_kb_alignment_audit()  # needs the mounted vault — skipped if unconfigured
    if args.skip_boot_smoke:
        skip_gate("snapshot boot smoke", "--skip-boot-smoke passed")

    # Fail before snapshotting rather than after two minutes of work.
    if args.strict_gates and SKIPPED_GATES:
        report_skipped_gates()
        fail("--strict-gates: refusing to release with gates skipped")

    with tempfile.TemporaryDirectory(prefix="eos-snap-") as tmp:
        temp_dir = Path(tmp)
        snapshot_to(temp_dir)
        sweep_cruft(temp_dir)
        if not args.all:
            filter_to_tiers(temp_dir, PUBLIC_TIERS)
            # Hold engineering/internal DOCS the same way the tier filter holds
            # engineering code (drop held-IP docs + strip public-exclude blocks).
            filter_docs(temp_dir)
            # release.toml ships, and it is the SOURCE docs/TIERS.md is generated
            # from — so holding the doc while shipping the source is cosmetic.
            filter_release_toml(temp_dir)
            # `private = true` is not the same question as "does this ship".
            # Six tiers are non-private and still ship nothing under
            # PUBLIC_TIERS, so the private filter alone left 46 held app names
            # in the public release.toml. Prune against the tree on disk.
            prune_release_toml_to_snapshot(temp_dir)
            # Same reasoning one level up: suites.toml catalogs the held
            # engineering apps by id, so holding the apps while shipping the
            # catalog that names them is cosmetic.
            filter_suites_toml(temp_dir)
            # Held app NAMES are secret; drop the internal prose that names them.
            # Prose-only by necessity — the names are load-bearing in code (see
            # HELD_REF_CODE_ALLOWLIST). This reduces disclosure, not closes it.
            scrub_prose_held_refs(temp_dir)
            # Commercial service implementations (paid calc SaaS, multi-tenant
            # control plane, hosted learner container + curriculum) are held. The
            # architecture stays documented; the liftable code does not ship.
            filter_commercial_services(temp_dir)
            # Shipped catalog lists only public apps. Run the snapshot's own
            # generator (its tree is already filtered) with --public-only so the
            # public APPS.md doesn't reference extension/labs apps absent here.
            step("Regenerate APPS.md (public-only) in snapshot")
            run([sys.executable, str(temp_dir / "scripts" / "generate_apps_doc.py"), "--public-only"], cwd=temp_dir)
            # Same treatment for the tier matrix. Must run AFTER
            # prune_release_toml_to_snapshot, because it reads the snapshot's
            # release.toml — which by now contains only tiers that ship.
            # Previously TIERS.md was dropped wholesale (PUBLIC_DOC_DROP), so
            # the public repo carried no tier matrix at all.
            step("Regenerate TIERS.md (public-only) in snapshot")
            run([sys.executable, str(temp_dir / "scripts" / "generate_tiers_doc.py"), "--public-only"], cwd=temp_dir)
        run_scans(temp_dir)  # snapshot
        if not args.all:
            check_docs_no_held_refs(temp_dir)  # prose-IP gate (after doc filter)
        # Runs in --all too: the cruft sweep is unconditional, so the extension
        # can be eaten on either path. No other gate can see a broken extension.
        check_extension_ships(temp_dir)
        scan_demo_vault(temp_dir)
        # Last gate before the push: the static scans have all passed on a tree
        # nobody has ever run. Boot it. Runs on the final, tier-filtered tree so
        # a public app depending on a dropped plugin/engine surfaces here.
        if not args.skip_boot_smoke:
            run_boot_smoke(temp_dir, args.boot_timeout)
        message = args.message or default_message(args.version)
        commit_and_push(temp_dir, args.version, message, args.dry_run, args.reset_history)

    if not args.no_tag_private and not args.dry_run:
        tag_private(args.version)

    report_skipped_gates()
    print(
        f"\n  ✓ Done. Public release: {PUBLIC_REMOTE.replace('.git', '')}/releases/tag/{args.version}\n"
    )


if __name__ == "__main__":
    main()
