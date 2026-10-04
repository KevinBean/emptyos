#!/usr/bin/env python3
"""Loop-registry ↔ reality reconciliation.

The ``emptyos/sdk/loops.py`` registry is a frozen static catalog — it can name
a loop's status/flag/components but cannot know whether the components still
exist or whether a dark flag is flipped on THIS machine. This scanner closes
that gap (the "nothing reconciles registry ↔ live flag state" finding of the
2026-07-22 loop-engineering audit). It reports four things:

  1. HARD — a registered loop whose component path does not resolve, or a dark
     loop whose flag string appears nowhere in the tree (a typo'd/renamed flag).
     These are real registry bugs; they drive the exit code.
  2. dark-but-live-here — a loop the registry ships ``dark`` whose flag is
     flipped truthy in ``emptyos.toml`` on this machine (e.g. test-fix-verify
     via ``apps.dogfood-agent.fix_agent_enabled = true``). Informational: the
     frozen registry can't express per-machine state, so this surfaces it.
  3. act-without-bound — a loop that drives changes but declares no stopping
     condition. Advisory: it checks the declaration, not whether the bound is
     real, so only a human reading the code can confirm the other half.
  4. not-installed-here — a component under gitignored ``apps/personal/``.
     Absent and wrong are different: personal components are missing in every
     fresh clone, and reporting that as drift made this checker fail in any
     tree but the authoring machine's.

It deliberately does NOT try to auto-detect shipped-but-unregistered loops:
"is this feature flag a loop?" is exactly the ambiguous, high-false-positive
heuristic ``.claude/rules/audits.md`` says must not gate. That mandate stays
with the human + ``docs/AGENT-FRAMEWORK.md`` ("register every new loop").

Kernel-free: loads ``loops.py`` by path (never boots the daemon). Registered
in preflight as advisory (never gates) per the audit rule above; its own exit
code still reflects HARD errors for anyone who wants to gate manually.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from scanner_lib import emit_json  # noqa: E402


def _load_loops():
    """Import loops.py by path — pure module, no kernel boot."""
    spec = importlib.util.spec_from_file_location(
        "eos_loops_registry", REPO_ROOT / "emptyos" / "sdk" / "loops.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # frozen dataclasses look up cls.__module__ in sys.modules during class
    # creation — register before exec so @dataclass(frozen=True) resolves.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_toml() -> dict:
    try:
        return tomllib.loads((REPO_ROOT / "emptyos.toml").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _flag_truthy(cfg: dict, flag: str) -> bool:
    """Walk a full dot-path flag against the toml exactly as Config.get would."""
    node: object = cfg
    for part in flag.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return False
    return bool(node)


def _flag_needle(flag: str) -> str:
    """The substring a call site actually reads: for apps.<id>.feature.x.enabled
    → feature.x.enabled; for apps.<id>.fix_agent_enabled → fix_agent_enabled;
    for a bare kernel key → its tail. Specific enough to catch a rename/typo."""
    parts = flag.split(".")
    return ".".join(parts[parts.index("feature"):]) if "feature" in parts else parts[-1]


def _needles_present(needles: set[str]) -> set[str]:
    """Which needles appear anywhere under the source roots — ONE tree walk for
    the whole set (not one walk per flag)."""
    remaining = set(needles)
    found: set[str] = set()
    if not remaining:
        return found
    files = [p for base in ("emptyos", "apps", "plugins", "scripts")
             for p in (REPO_ROOT / base).rglob("*.py")]
    files += list((REPO_ROOT / "apps").rglob("manifest.toml"))
    for p in files:
        if not remaining:
            break
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n in list(remaining):
            if n in text:
                found.add(n)
                remaining.discard(n)
    return found


def _flag_in_code(flag: str) -> bool:
    """True when a single flag's needle appears in the tree (test/API helper)."""
    n = _flag_needle(flag)
    return n in _needles_present({n})


def audit() -> dict:
    loops = _load_loops()
    cfg = _load_toml()
    hard: list[dict] = []
    live_here: list[dict] = []

    # One tree walk for every dark flag (not one per flag).
    dark = [lp for lp in loops.all_loops() if lp.status == "dark" and lp.flag]
    present = _needles_present({_flag_needle(lp.flag) for lp in dark})

    not_installed: list[dict] = []
    for lp in loops.all_loops():
        # 1. component paths resolve — but "absent" and "wrong" are different.
        #    `apps/personal/` is gitignored, so a loop legitimately naming a
        #    personal component reports missing in every fresh clone. Calling
        #    that registry drift made the checker fail in any tree but this
        #    machine's (7 findings, all false). Absent-because-not-installed is
        #    informational; a moved or typo'd tracked path is still a hard error.
        for comp in lp.components:
            if (REPO_ROOT / comp).exists():
                continue
            if comp.startswith("apps/personal/"):
                not_installed.append({"loop": lp.id, "detail": comp})
            else:
                hard.append({"loop": lp.id, "kind": "missing-component", "detail": comp})
    for lp in dark:
        # 2. dark loop's flag appears in code
        if _flag_needle(lp.flag) not in present:
            hard.append({"loop": lp.id, "kind": "flag-not-in-code", "detail": lp.flag})
        # 3. dark-but-live-here
        if _flag_truthy(cfg, lp.flag):
            live_here.append({"loop": lp.id, "flag": lp.flag})

    # 4. a loop that ACTS must declare how it stops.
    #
    # The registry's whole claim is that it can answer "which loops cannot
    # stop?". On 2026-08-01 that query returned two loops, and BOTH were wrong:
    # fix-agent (wall-clock on the CLI subprocess AND on the verify poll) and
    # dogfood-agent (_RUN_TIMEOUT_S + idle_timeout_s) had real bounds and had
    # simply never declared them. A false positive here sends someone to build
    # a stopping rule that already exists, which is why this is worth pinning.
    #
    # Advisory, never gating: this checks the *declaration*, not that the bound
    # is real — only a human reading the code can confirm that half.
    unbounded = [
        {"loop": lp.id, "status": lp.status, "stages": ",".join(lp.stages)}
        for lp in loops.all_loops()
        if "act" in lp.stages and "bound" not in lp.stages
    ]

    return {
        "total": len(loops.all_loops()),
        "hard": hard,
        "dark_but_live_here": live_here,
        "acts_without_bound": unbounded,
        "not_installed_here": not_installed,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Loop registry ↔ reality reconciliation.")
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    args = ap.parse_args()

    data = audit()
    hard = data["hard"]
    ok = not hard
    n_live = len(data["dark_but_live_here"])
    n_unbound = len(data["acts_without_bound"])
    msg = (f"{data['total']} loops · {len(hard)} registry error(s) · "
           f"{n_live} dark-but-live-here · {n_unbound} act-without-bound · "
           f"{len(data['not_installed_here'])} component(s) not installed here")

    if args.json:
        return emit_json(ok, "registry_drift" if hard else "ok", msg, data)

    print(msg)
    for h in hard:
        print(f"  ✗ {h['loop']}: {h['kind']} — {h['detail']}", file=sys.stderr)
    for lh in data["dark_but_live_here"]:
        print(f"  · {lh['loop']}: dark in registry, ON here ({lh['flag']})")
    for u in data["acts_without_bound"]:
        print(f"  · {u['loop']}: has `act` but declares no `bound` "
              f"({u['status']}, stages={u['stages']}) — either it genuinely "
              f"cannot stop, or the bound exists in code and is undeclared")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
