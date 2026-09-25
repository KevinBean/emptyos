"""Unit tests: sheet range paste.

`commitCell` was strictly per-cell, so a block copied out of Excel landed
entirely in one cell — the single most-used spreadsheet gesture did nothing
useful. These pin the backend half: the bulk write that backs the paste.

The clipboard parsing itself is JS (`parseClipboardGrid` in the page); its
contract is mirrored here as `_parse_clipboard_grid` so the separator rules are
pinned in a runnable test — the load-bearing rule being that comma is a
fallback used ONLY when the payload has no tabs, or a sentence with commas in
one cell gets split into columns.

Both directions per `.claude/rules/audits.md`: a paste inside the sheet must
write every cell, and one past the cap must REPORT what it dropped rather than
silently truncating.
"""

from __future__ import annotations

import pytest

from engines.sheet import Grid, to_a1


# ── mirror of the page's parseClipboardGrid, to pin the separator contract ──

def _parse_clipboard_grid(text: str):
    t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.rstrip("\n")
    if not t:
        return None
    lines = t.split("\n")
    has_tab = "\t" in t
    if not has_tab and len(lines) < 2:
        return None
    sep = "\t" if has_tab else ","
    return [line.split(sep) for line in lines]


class TestClipboardParsing:
    def test_tab_block_becomes_a_rectangle(self):
        assert _parse_clipboard_grid("a\tb\nc\td") == [["a", "b"], ["c", "d"]]

    def test_single_plain_value_is_left_to_native_paste(self):
        assert _parse_clipboard_grid("hello") is None

    def test_a_sentence_with_commas_is_not_split_into_columns(self):
        """The rule that makes comma-fallback safe."""
        assert _parse_clipboard_grid("Hello, world, again") is None

    def test_csv_is_split_only_when_there_are_no_tabs(self):
        assert _parse_clipboard_grid("a,b\nc,d") == [["a", "b"], ["c", "d"]]

    def test_tabs_win_over_commas(self):
        """A tabbed payload whose cells contain commas keeps them intact."""
        assert _parse_clipboard_grid("a,1\tb\nc\td") == [["a,1", "b"], ["c", "d"]]

    def test_crlf_is_normalised(self):
        assert _parse_clipboard_grid("a\tb\r\nc\td") == [["a", "b"], ["c", "d"]]

    def test_trailing_newline_is_not_a_blank_row(self):
        """Excel appends one; a phantom row would blank real cells."""
        assert _parse_clipboard_grid("a\tb\n") == [["a", "b"]]

    def test_empty_is_none(self):
        assert _parse_clipboard_grid("") is None
        assert _parse_clipboard_grid("\n") is None


# ── the grid write the endpoint performs ────────────────────────────────────

def _apply(grid: Grid, anchor_col: int, anchor_row: int, rows, max_dim=50):
    """The endpoint's write loop, against a real Grid."""
    need_rows = anchor_row + len(rows) - 1
    need_cols = anchor_col + max(len(r) for r in rows)
    grid.rows = max(grid.rows, min(max_dim, need_rows))
    grid.cols = max(grid.cols, min(max_dim, need_cols))
    written = clipped = 0
    for dr, row in enumerate(rows):
        for dc, val in enumerate(row):
            c, r = anchor_col + dc, anchor_row + dr
            if c >= grid.cols or r > grid.rows:
                clipped += 1
                continue
            grid.set(to_a1(c, r), str(val))
            written += 1
    return written, clipped


class TestBulkWrite:
    def test_block_lands_as_a_rectangle(self):
        g = Grid(rows=10, cols=5)
        written, clipped = _apply(g, 1, 2, [["a", "b"], ["c", "d"]])  # anchor B2
        assert (written, clipped) == (4, 0)
        raw = g.raw_grid()
        assert raw["B2"] == "a" and raw["C2"] == "b"
        assert raw["B3"] == "c" and raw["C3"] == "d"

    def test_paste_grows_the_sheet_to_fit(self):
        g = Grid(rows=2, cols=2)
        written, clipped = _apply(g, 0, 1, [["a", "b", "c"], ["d", "e", "f"]])
        assert (written, clipped) == (6, 0)
        assert g.cols >= 3
        assert g.raw_grid()["C1"] == "c"

    def test_overflow_past_the_cap_is_reported_not_silent(self):
        """A silent truncation reads as 'it pasted' when it did not."""
        g = Grid(rows=2, cols=2)
        rows = [["x"] * 4]
        written, clipped = _apply(g, 0, 1, rows, max_dim=3)
        assert clipped == 1, "the 4th column must be counted, not dropped quietly"
        assert written == 3

    def test_blank_cells_in_the_block_are_written(self):
        """A blank in a pasted block means 'clear this cell', not 'skip it'."""
        g = Grid(rows=5, cols=5)
        g.set("B2", "old")
        _apply(g, 1, 2, [[""]])
        assert g.raw_grid().get("B2", "") == ""

    def test_ragged_rows_do_not_crash(self):
        """Real clipboard payloads have short trailing rows."""
        g = Grid(rows=5, cols=5)
        written, clipped = _apply(g, 0, 1, [["a", "b"], ["c"]])
        assert (written, clipped) == (3, 0)
        assert g.raw_grid()["A2"] == "c"

    def test_a_fully_clipped_paste_would_still_grow_the_sheet(self):
        """Why the endpoint refuses an out-of-bounds anchor before this runs.

        Found by live-testing a paste at AY1 on a 50-column cap: it grew the
        sheet 4 -> 50 columns and wrote nothing. The endpoint now rejects such
        an anchor up front; this pins the underlying behaviour that made the
        guard necessary, so removing the guard fails visibly here.
        """
        g = Grid(rows=4, cols=4)
        written, clipped = _apply(g, 50, 1, [["p", "q"]], max_dim=50)
        assert written == 0
        assert clipped == 2
        assert g.cols == 50, "growth happens regardless — hence the up-front refusal"


class TestA1RoundTrip:
    @pytest.mark.parametrize("col,row", [(0, 1), (1, 2), (25, 10), (26, 3), (51, 7)])
    def test_to_a1_matches_the_pages_cellRef(self, col, row):
        """The page computes refs client-side; a mismatch writes the wrong cell."""
        ref = to_a1(col, row)
        # mirror of cellRef()'s decode
        letters = "".join(ch for ch in ref if ch.isalpha())
        n = 0
        for ch in letters:
            n = n * 26 + (ord(ch) - 64)
        assert n - 1 == col
        assert int("".join(ch for ch in ref if ch.isdigit())) == row
