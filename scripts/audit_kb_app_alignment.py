"""Audit alignment between vault KB notes and EmptyOS apps.

Read-only checks:
- KB frontmatter schema and link targets
- `implemented_in` repository paths and optional Python symbols
- app-side literal KB slugs and popover section anchors
- app manifest declarations for direct KB UI/API usage
- formula verification and implementation coverage

Usage:
    python scripts/audit_kb_app_alignment.py
    python scripts/audit_kb_app_alignment.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
VALID_KINDS = {
    "concept",
    "formula",
    "reference",
    "clause",
    "case",
    "lesson",
    "pattern",
    "doc",
    "moc",
}
VAULT_EXCLUDED_PREFIXES = {
    ("99_Attachments", "temp-backup"),
}

POPOVER_RE = re.compile(
    r"""kbPopover\(\s*['"](?P<slug>[a-z0-9][a-z0-9-]*)['"]"""
    r"""(?:\s*,\s*(?P<section>null|['"][^'"]+['"]))?""",
)
KB_SLUG_RE = re.compile(
    r"""\bkb_slug\b\s*[:=]\s*['"](?P<slug>[a-z0-9][a-z0-9-]*)['"]""",
)
KB_EXPLAIN_RE = re.compile(
    r"""kb_explain\(\s*['"](?P<slug>[a-z0-9][a-z0-9-]*)['"]""",
)
KB_INFO_RE = re.compile(
    r"""kbInfoBtn\(\s*['"](?P<slug>[a-z0-9][a-z0-9-]*)['"]""",
)
KB_NOTE_URL_RE = re.compile(
    r"""/kb/api/notes/(?P<slug>[a-z0-9][a-z0-9-]*)""",
)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def _load_config() -> dict[str, Any]:
    path = REPO_ROOT / "emptyos.toml"
    if not path.exists():
        path = REPO_ROOT / "emptyos.toml.example"
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_note(path: Path, vault_root: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        props = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(props, dict):
        return None
    tags = _as_list(props.get("tags") or props.get("tag"))
    if "kb" not in tags:
        return None
    body = parts[2].lstrip("\r\n")
    return {
        "slug": path.stem,
        "path": path.relative_to(vault_root).as_posix(),
        "file": str(path),
        "properties": props,
        "body": body,
        "headings": set(HEADING_RE.findall(body)),
    }


def _corpus_of(path: str) -> str:
    """Which KB corpus a vault-relative note path belongs to.

    The EmptyOS KB (30_Resources/EmptyOS/kb) and the personal KB
    (30_Resources/KB) are independent namespaces. Anything else is "other" —
    NOT a KB corpus, and therefore not this audit's business.
    """
    p = str(path).replace("\\", "/")
    if "30_Resources/EmptyOS/kb" in p:
        return "eos-kb"
    if "30_Resources/KB" in p:
        return "personal-kb"
    return "other"


def _is_excluded_vault_path(path: Path, vault_root: Path) -> bool:
    parts = path.relative_to(vault_root).parts
    return any(parts[: len(prefix)] == prefix for prefix in VAULT_EXCLUDED_PREFIXES)


def _active_manifest_paths() -> list[Path]:
    paths = []
    for path in (REPO_ROOT / "apps").rglob("manifest.toml"):
        rel_parts = path.relative_to(REPO_ROOT / "apps").parts
        if any(part.startswith("_") for part in rel_parts):
            continue
        paths.append(path)
    return sorted(paths)


def _manifest_info(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    app = data.get("app", {})
    requires = data.get("requires", {})
    return {
        "id": app.get("id") or path.parent.name,
        "dir": path.parent,
        "manifest": path,
        "requires_apps": set(_as_list(requires.get("apps"))),
        "optional_apps": set(_as_list(requires.get("optional_apps"))),
    }


def _parse_impl_ref(ref: Any) -> tuple[str, str, str]:
    if isinstance(ref, dict):
        if "method" in ref:
            return "method", str(ref["method"]).strip(), ""
        if "path" in ref:
            ref = ref["path"]
    text = str(ref).strip()
    kind = "path"
    body = text
    if text.lower().startswith("path:"):
        body = text.split(":", 1)[1].strip()
    elif text.lower().startswith("method:"):
        body = text.split(":", 1)[1].strip()
        kind = "method"
    code_path = body
    symbol = ""
    for sep in ("::", " — ", " – ", " - "):
        if sep in body:
            code_path, _, symbol = body.partition(sep)
            break
    return kind, code_path.strip().replace("\\", "/"), symbol.strip()


def _python_symbols(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    symbols = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
    for node in tree.body:
        # Module-level constants/aliases (e.g. MAX_SIDEWALL_PRESSURE = 4400) are
        # legitimate implemented_in symbols; without these, a note pointing at a
        # module constant false-positives as missing (kb_claim_audit resolves them).
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    symbols.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.add(f"{node.name}.{child.name}")
    return symbols


def _source_files(app_dir: Path) -> list[Path]:
    allowed = {".py", ".html", ".js", ".toml", ".md"}
    return [
        path
        for path in app_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in allowed
    ]


def _manifest_kb_refs(value: Any, key: str = "") -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            refs.extend(_manifest_kb_refs(child, str(child_key)))
        return refs
    if isinstance(value, list):
        for child in value:
            refs.extend(_manifest_kb_refs(child, key))
        return refs
    if not isinstance(value, str):
        return refs
    if key == "kb_slug":
        refs.append(("manifest_kb_slug", value))
    elif key == "kb_refs":
        refs.append(("manifest_kb_ref", value))
    elif key == "references":
        match = re.fullmatch(r"\[\[([a-z0-9][a-z0-9-]*)\]\]", value.strip())
        if match:
            refs.append(("manifest_reference", match.group(1)))
    return refs


def _scan_app_refs(app: dict[str, Any], known_slugs: set[str]) -> list[dict[str, Any]]:
    refs = []
    with app["manifest"].open("rb") as fh:
        manifest_data = tomllib.load(fh)
    manifest_rel = app["manifest"].relative_to(REPO_ROOT).as_posix()
    for kind, slug in _manifest_kb_refs(manifest_data):
        refs.append({
            "app": app["id"],
            "source": manifest_rel,
            "kind": kind,
            "slug": slug,
            "section": None,
        })
    for path in _source_files(app["dir"]):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if path.suffix.lower() in {".html", ".js"}:
            for slug in re.findall(r"""['"]([a-z0-9][a-z0-9-]*)['"]""", text):
                if "-" not in slug or slug not in known_slugs:
                    continue
                refs.append({
                    "app": app["id"],
                    "source": rel,
                    "kind": "ui_literal_slug",
                    "slug": slug,
                    "section": None,
                })
        for line in text.splitlines():
            if "kbPopover(" not in line:
                continue
            for slug in re.findall(r"""['"]([a-z0-9][a-z0-9-]*)['"]""", line):
                if slug not in known_slugs:
                    continue
                refs.append({
                    "app": app["id"],
                    "source": rel,
                    "kind": "popover_literal",
                    "slug": slug,
                    "section": None,
                })
        for match in POPOVER_RE.finditer(text):
            raw_section = match.group("section")
            section = None
            if raw_section and raw_section != "null":
                section = raw_section[1:-1]
            refs.append({
                "app": app["id"],
                "source": rel,
                "kind": "popover",
                "slug": match.group("slug"),
                "section": section,
            })
        for regex, kind in (
            (KB_SLUG_RE, "kb_slug"),
            (KB_EXPLAIN_RE, "kb_explain"),
            (KB_INFO_RE, "kb_info_btn"),
            (KB_NOTE_URL_RE, "note_url"),
        ):
            for match in regex.finditer(text):
                refs.append({
                    "app": app["id"],
                    "source": rel,
                    "kind": kind,
                    "slug": match.group("slug"),
                    "section": None,
                })
    return refs


def audit() -> dict[str, Any]:
    config = _load_config()
    vault_root = Path(config.get("notes", {}).get("path", ""))
    if not vault_root.exists():
        raise SystemExit(f"Configured vault does not exist: {vault_root}")

    notes = []
    for path in vault_root.rglob("*.md"):
        if _is_excluded_vault_path(path, vault_root):
            continue
        note = _parse_note(path, vault_root)
        if not note:
            continue
        # `tag: kb` alone does NOT make a note a KB note. A devlog tagged
        # [emptyos, dev-log, kb, ...] is saying "this session touched the kb
        # app" — a topic tag, not corpus membership. Judging those for a
        # missing `kind:` is a false positive. KB notes live in a KB corpus.
        if _corpus_of(note["path"]) == "other":
            continue
        notes.append(note)
    notes_by_slug: dict[str, dict[str, Any]] = {}
    notes_by_slug_all: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for note in notes:
        notes_by_slug_all[note["slug"]].append(note)
        notes_by_slug.setdefault(note["slug"], note)

    apps = [_manifest_info(path) for path in _active_manifest_paths()]
    app_ids = {app["id"] for app in apps}
    raw_app_refs = [ref for app in apps for ref in _scan_app_refs(app, set(notes_by_slug))]
    app_refs = []
    seen_app_refs = set()
    for ref in raw_app_refs:
        key = (ref["app"], ref["source"], ref["kind"], ref["slug"], ref["section"])
        if key in seen_app_refs:
            continue
        seen_app_refs.add(key)
        app_refs.append(ref)

    issues: dict[str, list[dict[str, Any]]] = defaultdict(list)
    impl_links = []

    # Duplicate slugs are an error only WITHIN one KB corpus — the EmptyOS KB
    # (30_Resources/EmptyOS/kb) and the personal KB (30_Resources/KB) are
    # independent namespaces (each separately --root audited, each with its own
    # internal references), so the same slug legitimately exists in both. Group
    # by (corpus, slug) and only flag a real intra-corpus collision.
    by_corpus_slug: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for slug, dups in notes_by_slug_all.items():
        for note in dups:
            by_corpus_slug[(_corpus_of(note["path"]), slug)].append(note)
    for (_corpus, slug), dups in by_corpus_slug.items():
        if len(dups) > 1:
            issues["duplicate_kb_slug"].append({
                "slug": slug,
                "paths": [note["path"] for note in dups],
            })

    for note in notes:
        props = note["properties"]
        slug = note["slug"]
        kind = props.get("kind")
        if kind not in VALID_KINDS:
            issues["invalid_kind"].append({"slug": slug, "kind": kind, "path": note["path"]})

        impl_refs = _as_list(props.get("implemented_in"))
        verify_refs = _as_list(props.get("verified_against"))
        if kind == "formula" and not verify_refs:
            issues["formula_missing_verification"].append({"slug": slug, "path": note["path"]})
        if kind == "formula" and not impl_refs:
            issues["formula_missing_implementation"].append({"slug": slug, "path": note["path"]})

        for target in verify_refs:
            target_slug = str(target).strip().strip("[]")
            if target_slug and target_slug not in notes_by_slug:
                issues["unresolved_verified_against"].append({
                    "slug": slug,
                    "target": target_slug,
                    "path": note["path"],
                })
            elif target_slug and notes_by_slug[target_slug]["properties"].get("kind") != "case":
                issues["verification_target_not_case"].append({
                    "slug": slug,
                    "target": target_slug,
                    "target_kind": notes_by_slug[target_slug]["properties"].get("kind"),
                    "path": note["path"],
                })

        for ref in impl_refs:
            ref_kind, code_path, symbol = _parse_impl_ref(ref)
            link = {
                "slug": slug,
                "note_kind": kind,
                "ref": str(ref),
                "ref_kind": ref_kind,
                "code_path": code_path,
                "symbol": symbol,
                "path": note["path"],
            }
            impl_links.append(link)
            if ref_kind == "method":
                continue
            target = REPO_ROOT / code_path
            if not code_path or not target.exists():
                issues["broken_implemented_in"].append(link)
                continue
            if symbol and target.is_file() and target.suffix == ".py":
                requested = {
                    part.strip().removesuffix("()")
                    for part in symbol.split(",")
                    if part.strip()
                }
                missing = sorted(requested - _python_symbols(target))
                if missing:
                    issues["missing_implemented_symbol"].append({**link, "missing": missing})

    for ref in app_refs:
        note = notes_by_slug.get(ref["slug"])
        if not note:
            issues["broken_app_kb_slug"].append(ref)
            continue
        if ref["section"] and ref["section"] not in note["headings"]:
            issues["broken_app_kb_section"].append(ref)

    refs_by_app: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ref in app_refs:
        refs_by_app[ref["app"]].append(ref)
    for app in apps:
        refs = refs_by_app.get(app["id"], [])
        if not refs:
            continue
        runtime_kinds = {
            "kb_slug",
            "kb_explain",
            "kb_info_btn",
            "note_url",
            "popover",
            "popover_literal",
            "ui_literal_slug",
        }
        runtime_refs = [ref for ref in refs if ref["kind"] in runtime_kinds]
        if not runtime_refs:
            continue
        declared = app["requires_apps"] | app["optional_apps"]
        if "kb" not in declared:
            issues["app_uses_kb_without_manifest_dependency"].append({
                "app": app["id"],
                "manifest": app["manifest"].relative_to(REPO_ROOT).as_posix(),
                "refs": len(runtime_refs),
            })

    impl_by_top = Counter()
    for link in impl_links:
        if link["code_path"]:
            impl_by_top[link["code_path"].split("/", 1)[0]] += 1

    app_ref_counts = Counter(ref["app"] for ref in app_refs)
    kind_counts = Counter(note["properties"].get("kind") for note in notes)

    return {
        "summary": {
            "vault": str(vault_root),
            "kb_notes": len(notes),
            "active_apps": len(apps),
            "implemented_in_links": len(impl_links),
            "app_side_kb_refs": len(app_refs),
            "apps_with_literal_kb_refs": len(app_ref_counts),
            "issue_count": sum(len(rows) for rows in issues.values()),
        },
        "counts": {
            "notes_by_kind": dict(sorted(kind_counts.items(), key=lambda item: str(item[0]))),
            "implemented_in_by_top_level": dict(sorted(impl_by_top.items())),
            "app_side_refs_by_app": dict(sorted(app_ref_counts.items())),
            "issues_by_type": {key: len(value) for key, value in sorted(issues.items())},
        },
        "issues": {key: value for key, value in sorted(issues.items())},
        "implementation_links": impl_links,
        "app_refs": app_refs,
        "app_ids": sorted(app_ids),
    }


def _print_text(result: dict[str, Any]) -> None:
    summary = result["summary"]
    print("KB/App alignment audit")
    print(f"  Vault: {summary['vault']}")
    print(f"  KB notes: {summary['kb_notes']}")
    print(f"  Active apps: {summary['active_apps']}")
    print(f"  implemented_in links: {summary['implemented_in_links']}")
    print(f"  App-side literal KB refs: {summary['app_side_kb_refs']}")
    print(f"  Issues: {summary['issue_count']}")
    print()
    for issue_type, rows in result["issues"].items():
        print(f"{issue_type}: {len(rows)}")
        for row in rows:
            print(f"  - {json.dumps(row, ensure_ascii=False, sort_keys=True)}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args()
    result = audit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_text(result)


if __name__ == "__main__":
    main()
