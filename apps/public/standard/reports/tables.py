"""Structured data tables for reports — requirements, risks, verification, stakeholders.

Each table is a YAML file at `<report-dir>/tables/<name>.yaml` containing a list of row dicts.
Row schemas come from `templates.TABLE_SCHEMAS`.

This module handles load/save/render. Rendering produces HTML tables for the
assembled report, and fenced-markdown tables for DOCX fallback.
"""

from __future__ import annotations

from pathlib import Path

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from .templates import table_schema


def load_table(table_path: Path) -> list[dict]:
    """Load a table YAML file as a list of row dicts. Returns [] if missing/empty/invalid."""
    if not table_path.exists():
        return []
    if not HAS_YAML:
        return []
    try:
        raw = yaml.safe_load(table_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if raw is None:
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def save_table(table_path: Path, rows: list[dict]) -> None:
    """Write rows to a table YAML file. Creates parent dirs."""
    if not HAS_YAML:
        raise RuntimeError("pyyaml is required for structured tables (pip install pyyaml)")
    table_path.parent.mkdir(parents=True, exist_ok=True)
    table_path.write_text(
        yaml.safe_dump(rows or [], sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )


def next_id(rows: list[dict], prefix: str) -> str:
    """Suggest the next sequential ID for a table (e.g. REQ-007 after REQ-006)."""
    if not prefix:
        return ""
    max_n = 0
    for r in rows:
        rid = str(r.get("id") or "")
        if rid.startswith(f"{prefix}-"):
            try:
                n = int(rid[len(prefix) + 1 :])
                if n > max_n:
                    max_n = n
            except ValueError:
                pass
    return f"{prefix}-{max_n + 1:03d}"


def scaffold_rows(table_name: str, count: int = 1) -> list[dict]:
    """Return `count` empty rows keyed to the table schema. ID is pre-filled if the schema has a prefix."""
    schema = table_schema(table_name)
    if not schema:
        return [{} for _ in range(count)]
    rows: list[dict] = []
    for i in range(count):
        row = {c["key"]: "" for c in schema["columns"]}
        prefix = schema.get("id_prefix") or ""
        if prefix and "id" in row:
            row["id"] = f"{prefix}-{i + 1:03d}"
        rows.append(row)
    return rows


def render_table_html(
    table_name: str, rows: list[dict], *, table_class: str = "report-table"
) -> str:
    """Render a table as HTML. Columns come from the schema; unknown fields are ignored."""
    schema = table_schema(table_name)
    if schema is None:
        # Unknown table: render whatever keys exist in the first row
        if not rows:
            return f'<table class="{table_class} empty"><caption>No {table_name} recorded.</caption></table>'
        keys = list(rows[0].keys())
        columns = [{"key": k, "label": k.replace("_", " ").title()} for k in keys]
    else:
        columns = schema["columns"]

    if not rows:
        labels = " / ".join(c["label"] for c in columns)
        return (
            f'<table class="{table_class} empty">'
            f"<caption>No {table_name} recorded — columns: {_html_escape(labels)}.</caption>"
            f"</table>"
        )

    head = "".join(f'<th scope="col">{_html_escape(c["label"])}</th>' for c in columns)
    body_rows = []
    for r in rows:
        cells = []
        for c in columns:
            val = r.get(c["key"], "")
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            cells.append(f"<td>{_html_escape(str(val))}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    body = "\n".join(body_rows)
    return (
        f'<table class="{table_class}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
    )


def render_table_markdown(table_name: str, rows: list[dict]) -> str:
    """Render a table as a GitHub-flavoured markdown table (used for DOCX fallback and previews)."""
    schema = table_schema(table_name)
    if schema is None:
        if not rows:
            return f"_No {table_name} recorded._"
        keys = list(rows[0].keys())
        columns = [{"key": k, "label": k.replace("_", " ").title()} for k in keys]
    else:
        columns = schema["columns"]

    if not rows:
        labels = " / ".join(c["label"] for c in columns)
        return f"_No {table_name} recorded — columns: {labels}._"

    header = "| " + " | ".join(c["label"] for c in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for r in rows:
        vals = []
        for c in columns:
            val = r.get(c["key"], "")
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            vals.append(str(val).replace("\n", " ").replace("|", "\\|"))
        body.append("| " + " | ".join(vals) + " |")
    return "\n".join([header, sep, *body])


def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
