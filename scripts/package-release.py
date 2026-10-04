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
    python scripts/package-release.py englishos-cloud --definition-pack=build/definition-pack/definitions.sqlite
    python scripts/package-release.py --check                   # dry-run all tiers
"""

import importlib.util
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


def prune_build(
    out_dir: Path, tier: dict, public_engines: set[str] | None = None
) -> tuple[list[str], list[str], list[str]]:
    """Drop never-published trees, then keep only the engines this build's code
    reaches (editions M13), and the tests that still bind to what is left.

    `engines/` is copied whole by `[include]`, so without this every build —
    an exe included — carried every engine. The kept set is what the collected
    apps, plugins and services reach (`release_filter.engines_used`). A tier's
    `engines = [...]` is NOT unioned in: on `[tiers.core]` that list is the
    public-snapshot allowlist ("may ship"), not a need, and every tier extends
    core. It is enforced instead: pass `public_engines` for a public tier, and a
    kept engine outside it stops the build rather than shipping a held engine.
    Tests bound to a removed engine or to an app outside the tier are dropped,
    so the build's own suite still collects.
    """
    from emptyos.sdk import release_filter as rf  # lazy, like resolve_tier's import

    # `[exclude]` names apps/ and engines/personal/ but not tests/personal/,
    # which `tests/` in `[include]` copied into every build since the private
    # repo began tracking it (2026-08-16). The shared constant covers all three.
    rf.prune_never_published(out_dir)
    rf.assert_never_published_absent(out_dir)
    engines_dir = out_dir / "engines"
    known = {p.name for p in engines_dir.iterdir() if p.is_dir()} if engines_dir.is_dir() else set()
    keep = rf.engines_used(out_dir)
    if public_engines is not None and keep - public_engines:
        raise SystemExit(
            "public build would ship held engine(s): "
            + ", ".join(sorted(keep - public_engines))
            + " — something shipped reaches them; see release_filter.engines_used"
        )
    report = rf.prune_snapshot(
        out_dir,
        allowed_apps=tier["apps"],
        allowed_plugins=tier["plugins"],
        allowed_engines=keep,
    )
    dropped_tests = rf.drop_tests_bound_to(
        out_dir, tier["apps"], keep, source_root=ROOT, known_engines=known
    )
    return sorted(keep), list(report.dropped_engines), dropped_tests


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


DEFINITION_PACK_MODULE = (
    ROOT / "apps" / "public" / "englishos" / "dictionary" / "definition_pack.py"
)


def check_definition_pack(source: Path) -> dict:
    """Open `source` with the dictionary's own reader; return its provenance.

    A tier can ship a generated dictionary definition pack, a build output that
    lives outside the collected trees (``build/definition-pack/`` by default).
    It reaches the artifact only through this check and the copy in `package`,
    so an artifact never carries a file the dictionary would refuse at boot,
    and MANIFEST.json records which pack it is.
    """
    if not source.is_file():
        raise SystemExit(f"definition pack not found: {source}")
    spec = importlib.util.spec_from_file_location("eos_definition_pack_release", DEFINITION_PACK_MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # pure module
    pack = mod.DefinitionPack(source)
    try:
        if not pack.available:
            raise SystemExit(f"definition pack unusable: {source}: {pack.error}")
        count = pack.meta.get("count")
        if not count:
            raise SystemExit(f"definition pack is empty: {source}")
        meta = dict(pack.meta)
    finally:
        # The reader keeps its connection open for lookups; on Windows an open
        # handle would keep the file locked for the rest of the run.
        conn = getattr(pack, "_conn", None)
        if conn is not None:
            conn.close()
    return {"entries": count, "generated_at": meta.get("generated_at"),
            "model": meta.get("model"), "headword_filter": meta.get("headword_filter"),
            "sha256": _sha256(source)}


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tier_setting(tiers: dict, name: str, key: str):
    """`key` from tier `name`, or inherited through its ``extends`` chain."""
    seen: set[str] = set()
    while name and name not in seen:
        seen.add(name)
        tier = tiers.get(name, {})
        if key in tier:
            return tier[key]
        name = tier.get("extends")
    return None


def definition_pack_choice(tiers: dict, name: str, flags: list[str]) -> tuple[str | None, Path | None]:
    """(destination in the artifact, source file) for a tier that declares
    ``definition_pack``, itself or through ``extends``; (None, None) otherwise.

    A declaring tier needs ``--definition-pack=<file>`` or an explicit
    ``--no-definition-pack``: without the pack, every dictionary lookup in the
    built image becomes a paid model call, which must never happen by omission.
    """
    dest = _tier_setting(tiers, name, "definition_pack")
    if not dest:
        return None, None
    source = next((f.split("=", 1)[1] for f in flags if f.startswith("--definition-pack=")), "")
    if source:
        return dest, Path(source)
    if "--no-definition-pack" in flags:
        return None, None
    raise SystemExit(
        f"this tier ships a dictionary definition pack at {dest}: pass "
        "--definition-pack=<definitions.sqlite> (built by scripts/build_definition_pack.py), "
        "or --no-definition-pack to build without one")


def package(tier_name: str, dry_run: bool = False, platform_override: str | None = None,
            flags: list[str] | None = None):
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

    # Before the long work, so a missing pack fails in seconds.
    pack_dest, pack_source = definition_pack_choice(release["tiers"], tier_name, flags or [])
    pack_info = check_definition_pack(pack_source) if pack_source else None
    if pack_info:
        print(f"  Definition pack: {pack_info['entries']} entries -> {pack_dest}")

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

    from emptyos.sdk.release_tiers import PUBLIC_TIERS, tier_union
    public_engines = (
        tier_union(release["tiers"], PUBLIC_TIERS, "engines") if tier_name in PUBLIC_TIERS else None
    )
    engines, dropped_engines, dropped_tests = prune_build(out_dir, tier, public_engines)
    print(f"  Engines: {len(engines)} kept, {len(dropped_engines)} dropped (M13)")
    if dropped_tests:
        print(f"  Tests dropped with their engines/apps: {len(dropped_tests)}")

    audit_output(out_dir)

    # After the audit, which checks what the collector gathered: the pack's
    # destination is gitignored in the repo, so a stray copy there would have
    # been dropped by collect_files, and the artifact's only pack is this one.
    if pack_info:
        target = out_dir / pack_dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pack_source, target)
        # The hash is of what shipped: a pack rebuilt during the long collect
        # above would otherwise be recorded under the checked file's hash.
        if _sha256(target) != pack_info["sha256"]:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise SystemExit(f"definition pack changed while packaging: {pack_source}")

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
        "engines": engines,
        "file_count": sum(1 for p in out_dir.rglob("*") if p.is_file()),
    }
    if pack_info:
        manifest["definition_pack"] = {"path": pack_dest, **pack_info}
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nPackaged to: {out_dir}")
    print(
        f"  {manifest['file_count']} files, {len(tier['apps'])} apps, "
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
        print("Usage: python scripts/package-release.py <tier> [--check] [--platform=<name>] "
              "[--definition-pack=<file> | --no-definition-pack]")
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
    package(tier_name, dry_run=dry_run, platform_override=platform_override, flags=flags)


if __name__ == "__main__":
    main()
