#!/usr/bin/env python
"""Flag settings toggles that do nothing: a manifest schema key the app reads
only through `app_config()`.

Declaring a key in `[provides.settings] schema` renders a live field in
`/settings` and in the app's own ⚙ panel. That field writes to the **settings
service**, keyed by the schema key verbatim. `app_config()` reads a *different*
store — `emptyos.toml`, loaded at boot. An app that declares a key and reads it
with `app_config` alone therefore ships a toggle the user can flip and the app
never looks at. The manifest looks right, the panel renders, the save succeeds,
and nothing changes (`.claude/rules/app-ui-patterns.md` § setting_or_config).

This was fixed by hand in four apps on 2026-07-17 and the scanner deferred until
"a 3rd app regresses". The first run of this script (2026-10-01) found 32 keys
across 11 apps — the defect is a class, not an instance.

What counts as wired: the schema key appears as the first argument of
`setting_or_config(...)` or `setting(...)` anywhere in the app's Python. The
check is **per key, not per call site** — one settings read makes the key live,
and a second read site that goes to TOML on purpose (a boot-time path) is not a
scanner's judgment to make.

What counts as dead (GATES): no settings read of the key, and an
`app_config(...)` read of either the schema key verbatim or the key with the
app's own `<id>.` prefix stripped — the two shapes every regression took.

What is only reported (ADVISORY, never affects the exit code): a schema key
read by neither helper under its literal name. It may be read by a page through
`/settings/api/config`, through a wrapper that builds the key with an f-string,
or by nothing at all; the regex cannot tell these apart, and an ambiguous signal
must not gate (`.claude/rules/audits.md`).

An app that reads its keys through such a wrapper is invisible here in BOTH
directions: its keys land in the advisory list, and if the same app ALSO
carries a literal `app_config("<key>")` read, the gate fires on a toggle that
works. That site is what the opt-out marker below is for — mark it and say why.

Limits: a literal string key as the FIRST positional argument only. A key built
at runtime, or passed as `key=`, is invisible here, so the dead list is a lower
bound. Source is read as text, so a commented-out read counts like a live one.

A deliberate TOML-only read of a declared key opts out at the call site, where
the intent lives — never in a central allowlist:

    # settings-dead-toggle: ignore <schema.key>

Usage:
    python scripts/check_settings_dead_toggle.py
    python scripts/check_settings_dead_toggle.py --json
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

# `python scripts/x.py` puts scripts/ on sys.path, but tests load this module by
# file path — where it is not. Anchor the sibling import either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

_IGNORE = re.compile(r"settings-dead-toggle:\s*ignore\s+([\w.\-]+)")
# Readers that see the settings service. `\bsetting\(` does not match
# `setting_or_config(` (an underscore follows), so the two are tested apart.
_SETTINGS_READERS = ("setting_or_config", "setting")
_CONFIG_READER = "app_config"


def _reads(src: str, fn: str, key: str) -> bool:
    """True if `fn("key"` / `fn('key'` appears with `key` as the FIRST argument."""
    return re.search(rf"\b{fn}\(\s*['\"]{re.escape(key)}['\"]", src) is not None


def _python_source(app_dir: Path) -> str:
    """Every .py under the app except its pages/ tree (pages are served, not run)."""
    parts = []
    for f in sorted(app_dir.rglob("*.py")):
        if "pages" in f.relative_to(app_dir).parts:
            continue
        parts.append(f.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def scan(repo: Path = REPO) -> dict:
    """Return {"dead": [...], "unread": [...]} over every app declaring a schema.

    Tracked track-tree apps only (`apps/<track>/<group>/<app>/`). `apps/personal/`
    is gitignored — a repo checker reporting on it would be noise nobody else can
    act on — so it is skipped by name at any depth (`apps/personal/labs/<id>/`
    is a documented layout). `_retired/` is not served at all.
    """
    dead, unread = [], []
    for man in sorted(repo.glob("apps/*/*/*/manifest.toml")):
        if "_retired" in man.parts or man.relative_to(repo).parts[1] == "personal":
            continue
        # A manifest this cannot parse is a defect in its own right; swallowing
        # it would report "no findings" for an app the loader also rejects.
        with open(man, "rb") as fh:
            try:
                m = tomllib.load(fh)
            except tomllib.TOMLDecodeError as e:
                e.add_note(f"manifest: {man.relative_to(repo).as_posix()}")
                raise
        schema = (m.get("provides", {}).get("settings") or {}).get("schema") or []
        keys = [f["key"] for f in schema if isinstance(f, dict) and "key" in f]
        if not keys:
            continue

        app_dir = man.parent
        app_id = (m.get("app") or {}).get("id") or app_dir.name
        src = _python_source(app_dir)
        ignored = set(_IGNORE.findall(src))
        rel = app_dir.relative_to(repo).as_posix()

        for key in keys:
            if key in ignored:
                continue
            if any(_reads(src, fn, key) for fn in _SETTINGS_READERS):
                continue
            short = key[len(app_id) + 1:] if key.startswith(app_id + ".") else None
            config_key = next(
                (k for k in (key, short) if k and _reads(src, _CONFIG_READER, k)), None
            )
            if config_key:
                dead.append({"app": app_id, "key": key, "config_key": config_key, "path": rel})
            else:
                unread.append({"app": app_id, "key": key, "path": rel})

    return {"dead": dead, "unread": unread}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable envelope")
    args = ap.parse_args(argv)

    try:
        result = scan(REPO)
    except tomllib.TOMLDecodeError as e:
        # The agent-cli contract is one JSON object on stdout; a traceback is
        # not one. Human mode keeps the loud failure.
        if not args.json:
            raise
        where = "; ".join(getattr(e, "__notes__", []) or [])
        return emit_json(False, "bad_manifest", f"{e} ({where})" if where else str(e))
    dead, unread = result["dead"], result["unread"]
    apps = sorted({d["app"] for d in dead})

    if args.json:
        return emit_json(not dead, "settings_dead_toggle",
                         f"{len(dead)} declared setting(s) read only via app_config "
                         f"in {len(apps)} app(s)", result)

    if not dead:
        print("check_settings_dead_toggle: OK — every declared setting is read "
              "through the settings service")
    else:
        print(f"check_settings_dead_toggle: {len(dead)} declared setting(s) across "
              f"{len(apps)} app(s) are read only via app_config — the toggle does nothing\n")
        for app in apps:
            rows = [d for d in dead if d["app"] == app]
            print(f"  {app}  ({rows[0]['path']})")
            for d in rows:
                ck = d["config_key"]
                fix = (f'setting_or_config("{d["key"]}", <default>)' if ck == d["key"]
                       else f'setting_or_config("{d["key"]}", <default>, config_key="{ck}")')
                print(f"    dead: {d['key']}  — replace app_config(\"{ck}\", <default>) with {fix}")
            print("    or, if the key is read through a wrapper this scan cannot see, mark the "
                  "site: # settings-dead-toggle: ignore <key>")
            print()

    if unread:
        print(f"note: {len(unread)} declared setting(s) in "
              f"{len({u['app'] for u in unread})} app(s) are read by neither helper under "
              f"their literal key (page-only, f-string wrapper, or never read) — advisory")

    # Exit code is the signal; never the count (256 findings would read as OK).
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
