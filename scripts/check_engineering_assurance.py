#!/usr/bin/env python3
"""Validate Trust Loop assurance packages without booting EmptyOS.

Apps opt in through manifest [assurance]. This scanner checks the
machine-readable declaration, controlled-document contract, specification to
implementation traceability, calculator surfaces, and test-file presence.

It deliberately does not infer engineering correctness. A structurally complete
package may remain a demonstration, and promotion to verified/released requires
current generated receipts.

## Two rules ported in from a second implementation (2026-08-13)

`D:/prelim-sizing` built a calculator to this loop and — not knowing this file
existed — wrote its own checker. Most of that divergence was drift and has been
resolved in its favour of this one. Two of its rules were genuinely ahead, both
closing a hole where a check passed *vacuously*:

**A traced id needs a table row, not a mention.** `algorithm_ids -
implementation_ids` is satisfied by an id appearing anywhere in the document,
including inside a sentence saying "not implemented". At ~70 ids that degrades
from evidence to ritual. An id now counts as traced only inside a markdown row
carrying three non-empty cells (id · code symbol · test), which keeps review a
scan-for-blanks rather than a reading-comprehension exercise. Measured before
porting: `trust-loop` scores 16/16 under the strict rule, unchanged.

**A required heading needs content under it.** Heading presence was a substring
test against the whole document, so a document of nothing but its `##` lines
satisfied the contract. Measured before porting: 0 empty required sections in
`trust-loop`.

`UX-*` also joins the id grammar so interface requirements can be traced like
any other.

## Three vacuity guards (2026-08-13)

Stating `UX-*` ids was held to be optional here, on the reasoning that
`trust-loop` stated none and a rule firing on the only healthy package is noise
(`.claude/rules/audits.md`). That reasoning was sound and its premise has since
gone: `trust-loop` states them, so `no-ux-ids` no longer fires on a healthy
package. It fires on a package whose Stage 5 carries no receipt at all — and the
join above cannot catch that, because `stated_ids - traced` over an empty
`ux_ids` is empty and reports full marks.

That failure shape is the same one three rules now close, and it is worth naming
once: **a set difference over an empty set reports success for work nobody did.**
`no-requirement-ids` closes it for the specification, `no-ux-ids` for the
interface, and `dataflow-unqualified` for the data flow. All three apply only at
`demonstration` and above; a `draft` is allowed to be a sketch.

`## Data flow` is required of `IMPLEMENTATION.md`, and its references resolve.
The section answers the one question the other seven documents leave
unanswerable — the path a number takes, as opposed to what each file is for —
and it is the evidence for the thin-app invariant, which is otherwise asserted
and never shown.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import envelope  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCS = {
    "index": (
        "## Current claim",
        "## Claim boundary",
        "## Evidence chain",
        "## Open deviations",
    ),
    "source_pack": (
        "## Engineering question",
        "## Authoritative source",
        "## Relevant evidence",
        "## Source ambiguity register",
        "## Currency and applicability",
        "## Review status",
    ),
    "algorithm": (
        "## Document control",
        "## Requirement index",
        "## 1. Scope",
        "## 2. Inputs",
        "## 3. Outputs",
        "## 4. Method",
        "## 5. Applicability limits",
        "## 6. Acceptance targets",
        "## 7. What this does not buy",
    ),
    "implementation": (
        "## Engine identity",
        "## Architecture",
        "## Data flow",
        "## Traceability",
        "## Numerical implementation",
        "## Dependencies",
        "## Error and refusal mapping",
        "## Known deviations",
        "## Human review",
    ),
    "validation": (
        "## Validation objective",
        "## Claimed applicability domain",
        "## Reference cases",
        "## Tolerance rationale",
        "## Coverage matrix",
        "## Structural checks",
        "## Acceptance rule",
        "## Failure routing",
        "## Validation limitations",
        "## Current evidence",
    ),
    "app_spec": (
        "## Why",
        "## Supported uses",
        "## Refused or out-of-scope uses",
        "## User journeys",
        "## Thin-app invariant",
        "## Input and result presentation",
        "## Relationships",
        "## Acceptance criteria",
        "## Accessibility",
        "## Open questions",
        "## Future",
    ),
    "report_spec": (
        "## Report purpose and audience",
        "## Required identity",
        "## Required inputs",
        "## Required calculation lines",
        "## Source and ambiguity disclosure",
        "## Validation statement",
        "## Rounding policy",
        "## Supported formats",
        "## Hand-check procedure",
        "## Consistency invariant",
    ),
    "release_verification": (
        "## Release identity",
        "## Included components",
        "## Required test matrix",
        "## Mandatory end-to-end journey",
        "## Environment matrix",
        "## Packaging checks",
        "## Known limitations",
        "## Deviations and skips",
        "## Failure routing",
        "## Release decision",
    ),
}

VALID_STATUS = {"draft", "demonstration", "verified", "released"}
#: `UX` is stated in the app-spec document rather than ALGORITHM.md — see the
#: module docstring for why it joins the grammar but is not required.
KINDS = ("ALG", "LIM", "REFUSE", "VAL", "PROP", "REPORT", "UX")
REQUIREMENT_RE = re.compile(rf"\b(?:{'|'.join(KINDS)})-[A-Z0-9-]+\b")

#: `X-01..03` / `X-01-03` / `X-01 to 03`. Rejected rather than expanded: the id
#: regex is word-bounded, so an author who writes a range means three ids and
#: creates one, and the other two become requirements nothing ever demands.
#: Teaching the parser to expand them would make a document's meaning depend on
#: this script. Measured 2026-08-13: 0 occurrences in trust-loop.
RANGE_RE = re.compile(
    rf"\b(?:{'|'.join(KINDS)})-[A-Z0-9-]*\d+\s*(?:\.\.+|—|–|\bto\b)\s*\d+"
)

#: A code reference inside `## Data flow`, written `` `relpath::symbol` ``.
#:
#: Only this form is resolved, and the narrowness is the whole false-positive
#: story. A data-flow diagram is prose with arrows in it: it says
#: `ALGORITHM.md § 2`, it says `<module>.run(...)` where the module is a
#: placeholder, it names JS render functions and HTTP routes. A rule over "any
#: dotted identifier" would fire on every one of those and be switched off
#: within a month. Requiring the qualified form is the same move `_traced_ids`
#: makes by demanding a table row over a prose mention: the author marks what
#: they mean to be checked, and `dataflow-unqualified` stops them marking
#: nothing.
DATAFLOW_SYMBOL_RE = re.compile(r"`([^`\s]+?\.[A-Za-z0-9_]+)::([A-Za-z_][\w.]*)`")

#: Statuses at which a package claims more than a sketch, so its vacuity guards
#: apply. `draft` is exempt on purpose — the guards exist to stop a finished
#: package passing on work nobody did, not to stop one being started.
CLAIMING_STATUS = {"demonstration", "verified", "released"}
RECEIPTS = {
    "source_review",
    "specification_approval",
    "implementation_review",
    "conformance",
    "app_acceptance",
    "report_check",
    "release_verification",
}


def _safe_doc(app_dir: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 1:
        return None
    resolved = (app_dir / rel).resolve()
    try:
        resolved.relative_to(app_dir.resolve())
    except ValueError:
        return None
    return resolved


# ─── Shared with D:/prelim-sizing — keep in sync, do not diverge ──────
#
# The three helpers below were written in that repository's own checker and
# ported here (2026-08-13). Two repositories with no shared package, so this is
# vendored code in both directions — the provenance discipline
# `emptyos/sdk/skill_scan.py` carries for its upstream, applied to a sibling.
#
# Change one, change both. A behavioural difference would mean one package's
# traceability claim means something the other's does not, which is worse than
# either rule alone. The pins are in
# `tests/test_unit_check_engineering_assurance.py`.
#
# Collapsing the two implementations into one was measured and rejected: it
# needs 43 headings restructured across six documents, and the contracts differ
# in decomposition rather than naming. See that repo's `docs/IMPLEMENTATION-LOG.md`
# F-19 for the evidence.
# ──────────────────────────────────────────────────────────────────────


def _section_body(text: str, heading: str) -> str | None:
    """Content under the first heading containing `heading`, or None if absent.

    The single definition of "does this document have that heading", so
    `document-contract` and `section-empty` cannot disagree about what counts.
    The section runs to the next heading at the same or shallower level, so a
    `##` whose first child is a `###` still reads as having content.

    Known limit, deliberately unhandled: a `#` at line-start inside a fenced
    code block reads as a heading and ends the section early. Zero occurrences
    across the assurance documents in this tree when measured 2026-08-13, so
    guarding it would be machinery for a shape nobody has hit
    (`.claude/rules/audits.md`). `_table_rows` is fence-blind the same way; fix
    both together behind one fence-aware line filter if either ever bites.
    """
    wanted = heading.lstrip("#").strip().lower()
    depth: int | None = None
    body: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if depth is not None and level <= depth:
                break
            if depth is None and wanted in stripped.lower():
                depth = level
            continue
        if depth is not None:
            body.append(line)
    return "\n".join(body) if depth is not None else None


def _table_rows(text: str) -> list[list[str]]:
    """Markdown table rows as lists of stripped cells (separator rows dropped)."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells if c):
            continue
        rows.append(cells)
    return rows


def _traced_ids(text: str) -> set[str]:
    """Ids appearing in a table row that carries three non-empty cells.

    A mention in prose does not count. The row must carry the id, a code symbol
    and a test, so that a reviewer scanning the table for blanks is doing real
    work rather than confirming the id exists somewhere in the file.
    """
    traced: set[str] = set()
    for cells in _table_rows(text):
        filled = [c for c in cells if c and c not in ("—", "-", "n/a", "N/A")]
        if len(filled) < 3:
            continue
        for cell in cells:
            traced.update(REQUIREMENT_RE.findall(cell))
    return traced


# ─── Data-flow resolve join — written here, keep D:/prelim-sizing in sync ───
#
# The other direction of the vendoring above: these two were written in this
# repository and belong in that one on the same terms. Same rule, or one
# package's data flow means something the other's does not.
# ────────────────────────────────────────────────────────────────────────────


def _module_symbols(path: Path) -> set[str]:
    """Names a Python file defines — bare, and qualified by their owner.

    A method is reachable as both `parse` and `Quantity.parse`, because a
    diagram may reasonably write either and neither is the wrong answer. An
    unparseable or unreadable file yields nothing, which surfaces as an
    unresolved reference rather than as a crash — a checker that dies on a file
    it cannot read teaches people to remove the file.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return set()
    names: set[str] = set()

    def walk(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = f"{prefix}.{child.name}" if prefix else child.name
                names.add(child.name)
                names.add(qualified)
                walk(child, qualified)
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
                        if prefix:
                            names.add(f"{prefix}.{target.id}")

    walk(tree)
    return names


def _resolve_reference(
    root: Path, app_dir: Path, engine_dirs: list[Path], rel: str, symbol: str
) -> bool:
    """True when `rel::symbol` names something that exists.

    `rel` is tried against the app directory, each declared engine package, and
    the repository root, in that order. Deliberately no shorthand vocabulary:
    the rule has to read identically in every repository that vendors it, and a
    `p/` prefix meaning `pages/` is a local habit that would quietly change what
    the gate accepts.

    A Python file is parsed. Anything else — a page, a script — falls back to a
    literal search for the trailing name, because `renderSteps` in a 600-line
    page is a string, and shipping a JS parser to check a diagram would cost
    more than the diagram.
    """
    for base in [app_dir, *engine_dirs, root]:
        try:
            resolved = (base / rel).resolve()
            resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if not resolved.is_file():
            continue
        if resolved.suffix == ".py":
            if symbol in _module_symbols(resolved):
                return True
            continue
        text = resolved.read_text(encoding="utf-8", errors="replace")
        if symbol.rsplit(".", 1)[-1] in text:
            return True
    return False


def _test_exists(root: Path, app_id: str, kind: str) -> bool:
    """Whether a `test_{kind}_{slug}*.py` suite exists for this app.

    A glob, not an exact filename. The convention CLAUDE.md documents is
    `tests/test_unit_{slug}*.py`, and a package that splits its unit tests by
    concern — `test_unit_x_spec.py` for the declaration, `test_unit_x_ui.py`
    for the interface — is following it. Requiring the bare name rejected that
    layout, which the control run against `D:/prelim-sizing` surfaced: it
    carries two well-named unit suites and scored `missing-tests`. `trust-loop`
    passed only because it happens to have the exact file too, which is how an
    accidental rule survives its own subject.
    """
    tests = root / "tests"
    if not tests.is_dir():
        return False
    variants = {app_id, app_id.replace("-", "_")}
    return any(
        any(tests.glob(f"test_{kind}_{name}*.py")) for name in variants
    )


def _unit_test_exists(root: Path, app_id: str, manifest: dict) -> bool:
    if _test_exists(root, app_id, "unit"):
        return True
    engine_ids = (manifest.get("requires") or {}).get("engines") or []
    for engine_id in engine_ids:
        tests_dir = root / "engines" / str(engine_id) / "tests"
        if tests_dir.is_dir() and any(tests_dir.rglob("test_*.py")):
            return True
    return False


def _audit_app(root: Path, manifest_path: Path, manifest: dict) -> tuple[list[dict], list[dict]]:
    app_dir = manifest_path.parent
    app_id = str((manifest.get("app") or {}).get("id") or app_dir.name)
    assurance = manifest.get("assurance") or {}
    hard: list[dict] = []
    warnings: list[dict] = []

    def issue(kind: str, detail: str) -> None:
        hard.append({"app": app_id, "kind": kind, "detail": detail})

    method = assurance.get("method")
    status = assurance.get("status")
    if method != "trust-loop-v1":
        issue("method", f"expected trust-loop-v1, got {method!r}")
    if status not in VALID_STATUS:
        issue("status", f"expected one of {sorted(VALID_STATUS)}, got {status!r}")

    texts: dict[str, str] = {}
    for key, headings in DOCS.items():
        path = _safe_doc(app_dir, assurance.get(key))
        if path is None:
            issue("unsafe-or-missing-declaration", key)
            continue
        if not path.is_file():
            issue("missing-document", f"{key}: {path.name}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        texts[key] = text
        missing = [heading for heading in headings if heading not in text]
        if missing:
            issue("document-contract", f"{path.name} missing {', '.join(missing)}")
        # A heading is not a claim. Without this, a document containing nothing
        # but its `##` lines satisfies the contract above in full.
        hollow = [
            heading for heading in headings
            if heading not in missing and not (_section_body(text, heading) or "").strip()
        ]
        if hollow:
            issue(
                "section-empty",
                f"{path.name}: {', '.join(hollow)} "
                "— heading present with no content under it",
            )
        for bad in RANGE_RE.findall(text):
            issue(
                "id-range",
                f"{path.name}: {bad!r} is a range. The id regex is word-bounded, "
                "so only the first id is visible and the rest become invisible "
                "requirements nothing demands. Enumerate them.",
            )

    # Engineering ids are stated in ALGORITHM.md; interface ids in the app-spec
    # document, because the document that owns the interface should be the one
    # its requirements live in.
    algorithm_ids = set(REQUIREMENT_RE.findall(texts.get("algorithm", "")))
    ux_ids = {
        i for i in REQUIREMENT_RE.findall(texts.get("app_spec", ""))
        if i.startswith("UX-")
    }
    stated_ids = algorithm_ids | ux_ids
    # Strict join — a table row with three non-empty cells, never a mention.
    # No kind is exempt: trust-loop traces its LIM id in a real row, so an
    # "applicability limits have no code symbol" carve-out has no evidence
    # behind it yet and would only loosen the gate.
    traced = _traced_ids(texts.get("implementation", ""))
    if not algorithm_ids:
        issue("traceability", "ALGORITHM.md has no stable requirement ids")
    missing_trace = sorted(stated_ids - traced)
    if missing_trace:
        issue(
            "traceability",
            "IMPLEMENTATION.md has no traceability row (id · code symbol · test) "
            "for " + ", ".join(missing_trace),
        )

    provides = manifest.get("provides") or {}

    # A package that serves a web surface and states no interface requirement
    # passes the join above vacuously: `stated_ids - traced` over an empty
    # `ux_ids` is empty, so Stage 5 scores full marks for work nobody did. This
    # is the same set-difference-over-nothing hole as `no-requirement-ids`,
    # which is why both are errors rather than warnings.
    if status in CLAIMING_STATUS and (provides.get("web") or {}) and not ux_ids:
        issue(
            "no-ux-ids",
            "app serves a web surface and states no UX-* ids, so Stage 5 carries "
            "no executable receipt — its only gate is that an acceptance test "
            "passes, which a bare number field satisfies",
        )

    # ── Data flow ───────────────────────────────────────────────────────────
    # The section is a claim about code, so its references resolve and its ids
    # are ids the package states. Only `path::symbol` tokens are read — see
    # DATAFLOW_SYMBOL_RE for why anything wider is unusable.
    flow_body = _section_body(texts.get("implementation", ""), "## Data flow")
    flow = flow_body or ""
    references = DATAFLOW_SYMBOL_RE.findall(flow)
    engine_dirs = [
        root / "engines" / str(engine_id)
        for engine_id in ((manifest.get("requires") or {}).get("engines") or [])
    ]
    unresolved = sorted({
        f"{rel}::{symbol}" for rel, symbol in references
        if not _resolve_reference(root, app_dir, engine_dirs, rel, symbol)
    })
    if unresolved:
        issue(
            "dataflow-symbol",
            "IMPLEMENTATION.md § Data flow names " + ", ".join(unresolved)
            + " — a diagram of the path a number takes is a claim about code "
            "that exists",
        )
    unstated = sorted(set(REQUIREMENT_RE.findall(flow)) - stated_ids)
    if unstated:
        issue(
            "dataflow-id",
            "IMPLEMENTATION.md § Data flow names " + ", ".join(unstated)
            + " — an id on an edge that the package never states",
        )
    # Guarded on the section existing: an absent one is already reported by
    # `document-contract`, and two errors for one defect train people to skim.
    if status in CLAIMING_STATUS and flow_body is not None and not references:
        issue(
            "dataflow-unqualified",
            "IMPLEMENTATION.md § Data flow qualifies no symbol as "
            "`path::symbol`, so nothing in it is checked and it can name a "
            "function deleted last month",
        )

    if not (provides.get("methods") or {}):
        issue("calculator-contract", "manifest has no provides.methods")
    if not (provides.get("conformance") or {}):
        issue("calculator-contract", "manifest has no provides.conformance")
    for endpoint, case_id, (kind, detail) in conformance_source_problems(root, provides):
        issue(kind, f"conformance {endpoint}/{case_id}: {detail}")

    if not _unit_test_exists(root, app_id, manifest):
        issue(
            "missing-tests",
            f"no root unit test or declared engine test suite for {app_id}",
        )
    if not _test_exists(root, app_id, "sys"):
        issue("missing-tests", f"no test_sys_{app_id.replace('-', '_')}.py")

    if status in {"verified", "released"}:
        receipts = assurance.get("receipts")
        if not isinstance(receipts, dict):
            issue("promotion-without-receipts", f"{status} requires [assurance.receipts]")
        else:
            missing_receipts = sorted(RECEIPTS - set(receipts))
            if missing_receipts:
                issue(
                    "promotion-without-receipts",
                    "missing receipt declarations: " + ", ".join(missing_receipts),
                )
            for key, value in receipts.items():
                path = _safe_doc(app_dir, value)
                if key in RECEIPTS and (path is None or not path.is_file()):
                    issue("missing-receipt", f"{key}: {value!r}")
    elif status == "demonstration":
        warnings.append({
            "app": app_id,
            "kind": "demonstration",
            "detail": "structure is checked; durable receipts are not claimed",
        })

    return hard, warnings


#: A `source` must say WHERE the expected numbers were read, not merely which
#: document: a page, a table, a figure, an equation, an example, an annex or a
#: clause. "IEC 60949" alone is a label; "IEC 60949:1988 Table III, p. 19" is a
#: locator a reviewer can turn to.
SOURCE_LOCATOR_RE = re.compile(
    r"(\bpp?\.\s*\d|\bpage\s*\d|\btable\b|\bfig(?:ure|\.)|\beq(?:uation|\.|\s*\()|"
    r"\bexample\b|\bannex\b|\bappendix\b|§|\bcl(?:ause|\.)\s*[\dA-Z])",
    re.IGNORECASE,
)


def conformance_source_problems(root: Path, provides: dict) -> list[tuple[str, str, tuple[str, str]]]:
    """(endpoint, case_id, (kind, detail)) for every conformance case whose
    `source` is missing, is not a locator, or names a provenance record that
    does not resolve / is not `cited` / was not read from page images.

    Two accepted forms. A locator string (SOURCE_LOCATOR_RE), or
    `engines/<pkg>/data/<name>.json#<source-id>`, resolved through
    `engines.provenance` so the record itself says how it was checked. Either
    way this is paperwork: the scanner resolves it and cannot open the page.
    `eos-citation-verify` is what checks the claim (kb-fact-integrity T6).
    """
    out: list[tuple[str, str, tuple[str, str]]] = []
    for endpoint, items in (provides.get("conformance") or {}).items():
        for raw in (items if isinstance(items, list) else []):
            if not isinstance(raw, dict):
                continue
            case_id = str(raw.get("case_id") or "?")
            src = str(raw.get("source") or "").strip()
            if not src:
                out.append((endpoint, case_id, ("conformance-source",
                            "names no `source` (where were the expected numbers read?)")))
                continue
            problem = _source_locator_problem(root, src)
            if problem:
                out.append((endpoint, case_id, ("conformance-source-locator", problem)))
    return out


def _source_locator_problem(root: Path, src: str) -> str:
    if "#" in src and src.split("#", 1)[0].endswith(".json"):
        rel, sid = src.split("#", 1)
        path = (root / rel).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            return f"source record file not found under the repo: {rel}"
        try:
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from engines.provenance import load_table_file, source_record
            rec = source_record(load_table_file(path), sid)
        except Exception as e:  # a malformed registry is reported, never passed
            return f"cannot load {rel}: {e}"
        if rec is None:
            return f"no source id {sid!r} in {rel}"
        if rec.get("tier") != "cited":
            return f"{sid} is tier {rec.get('tier')!r}, not cited"
        if rec.get("checked_method") != "page-images":
            return f"{sid} was checked by {rec.get('checked_method')!r}, not page-images"
        return ""
    if not SOURCE_LOCATOR_RE.search(src):
        return (f"`source` {src!r} names a document but no page / table / figure / "
                "equation / example / annex / clause")
    return ""


def audit(root: Path = REPO_ROOT) -> dict:
    apps: list[str] = []
    hard: list[dict] = []
    warnings: list[dict] = []
    for manifest_path in sorted((root / "apps").rglob("manifest.toml")):
        try:
            manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if "assurance" not in manifest:
            # Not a Trust Loop package: the source rule is advisory here — a
            # per-app count of cases with no `source`, so the backlog is visible
            # without blocking anyone (kb-fact-integrity T6).
            provides = manifest.get("provides") or {}
            missing = [c for _, c, (k, _d) in conformance_source_problems(root, provides)
                       if k == "conformance-source"]
            if missing:
                warnings.append({
                    "app": str((manifest.get("app") or {}).get("id") or manifest_path.parent.name),
                    "kind": "conformance-source-coverage",
                    "detail": f"{len(missing)} conformance case(s) name no source: " + ", ".join(missing),
                })
            continue
        app_id = str((manifest.get("app") or {}).get("id") or manifest_path.parent.name)
        apps.append(app_id)
        app_hard, app_warnings = _audit_app(root, manifest_path, manifest)
        hard.extend(app_hard)
        warnings.extend(app_warnings)
    return {"apps": apps, "hard": hard, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate engineering assurance packages.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit()
    ok = not result["hard"]
    summary = (
        f"{len(result['apps'])} assurance app(s) - "
        f"{len(result['hard'])} error(s) - {len(result['warnings'])} note(s)"
    )
    if args.json:
        # The agent-cli envelope (`.claude/rules/agent-cli.md`), built by the
        # shared helper so its shape cannot drift. `emit_json` is deliberately
        # not used: it returns 0/1, and this gate's exit code carries the
        # finding count, which preflight and `&&` chains both already read.
        print(json.dumps(
            envelope(ok, "findings", summary, result), indent=2))
    else:
        print(summary)
        for finding in result["hard"]:
            print(
                f"  X {finding['app']}: {finding['kind']} - {finding['detail']}",
                file=sys.stderr,
            )
        for finding in result["warnings"]:
            print(f"  - {finding['app']}: {finding['detail']}")
    return 0 if ok else min(len(result["hard"]), 99)


if __name__ == "__main__":
    raise SystemExit(main())
