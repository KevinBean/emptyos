"""Text-first tabular layer — `emptyos/sdk/tabular.py`.

Ephemeral in-memory DuckDB over text-derived rows: markdown tables /
vault_query records / CSV files / read-only SQLite ATTACH. The module must
import (and its pure helpers work) without duckdb installed; SQL tests skip
when the `data` extra is absent.

Run: python -m pytest tests/test_sdk_tabular.py -v
"""
from __future__ import annotations

import importlib.util
import sqlite3

import pytest

from emptyos.sdk.tabular import (
    QueryUnavailable,
    _infer_columns,
    duckdb_available,
    flatten_note_records,
    query_files,
    query_mixed,
    query_rows,
)
from emptyos.sdk.utils import parse_markdown_table

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None

pytestmark = pytest.mark.unit


# ── Pure helpers — no duckdb needed ─────────────────────────────────────────


class TestPureHelpers:
    def test_query_raises_unavailable_without_duckdb(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "duckdb":
                raise ImportError("no module named duckdb")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(QueryUnavailable, match=r"emptyos\[data\]"):
            query_rows("SELECT 1", {"t": [{"a": 1}]})

    def test_duckdb_available_reports_reality(self):
        assert duckdb_available() is HAS_DUCKDB

    def test_flatten_note_records(self):
        records = [
            {
                "path": "20_Areas/x.md",
                "name": "x",
                "folder": "20_Areas",
                "ext": ".md",
                "tags": ["job-application", "person"],
                "properties": {"company": "Acme", "salary": "150000", "name": "override-me"},
            }
        ]
        rows = flatten_note_records(records)
        assert rows[0]["path"] == "20_Areas/x.md"
        assert rows[0]["company"] == "Acme"
        assert rows[0]["tags"] == "job-application, person"
        # reserved key wins; colliding property is prefixed
        assert rows[0]["name"] == "x"
        assert rows[0]["prop_name"] == "override-me"
        assert "ext" not in rows[0]  # not in default keep

    def test_infer_columns_types(self):
        rows = [
            {"i": 1, "f": 1.5, "b": True, "s": "hello", "num_str": "42", "money": "$35", "empty": None},
            {"i": 2, "f": 2, "b": False, "s": "world", "num_str": "3.5", "money": "1,200", "empty": None},
        ]
        cols = dict(_infer_columns(rows))
        assert cols["i"] == "BIGINT"
        assert cols["f"] == "DOUBLE"
        assert cols["b"] == "BOOLEAN"
        assert cols["s"] == "VARCHAR"
        assert cols["num_str"] == "DOUBLE"  # "42" + "3.5" mix → DOUBLE
        assert cols["money"] == "VARCHAR"  # "$35" / "1,200" stay text
        assert cols["empty"] == "VARCHAR"  # all-None → VARCHAR

    def test_infer_columns_union_of_keys_first_seen_order(self):
        rows = [{"a": 1}, {"b": 2, "a": 3}, {"c": "x"}]
        assert [c for c, _ in _infer_columns(rows)] == ["a", "b", "c"]


# ── SQL tests — need the data extra ─────────────────────────────────────────


@pytest.mark.skipif(not HAS_DUCKDB, reason="data extra not installed")
class TestQueryRows:
    def test_markdown_table_roundtrip(self):
        """The full text-first pipeline: vault markdown → rows → SQL → rows."""
        md = (
            "| date | amount | category |\n"
            "|---|---|---|\n"
            "| 2026-07-01 | 12.50 | food |\n"
            "| 2026-07-02 | 40 | transport |\n"
            "| 2026-07-02 | 7.5 | food |\n"
        )
        rows = parse_markdown_table(md)
        out = query_rows(
            "SELECT category, SUM(amount) AS total FROM t GROUP BY category ORDER BY total DESC",
            {"t": rows},
        )
        assert out == [
            {"category": "transport", "total": 40.0},
            {"category": "food", "total": 20.0},
        ]

    def test_heterogeneous_rows_null_fill(self):
        rows = [{"a": 1, "b": "x"}, {"a": 2}]
        out = query_rows("SELECT a, b FROM t ORDER BY a", {"t": rows})
        assert out == [{"a": 1, "b": "x"}, {"a": 2, "b": None}]

    def test_empty_rows_and_empty_tables(self):
        assert query_rows("SELECT COUNT(*) AS n FROM t", {"t": []}) == [{"n": 0}]
        assert query_rows("SELECT 1 AS one", {}) == [{"one": 1}]

    def test_params(self):
        rows = [{"a": 1}, {"a": 2}, {"a": 3}]
        out = query_rows("SELECT a FROM t WHERE a > ? ORDER BY a", {"t": rows}, params=[1])
        assert [r["a"] for r in out] == [2, 3]

    def test_bad_table_name_rejected(self):
        with pytest.raises(ValueError, match="invalid table name"):
            query_rows("SELECT 1", {"bad name; DROP": [{"a": 1}]})

    def test_join_across_two_tables(self):
        expenses = [{"cat": "food", "amount": 10}, {"cat": "toys", "amount": 99}]
        budgets = [{"cat": "food", "cap": 500}, {"cat": "toys", "cap": 50}]
        out = query_rows(
            "SELECT e.cat FROM e JOIN b ON e.cat = b.cat WHERE e.amount > b.cap",
            {"e": expenses, "b": budgets},
        )
        assert out == [{"cat": "toys"}]

    def test_weird_column_names_quoted(self):
        rows = [{"col with space": 1, 'quo"te': "x"}]
        out = query_rows('SELECT "col with space" AS c FROM t', {"t": rows})
        assert out == [{"c": 1}]

    def test_ephemerality_no_state_between_calls(self):
        import duckdb

        query_rows("SELECT * FROM t", {"t": [{"a": 1}]})
        with pytest.raises(duckdb.Error):  # table from call 1 gone in call 2
            query_rows("SELECT * FROM t", {})


@pytest.mark.skipif(not HAS_DUCKDB, reason="data extra not installed")
class TestQueryFiles:
    def test_csv_file(self, tmp_path):
        p = tmp_path / "data.csv"
        p.write_text("name,score\nalice,10\nbob,20\n", encoding="utf-8")
        out = query_files("SELECT name, score FROM d WHERE score > 15", {"d": p})
        assert out == [{"name": "bob", "score": 20}]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            query_files("SELECT 1", {"d": tmp_path / "nope.csv"})

    def test_unsupported_suffix_raises(self, tmp_path):
        p = tmp_path / "data.parquet"
        p.write_bytes(b"")
        with pytest.raises(ValueError, match="unsupported file type"):
            query_files("SELECT 1", {"d": p})

    def test_sqlite_attach_readonly_timeseries(self, tmp_path):
        """The telemetry carve-out — read-only ATTACH over a real TimeSeriesCounter DB."""
        from emptyos.sdk.time_series import TimeSeriesCounter

        db = tmp_path / "app.db"
        conn = sqlite3.connect(db)
        counter = TimeSeriesCounter(conn, "usage", dims=["app"])
        counter.bump(dims={"app": "task"})
        counter.bump(dims={"app": "task"})
        counter.bump(dims={"app": "journal"})
        conn.close()

        try:
            out = query_files(
                "SELECT app, SUM(count) AS n FROM t.usage GROUP BY app ORDER BY n DESC",
                {"t": db},
            )
        except QueryUnavailable:
            pytest.skip("duckdb sqlite extension not available offline")
        assert out == [{"app": "task", "n": 2}, {"app": "journal", "n": 1}]

    def test_mixed_rows_and_file(self, tmp_path):
        p = tmp_path / "caps.csv"
        p.write_text("cat,cap\nfood,500\ntoys,50\n", encoding="utf-8")
        out = query_mixed(
            "SELECT e.cat FROM e JOIN caps ON e.cat = caps.cat WHERE e.amount > caps.cap",
            tables={"e": [{"cat": "toys", "amount": 99}, {"cat": "food", "amount": 10}]},
            sources={"caps": p},
        )
        assert out == [{"cat": "toys"}]
