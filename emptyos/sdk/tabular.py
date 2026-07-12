"""Ephemeral SQL over text-derived rows — the text-first data layer.

Text is the source of truth; DuckDB is an ephemeral in-memory compute layer:
load rows, run SQL, return dicts, connection dies. Never create a persistent
DuckDB file — nothing may depend on a database existing between calls. The
sole carve-out: existing SQLite files (machine telemetry like
``TimeSeriesCounter`` DBs) may be ATTACHed **read-only** for querying.

Companions in ``emptyos/sdk/utils.py`` (``parse_markdown_table``,
``format_markdown_table``, ``rows_to_csv``, ``csv_to_rows``) produce and
consume the same list-of-dicts interchange shape, so the full pipeline is:
vault markdown → rows → ``query_rows`` → rows → back to markdown/CSV.

duckdb is an optional dependency (``pip install 'emptyos[data]'``); importing
this module never requires it. Full doctrine: ``.claude/rules/text-first-data.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "QueryUnavailable",
    "duckdb_available",
    "query_rows",
    "query_files",
    "query_mixed",
    "flatten_note_records",
]

_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_INSTALL_HINT = "duckdb not installed — pip install 'emptyos[data]'"


class QueryUnavailable(RuntimeError):
    """Raised when duckdb (or one of its extensions) is not available."""


def duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ImportError:
        return False
    return True


def _duckdb():
    try:
        import duckdb
    except ImportError as e:
        raise QueryUnavailable(_INSTALL_HINT) from e
    return duckdb


def _safe_ident(s: str) -> str:
    if not s or not s.replace("_", "").isalnum():
        raise ValueError(f"invalid table name: {s!r}")
    return s


def _quote_col(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _numeric_kind(value) -> str | None:
    """Classify one value as 'int', 'float', or None (not numeric)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        s = value.strip()
        if _INT_RE.match(s):
            return "int"
        if _FLOAT_RE.match(s):
            return "float"
    return None


def _infer_columns(rows: list[dict]) -> list[tuple[str, str]]:
    """Infer (name, duck_type) per column across ``rows``.

    Union of keys in first-seen order. All-bool → BOOLEAN; all numeric
    (ints and numeric-literal strings like "42"/"3.5") → BIGINT/DOUBLE;
    anything else → VARCHAR. Formatted strings ("$35", "1,200", dates)
    deliberately stay VARCHAR — callers TRY_CAST in SQL.
    """
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)

    out: list[tuple[str, str]] = []
    for col in columns:
        values = [r.get(col) for r in rows if r.get(col) is not None]
        if not values:
            out.append((col, "VARCHAR"))
            continue
        if all(isinstance(v, bool) for v in values):
            out.append((col, "BOOLEAN"))
            continue
        kinds = [_numeric_kind(v) for v in values]
        if all(k is not None for k in kinds):
            out.append((col, "BIGINT" if all(k == "int" for k in kinds) else "DOUBLE"))
        else:
            out.append((col, "VARCHAR"))
    return out


def _coerce(value, duck_type: str):
    if value is None:
        return None
    if duck_type == "BOOLEAN":
        return bool(value)
    if duck_type == "BIGINT":
        return int(str(value).strip()) if not isinstance(value, bool) else int(value)
    if duck_type == "DOUBLE":
        return float(str(value).strip())
    if isinstance(value, str):
        return value
    return str(value)


def _register_rows(conn, name: str, rows: list[dict]) -> None:
    name = _safe_ident(name)
    if not rows:
        conn.execute(f"CREATE TEMP TABLE {name} (_empty VARCHAR)")
        return
    cols = _infer_columns(rows)
    decl = ", ".join(f"{_quote_col(c)} {t}" for c, t in cols)
    conn.execute(f"CREATE TEMP TABLE {name} ({decl})")
    placeholders = ", ".join("?" for _ in cols)
    data = [tuple(_coerce(r.get(c), t) for c, t in cols) for r in rows]
    conn.executemany(f"INSERT INTO {name} VALUES ({placeholders})", data)


def _sql_str(path: Path) -> str:
    return "'" + str(path).replace("\\", "/").replace("'", "''") + "'"


def _register_file(conn, name: str, path: str | Path) -> None:
    name = _safe_ident(name)
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    suffix = p.suffix.lower()
    if suffix == ".csv":
        conn.execute(f"CREATE TEMP VIEW {name} AS SELECT * FROM read_csv_auto({_sql_str(p)})")
    elif suffix in _SQLITE_SUFFIXES:
        try:
            conn.execute(f"ATTACH {_sql_str(p)} AS {name} (TYPE sqlite, READ_ONLY)")
        except Exception as e:  # extension missing/offline — actionable hint
            raise QueryUnavailable(
                "duckdb sqlite extension not available — run once: "
                "python -c \"import duckdb; duckdb.install_extension('sqlite')\""
            ) from e
    else:
        raise ValueError(f"unsupported file type for query_files: {p.name!r}")


def query_mixed(
    sql: str,
    *,
    tables: dict[str, list[dict]] | None = None,
    sources: dict[str, str | Path] | None = None,
    params: list | None = None,
) -> list[dict]:
    """Run one SQL statement over row-dict tables and/or CSV / read-only SQLite files.

    ``tables`` maps table name → list-of-dicts (the interchange shape from
    ``parse_markdown_table`` / ``vault_query`` / an app's ``list_all``).
    ``sources`` maps name → path: ``.csv`` becomes a view of that name;
    ``.db``/``.sqlite``/``.sqlite3`` is ATTACHed read-only (reference its
    tables as ``<name>.<table>``). Everything runs in a per-call in-memory
    connection — no state survives the return.
    """
    duckdb = _duckdb()
    conn = duckdb.connect(":memory:")
    try:
        for name, rows in (tables or {}).items():
            _register_rows(conn, name, rows)
        for name, path in (sources or {}).items():
            _register_file(conn, name, path)
        cursor = conn.execute(sql, params or [])
        col_names = [d[0] for d in cursor.description or []]
        return [dict(zip(col_names, row, strict=False)) for row in cursor.fetchall()]
    finally:
        conn.close()


def query_rows(sql: str, tables: dict[str, list[dict]], *, params: list | None = None) -> list[dict]:
    """Ephemeral in-memory SQL over Python row-dicts. See ``query_mixed``."""
    return query_mixed(sql, tables=tables, params=params)


def query_files(sql: str, sources: dict[str, str | Path], *, params: list | None = None) -> list[dict]:
    """Ephemeral SQL over CSV files and read-only SQLite ATTACHes. See ``query_mixed``."""
    return query_mixed(sql, sources=sources, params=params)


def flatten_note_records(
    records: list[dict],
    *,
    keep: tuple[str, ...] = ("path", "name", "folder"),
) -> list[dict]:
    """Flatten ``vault_query`` output into plain rows for ``query_rows``.

    Keeps the ``keep`` metadata keys, joins ``tags`` into a comma-separated
    string, and merges the ``properties`` frontmatter dict up one level.
    A frontmatter key colliding with a kept key lands as ``prop_<key>``.
    Pure — needs no duckdb.
    """
    out: list[dict] = []
    for rec in records:
        row = {k: rec.get(k) for k in keep}
        if "tags" in rec:
            tags = rec.get("tags") or []
            row["tags"] = ", ".join(tags) if isinstance(tags, list) else str(tags)
        for key, value in (rec.get("properties") or {}).items():
            row[f"prop_{key}" if key in row else key] = value
        out.append(row)
    return out
