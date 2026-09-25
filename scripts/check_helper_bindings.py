#!/usr/bin/env python3
"""Every bound helper an app calls via ``self.X`` must be bound in its spine.

Multi-module apps (`.claude/rules/multi-module-apps.md`) define helpers at
module level taking ``self``, then re-bind them in the app class body:

    api_figure = _figures.api_figure

Forget the binding line and the call raises AttributeError — but only when
that branch runs. Several call sites in the codebase sit inside
``except Exception``, so a missing binding degrades *silently* rather than
failing loudly. That is not hypothetical: it shipped in publish
(`_set_post_frontmatter_field_locked`, `_rewrite_post_locked`), which is why
`tests/test_unit_publish_media_bindings.py` exists as a per-app pin. This is
the repo-wide generalisation.

**The discriminator is the call site, not the signature.** Two narrower rules
were measured and rejected against the real tree:

* "every module-level def must be bound" — 84 of 235 modules flagged (36%).
  Almost all were plain module functions (`_parse_pytest_failures`) that are
  not methods at all.
* "every def taking ``self`` must be bound" — still 22 modules (9%). Taking
  ``self`` is not the same as being *called* on it: a module-private helper is
  legitimately invoked as ``_helper(self, ...)`` from inside its own module,
  and needs no binding (`dictionary/reading.py` does this ~24 times).

Requiring BOTH — takes ``self`` AND is invoked somewhere as ``self.NAME(`` —
gives 0 findings on a healthy tree. But that rule alone is *vacuous for the
most important case*: a ``@web_route`` handler is never called as
``self.api_x(``; the framework reaches it through the class attribute. Deleting
a route's binding line therefore passed the self-call rule cleanly while the
endpoint silently stopped existing. So a registration decorator
(``@web_route`` / ``@cli_command`` / ``@on_event`` / ``@scheduled``) is a
second, independent reason a helper must be bound — the decorator stamps
metadata on the function object, and that metadata is only ever read off the
class.

Final rule: takes ``self`` AND (called via ``self.NAME(`` OR carries a
registration decorator) AND not bound. **0 findings across 235 helper modules
on a healthy tree, and red when any binding is removed** — both directions
pinned in `tests/test_unit_check_helper_bindings.py`.

**The inverse direction — a binding pointing at a name that does not exist**
(added 2026-08-30) — is the other half, and the more destructive one. An
unbound helper silently disables one endpoint; a *dangling* binding raises
AttributeError while the class body executes, so the app never loads at all,
and every app naming it in ``[requires].apps`` fails with it.

That is not hypothetical either: a rename reached ``english/events.py`` and
never reached its spine, taking down english, briefing, hub-life and tracker
for two days **while this checker was green**, because it only ever looked the
other way. The failure surfaced as ``module 'eos_apps.english.events' has no
attribute 'on_reader'`` in syslog, nowhere near a commit.

A binding is dangling when the spine's ``name = _alias.attr`` names a *sibling
module of the same app* that does not export ``attr``. Three things count as
exported besides the module's own defs, and each is an FP guard with a test:
re-exported imports (``from .shared import slugify`` really does expose
``_helper.slugify``), conditionally-defined names (optional-dependency
try/except shims), and — as a skip rather than a pass — modules with
``__getattr__`` or a star-import, where the namespace is not statically
knowable. Aliases that are not local modules (``_json.dumps``) are ignored: the
checker can only speak about files it can read. 0 findings across 274 modules.

**The third direction — the banner itself** (added 2026-08-30, from the same
incident). Each helper carries a comment listing every name it exports back to
the spine; that banner is the convention's own drift-prevention device, and
nothing validated it. After the rename above, english's banner still advertised
both renamed rows (``on_reader`` / ``on_reader_add``, now ``on_highlight_review``
/ ``on_highlight_add``), so the comment that exists to prevent the outage was
describing it — and the one repair it invited, restoring those bindings, is the
outage.

Only one of its three shapes gates. A banner row that is neither bound nor
defined (``hazardous``) is the residue a half-applied rename leaves, and an
invitation to "restore" a binding that would not load — **0 across the 2559
scored banner rows** (of 2587 in 271 modules), so it can gate.
Advertised-but-still-defined (5) and bound-but-undocumented (62) are advisory
— not because they are noise, but because neither can break anything: the
binding is re-addable in one case and merely undocumented in the other, so
gating would block a commit over a comment. They are true findings held below
the gate, which is a different thing from a false-positive band.

The one skip is a row qualified by a CLASS defined in the helper —
one engineering app writes ``read_spec = _LibraryMixin.read_spec``, a mixin whose
members are not module-level names, and scoring those 28 rows reported every
one of a working app's banner entries as hazardous. It is deliberately not
keyed on "the qualifier matches the spine's import name": banners legitimately
spell it differently (`rooms/chat.py` documents ``_chat`` while the spine
imports ``_chat_mod``, the collision convention
`.claude/rules/multi-module-apps.md` § 7 mandates), and keying on the name
suppressed 84 rows across 12 modules — blinding the gate on the modules that
followed the rule.

Two measurement traps this direction walked into, both worth knowing before
trusting a count here. ``staticmethod(_m.x)`` is an ``ast.Call``, so reading a
binding's value directly makes every descriptor-wrapped helper invisible; that
exempted them from the *dangling* gate too, and reported 13 correctly-bound
names as drift. And the advisory count reaches an operator only on a direct
run: preflight shows one summary line for a passing check, so on a green tree
the count is visible via ``--json`` or by invoking this script, not from
``/preflight``. ``--banner-detail`` lists the rows.

Static AST; no kernel, no daemon. Personal apps are included: they are exactly
where a broken binding is least likely to be caught by a test — and are where
the live incident happened.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scanner_lib import emit_json  # noqa: E402

from emptyos.sdk.app_layout import iter_app_dirs  # noqa: E402

# The banner every decomposed helper module carries. Using it as the gate keeps
# the scan to modules that have opted into the pattern, rather than every .py
# that happens to sit beside an app.py.
BIND_BANNER = re.compile(r"Bind to \w+ class as", re.I)
IGNORE = re.compile(r"helper-bindings:\s*ignore\b", re.I)

# Decorators that register a method with the platform. They stamp metadata on
# the function object, and the loader reads that metadata off the CLASS — so an
# unbound decorated helper is a route/command/handler that silently never
# exists. These are never called as `self.name(`, which is why the self-call
# rule alone cannot see them.
REGISTRATION_DECORATORS = frozenset({
    "web_route", "cli_command", "on_event", "scheduled", "cron",
})

# One row of a binding banner: `#   api_list   = _feature.api_list`. Each part
# must be identifier-shaped — a bare `\w+` also matches digits, so a formula in
# a comment (`# kappa = 1.02 + ...`) parses as name=kappa, alias=1, attr=02.
BANNER_ROW = re.compile(r"#\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)")


def _rel(path: Path) -> str:
    """Repo-relative display path, tolerating a root outside the repo.

    `scan()` takes an apps_root argument, so tests point it at a tmp tree —
    `relative_to(ROOT)` then raises. Report the absolute path rather than
    crashing on a path the scanner was legitimately asked to look at.
    """
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def self_taking_defs(tree: ast.Module) -> set[str]:
    """Module-level defs whose first parameter is ``self``."""
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("__"):
            continue
        args = node.args.posonlyargs + node.args.args
        if args and args[0].arg == "self":
            out.add(node.name)
    return out


def call_app_methods(apps_root: Path) -> set[str]:
    """Method names any app invokes via ``call_app(<app>, "<method>")``.

    A third reason a helper must be bound, independent of the other two: a
    public verb reached cross-app is never called as ``self.name(`` in its own
    app and carries no decorator, yet ``call_app`` resolves it on the instance
    — so an unbound one fails at dispatch. `kb.attach_figure` is exactly this
    shape (viz calls it; kb never does).

    The app argument is often dynamic (``call_app(dest["app"], "verb")``), so
    attribution to a specific app is unreliable; the method NAME is collected
    globally instead and only matters when an app actually defines a helper of
    that name. Measured at 0 findings on a healthy tree, so the looseness costs
    nothing today.
    """
    out: set[str] = set()
    for path in apps_root.rglob("*.py"):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "call_app" or len(node.args) < 2:
                continue
            method = node.args[1]
            if isinstance(method, ast.Constant) and isinstance(method.value, str):
                out.add(method.value)
    return out


def _decorator_name(node: ast.expr) -> str:
    """Bare name of a decorator, whether or not it is called or dotted."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def registered_defs(tree: ast.Module) -> set[str]:
    """Module-level defs carrying a platform-registration decorator."""
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(_decorator_name(d) in REGISTRATION_DECORATORS for d in node.decorator_list):
            out.add(node.name)
    return out


def class_bound_names(tree: ast.Module) -> set[str]:
    """Names assigned or defined in any class body of the spine."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        out.add(target.id)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(stmt.name)
    return out


def local_module_aliases(tree: ast.Module, app_dir: Path) -> dict[str, Path]:
    """`from . import events as _events` -> {"_events": <app_dir>/events.py}.

    Only sibling modules of this app resolve; anything else (SDK, stdlib, a
    subpackage) is skipped, because this check can only speak about files it
    can read.
    """
    out: dict[str, Path] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        # `from . import x` — level 1, no module. A deeper relative import or a
        # `from .mod import name` form is not an alias-to-module binding.
        if node.level != 1 or node.module:
            continue
        for alias in node.names:
            candidate = app_dir / f"{alias.name}.py"
            if candidate.is_file():
                out[alias.asname or alias.name] = candidate
    return out


def module_level_names(tree: ast.Module) -> set[str] | None:
    """Every attribute `module.X` could legitimately resolve to.

    Includes re-exports: a helper doing `from .shared import slugify` really
    does expose `_helper.slugify`, so counting only its own defs would fire
    falsely. Returns None when the module defines `__getattr__`, i.e. attribute
    access is dynamic and nothing static can be concluded.
    """
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "__getattr__":
                return None
            out.add(node.name)
        elif isinstance(node, ast.ClassDef):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    # A star-import makes the namespace unknowable statically.
                    return None
                out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            # Conditionally-defined names (TYPE_CHECKING guards, optional deps)
            # still exist at runtime on the branch that ran; collect both.
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    out.add(sub.name)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        if alias.name != "*":
                            out.add(alias.asname or alias.name.split(".")[0])
    return out


def _binding_target(value: ast.expr) -> ast.Attribute | None:
    """The `_alias.attr` in a class-body binding, unwrapping the descriptor form.

    `_now = staticmethod(_library._LibraryMixin._now)` is an ast.Call, not an
    ast.Attribute, so reading the value directly makes every staticmethod-bound
    helper invisible. That is not cosmetic: it silently exempted them from the
    dangling gate, and reported 13 correctly-bound names as banner drift.
    """
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in ("staticmethod", "classmethod")
        and value.args
    ):
        value = value.args[0]
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        return value
    return None


def class_alias_bindings(tree: ast.Module) -> list[tuple[str, str, str, int]]:
    """Class-body `name = _alias.attr` bindings as (name, alias, attr, lineno).

    The bound NAME matters as well as the target: the banner check compares
    what a helper's banner advertises against the names the spine actually
    binds from that helper. Every Name target is emitted, so a chained
    `a = b = _m.x` records both rather than dropping one.
    """
    out: list[tuple[str, str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            target = _binding_target(stmt.value)
            if target is None:
                continue
            for name in (t.id for t in stmt.targets if isinstance(t, ast.Name)):
                out.append((name, target.value.id, target.attr, stmt.lineno))
    return out


def helper_class_names(tree: ast.Module) -> set[str]:
    """Classes defined at module level in a helper.

    A banner may document a mixin — `read_spec = _LibraryMixin.read_spec` — where
    the qualifier is a class inside the helper rather than the spine's module
    alias. Those rows address a namespace this check does not model.
    """
    return {n.name for n in tree.body if isinstance(n, ast.ClassDef)}


def self_attr_names(trees: list[ast.Module]) -> set[str]:
    """Attribute names read off ``self`` anywhere in the app."""
    out: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                out.add(node.attr)
    return out


def dangling_bindings(spine: ast.Module, spine_path: Path, app_dir: Path) -> list[dict]:
    """Spine bindings pointing at a name the helper module does not define.

    The inverse of the unbound-helper check, and the more destructive of the
    two directions: an unbound helper silently disables one endpoint, while a
    dangling binding raises AttributeError while the class body executes, so
    the app fails to LOAD — and every app declaring it in `[requires].apps`
    fails with it. Found live 2026-08-30, where a rename applied to
    `english/events.py` never reached the spine and took down english,
    briefing, hub-life and tracker for two days while this checker was green.
    """
    aliases = local_module_aliases(spine, app_dir)
    if not aliases:
        return []
    exported: dict[str, set[str] | None] = {}
    out: list[dict] = []
    for _name, alias, attr, lineno in class_alias_bindings(spine):
        helper_path = aliases.get(alias)
        if helper_path is None:
            continue
        if alias not in exported:
            tree = _parse(helper_path)
            exported[alias] = module_level_names(tree) if tree is not None else None
        names = exported[alias]
        if names is None:          # dynamic namespace — nothing provable
            continue
        if attr not in names:
            out.append({
                "spine": _rel(spine_path),
                "line": lineno,
                "binding": f"{alias}.{attr}",
                "helper": _rel(helper_path),
            })
    return out


def banner_entries(text: str) -> list[tuple[str, str, str, int]]:
    """Binding-banner rows as (name, alias, attr, lineno).

    Only the comment block introduced by the banner header is read, and it ends
    at the first non-comment line — a `#   x = _m.x` written anywhere else in
    the file is prose, not a declaration.
    """
    out: list[tuple[str, str, str, int]] = []
    started = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if not started:
            started = bool(BIND_BANNER.search(line))
            continue
        if not line.lstrip().startswith("#"):
            break
        match = BANNER_ROW.match(line.lstrip())
        if match:
            out.append((match.group(1), match.group(2), match.group(3), lineno))
    return out


def banner_drift(
    spine: ast.Module,
    spine_path: Path,
    app_dir: Path,
    helper_path: Path,
    helper_tree: ast.Module,
    helper_text: str,
) -> list[dict]:
    """Banner rows that disagree with the bindings the spine actually has.

    The banner is the convention's own drift-prevention device
    (`.claude/rules/multi-module-apps.md`): it exists so a reader adding a
    method sees that a matching binding line is required. Nothing validated the
    banner itself, so it could rot into advertising the opposite.

    Three shapes, and only the first gates.

    ``hazardous`` — advertised, not bound, AND the helper no longer defines it.
    This is the residue a rename leaves when it reaches the helper and the
    spine but not the comment, and it is an active invitation to "restore" the
    missing binding — which re-creates the dangling binding that took english,
    briefing, hub-life and tracker down for two days on 2026-08-30. Measured 0
    across 2559 scored rows once english was corrected, so it can gate.

    ``stale`` — advertised, not bound, but still defined. Re-adding the binding
    would work, so this is documentation drift and not a trap. 5 on a healthy
    tree.

    ``missing`` — bound from this helper, absent from the banner. The banner
    under-documents; nothing breaks. 62 on a healthy tree.

    The latter two are advisory because neither can stop an app loading — true
    findings held below the gate, which is not the same as a signal too noisy
    to trust.
    """
    rows = banner_entries(helper_text)
    if not rows:
        return []
    # Aliases in this spine that resolve to THIS helper file.
    mine = {a for a, p in local_module_aliases(spine, app_dir).items() if p == helper_path}
    bound = {
        name
        for name, alias, _attr, _lineno in class_alias_bindings(spine)
        if alias in mine
    }
    defined = module_level_names(helper_tree)
    classes = helper_class_names(helper_tree)
    out: list[dict] = []
    for name, alias, attr, lineno in rows:
        # Skip only a row addressing a CLASS inside this helper —
        # one engineering app writes `read_spec = _LibraryMixin.read_spec`, whose
        # members are not module-level names, so scoring those reported all 28
        # rows of a working app as hazardous.
        #
        # Deliberately NOT keyed on "the alias matches the spine's import name":
        # the banner is prose living in the helper, and its qualifier is often
        # spelled differently on purpose — `rooms/chat.py` banners `_chat` while
        # the spine imports `_chat_mod`, the collision convention
        # `.claude/rules/multi-module-apps.md` § 7 mandates. Matching on the name
        # suppressed 84 rows across 12 modules, blinding the gate on exactly the
        # modules that followed the rule.
        if alias in classes:
            continue
        if name in bound:
            continue
        hazardous = defined is not None and attr not in defined
        out.append({
            "kind": "hazardous" if hazardous else "stale",
            "helper": _rel(helper_path),
            "line": lineno,
            "name": name,
            "spine": _rel(spine_path),
        })
    declared = {name for name, alias, _attr, _lineno in rows if alias not in classes}
    for name in sorted(bound - declared):
        out.append({
            "kind": "missing",
            "helper": _rel(helper_path),
            "line": 0,
            "name": name,
            "spine": _rel(spine_path),
        })
    return out


def scan(apps_root: Path) -> tuple[int, list[dict], list[dict], list[dict]]:
    scanned = 0
    findings: list[dict] = []
    dangling: list[dict] = []
    banners: list[dict] = []
    rpc = call_app_methods(apps_root)
    for _app_id, app_dir in iter_app_dirs(apps_root, include_personal=True):
        spine_path = app_dir / "app.py"
        spine = _parse(spine_path)
        if spine is None:
            continue
        py_files = sorted(app_dir.glob("*.py"))
        trees = [t for t in (_parse(p) for p in py_files) if t is not None]
        used = self_attr_names(trees)
        bound = class_bound_names(spine)
        # One marker, both spine-level gates: an app that opted out of the
        # binding check must not still be hard-gated by a banner row.
        spine_opted_out = bool(
            IGNORE.search(spine_path.read_text(encoding="utf-8", errors="replace"))
        )
        if not spine_opted_out:
            dangling.extend(dangling_bindings(spine, spine_path, app_dir))

        for helper_path in py_files:
            if helper_path.name in ("app.py", "__init__.py"):
                continue
            text = helper_path.read_text(encoding="utf-8", errors="replace")
            if not BIND_BANNER.search(text) or IGNORE.search(text):
                continue
            tree = _parse(helper_path)
            if tree is None:
                continue
            scanned += 1
            if not spine_opted_out:
                banners.extend(
                    banner_drift(spine, spine_path, app_dir, helper_path, tree, text)
                )
            must_bind = self_taking_defs(tree) & (used | registered_defs(tree) | rpc)
            missing = sorted(must_bind - bound)
            if missing:
                findings.append({
                    "helper": _rel(helper_path),
                    "spine": _rel(spine_path),
                    "unbound": missing,
                })
    return scanned, findings, dangling, banners


def _report_advisory(advisory: list[dict], detail: bool) -> None:
    """One summary line for banner drift that cannot break anything.

    Printed even on a pass, because the whole point is that the banner is
    documentation nobody was checking. Never affects the exit code — these are
    true findings that cannot stop an app loading, so they are held below the
    gate rather than blocking a commit over a comment.

    Note the reach: preflight prints one summary line for a passing check, so on
    a green tree this count is seen by a direct run or via ``--json``, not from
    ``/preflight``.
    """
    if not advisory:
        return
    stale = sum(1 for b in advisory if b["kind"] == "stale")
    missing = len(advisory) - stale
    modules = len({b["helper"] for b in advisory})
    print(f"  advisory: banner drift in {modules} module(s) — "
          f"{stale} advertised-but-unbound, {missing} bound-but-undocumented"
          f"{'' if detail else '  (--banner-detail to list)'}")
    if not detail:
        return
    for b in sorted(advisory, key=lambda x: (x["helper"], x["kind"], x["name"])):
        where = f":{b['line']}" if b["line"] else ""
        note = "not bound" if b["kind"] == "stale" else "not in banner"
        print(f"    {b['helper']}{where}  {b['name']} ({note})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    ap.add_argument(
        "--banner-detail",
        action="store_true",
        help="list the advisory banner drift instead of only counting it",
    )
    args = ap.parse_args()

    scanned, findings, dangling, banners = scan(ROOT / "apps")
    hazardous = [b for b in banners if b["kind"] == "hazardous"]
    advisory = [b for b in banners if b["kind"] != "hazardous"]
    ok = not findings and not dangling and not hazardous
    if args.json:
        code = "ok"
        if dangling:
            code = "dangling_bindings"
        elif hazardous:
            code = "hazardous_banner"
        elif findings:
            code = "unbound_helpers"
        return emit_json(
            ok,
            code,
            f"{len(dangling)} dangling binding(s), {len(hazardous)} hazardous banner "
            f"row(s), {len(findings)} helper module(s) with unbound methods"
            if not ok else f"{scanned} helper modules, bindings resolve both ways",
            {
                "scanned": scanned,
                "findings": findings,
                "dangling": dangling,
                "banners": banners,
            },
        )

    # Before the verdict, never after: preflight reads the LAST stdout line as
    # the check's summary and hides detail for a passing check, so an advisory
    # printed last would replace "OK" on the row with a finding-shaped line.
    _report_advisory(advisory, args.banner_detail)

    if ok:
        print(f"check-helper-bindings: OK — {scanned} helper modules, "
              f"every self-called helper is bound and every binding resolves")
        return 0

    # Dangling first: it is the direction that stops an app booting.
    if dangling:
        print(f"check-helper-bindings: {len(dangling)} spine binding(s) point at a name "
              f"the helper module does not define\n")
        for d in dangling:
            print(f"  {d['spine']}:{d['line']}  ->  {d['binding']}")
            print(f"    {d['helper']} defines no `{d['binding'].split('.', 1)[1]}`")
            print(f"    fix: rename the binding to the helper's current name, or "
                  f"delete it if the handler is gone.\n")
    if findings:
        print(f"check-helper-bindings: {len(findings)} helper module(s) define a method "
              f"called via self.X but never bound in the spine\n")
        for f in findings:
            print(f"  {f['helper']}")
            print(f"    unbound: {', '.join(f['unbound'])}")
            print(f"    fix: add `<name> = _<module>.<name>` in the class body of "
                  f"{f['spine']}\n")
    if hazardous:
        print(f"check-helper-bindings: {len(hazardous)} binding-banner row(s) advertise "
              f"a name that is neither bound nor defined\n")
        for b in hazardous:
            print(f"  {b['helper']}:{b['line']}  ->  {b['name']}")
            print(f"    {b['spine']} does not bind it, and the helper no longer "
                  f"defines it")
            print("    fix: update the banner row to the current name — restoring "
                  "the binding instead would not load.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
