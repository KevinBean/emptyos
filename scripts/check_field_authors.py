"""Find behaviour-driving fields that nothing can actually set.

The recurring EmptyOS defect is a mechanism built correctly and never reached.
`scripts/check_dark_flags.py` covers the half that hides behind a flag. This
covers the other half: a field an app *reads* to choose behaviour, which no
prompt, skill, page, or doc ever *writes*. The branch is live, the tests pass,
and the feature is unreachable because nobody can ask for it.

Found by hand on 2026-07-28: `atmosphere_subject` selects what the
layered-atmosphere layer renders, and nothing authored it — so every atmosphere
shot in every song fell back to the same generic haze. Its sibling
`atmosphere_opacity` was pinned at its default for the same reason.

**Scope is deliberately narrow, because a broad version is noise.** Only fields
an app *declares* as director-editable are considered — a module-level tuple or
set whose name matches `*EDITABLE*` / `*_FIELDS` / `SETTABLE_*`. Declaring a
field editable is a promise that something can edit it, so a declared field
with no author is a broken promise rather than a guess about intent. An earlier
attempt that scanned every `.get("x")` in the tree, and a sibling that scanned
event wiring, both fired on healthy code and were discarded (`.claude/rules/audits.md`).

Two severities, and only one gates:

    no-author      (gate)     nothing anywhere writes it — unreachable
    code-only   (advisory)    machinery writes it internally, but the human or
                              prompt the declaration promises cannot

Calibration on music-studio's 31 declared fields: 27 clean, 0 false positives,
4 flagged — one no-author, three code-only.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

DECL_NAME = re.compile(r"(EDITABLE|_FIELDS$|_KEYS$|^SETTABLE_)", re.I)

# Where a human, a prompt, or a UI could legitimately author a field.
AUTHOR_GLOBS = (
    "apps/**/prompts.py",
    "skills/**/*.md",
    ".claude/skills/**/*.md",
    ".agents/skills/**/*.md",
    "apps/**/pages/*.js",
    "apps/**/pages/*.html",
    "docs/*.md",
)

# Opt-out at the call site, with the reason next to the code it excuses — an
# inline marker beats a central allowlist, which turns every new legitimate
# case into a build break (`.claude/rules/audits.md`).
#
#   # field-authors: ignore                  -> the whole file
#   # field-authors: ignore body_weight ...  -> only the named fields
#
# The named form exists for ALIAS SETS, which are the checker's main blind
# spot: earthing's `_TOLERABLE_FIELDS` lists several accepted spellings of one
# parameter, so the aliases look unauthored while the canonical name is wired
# to the UI. Silencing a whole engineering app to excuse three aliases would
# hide any real finding in it later.
IGNORE_RE = re.compile(r"#\s*field-authors:\s*ignore([^\n]*)")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def declared_fields(tree: ast.AST) -> dict[str, set[str]]:
    """`NAME = (...)` / `{...}` of string literals, by variable, at any scope.

    Function-local matters: music-studio declares its director-editable scene
    fields inside a merge helper, and a module-level-only walk missed the case
    this check was written for.
    """
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or not DECL_NAME.search(target.id):
                continue
            vals = {
                el.value
                for el in getattr(node.value, "elts", [])
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }
            if len(vals) >= 3:          # a one-off pair is not a declaration
                out.setdefault(target.id, set()).update(vals)
    return out


def audit(root: Path) -> list[dict]:
    author_text = "\n".join(
        _read(p) for g in AUTHOR_GLOBS for p in root.glob(g)
        if "_retired" not in str(p)
    )
    findings: list[dict] = []
    for app_dir in sorted({p.parent for p in root.glob("apps/**/manifest.toml")}):
        if "_retired" in str(app_dir):
            continue
        pys = list(app_dir.glob("*.py"))
        if not pys:
            continue
        code = "\n".join(_read(p) for p in pys)
        ignored: set[str] = set()
        skip_app = False
        for m in IGNORE_RE.finditer(code):
            names = re.findall(r"[a-z_][a-z0-9_]*", m.group(1) or "")
            if names:
                ignored.update(names)
            else:
                skip_app = True
        if skip_app:
            continue
        for py in pys:
            src = _read(py)
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for var, fields in declared_fields(tree).items():
                # A declaration iterated into a dynamic settings/config lookup
                # (`for f in VAR: svc.get(f"publish.{f}")`) is authored by the
                # user at runtime through /settings or emptyos.toml. Those keys
                # will never appear in a prompt or page, so scanning for one is
                # measuring the wrong surface — publish's _SITE_FIELDS produced
                # 12 such false positives before this check existed.
                # The loop variable must actually be interpolated into the
                # lookup. Merely containing `app_config(` is not evidence —
                # nearly every app does, and an earlier version of this check
                # used that and silently suppressed the very findings it was
                # written for.
                loops = re.findall(rf"for\s+(\w+)\s+in\s+{re.escape(var)}\b", code)
                if any(
                    re.search(rf'\.get\(\s*f["\'][^"\']*\{{{lv}\}}', code)
                    for lv in loops
                ):
                    continue
                for f in sorted(fields):
                    if f in ignored:
                        continue
                    # Only fields that actually steer behaviour somewhere.
                    if not re.search(rf'\.get\(\s*["\']{re.escape(f)}["\']', code):
                        continue
                    authored = len(re.findall(rf"\b{re.escape(f)}\b", author_text))
                    written = len(
                        re.findall(rf'\[\s*["\']{re.escape(f)}["\']\s*\]\s*=', code)
                    ) + len(re.findall(rf'["\']{re.escape(f)}["\']\s*:\s*\S', code))
                    if authored:
                        continue
                    findings.append({
                        "app": app_dir.name,
                        "declaration": f"{py.name}:{var}",
                        "field": f,
                        "severity": "no-author" if not written else "code-only",
                        "detail": (
                            "nothing anywhere writes it — the branch is unreachable"
                            if not written else
                            f"written only by app code ({written}x); the human or "
                            f"prompt the declaration promises cannot set it"
                        ),
                    })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    findings = audit(Path(args.root).resolve())
    gating = [f for f in findings if f["severity"] == "no-author"]

    if args.json:
        print(json.dumps({"ok": not gating, "findings": findings}, indent=2))
    elif not findings:
        print("OK: every declared editable field has an author")
    else:
        for f in findings:
            tag = "FAIL" if f["severity"] == "no-author" else "warn"
            print(f"  {tag}  {f['app']}/{f['declaration']}  {f['field']}")
            print(f"        {f['detail']}")
        print(f"\n{len(gating)} unreachable, "
              f"{len(findings) - len(gating)} advisory")
    return 1 if gating else 0


if __name__ == "__main__":
    sys.exit(main())
