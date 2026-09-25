#!/usr/bin/env python3
"""Check `[provides.report]` declarations against what the apps actually do.

Contract: `.claude/rules/app-reports.md`.

**There is deliberately no path-based detector here.** Measured 2026-09-01, 29
routes repo-wide match `report` in their URL and 19 of them are something else
entirely — monthly summary notes, vault-health scans, telemetry views, an audit
result, and the 13 routes of the `reports` authoring app. That is a 66% false
positive rate, well past the band `.claude/rules/audits.md` says never to ship.
So every rule below keys on the *declaration* or on an *exact call*, never on a
guess about what a URL means.

Four rules:

  R1  A declaration is well-formed (kind, method, formats in the supported set).
  R2  `method` resolves to something the app defines — the class body, a
      module-level def in a helper module, or a helper binding. Same failure
      shape as `check_helper_bindings.py`: the manifest looks right, and the
      route 500s at runtime.
  R3  `kind = "calculation-sheet"` carries a `spec` whose file exists. The
      ten-heading contract itself stays with `check_engineering_assurance.py`;
      this only catches a spec that was declared and never written.
  R4  Declaration and implementation agree in both directions — an app calling
      `report_response` declares `[provides.report]`, and one that declares it
      calls it. Exact signal, no heuristic.

Advisory (never gates) while adoption is partial: the nine pre-existing report
routes have not migrated yet, so R4's "calls but does not declare" half is
expected to be quiet and its "declares but does not call" half only fires on
work in progress.
"""
from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

VALID_KINDS = {"calculation-sheet", "study-record"}
VALID_FORMATS = {"md", "pdf"}
SPEC_REQUIRED_KINDS = {"calculation-sheet"}


def _defined_names(app_dir: Path) -> set[str]:
    """Every name the app could answer to — class methods, module-level defs,
    and helper bindings (`name = _mod.name`, the multi-module-apps pattern)."""
    names: set[str] = set()
    for py in app_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                # `api_report = _reporting.api_report` — the binding line.
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def _calls_report_response(app_dir: Path) -> bool:
    for py in app_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if "report_response" in py.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def scan(root: Path = ROOT) -> list[dict]:
    findings: list[dict] = []
    for manifest in sorted((root / "apps").rglob("manifest.toml")):
        app_dir = manifest.parent
        rel = manifest.relative_to(root).as_posix()
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            continue

        app_id = (data.get("app") or {}).get("id") or app_dir.name
        decl = (data.get("provides") or {}).get("report")
        calls = _calls_report_response(app_dir)

        if decl is None:
            # R4, one direction: implements the shape without declaring it, so
            # nothing can enumerate it.
            if calls:
                findings.append({
                    "app": app_id, "file": rel, "rule": "R4",
                    "detail": "calls report_response but declares no [provides.report]",
                })
            continue

        if not isinstance(decl, dict):
            findings.append({"app": app_id, "file": rel, "rule": "R1",
                             "detail": "[provides.report] is not a table"})
            continue

        kind = decl.get("kind")
        if kind not in VALID_KINDS:
            findings.append({
                "app": app_id, "file": rel, "rule": "R1",
                "detail": f"kind {kind!r} not in {sorted(VALID_KINDS)}",
            })

        method = decl.get("method")
        if not isinstance(method, str) or not method.strip():
            findings.append({"app": app_id, "file": rel, "rule": "R1",
                             "detail": "no method declared"})
        elif method not in _defined_names(app_dir):
            # R2 — the manifest names something the app does not define.
            findings.append({
                "app": app_id, "file": rel, "rule": "R2",
                "detail": f"method {method!r} is not defined or bound in {app_dir.name}/",
            })

        formats = decl.get("formats") or ["md"]
        if not isinstance(formats, list) or not set(formats) <= VALID_FORMATS:
            findings.append({
                "app": app_id, "file": rel, "rule": "R1",
                "detail": f"formats {formats!r} outside {sorted(VALID_FORMATS)}",
            })

        if kind in SPEC_REQUIRED_KINDS:
            spec = decl.get("spec")
            if not isinstance(spec, str) or not spec.strip():
                findings.append({
                    "app": app_id, "file": rel, "rule": "R3",
                    "detail": f"kind={kind} requires a spec (REPORT-SPEC.md)",
                })
            elif not (app_dir / spec).is_file():
                findings.append({
                    "app": app_id, "file": rel, "rule": "R3",
                    "detail": f"spec {spec!r} declared but the file is missing",
                })

        if not calls:
            findings.append({
                "app": app_id, "file": rel, "rule": "R4",
                "detail": "declares [provides.report] but never calls report_response",
            })

    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    findings = scan()
    ok = not findings
    msg = "no report-declaration drift" if ok else f"{len(findings)} finding(s)"

    if args.json:
        return emit_json(ok, "ok" if ok else "drift", msg, {"findings": findings})

    for f in findings:
        print(f"{f['rule']}  {f['app']:<22} {f['detail']}")
        print(f"      {f['file']}")
    print(msg)
    # Advisory while adoption is partial — see the module docstring.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
