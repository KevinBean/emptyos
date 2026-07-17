#!/usr/bin/env python3
"""Access .mdb/.accdb reader tool — info, schema, table, query, search, dump.

Read-only by construction: opens with ADO `Mode=1` (adModeRead) and refuses any
statement that isn't a SELECT. Never writes to the database.

Needs pywin32 + the Microsoft ACE OLEDB provider (Windows). No pyodbc — the
daemon interpreter doesn't have it, and ACE is already present on a machine that
runs Access-backed engineering tools.

Commands:
    info   <file.mdb>                  tables, row counts, column counts, SHA-256
    schema <file.mdb> [--table T]      columns + ADO types (all tables, or one)
    table  <file.mdb> <name>           one table as a markdown table, stdout
    query  <file.mdb> "SELECT ..."     ad-hoc read-only SELECT, stdout
    search <file.mdb> <keyword>        rows containing keyword, across all tables
    dump   <file.mdb> <out.md>         every row of every table -> one markdown file

Sibling of skills/tool-pdf-reader/pdf_tool.py and skills/tool-chm-reader/chm_tool.py
— same info/schema/search/dump shape, Access-shaped (tables instead of pages).

Two source-fidelity decisions, both deliberate (see README/SKILL.md):
  * Doubles render at 15 significant digits, matching what Access and the owning
    application display. The stored doubles carry IEEE-754 round-trip noise
    (a cell shown as 13.3 reads back as 13.299999999999999); rendering repr()
    would make the dump unreadable and break grep for the value a user knows.
  * Trailing NUL record-padding is stripped. Some tools pad fixed-width records
    with NUL; left in, a single such row makes the whole output file "binary" to
    grep, defeating the point of a greppable reference dump.
Neither is safe on a write path — read the MDB directly if you need bit-exact
doubles or byte-exact padding.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

try:
    import win32com.client
except ImportError:  # pragma: no cover - environment guard
    # Deliberately not sys.exit here: the rendering helpers below are pure and
    # worth importing (and testing) on any platform. connect() raises the real
    # error the moment a caller actually needs the database.
    win32com = None

# ADO DataTypeEnum -> readable name (the subset Access emits).
TYPES = {
    2: "SmallInt", 3: "Integer", 4: "Single", 5: "Double", 6: "Currency",
    7: "Date", 11: "Boolean", 17: "TinyInt", 72: "GUID", 128: "Binary",
    130: "WChar", 131: "Numeric", 133: "DBDate", 135: "DBTimeStamp",
    202: "VarWChar", 203: "LongVarWChar", 205: "LongVarBinary",
}

ADO_SCHEMA_TABLES = 20
ADO_MODE_READ = 1

# A single-column table of long text is report-template data; a markdown table
# would mangle it. Rendered as a fenced block instead.
CODEBLOCK_IF_SINGLE_COLUMN = True


# ---------------------------------------------------------------- connection


def connect(src: Path):
    if win32com is None:
        sys.exit(
            "pywin32 is required: pip install pywin32\n"
            "(this tool drives the Microsoft ACE OLEDB provider via COM; Windows only)"
        )
    cn = win32com.client.Dispatch("ADODB.Connection")
    try:
        cn.Open(
            f"Provider=Microsoft.ACE.OLEDB.12.0;"
            f"Data Source={src.resolve()};Mode={ADO_MODE_READ}"
        )
    except Exception as e:  # pragma: no cover - environment guard
        sys.exit(
            f"could not open {src.name}: {e}\n"
            "Needs the Microsoft ACE OLEDB provider (Access Database Engine).\n"
            "Bitness must match the Python interpreter (64-bit Python -> 64-bit ACE)."
        )
    return cn


def user_tables(cn) -> list[str]:
    rs = cn.OpenSchema(ADO_SCHEMA_TABLES)
    names: list[str] = []
    while not rs.EOF:
        if rs.Fields.Item("TABLE_TYPE").Value == "TABLE":
            names.append(rs.Fields.Item("TABLE_NAME").Value)
        rs.MoveNext()
    rs.Close()
    return names


def columns_of(cn, table: str) -> tuple[list[str], list[str]]:
    rs = cn.Execute(f"SELECT TOP 1 * FROM [{table}]")[0]
    cols, types = [], []
    for i in range(rs.Fields.Count):
        f = rs.Fields.Item(i)
        cols.append(f.Name)
        types.append(TYPES.get(int(f.Type), f"type{f.Type}"))
    rs.Close()
    return cols, types


def row_count(cn, table: str) -> int:
    rs = cn.Execute(f"SELECT COUNT(*) FROM [{table}]")[0]
    n = int(rs.Fields.Item(0).Value)
    rs.Close()
    return n


# ---------------------------------------------------------------- rendering


def text(v) -> str:
    """String cell with NUL record-padding removed.

    `str.rstrip()` will not remove NUL (it isn't whitespace), so this is not
    redundant. An interior NUL becomes a visible marker rather than vanishing.
    """
    s = str(v).rstrip("\x00")
    return s.replace("\x00", "␀")


def cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.15g}"  # see module docstring
    s = text(v)
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return s.replace("|", "\\|").strip()


def md_table(cols: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |",
           "|" + "|".join([" --- "] * len(cols)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def fetch(cn, sql: str) -> tuple[list[str], list[list[str]]]:
    rs = cn.Execute(sql)[0]
    cols = [rs.Fields.Item(i).Name for i in range(rs.Fields.Count)]
    rows = []
    while not rs.EOF:
        rows.append([cell(rs.Fields.Item(i).Value) for i in range(len(cols))])
        rs.MoveNext()
    rs.Close()
    return cols, rows


# ---------------------------------------------------------------- commands


def cmd_info(src: Path) -> None:
    cn = connect(src)
    tables = user_tables(cn)
    digest = hashlib.sha256(src.read_bytes()).hexdigest().upper()
    print(f"File:   {src}")
    print(f"Size:   {src.stat().st_size / 1024:.0f} KB")
    print(f"SHA256: {digest}")
    print(f"Tables: {len(tables)}")
    print()
    total = 0
    for t in tables:
        n = row_count(cn, t)
        total += n
        cols, _ = columns_of(cn, t)
        print(f"  {t:<24} {n:>6} rows  {len(cols):>3} cols")
    print(f"\n  {'TOTAL':<24} {total:>6} rows")
    cn.Close()


def cmd_schema(src: Path, table: str) -> None:
    cn = connect(src)
    targets = [table] if table else user_tables(cn)
    for t in targets:
        cols, types = columns_of(cn, t)
        print(f"\n{t}  ({len(cols)} columns)")
        for c, ty in zip(cols, types):
            print(f"  {c:<24} {ty}")
    cn.Close()


def cmd_table(src: Path, table: str) -> None:
    cn = connect(src)
    cols, rows = fetch(cn, f"SELECT * FROM [{table}]")
    print(f"## {table}\n\n{len(rows)} row(s)\n")
    print("\n".join(md_table(cols, rows)))
    cn.Close()


def is_select(sql: str) -> bool:
    """True iff `sql` is a bare SELECT (the only statement this tool runs).

    Pure so the guard is auditable and testable without a database. The
    connection is opened read-only anyway; this refuses earlier and with a
    legible message instead of an opaque provider error.
    """
    s = sql.strip()
    while s.startswith("("):
        s = s[1:].lstrip()
    # \b matters: a bare startswith("select") also accepts "selective_thing".
    return re.match(r"select\b", s, re.IGNORECASE) is not None


def cmd_query(src: Path, sql: str) -> None:
    if not is_select(sql):
        sys.exit("refused: only SELECT statements are allowed (this tool is read-only)")
    cn = connect(src)
    cols, rows = fetch(cn, sql)
    print(f"{len(rows)} row(s)\n")
    print("\n".join(md_table(cols, rows)))
    cn.Close()


def cmd_search(src: Path, keyword: str, limit: int) -> None:
    cn = connect(src)
    kw = keyword.lower()
    hits = 0
    for t in user_tables(cn):
        cols, rows = fetch(cn, f"SELECT * FROM [{t}]")
        matched = [(i, r) for i, r in enumerate(rows, 1)
                   if any(kw in c.lower() for c in r)]
        if not matched:
            continue
        hits += len(matched)
        print(f"\n--- {t}  ({len(matched)} of {len(rows)} rows)")
        print("    " + " | ".join(cols))
        for i, r in matched[:limit]:
            print(f"    [{i}] " + " | ".join(r))
        if len(matched) > limit:
            print(f"    ... {len(matched) - limit} more (raise --limit to see)")
    print(f"\n{hits} row(s) matched '{keyword}'")
    cn.Close()


def cmd_dump(src: Path, out: Path) -> None:
    cn = connect(src)
    tables = user_tables(cn)
    digest = hashlib.sha256(src.read_bytes()).hexdigest().upper()

    L: list[str] = [
        f"# {src.name} — full table dump",
        "",
        f"> Extracted {time.strftime('%Y-%m-%d')} from `{src.name}` via ADO/ACE OLEDB (read-only).",
        f"> Source: {src.stat().st_size / 1024:.0f} KB · SHA-256 `{digest}`",
        "> Every row of every user table. Newlines inside cells are flattened to spaces and",
        "> trailing NUL record-padding is stripped. Floating-point columns are rendered at 15",
        "> significant digits (as Access and the owning application display them), suppressing",
        "> IEEE-754 last-ULP noise. Read the MDB itself if you need bit-exact doubles.",
        "",
        "## Tables",
        "",
        "| Table | Rows | Columns |",
        "| --- | --- | --- |",
    ]

    meta: dict[str, tuple[int, list[str], list[str]]] = {}
    for t in tables:
        n = row_count(cn, t)
        cols, types = columns_of(cn, t)
        meta[t] = (n, cols, types)
        anchor = t.lower().replace(" ", "-")
        L.append(f"| [`{t}`](#{anchor}) | {n} | {len(cols)} |")

    for t in tables:
        n, cols, types = meta[t]
        schema = ", ".join(f"`{c}` *({ty})*" for c, ty in zip(cols, types))
        L += ["", f"## {t}", "", f"{n} row(s) · {len(cols)} column(s)", "",
              f"Schema: {schema}", ""]

        if n == 0:
            L.append("*(empty in this database)*")
            continue

        rs = cn.Execute(f"SELECT * FROM [{t}]")[0]

        if CODEBLOCK_IF_SINGLE_COLUMN and len(cols) == 1:
            L.append("```text")
            while not rs.EOF:
                v = rs.Fields.Item(0).Value
                L.append("" if v is None else text(v).rstrip())
                rs.MoveNext()
            L.append("```")
            rs.Close()
            continue

        L += md_table(cols, [])
        while not rs.EOF:
            L.append("| " + " | ".join(cell(rs.Fields.Item(i).Value)
                                       for i in range(len(cols))) + " |")
            rs.MoveNext()
        rs.Close()

    cn.Close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB, {len(tables)} tables, "
          f"{sum(m[0] for m in meta.values())} rows)")


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Access .mdb/.accdb reader: info/schema/table/query/search/dump")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, *extra):
        s = sub.add_parser(name)
        s.add_argument("mdb", type=Path)
        for e in extra:
            e(s)
        return s

    add("info")
    add("schema", lambda s: s.add_argument("--table", default=""))
    add("table", lambda s: s.add_argument("name"))
    add("query", lambda s: s.add_argument("sql"))
    add("search",
        lambda s: s.add_argument("keyword"),
        lambda s: s.add_argument("--limit", type=int, default=20))
    add("dump", lambda s: s.add_argument("out", type=Path))

    args = ap.parse_args()
    src: Path = args.mdb
    if not src.exists():
        sys.exit(f"no such file: {src}")

    if args.cmd == "info":
        cmd_info(src)
    elif args.cmd == "schema":
        cmd_schema(src, args.table)
    elif args.cmd == "table":
        cmd_table(src, args.name)
    elif args.cmd == "query":
        cmd_query(src, args.sql)
    elif args.cmd == "search":
        cmd_search(src, args.keyword, args.limit)
    elif args.cmd == "dump":
        cmd_dump(src, args.out)


if __name__ == "__main__":
    main()
