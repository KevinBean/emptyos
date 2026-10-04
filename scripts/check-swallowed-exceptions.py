"""Lint for exception handlers that silently swallow real errors.

EmptyOS leans hard on a fail-soft try/except idiom, and most of it is
intentional — scanners that `continue` past a bad file, vault reads that
return `{}` on a parse error, aggregate loops that skip a day whose note
won't parse. This scanner does NOT try to flag all of that (it would fire on
every healthy app — the audits.md false-positive trap). It separates the one
genuinely-always-wrong pattern from the saturated-but-deliberate idiom:

  HIGH — `bare-except`
    `except:` with no exception type whose body just swallows. This catches
    SystemExit and KeyboardInterrupt too, so Ctrl-C / shutdown get eaten, and
    it hides every error indiscriminately. There is no benign reason for it.
    The codebase currently has ZERO of these — so the HIGH tier is a clean
    invariant: it gates against someone re-introducing the pattern. The exit
    code is the HIGH count, so this can graduate to a hard gate later.

  MEDIUM — `broad-swallow` (audit census, not a bug list)
    `except Exception:` / `except BaseException:` (or a tuple containing one)
    whose body is *exactly* a swallow (`pass`, `...`, `return`, `return None`)
    with no comment. Most are the deliberate defensive idiom; a minority hide
    real bugs. Shown count-by-file by default (use --show-medium to expand).
    A ⚠ marks swallows over a *multi-statement* try body — those are more
    suspicious, because a failure after an earlier step's side effect leaves
    inconsistent state with no trace.

  LOW — count-only (`narrow-swallow` / `loop-swallow` / `noted-swallow`)
    Specific-type swallows, skip-on-error loop bodies, and broad swallows that
    DO carry an explanatory comment. Almost always deliberate; listed so a
    curious auditor can sweep them (--show-low).

A handler whose body does any real work (logs, re-raises, returns a value,
falls back) is never flagged — "exactly a swallow statement" means a logging
call or a real return removes it from scope automatically.

Advisory: exit code is the HIGH (bare-except) count; wired into preflight as
`gate=False` for now. Run it, keep HIGH at zero, sweep MEDIUM when auditing.

Usage:
    python scripts/check-swallowed-exceptions.py
    python scripts/check-swallowed-exceptions.py apps/rooms     # subset
    python scripts/check-swallowed-exceptions.py --show-medium  # full MEDIUM list
    python scripts/check-swallowed-exceptions.py --show-low     # full LOW list
    python scripts/check-swallowed-exceptions.py --json         # machine-readable

Suppress a single handler with a trailing `# noqa: eos-swallow` on the
`except` line (optionally `# noqa: eos-swallow:broad-swallow`).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from check_base import REPO_ROOT, filter_noqa, iter_py_files

DEFAULT_ROOTS = ["apps", "plugins", "emptyos"]

# Exception names treated as "broad" — catching these means "any error".
BROAD_NAMES = {"Exception", "BaseException"}


def _type_names(t: ast.expr | None) -> list[str]:
    """Flatten an except type into the names it catches.

    `Exception` -> ['Exception']; `(KeyError, ValueError)` -> [...];
    `json.JSONDecodeError` -> ['JSONDecodeError'].
    """
    names: list[str] = []

    def collect(n: ast.expr) -> None:
        if isinstance(n, ast.Name):
            names.append(n.id)
        elif isinstance(n, ast.Attribute):
            names.append(n.attr)
        elif isinstance(n, ast.Tuple):
            for e in n.elts:
                collect(e)

    if t is not None:
        collect(t)
    return names


def _swallow_kind(body: list[ast.stmt]) -> str | None:
    """If the handler body is *exactly* a swallow statement, name it; else None.

    A body that does anything else (logs, re-raises, returns a value, has
    multiple statements) is not a swallow and returns None.
    """
    if len(body) != 1:
        return None
    s = body[0]
    if isinstance(s, ast.Pass):
        return "pass"
    if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis:
        return "..."
    if isinstance(s, ast.Return):
        if s.value is None:
            return "return"
        if isinstance(s.value, ast.Constant) and s.value.value is None:
            return "return None"
        return None  # `return <value>` is a real fallback, not a swallow
    if isinstance(s, ast.Continue):
        return "continue"
    if isinstance(s, ast.Break):
        return "break"
    return None


def _strip_strings(line: str) -> str:
    """Blank out simple quoted spans so a `#` inside a string isn't a comment."""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(" ")
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _has_comment(lines: list[str], start: int, end: int) -> bool:
    """Any `#` comment within the handler's 1-based line span [start, end]."""
    for i in range(start, end + 1):
        if 0 <= i - 1 < len(lines) and "#" in _strip_strings(lines[i - 1]):
            return True
    return False


class Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, lines: list[str]):
        self.path = path
        self.lines = lines
        self.findings: list[dict] = []

    def _report(self, rule: str, lineno: int, detail: str, multi: bool = False) -> None:
        self.findings.append(
            {
                "rule": rule,
                "file": str(self.path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "line": lineno,
                "detail": detail,
                "multi": multi,
            }
        )

    def visit_Try(self, node: ast.Try):
        multi = len(node.body) > 1  # swallow covers more than one step
        for h in node.handlers:
            self._handle(h, multi)
        self.generic_visit(node)

    def _handle(self, node: ast.ExceptHandler, multi: bool) -> None:
        kind = _swallow_kind(node.body)
        if kind is None:
            return
        end = node.end_lineno or node.lineno

        if node.type is None:
            self._report(
                "bare-except",
                node.lineno,
                f"bare `except:` body is `{kind}` — eats KeyboardInterrupt/"
                "SystemExit and hides every error; name the exceptions you expect",
            )
            return

        names = _type_names(node.type)
        is_broad = any(n in BROAD_NAMES for n in names)
        commented = _has_comment(self.lines, node.lineno, end)

        if is_broad and kind in ("continue", "break"):
            self._report("loop-swallow", node.lineno,
                         f"broad `except {'/'.join(names)}:` body is `{kind}` "
                         "(skip-on-error idiom — usually fine)")
        elif is_broad and commented:
            self._report("noted-swallow", node.lineno,
                         f"broad swallow (`{kind}`) but carries an explanatory comment")
        elif is_broad:
            mark = " over a multi-statement try" if multi else ""
            self._report("broad-swallow", node.lineno,
                         f"broad `except {'/'.join(names)}:` body is `{kind}`{mark} "
                         "with no log, re-raise, or comment", multi=multi)
        else:
            self._report("narrow-swallow", node.lineno,
                         f"`except {'/'.join(names)}:` body is `{kind}` "
                         "(specific type — usually deliberate)")


HIGH = ("bare-except",)
MEDIUM = ("broad-swallow",)
LOW = ("narrow-swallow", "loop-swallow", "noted-swallow")


def _count_by_file(hits: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for h in hits:
        out[h["file"]] = out.get(h["file"], 0) + 1
    return out


def main() -> int:
    args = sys.argv[1:]
    as_json = "--json" in args
    show_medium = "--show-medium" in args
    show_low = "--show-low" in args
    args = [a for a in args if a not in ("--json", "--show-medium", "--show-low")]
    roots = args or DEFAULT_ROOTS

    all_findings: list[dict] = []
    for f in iter_py_files(roots):
        try:
            src = f.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(f))
        except (SyntaxError, UnicodeDecodeError):
            continue
        v = Visitor(f, src.splitlines())
        v.visit(tree)
        all_findings.extend(v.findings)

    all_findings = filter_noqa(all_findings, "eos-swallow")

    if as_json:
        print(json.dumps(all_findings, indent=2))
        return sum(1 for h in all_findings if h["rule"] in HIGH)

    by_rule: dict[str, list[dict]] = {}
    for x in all_findings:
        by_rule.setdefault(x["rule"], []).append(x)

    # ── HIGH — always shown line-by-line; the gateable invariant ────────────
    print("=" * 64)
    print("HIGH — bare `except:` that swallows (always wrong; keep at zero)")
    print("=" * 64)
    high = by_rule.get("bare-except", [])
    if high:
        for h in high:
            print(f"  {h['file']}:{h['line']}  {h['detail']}")
    else:
        print("  Clean — no bare-except swallows.")

    # ── MEDIUM — broad swallows; audit census, multi-statement first ────────
    med = by_rule.get("broad-swallow", [])
    multi_n = sum(1 for h in med if h.get("multi"))
    print()
    print("=" * 64)
    print(f"MEDIUM — broad `except Exception` swallows: audit census ({len(med)}, "
          f"{multi_n} over multi-statement try)")
    print("=" * 64)
    print("  (mostly the deliberate fail-soft idiom; ⚠ = covers >1 step, more suspect)")
    if med:
        if show_medium:
            for h in sorted(med, key=lambda x: (not x.get("multi"), x["file"], x["line"])):
                flag = "⚠ " if h.get("multi") else "  "
                print(f"  {flag}{h['file']}:{h['line']}  {h['detail']}")
        else:
            bf = _count_by_file(med)
            mf = _count_by_file([h for h in med if h.get("multi")])
            for path, n in sorted(bf.items(), key=lambda kv: -kv[1])[:12]:
                m = mf.get(path, 0)
                print(f"  {n:>3} ({m}⚠) {path}")
            if len(bf) > 12:
                print(f"  ... +{len(bf) - 12} more files  (use --show-medium for all)")

    # ── LOW — idiom census, count-only ──────────────────────────────────────
    print()
    print("=" * 64)
    print("LOW — narrow / loop / commented swallows (usually deliberate)")
    print("=" * 64)
    low_total = 0
    for rule in LOW:
        hits = by_rule.get(rule, [])
        if not hits:
            continue
        low_total += len(hits)
        bf = _count_by_file(hits)
        print(f"\n{rule} ({len(hits)} total, {len(bf)} files):")
        if show_low:
            for h in hits:
                print(f"  {h['file']}:{h['line']}  {h['detail']}")
        else:
            for path, n in sorted(bf.items(), key=lambda kv: -kv[1])[:8]:
                print(f"  {n:>3}  {path}")
            if len(bf) > 8:
                print(f"  ... +{len(bf) - 8} more files")
    if low_total == 0:
        print("\n  None.")

    print()
    print("Suppress per-handler with: # noqa: eos-swallow[:rule-id] on the except line")
    print(f"Total: {len(all_findings)} findings  ·  HIGH {len(high)} · "
          f"MEDIUM {len(med)} · LOW {low_total}")
    return len(high)


if __name__ == "__main__":
    sys.exit(main())
