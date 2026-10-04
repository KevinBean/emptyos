"""Pins for scripts/scanner_lib.py — the shared scanner plumbing.

`.claude/rules/agent-cli.md` puts the JSON envelope here "so the envelope never
drifts", and `page_files` is here for the same reason: an exclusion set is a
correctness property, and twenty-three scanners each carrying their own is how
one shipped reading a minified bundle and a scaffold.

These are the shapes a caller relies on, so they are pinned rather than left to
whichever scanner happens to notice first.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "scanner_lib.py"
_spec = importlib.util.spec_from_file_location("scanner_lib", SCRIPT)
sl = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sl)


# ── envelope ─────────────────────────────────────────────────────────


def test_envelope_forces_code_ok_when_ok():
    assert sl.envelope(True, "drift", "fine")["code"] == "ok"


def test_envelope_keeps_the_code_when_not_ok():
    e = sl.envelope(False, "drift", "3 stale", {"n": 3})
    assert (e["ok"], e["code"], e["data"]) == (False, "drift", {"n": 3})


def test_envelope_keys_are_exactly_the_reserved_set():
    assert set(sl.envelope(True, "ok", "m")) == set(sl.ENVELOPE_KEYS)


# ── page_files ───────────────────────────────────────────────────────


def build(tmp: Path, *rel: str) -> None:
    for r in rel:
        p = tmp / "apps" / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")


def names(tmp: Path, **kw) -> set[str]:
    return {p.name for p in sl.page_files(tmp, **kw)}


def test_finds_html_and_js_under_pages(tmp_path):
    build(tmp_path, "public/core/a/pages/index.html", "public/core/a/pages/a.js")
    assert names(tmp_path) == {"index.html", "a.js"}


def test_ignores_files_outside_a_pages_directory(tmp_path):
    build(tmp_path, "public/core/a/app.py", "public/core/a/notes.html")
    assert names(tmp_path) == set()


def test_skips_retired_scaffold_and_vendor_trees(tmp_path):
    build(
        tmp_path,
        "_retired/old/pages/index.html",
        "_example/pages/index.html",
        "_catalog/x/pages/index.html",
        "public/core/a/pages/vendor/lib.js",
        "public/core/a/pages/keep.html",
    )
    assert names(tmp_path) == {"keep.html"}


def test_skips_minified_bundles_outside_vendor(tmp_path):
    """The suffix test is not redundant with the directory test — three
    `.min.js` files sit directly in a `pages/` directory in this repo."""
    build(tmp_path, "public/core/a/pages/vis-network.min.js",
          "public/core/a/pages/index.html")
    assert names(tmp_path) == {"index.html"}
    assert names(tmp_path, skip_minified=False) == {"vis-network.min.js", "index.html"}


def test_suffixes_are_caller_controlled(tmp_path):
    build(tmp_path, "public/core/a/pages/index.html", "public/core/a/pages/a.js")
    assert names(tmp_path, suffixes=(".html",)) == {"index.html"}


def test_result_is_sorted(tmp_path):
    build(tmp_path, "public/core/z/pages/index.html", "public/core/a/pages/index.html")
    paths = sl.page_files(tmp_path)
    assert paths == sorted(paths)


def test_walks_the_real_tree(tmp_path):
    """A guard against the walk silently matching nothing, which would make
    every scanner built on it pass vacuously."""
    assert len(sl.page_files(REPO)) > 100


# --- is_brand_island -------------------------------------------------------
# A "private namespace" is defined by what the prefix MEANS, not its length.
# Two scanners bounded the length instead and were wrong in opposite
# directions: 4 chars missed `--boards-*`, 6 chars read theme.css's own
# `--accent-ink/-dim/-bg` as private. These pin the meaning-based rule.

GLOBALS = ("accent", "bg", "text", "border", "space", "fs")


def test_private_namespace_is_an_island():
    css = ".x { --cm-panel:#111; --cm-line:#222; --cm-text:#333; }"
    assert sl.is_brand_island(css, global_prefixes=GLOBALS)


def test_long_private_prefix_is_still_private():
    css = ":root { --boards-a:#1; --boards-b:#2; --boards-c:#3; }"
    assert sl.is_brand_island(css, global_prefixes=GLOBALS)


def test_namespace_outside_root_counts():
    css = ".wrap { --cm-a:#1; --cm-b:#2; --cm-c:#3; }"
    assert sl.is_brand_island(css, global_prefixes=GLOBALS)


def test_global_family_is_not_a_private_namespace():
    css = ":root { --accent-ink:#fff; --accent-dim:#666; --accent-bg:#eee; }"
    assert not sl.is_brand_island(css, global_prefixes=GLOBALS)


def test_below_threshold_is_not_an_island():
    css = ":root { --cm-a:#1; --cm-b:#2; }"
    assert not sl.is_brand_island(css, global_prefixes=GLOBALS)


def test_unprefixed_token_is_not_a_namespace():
    # `--accent`, `--bg` — a bare token has no namespace to count.
    css = ":root { --a:#1; --b:#2; --c:#3; }"
    assert not sl.is_brand_island(css, global_prefixes=GLOBALS)


def test_real_theme_css_is_not_an_island():
    """theme.css declares only global families, by definition."""
    import sys
    sys.path.insert(0, str(REPO / "scripts"))
    import theme_css
    css = theme_css.load()
    assert not sl.is_brand_island(css, global_prefixes=theme_css.global_token_prefixes(css))
