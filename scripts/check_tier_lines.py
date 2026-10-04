#!/usr/bin/env python3
"""Every release tier belongs to one product line, and stays inside it.

EmptyOS is the trunk; a branch (a product line with its own audience) is not a
fork but a set of tiers. `release.toml` records the membership as a
`product_line` field on every tier — "emptyos" for the trunk, the branch id
otherwise. Six rules follow, and this is the only place they are checked:

  missing_line       every tier declares `product_line` as a lowercase slug.
                     A tier without one sits outside the edition model, which
                     is how a line came to be defined twice before the field
                     existed (one tier extending standard, one a bare list).

  unknown_line       that slug has a `[product_lines.<id>]` entry. The entry
                     carries the line's display name for docs/TIERS.md, and
                     requiring it is what catches a mistyped line id.

  private_line       a non-private tier does not belong to a `private = true`
                     line. The public release drops private lines, so such a
                     tier would ship naming a line its own file no longer has.

  cross_line_extends a tier extends a tier of its OWN line, or a trunk tier.
                     A branch tier may build on the trunk; it may not inherit
                     another branch's apps, and a trunk tier may not inherit a
                     branch's.

  hosted_not_subset  a tier with no `extends` is a direct allowlist — how a
                     hosted edition keeps a slim image, because tiers cannot
                     subtract. Its apps AND plugins must both be a subset of
                     the resolved set of ONE other tier in the same line, so
                     the hosted image cannot quietly ship something its line
                     never shipped. The tier it is measured against must be a
                     real extending tier that does not itself build on the
                     allowlist: a descendant contains its ancestor by
                     construction, and two bare allowlists would vouch for
                     each other. The root, `core`, is the one exemption — every
                     trunk tier descends from it, so it has nothing to be
                     measured against.

  bad_extends        `extends` names an existing tier by a non-empty string,
                     and the chain ends without a cycle. A misspelt, empty or
                     list-valued `extends` would otherwise slip past the
                     rules above: not an allowlist, and no parent line to
                     compare against.

The branch ids are deliberately not listed in this script: it ships in the
public snapshot, and a hardcoded list would name held lines. The registry is
`[product_lines.*]` in `release.toml`, whose held entries the release drops.
That a tier's app and plugin ids exist on disk is `check-tier-folder.py`'s job,
not this one's.

Exit code = number of findings. Silent-ish when clean.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: The trunk's line id. Every branch may extend a tier of this line.
TRUNK = "emptyos"
#: The trunk's base tier: extends-less, and not an allowlist of anything.
ROOT = "core"
#: The keys a hosted allowlist must keep inside its line.
SUBSET_KEYS = ("apps", "plugins")

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _load_resolve_tier():
    """`emptyos/sdk/release_tiers.resolve_tier`, imported by path — the SDK
    package import pulls in the kernel-facing base, which a release script in a
    bare checkout must not need (same reason `check_tier_plugin_reach` loads
    `app_layout` this way)."""
    import importlib.util

    path = REPO / "emptyos" / "sdk" / "release_tiers.py"
    spec = importlib.util.spec_from_file_location("_release_tiers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.resolve_tier


resolve_tier = _load_resolve_tier()


def _line(tier: dict) -> str | None:
    value = tier.get("product_line")
    return value if isinstance(value, str) and _SLUG.match(value) else None


def _ancestors(tiers: dict, name: str) -> tuple[list[str], tuple[str, str] | None]:
    """The tiers `name` extends, nearest first, and the break in that chain as
    `(tier whose extends is at fault, why)` — None when it ends cleanly."""
    chain: list[str] = []
    seen = {name}
    at, t = name, tiers[name]
    # Bounded, not `while`: a chain cannot be longer than the tier count, and
    # an unbounded walk over a cycle grows `chain` until MemoryError — measured
    # at 21 GB when the `seen` check below was mutated away.
    for _ in range(len(tiers)):
        if "extends" not in t:
            return chain, None
        parent = t["extends"]
        if not isinstance(parent, str):
            return chain, (at, f"extends is {parent!r}, not a tier name")
        if parent not in tiers:  # includes the empty string
            return chain, (at, f"extends {parent!r}, which is not a tier")
        if parent in seen:
            return chain, (at, f"extends cycle through {parent!r}")
        chain.append(parent)
        seen.add(parent)
        at, t = parent, tiers[parent]
    # Only reachable if the `seen` check above failed: longer than the tier
    # count means the walk went round a cycle.
    return chain, (at, "extends chain is longer than the tier count (a cycle)")


def scan_tiers(tiers: dict, product_lines: dict) -> list[dict]:
    if not isinstance(tiers, dict) or not tiers:
        # A release.toml with no tiers is a broken input, not a clean tree —
        # "0 findings" here would certify nothing.
        raise ValueError("release.toml has no [tiers.*] tables")

    findings: list[dict] = []
    lines = {name: _line(t) for name, t in tiers.items()}
    chains = {name: _ancestors(tiers, name) for name in tiers}

    for name in sorted(tiers):
        line = lines[name]
        if line is None:
            findings.append({
                "tier": name, "code": "missing_line",
                "detail": f"product_line is {tiers[name].get('product_line')!r}; "
                          f"expected a lowercase slug ({TRUNK!r} for the trunk)",
            })
        elif not isinstance(product_lines.get(line), dict):
            findings.append({
                "tier": name, "code": "unknown_line",
                "detail": f"product_line {line!r} has no [product_lines.{line}] entry",
            })
        elif product_lines[line].get("private") and not tiers[name].get("private"):
            findings.append({
                "tier": name, "code": "private_line",
                "detail": f"a public tier in private line {line!r}; the public "
                          f"release would drop the line and keep the tier",
            })

    # Each break reported once, on the tier whose `extends` is at fault — not
    # again on every descendant that inherits it.
    breaks = dict(chains[name][1] for name in tiers if chains[name][1])
    for at in sorted(breaks):
        findings.append({"tier": at, "code": "bad_extends", "detail": breaks[at]})

    for name in sorted(tiers):
        parent = tiers[name].get("extends")
        if not isinstance(parent, str) or parent not in tiers:
            continue
        mine, theirs = lines[name], lines[parent]
        if mine and theirs and mine != theirs and theirs != TRUNK:
            findings.append({
                "tier": name, "code": "cross_line_extends",
                "detail": f"line {mine!r} extends {parent!r} of line {theirs!r}; "
                          f"a tier extends its own line or the trunk",
            })

    for name in sorted(tiers):
        if name == ROOT or "extends" in tiers[name] or lines[name] is None:
            continue
        own = {k: resolve_tier(tiers, name, k) for k in SUBSET_KEYS}
        # A measuring tier extends cleanly, and not from this allowlist.
        siblings = [
            s for s in sorted(tiers)
            if s != name and lines[s] == lines[name]
            and chains[s][0] and chains[s][1] is None and name not in chains[s][0]
        ]
        gaps = []
        for sib in siblings:
            gap = {k: sorted(own[k] - resolve_tier(tiers, sib, k)) for k in SUBSET_KEYS}
            gaps.append((sum(len(v) for v in gap.values()), sib, gap))
        if any(size == 0 for size, _, _ in gaps):
            continue
        if not gaps:
            findings.append({
                "tier": name, "code": "hosted_not_subset",
                "detail": f"a direct allowlist, but no extending tier in line "
                          f"{lines[name]!r} (outside its own descendants) to be a subset of",
            })
        else:
            _, sib, gap = min(gaps)
            extra = "; ".join(f"{k}: {', '.join(v)}" for k, v in gap.items() if v)
            findings.append({
                "tier": name, "code": "hosted_not_subset",
                "detail": f"not a subset of any tier in line {lines[name]!r} "
                          f"(closest {sib!r} lacks {extra})",
            })
    return findings


def scan(repo: Path | None = None) -> list[dict]:
    with open((repo or REPO) / "release.toml", "rb") as f:
        data = tomllib.load(f)
    return scan_tiers(data.get("tiers", {}), data.get("product_lines", {}))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    findings = scan()
    msg = (f"{len(findings)} tier(s) break the product-line rules"
           if findings else "every tier sits inside its product line")
    if args.json:
        return emit_json(not findings, "tier_lines", msg, {"findings": findings})

    if not findings:
        print(f"check-tier-lines: OK — {msg}.")
        return 0
    print(f"{msg}.\n")
    for f in findings:
        print(f"  [{f['tier']}] {f['code']}: {f['detail']}")
    print("\nFix in release.toml: declare `product_line` on the tier and "
          "register it under [product_lines.<id>]; keep a public tier out of a "
          "private line (mark the tier private, or the line public); extend a tier "
          "of the same line (or the trunk), or bring the allowlist back inside a "
          "tier of its line.")
    return len(findings)


if __name__ == "__main__":
    raise SystemExit(main())
