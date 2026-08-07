#!/usr/bin/env python3
"""Package EmptyOS for release — copies a clean tier-specific distribution to dist/.

This collector builds ``dist/`` from an allowlisted copy plan. It intentionally
does not use ``emptyos.sdk.release_filter``, which prunes already-built snapshots
for ``release-public.py`` and ``release.py``.

Usage:
    python scripts/package-release.py core                      # minimum OS
    python scripts/package-release.py standard                  # full community OS
    python scripts/package-release.py demo                      # VPS showcase
    python scripts/package-release.py standard --platform=vps-cpu
    python scripts/package-release.py --check                   # dry-run all tiers
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # Python <3.11 fallback

ROOT = Path(__file__).resolve().parent.parent
RELEASE_TOML = ROOT / "release.toml"
DIST = ROOT / "dist"


def load_release() -> dict:
    with open(RELEASE_TOML, "rb") as f:
        return tomllib.load(f)


def resolve_tier(release: dict, tier_name: str) -> dict:
    """Resolve a tier's apps/plugins/skills/services, including inherited items.

    Delegates to the shared resolver that release-public.py and release.py
    already use, so "what is in tier X" means one thing across all three
    scripts. Returns sorted, de-duplicated lists: the private implementation
    this replaced concatenated parent + child, so an entry named in both was
    counted (and copied) twice — `plekto` declared two plugins its parent
    already had.
    """
    tiers = release.get("tiers", {})
    if tier_name not in tiers:
        print(f"Error: unknown tier '{tier_name}'. Available: {', '.join(tiers)}")
        sys.exit(1)

    # Lazy: emptyos.sdk.__init__ pulls heavy runtime deps, and this script must
    # stay importable in a bare checkout until it needs them.
    from emptyos.sdk.release_tiers import resolve_tier as _resolve_shared

    return {
        key: sorted(_resolve_shared(tiers, tier_name, key))
        for key in ("apps", "plugins", "skills", "services")
    }


def load_plugin_platforms(plugin_id: str) -> list[str] | None:
    """Read plugins/<id>/manifest.toml [platforms].supports.

    Returns None if manifest is absent or the block is missing (= supports all).
    """
    manifest = ROOT / "plugins" / plugin_id / "manifest.toml"
    if not manifest.exists():
        return None
    try:
        with open(manifest, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None
    supports = data.get("platforms", {}).get("supports")
    return list(supports) if isinstance(supports, list) else None


def filter_plugins_by_platform(
    plugins: list[str], target: str | None
) -> tuple[list[str], list[str]]:
    """Return (kept, dropped) plugin ids for target platform.

    Plugins without a [platforms] block are kept (backward-compatible).
    """
    if not target:
        return plugins, []
    kept, dropped = [], []
    for pid in plugins:
        supports = load_plugin_platforms(pid)
        if supports is None or target in supports:
            kept.append(pid)
        else:
            dropped.append(pid)
    return kept, dropped


def run_safety_checks() -> bool:
    """Run personal data + branding checks. Returns True if clean."""
    ok = True
    for script in ["scripts/check-personal.py", "scripts/check-branding.py"]:
        script_path = ROOT / script
        if not script_path.exists():
            print(f"  Warning: {script} not found, skipping")
            continue
        r = subprocess.run(
            [sys.executable, str(script_path)], cwd=str(ROOT), capture_output=True, text=True
        )
        if r.returncode != 0:
            print(f"  FAIL: {script}")
            if r.stdout:
                print(r.stdout)
            if r.stderr:
                # check-branding writes violations to stderr; without this they
                # vanish from CI logs and "FAIL" is opaque.
                print(r.stderr)
            ok = False
        else:
            print(f"  OK: {script}")
    return ok


# Secret-shaped filenames. Belt-and-braces behind the .gitignore filter below:
# a key that .gitignore has never been told about must still not ship.
SECRET_GLOBS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa", "id_ed25519", "credentials.json", "*-token.json", "*-client.json",
)


def gitignored(paths: list[Path]) -> set[Path]:
    """The subset of ``paths`` that git ignores.

    This collector copies from the **working tree**, not from a git snapshot, so
    without this filter every gitignored file inside an included directory ships.
    That is not hypothetical: before this existed, a `standard` artifact carried
    129 of them — ``tests/personal/`` (gitignored precisely because it is
    personal), ``tests/_dogfood-phone/*.png`` (screenshots of a real vault),
    ``emptyos/.obsidian/``, and a live ``skills/*/.env`` API key.

    .gitignore is already the repo's declaration of "per-machine, secret, or
    generated". Honouring it here cannot over-exclude: a tracked file is never
    ignored, and everything meant to ship is tracked.
    """
    if not paths:
        return set()
    rels = [p.relative_to(ROOT).as_posix() for p in paths]
    # NUL-separated bytes, not text=True: on Windows text mode rewrites "\n" to
    # "\r\n" on the way *in*, so git sees filenames ending in a carriage return
    # and C-quotes them back ("tests/x.png\r"), which then match nothing.
    payload = b"\0".join(r.encode("utf-8") for r in rels)
    try:
        r = subprocess.run(
            ["git", "check-ignore", "-z", "--stdin"], cwd=str(ROOT),
            input=payload, capture_output=True,
        )
    except FileNotFoundError:
        print("  Warning: git not on PATH — cannot honour .gitignore; "
              "relying on secret-name denylist only")
        return set()
    # 0 = some ignored, 1 = none ignored. Anything else is a real git failure.
    if r.returncode not in (0, 1):
        raise SystemExit(f"git check-ignore failed: {r.stderr.decode(errors='replace').strip()}")
    return {(ROOT / part.decode("utf-8")).resolve()
            for part in r.stdout.split(b"\0") if part}


def is_secret_name(path: Path) -> bool:
    # A template carries no secret and is worth shipping — `.env.example` is
    # documentation, not a credential.
    if path.suffix in (".example", ".sample", ".template"):
        return False
    return any(path.match(g) for g in SECRET_GLOBS)


def collect_files(release: dict, tier: dict) -> list[tuple[Path, Path]]:
    """Collect (source, relative_dest) pairs for the release.

    Returns list of (absolute_source, relative_path) tuples.
    """
    exclude_patterns = release.get("exclude", {}).get("patterns", [])
    include_paths = release.get("include", {}).get("paths", [])

    files: list[tuple[Path, Path]] = []

    def should_exclude(rel: str) -> bool:
        for pat in exclude_patterns:
            if pat.endswith("/"):
                if rel.startswith(pat) or f"/{pat}" in f"/{rel}":
                    return True
            elif pat.startswith("*."):
                if rel.endswith(pat[1:]):
                    return True
            elif rel == pat or rel.endswith(f"/{pat}"):
                return True
        # Skip __pycache__ everywhere
        if "__pycache__" in rel:
            return True
        return False

    def add_path(src: Path, rel_prefix: str = ""):
        if src.is_file():
            rel = rel_prefix or src.relative_to(ROOT).as_posix()
            if not should_exclude(rel):
                files.append((src, Path(rel)))
        elif src.is_dir():
            for child in sorted(src.rglob("*")):
                if child.is_file():
                    rel = child.relative_to(ROOT).as_posix()
                    if not should_exclude(rel):
                        files.append((child, Path(rel)))

    # Platform files (always included)
    for inc in include_paths:
        p = ROOT / inc
        if p.exists():
            add_path(p)

    # Tier apps (resolve wherever each lives in the track tree)
    from emptyos.sdk.app_layout import resolve_app_dir
    for app_id in tier["apps"]:
        app_dir = resolve_app_dir(ROOT / "apps", app_id, include_personal=True)
        if app_dir and app_dir.exists():
            add_path(app_dir)
        else:
            print(f"  Warning: app '{app_id}' not found under apps/")

    # Tier plugins
    for plugin_id in tier["plugins"]:
        plugin_dir = ROOT / "plugins" / plugin_id
        if plugin_dir.exists():
            add_path(plugin_dir)
        else:
            print(f"  Warning: plugin '{plugin_id}' not found at {plugin_dir}")

    # Tier skills
    for skill_id in tier["skills"]:
        legacy_skill_dir = ROOT / "skills" / skill_id
        agent_skill_dir = ROOT / ".agents" / "skills" / skill_id
        if legacy_skill_dir.exists():
            add_path(legacy_skill_dir)
        elif agent_skill_dir.exists():
            add_path(agent_skill_dir)
        else:
            print(
                f"  Warning: skill '{skill_id}' not found at "
                f"{legacy_skill_dir} or {agent_skill_dir}"
            )

    # Tier standalone services. These are release resources, not kernel
    # services: they are copied under services/ and supervised by a plugin.
    for service_id in tier["services"]:
        service_dir = ROOT / "services" / service_id
        if service_dir.exists():
            add_path(service_dir)
        else:
            print(f"  Warning: service '{service_id}' not found at {service_dir}")

    # Drop anything git already knows we must not ship. Last step, so it applies
    # uniformly to platform paths, apps, plugins and skills alike.
    ignored = gitignored([src for src, _ in files])
    kept, dropped = [], []
    for src, rel in files:
        if src.resolve() in ignored or is_secret_name(src):
            dropped.append(rel.as_posix())
        else:
            kept.append((src, rel))
    if dropped:
        secrets = [d for d in dropped if is_secret_name(Path(d))]
        print(f"  Excluded {len(dropped)} gitignored/secret file(s) from the release")
        for s in secrets:
            print(f"    secret withheld: {s}")
    return kept


def audit_output(out_dir: Path) -> None:
    """Refuse to hand back an artifact that carries something git told us to hide.

    The collector already filters, so this can only fire on a regression — a new
    include path, a copy that bypasses ``collect_files``, or a secret shape the
    denylist gained after the filter ran. It is cheap and it is the last gate
    before the tree is frozen into an exe and handed to a stranger.
    """
    copied = [p for p in out_dir.rglob("*") if p.is_file()]
    secrets = [p for p in copied if is_secret_name(p)]

    # Re-ask git, this time about the *source* paths the artifact mirrors.
    sources = []
    for p in copied:
        src = ROOT / p.relative_to(out_dir)
        if src.exists():
            sources.append(src)
    leaked = gitignored(sources)

    problems = sorted(
        {p.relative_to(out_dir).as_posix() for p in secrets}
        | {s.relative_to(ROOT).as_posix() for s in leaked}
    )
    if problems:
        print(f"\nABORTED: {len(problems)} file(s) in the artifact must not ship:")
        for p in problems[:20]:
            print(f"  {p}")
        if len(problems) > 20:
            print(f"  ... and {len(problems) - 20} more")
        shutil.rmtree(out_dir, ignore_errors=True)
        sys.exit(1)


def package(tier_name: str, dry_run: bool = False, platform_override: str | None = None):
    release = load_release()
    version = release.get("release", {}).get("version", "0.0.0")
    tier = resolve_tier(release, tier_name)

    # Resolve target platform: CLI override > tier's target_platform > None
    target_platform = platform_override or release["tiers"][tier_name].get("target_platform")
    if target_platform:
        known = set(release.get("platforms", {}).keys())
        if known and target_platform not in known:
            print(
                f"Error: unknown platform '{target_platform}'. Available: {', '.join(sorted(known))}"
            )
            sys.exit(1)
        kept, dropped = filter_plugins_by_platform(tier["plugins"], target_platform)
        tier["plugins"] = kept
        tier["_platform"] = target_platform
        tier["_platform_dropped"] = dropped

    print(f"\n{'=' * 60}")
    print(f"  EmptyOS {version} — {tier_name} tier", end="")
    if target_platform:
        print(f" @ {target_platform}")
    else:
        print()
    print(f"{'=' * 60}")
    print(f"  Apps:    {len(tier['apps'])}")
    print(f"  Plugins: {len(tier['plugins'])}", end="")
    if target_platform and tier.get("_platform_dropped"):
        dropped = tier["_platform_dropped"]
        print(f"  (dropped for platform: {', '.join(dropped)})")
    else:
        print()
    print(f"  Skills:  {len(tier['skills'])}")
    print(f"  Services:{len(tier['services']):>3}")
    print()

    # Safety checks
    print("Running safety checks...")
    if not run_safety_checks():
        print("\nABORTED: fix violations before packaging.")
        sys.exit(1)
    print()

    # Collect files
    files = collect_files(release, tier)
    print(f"Collected {len(files)} files")

    if dry_run:
        print("\n--- DRY RUN (no files copied) ---")
        # Show summary by directory
        dirs: dict[str, int] = {}
        for _, rel in files:
            top = rel.parts[0] if rel.parts else "."
            dirs[top] = dirs.get(top, 0) + 1
        for d, count in sorted(dirs.items()):
            print(f"  {d}/  ({count} files)")
        print(f"\nTotal: {len(files)} files")
        return

    # Build output directory
    out_name = f"emptyos-{tier_name}-{version}"
    out_dir = DIST / out_name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    for src, rel in files:
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    audit_output(out_dir)

    # Write manifest
    manifest = {
        "name": "emptyos",
        "version": version,
        "tier": tier_name,
        "description": release["tiers"][tier_name].get("description", ""),
        "platform": target_platform,
        "apps": tier["apps"],
        "plugins": tier["plugins"],
        "plugins_dropped_for_platform": tier.get("_platform_dropped", []),
        "skills": tier["skills"],
        "services": tier["services"],
        "file_count": len(files),
    }
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nPackaged to: {out_dir}")
    print(
        f"  {len(files)} files, {len(tier['apps'])} apps, "
        f"{len(tier['plugins'])} plugins, {len(tier['skills'])} skills, "
        f"{len(tier['services'])} services"
    )
    print("  MANIFEST.json written")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    dry_run = "--check" in flags or "--dry-run" in flags

    platform_override: str | None = None
    for f in flags:
        if f.startswith("--platform="):
            platform_override = f.split("=", 1)[1].strip() or None

    if not args and not dry_run:
        release = load_release()
        tier_names = ", ".join(release.get("tiers", {}).keys())
        platform_names = ", ".join(release.get("platforms", {}).keys())
        print("Usage: python scripts/package-release.py <tier> [--check] [--platform=<name>]")
        print(f"Tiers: {tier_names}")
        if platform_names:
            print(f"Platforms: {platform_names}")
        sys.exit(1)

    if dry_run and not args:
        # Validate all tiers
        release = load_release()
        print("Running safety checks...")
        if not run_safety_checks():
            sys.exit(1)
        print("\nAll checks passed.")
        for name, raw in release.get("tiers", {}).items():
            tier = resolve_tier(release, name)
            target = platform_override or raw.get("target_platform")
            dropped: list[str] = []
            if target:
                tier["plugins"], dropped = filter_plugins_by_platform(tier["plugins"], target)
            suffix = f" @ {target}" if target else ""
            drop_note = f"  (dropped: {', '.join(dropped)})" if dropped else ""
            print(
                f"\n  {name}{suffix}: {len(tier['apps'])} apps, "
                f"{len(tier['plugins'])} plugins, {len(tier['skills'])} skills, "
                f"{len(tier['services'])} services{drop_note}"
            )
        return

    tier_name = args[0]
    package(tier_name, dry_run=dry_run, platform_override=platform_override)


if __name__ == "__main__":
    main()
