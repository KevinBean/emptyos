#!/usr/bin/env python3
"""A closed app may sit only in a private export group.

`export-groups.toml` ships in the public snapshot. A `[[group]]` names apps
that build together into one bundle, so a group listing a closed app would
publish that app's id and advertise it as buildable. `release-public.py`
drops groups marked `private = true` (`filter_export_groups_toml`); this is
what makes sure every group that needs the mark has it.

A "closed" app is one the public release does not ship: not in the resolved
union of PUBLIC_TIERS (emptyos/sdk/release_tiers.py). That is the release's own rule —
`filter_to_tiers` keeps exactly that union — and it is stricter than the folder:
`apps/public/labs/` holds apps no public tier carries. Findings:

  closed_app_in_public_group  a public group lists an app the release does not ship
  unknown_app                 a public group lists an id no app declares (a typo
                              leaves the member silently out of every build);
                              not checked for private groups, which never ship
                              and may name personal apps absent from a clone
  private_not_boolean         `private` is set to something other than true/false;
                              the release filter treats any truthy value as
                              private, so `private = "false"` would drop a group

A group whose apps cannot be read at all is an error, not a pass: "0 findings"
over input the scanner did not understand certifies nothing.

Exit code = number of findings.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
#: Where this script's own code lives. Never patched: the SDK helper is loaded
#: from here even when scanning another tree (a release snapshot, a fixture).
_CODE_ROOT = REPO


def _load_app_layout():
    """`app_layout` by path: a release script must run in a bare checkout
    without importing the SDK package (same reason as check_tier_plugin_reach)."""
    path = _CODE_ROOT / "emptyos" / "sdk" / "app_layout.py"
    spec = importlib.util.spec_from_file_location("_app_layout_eg", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_public_tiers() -> tuple[str, ...]:
    """PUBLIC_TIERS from emptyos/sdk/release_tiers.py — the one definition the
    release filters by — loaded by path, like `app_layout` below."""
    path = _CODE_ROOT / "emptyos" / "sdk" / "release_tiers.py"
    spec = importlib.util.spec_from_file_location("_release_tiers_eg", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tuple(mod.PUBLIC_TIERS)


PUBLIC_TIERS = _load_public_tiers()


def app_ids(repo: Path) -> set[str]:
    """Every app id declared anywhere in the tree, personal and parked included."""
    al = _load_app_layout()
    return {aid for aid, _ in al.iter_app_dirs(repo / "apps", include_personal=True,
                                               include_catalog=True)}


def public_apps(repo: Path) -> set[str]:
    """The apps the public release ships: the resolved PUBLIC_TIERS union."""
    with open(repo / "release.toml", "rb") as f:
        tiers = tomllib.load(f).get("tiers", {})

    def resolve(name: str, seen: frozenset = frozenset()) -> set[str]:
        if name in seen or name not in tiers:
            return set()
        t = tiers[name]
        out = {a for a in t.get("apps", []) or [] if isinstance(a, str)}
        parent = t.get("extends")
        return out | (resolve(parent, seen | {name}) if isinstance(parent, str) else set())

    union: set[str] = set()
    for name in PUBLIC_TIERS:
        union |= resolve(name)
    if not union:
        raise ValueError("release.toml resolves no public apps — cannot judge groups")
    return union


def scan_groups(groups: list, known: set[str], public: set[str]) -> list[dict]:
    if not isinstance(groups, list):
        raise ValueError("export-groups.toml: [[group]] is not an array of tables")
    findings: list[dict] = []
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str):
            raise ValueError(f"export-groups.toml: a [[group]] has no string id: {g!r}")
        apps = g.get("apps")
        if not isinstance(apps, list) or not all(isinstance(a, str) for a in apps):
            raise ValueError(f"export-groups.toml: group {g['id']!r} apps is not a list of ids")
        flag = g.get("private", False)
        if not isinstance(flag, bool):
            findings.append({"group": g["id"], "app": "", "code": "private_not_boolean",
                             "detail": f"private = {flag!r}; use true or false"})
        if flag is True:
            continue
        for app in apps:
            if app not in known:
                findings.append({"group": g["id"], "app": app, "code": "unknown_app",
                                 "detail": "no app declares this id"})
            elif app not in public:
                findings.append({"group": g["id"], "app": app, "code": "closed_app_in_public_group",
                                 "detail": "not shipped by the public release; mark the group private = true"})
    return findings


def scan(repo: Path | None = None) -> list[dict]:
    repo = repo or REPO
    path = repo / "export-groups.toml"
    if not path.is_file():
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return scan_groups(data.get("group", []), app_ids(repo), public_apps(repo))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    findings = scan()
    msg = (f"{len(findings)} export-group member(s) need attention"
           if findings else "every export group is public-safe")
    if args.json:
        return emit_json(not findings, "export_groups", msg, {"findings": findings})
    if not findings:
        print(f"check-export-groups: OK — {msg}.")
        return 0
    print(f"{msg}.\n")
    for f in findings:
        print(f"  [{f['group']}] {f['app']}: {f['code']} — {f['detail']}")
    return len(findings)


if __name__ == "__main__":
    raise SystemExit(main())
