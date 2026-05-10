#!/usr/bin/env python3
"""Package EmptyOS for release — copies a clean tier-specific distribution to dist/.

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
    """Resolve a tier, merging 'extends' parent if present."""
    tiers = release.get("tiers", {})
    tier = tiers.get(tier_name)
    if not tier:
        print(f"Error: unknown tier '{tier_name}'. Available: {', '.join(tiers)}")
        sys.exit(1)

    result = {
        "apps": list(tier.get("apps", [])),
        "plugins": list(tier.get("plugins", [])),
        "skills": list(tier.get("skills", [])),
    }

    parent_name = tier.get("extends")
    if parent_name and parent_name in tiers:
        parent = resolve_tier(release, parent_name)
        result["apps"] = parent["apps"] + result["apps"]
        result["plugins"] = parent["plugins"] + result["plugins"]
        result["skills"] = parent["skills"] + result["skills"]

    return result


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
            print(r.stdout)
            ok = False
        else:
            print(f"  OK: {script}")
    return ok


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

    # Tier apps
    for app_id in tier["apps"]:
        app_dir = ROOT / "apps" / app_id
        if app_dir.exists():
            add_path(app_dir)
        else:
            print(f"  Warning: app '{app_id}' not found at {app_dir}")

    # Tier plugins
    for plugin_id in tier["plugins"]:
        plugin_dir = ROOT / "plugins" / plugin_id
        if plugin_dir.exists():
            add_path(plugin_dir)
        else:
            print(f"  Warning: plugin '{plugin_id}' not found at {plugin_dir}")

    # Tier skills
    for skill_id in tier["skills"]:
        skill_dir = ROOT / "skills" / skill_id
        if skill_dir.exists():
            add_path(skill_dir)
        else:
            print(f"  Warning: skill '{skill_id}' not found at {skill_dir}")

    return files


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
        "file_count": len(files),
    }
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nPackaged to: {out_dir}")
    print(
        f"  {len(files)} files, {len(tier['apps'])} apps, "
        f"{len(tier['plugins'])} plugins, {len(tier['skills'])} skills"
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
                f"{len(tier['plugins'])} plugins, {len(tier['skills'])} skills{drop_note}"
            )
        return

    tier_name = args[0]
    package(tier_name, dry_run=dry_run, platform_override=platform_override)


if __name__ == "__main__":
    main()
