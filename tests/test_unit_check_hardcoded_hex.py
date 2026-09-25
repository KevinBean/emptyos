"""Pin check_hardcoded_hex in BOTH directions.

A colour scanner that only ever runs green has proved nothing (`.claude/rules/
audits.md` § Failure mode 3). Every exclusion below is a rule the scanner claims
to implement, so each gets a paired test: one input it MUST flag, one it MUST
NOT. The exclusions are not decoration — four of them were filter bugs that
shipped in a hand-rolled version of this scan on 2026-09-03, and three of those
pointed the migration at the best-behaved apps in the tree.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "check_hardcoded_hex", ROOT / "scripts" / "check_hardcoded_hex.py"
)
assert _SPEC and _SPEC.loader
sys.path.insert(0, str(ROOT / "scripts"))
chh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chh)


def _tree(tmp_path: Path, rel: str, body: str) -> Path:
    """Write one page file into a fake repo root and return that root."""
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return tmp_path


def _hits(root: Path, **kw) -> list[dict]:
    return chh.scan(root, **kw)[0]


def _islands(root: Path, **kw) -> list[str]:
    return chh.scan(root, **kw)[2]


APP = "apps/public/standard/demo/pages/index.html"


# --- the positive direction: it must actually bite -------------------------

def test_flags_a_bare_six_digit_hex(tmp_path):
    root = _tree(tmp_path, APP, "<style>.x { color: #c0392b; }</style>")
    hits = _hits(root)
    assert [h["hex"] for h in hits] == ["#c0392b"]
    assert hits[0]["line"] == 1


def test_flags_a_three_digit_hex(tmp_path):
    """The claim that 3-digit forms 'almost never appear' was false and cost a
    half-migrated file: eink-bridge's base `pre { background: #111 }` was left
    behind while its own inline overrides were tokenised."""
    root = _tree(tmp_path, APP, "<style>.x { background: #111; }</style>")
    assert [h["hex"] for h in _hits(root)] == ["#111"]


def test_flags_hex_in_a_sibling_css_and_js_file(tmp_path):
    """Scope is {html,css,js} — page logic routinely builds style strings in a
    sibling .js, and scanning only top-level HTML lets a migration wander into
    files the audit never measured."""
    root = _tree(tmp_path, "apps/public/standard/demo/pages/a.css", ".x { color: #abcdef; }")
    _tree(root, "apps/public/standard/demo/pages/b.js", "var s = 'color: #123456';")
    assert len(_hits(root)) == 2


def test_flags_a_second_hex_on_the_same_line(tmp_path):
    root = _tree(tmp_path, APP, "<style>.x { color: #aabbcc; border-color: #ddeeff; }</style>")
    assert len(_hits(root)) == 2


# --- the negative direction: each documented exclusion ---------------------

def test_var_fallback_is_not_a_violation(tmp_path):
    """The token is already wired. This was 86 of 189 hits and concentrated in
    the apps that had done the right thing."""
    root = _tree(tmp_path, APP, "<style>.x { color: var(--red, #f85149); }</style>")
    assert _hits(root) == []


def test_var_fallback_does_not_mask_a_real_hex_on_the_same_line(tmp_path):
    """Stripping the fallback must not blind the rest of the line."""
    root = _tree(
        tmp_path, APP, "<style>.x { color: var(--red, #f85149); background: #0d0d0d; }</style>"
    )
    assert [h["hex"] for h in _hits(root)] == ["#0d0d0d"]


def test_theme_color_meta_is_out_of_scope_via_the_quote_anchor(tmp_path):
    """No dedicated rule for this any more — the quote-anchor already covers it.

    A `theme-color` substring test used to live in the scanner. It was dead
    (the value is quote-opened) AND over-wide (it exempted any line containing
    the string, e.g. `.theme-color-swatch{background:#0d0d0d}`). Deleted; this
    test now pins the mechanism that actually does the work.
    """
    assert _hits(_tree(tmp_path, APP, '<meta name="theme-color" content="#0d1117">')) == []
    swatch = "<style>.theme-color-swatch{background:#0d0d0d}</style>"
    assert [h["hex"] for h in _hits(_tree(tmp_path, APP, swatch))] == ["#0d0d0d"]


def test_brand_island_is_exempt_by_its_own_namespace(tmp_path):
    """Three tokens in a private family = a deliberate visual island."""
    body = "<style>:root{--cm-bg:#101014;--cm-line:#222;--cm-text:#eee}.x{color:#abcdef}</style>"
    root = _tree(tmp_path, APP, body)
    assert _hits(root) == []


def test_two_private_tokens_is_not_yet_an_island(tmp_path):
    """The island threshold is 3 — two is routinely just drift."""
    body = "<style>:root{--cm-bg:#101014;--cm-line:#222}.x{color:#abcdef}</style>"
    assert _hits(_tree(tmp_path, APP, body)) != []


def test_island_marker_exempts_the_whole_file(tmp_path):
    body = "<!-- design-hex: island - standalone doc -->\n<style>.x{color:#abcdef}</style>"
    assert _hits(_tree(tmp_path, APP, body)) == []


def test_island_marker_without_a_reason_is_refused(tmp_path):
    """A bare marker would let an exemption ship with no audit context."""
    body = "<!-- design-hex: island -->\n<style>.x{color:#abcdef}</style>"
    assert _hits(_tree(tmp_path, APP, body)) != []


def test_ignore_marker_exempts_only_its_own_line(tmp_path):
    """The bounded form. A wider 'until blank line' rule was tried and dropped:
    this codebase writes dense CSS with no blank lines, so it would have run to
    the end of the block and silently covered hexes added later."""
    body = (
        "<style>\n"
        ".a { color: #abcdef; } /* design-hex: ignore - brand hue */\n"
        ".b { color: #123456; }\n"
        "</style>"
    )
    hits = _hits(_tree(tmp_path, APP, body))
    assert [h["hex"] for h in hits] == ["#123456"], "the NEXT line must stay flagged"


def test_hex_named_inside_a_block_comment_is_not_a_declaration(tmp_path):
    """The continuation line is the hard case — it starts with a word, so a
    line-start test cannot see it. assistant/index.html documents its measured
    contrast ratio this way, and flagging it punishes the file for explaining
    itself."""
    body = (
        "<style>\n"
        "/* Provider brand hues, blended toward --text so they stay readable\n"
        "   (raw #da7756 measured 2.33:1 on digital-garden). */\n"
        ".a { color: var(--text); }\n"
        "</style>"
    )
    assert _hits(_tree(tmp_path, APP, body)) == []


def test_a_url_is_not_a_line_comment(tmp_path):
    """`//` after a scheme must not blank the rest of the line."""
    body = "<style>.a{background:url(https://x.test/i.png);color:#abcdef}</style>"
    assert [h["hex"] for h in _hits(_tree(tmp_path, APP, body))] == ["#abcdef"]


def test_a_protocol_relative_url_is_not_a_line_comment(tmp_path):
    """The scheme-less form has no `:` to guard against, so a `(?<![:/])`
    lookbehind treats `//cdn...` as a comment and blanks every colour after it
    on that line — a silent miss that reads as a clean file."""
    body = '<a href="//cdn.example/x">y</a><style>.a{color:#0d0d0d}</style>'
    assert [h["hex"] for h in _hits(_tree(tmp_path, APP, body))] == ["#0d0d0d"]


def test_a_marker_inside_a_string_literal_is_not_a_marker(tmp_path):
    """Markers are read off a comment-only projection.

    Read from raw text, a page that merely *documents* this convention — a
    design-system viewer, a `<pre>` sample — would exempt itself by quoting the
    marker, which is the one page most likely to quote it.
    """
    body = (
        "<script>var doc = 'design-hex: island - explaining the convention';</script>"
        "<style>.a{color:#0d0d0d}</style>"
    )
    assert [h["hex"] for h in _hits(_tree(tmp_path, APP, body))] == ["#0d0d0d"]


def test_an_island_marker_below_the_code_it_silences_is_refused(tmp_path):
    """A file-level exemption must sit in the header, where a reviewer reading
    top-down actually meets it — not bolted onto the bottom to quiet a gate."""
    body = (
        "<style>.a{color:#0d0d0d}</style>\n"
        + "\n" * 60
        + "<!-- design-hex: island - added later at the bottom -->\n"
    )
    assert [h["hex"] for h in _hits(_tree(tmp_path, APP, body))] == ["#0d0d0d"]


def test_black_and_white_have_no_token_to_migrate_to(tmp_path):
    body = "<style>.a{background:#fff;color:#000}.b{border-color:#ffffff}</style>"
    assert _hits(_tree(tmp_path, APP, body)) == []


def test_black_and_white_are_skipped_with_an_alpha_suffix_too(tmp_path):
    """`#0008` is a scrim, the same class as `#000`. Skipping only the opaque
    forms would flag `#0008` while ignoring `#000` on the next line — an
    inconsistency that reads as a scanner bug rather than a rule.

    One hex per line on purpose: three in a single declaration is a *ramp*, so a
    one-line fixture passes via `_looks_like_ramp` and proves nothing about the
    ink/paper rule. (Caught by mutation — the first version of this test did
    exactly that.)
    """
    body = "<style>\n.a{background:#0008}\n.b{background:#000c}\n.c{color:#ffffff80}\n</style>"
    assert _hits(_tree(tmp_path, APP, body)) == []


def test_an_alpha_hex_that_is_not_ink_or_paper_is_flagged(tmp_path):
    """`_VAR_FALLBACK` already accepted `{3,8}`; excluding alpha forms from the
    match was an inconsistency inside one file. `#0d0d0dcc` is as hardcoded as
    `#0d0d0d`."""
    body = "<style>.a{background:#0d0d0dcc}.b{color:#abcd}</style>"
    assert len(_hits(_tree(tmp_path, APP, body))) == 2


def test_a_three_stop_palette_is_content_not_chrome(tmp_path):
    """A categorical series carries identity; re-theming it destroys the
    distinction it exists to draw."""
    body = "<style>.chart{--series:#4dabf7,#40c057,#fab005}</style>"
    assert _hits(_tree(tmp_path, APP, body)) == []


def test_a_two_stop_pair_is_still_flagged(tmp_path):
    """Two hexes in ONE declaration — the real shape, a gradient.

    The earlier fixture put them in two declarations, where the per-declaration
    ramp rule can never fire, so `_RAMP_MIN` had no lower-bound pin and dropping
    it to 2 left the suite green. A 2-stop gradient is exactly what forced the
    hand-written markers in improv, so it is the case that must stay flagged.
    """
    body = "<style>.a{background:linear-gradient(135deg,#4dabf7,#40c057)}</style>"
    assert len(_hits(_tree(tmp_path, APP, body))) == 2


def test_three_properties_on_one_line_are_drift_not_a_palette(tmp_path):
    """The ramp rule is scoped to a single DECLARATION, never the whole line.

    This codebase writes dense one-line rules, so a per-line rule would read
    three unrelated properties as a palette and skip exactly the drift the
    scanner exists to catch — while still reporting a clean tree.
    """
    body = "<style>.a{color:#111;background:#222;border-color:#333}</style>"
    assert len(_hits(_tree(tmp_path, APP, body))) == 3


@pytest.mark.parametrize(
    "body",
    [
        '<svg><rect fill="#123456"/></svg>',
        '<svg><rect stroke="#123456"/></svg>',
        "<script>ctx.fillStyle = '#abcdef';</script>",
        '<div data-color="#abcdef">x</div>',
    ],
)
def test_a_quote_opened_hex_is_out_of_scope(tmp_path, body):
    """A DECISION, not an accident of the anchor — pinned so nobody 'fixes' it.

    Widening the anchor to accept `"`/`'` takes this tree from 0 findings to 304
    across 49 files, nearly all canvas/SVG drawing code and chart palettes where
    the literal IS the correct value. That is far past the 30% false-positive
    bar in audits.md, and this scanner gates.
    """
    assert _hits(_tree(tmp_path, APP, body)) == []


def test_a_css_value_hex_is_still_in_scope_next_to_a_quote(tmp_path):
    """The boundary above must not swallow a real declaration on the same line."""
    body = '<div style="color:#abcdef" data-x="#112233">y</div>'
    assert [h["hex"] for h in _hits(_tree(tmp_path, APP, body))] == ["#abcdef"]


def test_personal_skip_works_from_a_relative_root(tmp_path, monkeypatch):
    """`/apps/personal/` matched against an ABSOLUTE path silently stops working
    when the root is relative — and it fails open, producing more findings, so
    it reads as the scanner working rather than as a broken exclusion."""
    _tree(tmp_path, "apps/personal/mine/pages/index.html", "<style>.x{color:#abcdef}</style>")
    monkeypatch.chdir(tmp_path)
    assert chh.scan(Path("."))[0] == []


def test_html_entity_is_not_a_colour(tmp_path):
    """The value-anchor, not a length bound, is what excludes entities."""
    root = _tree(tmp_path, APP, "<p>&#8203;&#128424;</p>")
    assert _hits(root) == []


@pytest.mark.parametrize(
    "rel",
    [
        "apps/public/standard/demo/_retired/old/pages/index.html",
        "apps/public/standard/demo/pages/index.legacy.html",
        "apps/extension/engineering/x/dist/pages/index.html",
        "apps/public/standard/demo/pages/vendor/lib.js",
        "apps/public/standard/demo/pages/lib.min.js",
    ],
)
def test_unshipped_or_third_party_trees_are_skipped(tmp_path, rel):
    """`_retired` is not loaded by the app loader at all, so a finding there is
    in code that cannot run — it supplied the top two 'offenders' (195 hits) in
    the hand-rolled version of this scan."""
    assert _hits(_tree(tmp_path, rel, "<style>.x{color:#abcdef}</style>")) == []


def test_personal_is_skipped_by_default_and_visible_on_demand(tmp_path):
    """Gitignored, absent from CI, and the one tree a review pass may not edit —
    a gate whose findings nobody may fix is a gate that gets switched off."""
    root = _tree(tmp_path, "apps/personal/mine/pages/index.html", "<style>.x{color:#abcdef}</style>")
    assert _hits(root) == []
    assert len(_hits(root, include_personal=True)) == 1


# --- the tree itself -------------------------------------------------------

def test_auto_island_count_is_pinned():
    """An auto-detected island buys a whole-file exemption with no marker, no
    reason, and nothing in the diff — a wider escape hatch than the markers this
    scanner argues for. Pinning the count means a page that quietly goes dark by
    adding three `--myapp-*` tokens has to be noticed here."""
    _, _, islands = chh.scan(ROOT)
    assert len(islands) <= 15, "new auto-exempt island(s):\n" + "\n".join(sorted(islands))


def test_main_gates_and_never_masks_its_exit_code(tmp_path, capsys):
    """`main()` decides what actually gates and had no test — the exact omission
    audits.md records from a previous checker. Returning `len(findings)` would
    exit 0 on exactly 256 findings, i.e. succeed on its worst-ever run."""
    # THREE findings, not one: a one-finding fixture cannot tell `return 1` from
    # `return len(findings)` — both give 1. (Caught by mutation; the first
    # version of this very test was itself vacuous.)
    _tree(tmp_path, APP, "<style>.a{color:#0d0d0d;background:#123456;border-color:#abcdef}</style>")
    assert len(_hits(tmp_path)) == 3
    assert chh.main(["--root", str(tmp_path)]) == 1
    assert chh.main(["--root", str(tmp_path / "empty")]) == 0


def test_repo_is_clean():
    findings, scanned, _ = chh.scan(ROOT)
    assert scanned > 100, "the walk found almost nothing — scope is probably wrong"
    assert findings == [], "\n".join(
        f"{f['file']}:{f['line']} {f['hex']}  {f['text']}" for f in findings[:20]
    )
