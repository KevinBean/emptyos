---
paths:
  - "emptyos/sdk/tabular.py"
  - "emptyos/sdk/utils.py"
  - "apps/**"
---
# Text-First Data Rule — SQL as an ephemeral calculator over text

EmptyOS processes data in **text form** (markdown tables, frontmatter, CSV)
wherever possible. When text-shaped data needs real querying — joins, window
functions, HAVING, pivots — the answer is `emptyos/sdk/tabular.py`: an
**ephemeral in-memory DuckDB** loaded from text-derived rows per call. The
database is a calculator, never a store.

**Core:** `emptyos/sdk/tabular.py` (`query_rows`, `query_files`, `query_mixed`,
`flatten_note_records`, `QueryUnavailable`, `duckdb_available`) — pure,
unit-tested without a daemon (`tests/test_sdk_tabular.py`). **App surface:**
`BaseApp.query()` / `BaseApp.query_notes()` (fail-soft — return
`{"error", "hint"}` instead of raising). **Companions:**
`parse_markdown_table` / `format_markdown_table` / `rows_to_csv` /
`csv_to_rows` in `emptyos/sdk/utils.py` — the same list-of-dicts interchange
shape on the text side. **First consumer:** `apps/public/standard/expense/`
`_sql_breakdown` (dark: `[apps.expense] feature.sql-analytics.enabled`).

## Principles and carve-outs

1. **Process data in text format as much as possible.** Markdown tables,
   frontmatter, and CSV are the source of truth — human-readable,
   vault-owned, git/sync-friendly, editable without EmptyOS running.
2. **Database tools serve the user's data processing, NOT EmptyOS's own
   storage.** DuckDB exists to answer questions over text-derived rows. No
   app may adopt it (or any new DB) as its store just because querying got
   easy — vault notes and the existing small per-app SQLite/JSON telemetry
   stores remain the only persistence layers.
3. **Carve-out: read-only ATTACH of existing SQLite.** Machine-telemetry
   files that already exist (`TimeSeriesCounter` DBs, syslog) may be ATTACHed
   `READ_ONLY` for querying. Never read-write, never a new `.db` created for
   query convenience.
4. **Carve-out: a shipped, read-only reference pack.** A build may ship a
   SQLite file of reference data every user reads and nobody writes at runtime
   — the dictionary's definition pack
   (`apps/extension/english-learning/dictionary/definition_pack.py`) is the one
   instance. It is a build asset, not a store: it holds no user data, is opened
   `mode=ro`, and is replaced by building a new file. SQLite rather than text
   because one learner daemon runs per user, and a point lookup reads one row
   where loading a text pack would hold the whole thing in every daemon's
   memory. Anything a user writes still goes to the vault.

## The ephemeral-only rule (load-bearing)

Every query opens `duckdb.connect(":memory:")` and closes it in a `finally`.
No `.duckdb` file is ever created; no state survives between calls; nothing
in the system may depend on a DuckDB database existing.

**Grep invariant:** `duckdb.connect(` appears in exactly one file —
`emptyos/sdk/tabular.py` — and only with `":memory:"`. A second call site or
a path argument is a rule violation, not a refactor.

If a query result deserves persistence, the **caller** writes it back as
text: a markdown table (`format_markdown_table`) or CSV in the vault. The
result is content; the database was never there.

## Decision table — which tool for which job

| Job | Use | Don't use |
|---|---|---|
| One grouping + one aggregate over one list | Plain Python (dict/comprehension) — expense's `_summarize` is the canonical example | SQL ceremony for a 5-line loop |
| Board/dashboard views over board items | `board_engine.aggregate()` + `sdk/formulas.py` — the view layer owns its own aggregation | Rerouting boards through tabular |
| Joins across ≥2 row sources, window fns, HAVING, pivots, month-over-month — anything >~15 lines of dict-wrangling | `query_rows` / `BaseApp.query` | Hand-rolled triple-nested dicts |
| Vault notes as rows (frontmatter → columns) | `BaseApp.query_notes` (composes `vault_query` → `flatten_note_records` → SQL) | A new per-app frontmatter flattener |
| An existing CSV artifact or SQLite telemetry file as input | `query_files` (CSV view / read-only ATTACH) | Copying the data into a new store first |

Type note: frontmatter values are strings. `_infer_columns` types pure
numeric literals (`"42"`, `"3.5"`) automatically; formatted values (`"$35"`,
`"1,200"`, dates) stay VARCHAR — use `TRY_CAST` / DuckDB date casts in SQL.

## Fail-soft contract

The `data` extra (`pip install 'emptyos[data]'`) is optional. Pure module
functions raise `QueryUnavailable` with the install hint; `BaseApp.query`
converts to `{"error", "hint"}`. **Apps must degrade gracefully without the
extra** — the expense insight enrichment returns `None` and the endpoint
behaves exactly as before. A feature that hard-requires duckdb to render its
primary surface is mis-designed; SQL enriches, it never gates.

The duckdb `sqlite` extension autoloads over the network once per duckdb
version; when offline that surfaces as `QueryUnavailable` with a one-time
`install_extension('sqlite')` hint — also fail-soft.

## Never

- **SQL over HTTP.** No route accepts raw SQL from a client — injection +
  exfiltration surface (vault_query already needed a tag allowlist for
  external agents; raw SQL is strictly worse). An operator-facing ad-hoc
  query surface, if ever wanted, ships as a local CLI command
  (`docs/DEFERRED-WORK.md`).
- **NL→SQL** — deferred; when built it's a rule-12 prompt artifact feeding
  `query_rows`, still never exposed over HTTP.
- **Persisting results anywhere but vault markdown/CSV.**
- **dbt-style transform graphs / materialized views.** The answer is the
  output; recompute from text.
- **Changing vault storage itself.** VaultIndex stays in-memory over
  markdown; this layer reads its output, never replaces it.

## Cross-references

- `emptyos/sdk/utils.py` § Markdown tables — the text side of the pipeline
  ("CSV is a transient transport format, never the source of truth").
- `.claude/rules/boards-as-view-layer.md` — boards' own aggregation layer;
  deliberately separate from this one.
- `docs/DEFERRED-WORK.md` — NL→SQL verb, app-analytics ad-hoc SQL CLI,
  boards-on-query_rows (all deferred with triggers).
- Audit + external-tool verdicts that led here:
  `30_Resources/EmptyOS/insights/outputs/2026-07-03-data-engineering-audit.md`
  (DuckDB = borrow; dbt/GE/Airflow/Dagster/Prefect/BI platforms = skip).
