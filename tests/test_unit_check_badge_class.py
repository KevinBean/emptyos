"""Pin both directions of the undefined-badge-class scanner.

A scanner sitting at zero on a healthy tree has proved nothing until it has been
watched going red for each failure shape it claims to cover — the lesson
`check_helper_bindings` left in `.claude/rules/audits.md`. This one earns that
warning twice over, because its first cut reported 32 findings of which 11 were
false: a comment quoting the broken class, the prefix of every concatenation
double-reported as a literal, and a property access (`src.cls`) resolved through
an unrelated binding elsewhere in the file. Each of those is pinned quiet below,
beside the defects that must stay loud.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_badge_class", ROOT / "scripts" / "check_badge_class.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

# The real shared vocabulary, so the fixtures exercise the same membership test
# the tree does rather than a toy set that could drift from it.
ALLOWED = mod.allowed_classes(ROOT)


def emits(src: str) -> list[str]:
    """Every undefined class name the scanner says this source emits."""
    findings, _ = mod.scan_text(src, "fixture.js", ALLOWED)
    return sorted(c for f in findings for c in f["emits"])


def unresolved(src: str) -> int:
    return len(mod.scan_text(src, "fixture.js", ALLOWED)[1])


def test_shared_vocabulary_is_present() -> None:
    """Guard the fixtures: an empty ALLOWED would make every quiet case vacuous."""
    assert "eos-badge-status-active" in ALLOWED
    assert "eos-badge-neutral" in ALLOWED


# ── the loud direction — one case per resolution shape ───────────────────────

def test_literal_unknown_token() -> None:
    src = """h += '<span class="eos-badge eos-badge-status-waiting">thin</span>';"""
    assert emits(src) == ["eos-badge-status-waiting"]


def test_guard_map_with_a_drifted_key() -> None:
    """The music-studio shape: a local `known` map that drifted from the set."""
    src = (
        "var known = {idea:1,draft:1,recorded:1,mixed:1,published:1};\n"
        "var cls = known[st] ? 'eos-badge eos-badge-status-'+st : 'eos-badge';\n"
    )
    assert emits(src) == ["eos-badge-status-mixed", "eos-badge-status-recorded"]


def test_value_map_with_a_bad_value() -> None:
    src = (
        "var MAP = {sent:'draft', replied:'active', lost:'gone'};\n"
        "var badge = MAP[x.status] || 'draft';\n"
        """row += '<span class="eos-badge eos-badge-status-'+badge+'">y</span>';\n"""
    )
    assert emits(src) == ["eos-badge-status-gone"]


def test_ternary_with_a_bad_arm() -> None:
    src = (
        """h += '<span class="eos-badge eos-badge-status-'"""
        """ + (closed ? 'completed' : 'pending') + '">x</span>';"""
    )
    assert emits(src) == ["eos-badge-status-pending"]


def test_double_prefix_is_caught_by_the_same_rule() -> None:
    """statusVariant already returns `status-active`; prefixing again kills it.

    This is why the check concatenates prefix + token and asks the stylesheet,
    rather than checking the token against STATUS_VARIANTS: one rule covers both
    defects named in `.claude/rules/shared-frontend.md`.
    """
    src = (
        "var v = MAP[s] || 'status-active';\n"
        "var MAP = {a:'status-active'};\n"
        """h = '<span class="eos-badge eos-badge-status-'+v+'">x</span>';\n"""
    )
    assert emits(src) == ["eos-badge-status-status-active"]


# ── the quiet direction — the eleven false positives that shipped first ──────

@pytest.mark.parametrize(
    "src",
    [
        # A literal that IS defined.
        """h = '<span class="eos-badge eos-badge-status-active">on</span>';""",
        # Value map whose every value is valid — the jobs / staff shape. Four
        # such sites exist; reporting them was the >30% FP band audits.md bans.
        (
            "var MAP = {sent:'draft', replied:'active', ghosted:'shelved'};\n"
            "var badge = MAP[x.status] || 'draft';\n"
            """r += '<span class="eos-badge eos-badge-status-'+badge+'">y</span>';\n"""
        ),
        # Ternary whose arms are both valid — the projects shape.
        (
            """h += '<span class="eos-badge eos-badge-status-'"""
            """ + (isClosed ? 'completed' : 'active') + '">x</span>';"""
        ),
        # Guard map entirely inside the shared set.
        (
            "var known = {idea:1,draft:1,published:1};\n"
            "var cls = known[st] ? 'eos-badge eos-badge-status-'+st : 'eos-badge';\n"
        ),
        # Delegating to the helper that owns the concatenation.
        """var cls = 'eos-badge eos-badge-' + EOS_UI.statusVariant(status, map);""",
        # A comment quoting the broken class to warn about it — eos-components.js
        # does exactly this, and the first cut read the warning as the defect.
        "// `'eos-badge-status-' + status` emits a class with no styling.\n",
        "/* eos-badge-status-recorded is what NOT to write */\n",
    ],
)
def test_quiet(src: str) -> None:
    assert emits(src) == []


def test_concat_prefix_is_not_also_reported_as_a_literal() -> None:
    """`\\b` stops before the trailing `-`, so the prefix reaches the literal rule.

    Unguarded, this puts a second finding on every concatenation site — including
    the four that concatenate a perfectly valid variant.
    """
    src = """h = '<span class="eos-badge eos-badge-status-'+badge+'">y</span>';"""
    assert "eos-badge-status" not in emits(src)


def test_property_access_is_unresolved_not_guessed() -> None:
    """podcast's `'eos-badge-' + src.cls` resolved via an unrelated ternary.

    An unsound answer is worse than admitting the expression cannot be read, so
    a property access must fall through to the advisory bucket, not to a finding.
    """
    src = (
        "var pick = flag ? 'spk-a' : 'spk-b';\n"
        """b += '<span class="eos-badge eos-badge-' + src.cls + '">x</span>';\n"""
    )
    assert emits(src) == []
    assert unresolved(src) == 1


def test_locally_defined_class_is_accepted(tmp_path: Path) -> None:
    """An app may extend the family in its own stylesheet; that is not a defect."""
    page = tmp_path / "index.html"
    src = """<span class="eos-badge eos-badge-status-brewed">brewed</span>"""
    page.write_text(src, encoding="utf-8")

    assert emits(src) == ["eos-badge-status-brewed"]  # shared CSS alone: a finding

    (tmp_path / "app.css").write_text(
        ".eos-badge-status-brewed { color: var(--amber); }", encoding="utf-8"
    )
    local = mod.local_classes(page, src)
    assert "eos-badge-status-brewed" in local
    assert mod.scan_text(src, "index.html", ALLOWED | local)[0] == []


def test_blank_comments_preserves_offsets_and_lines() -> None:
    """Line numbers are computed from the blanked text, so it must not shrink."""
    src = "a\n// eos-badge-status-nope\nb\n"
    out = mod.blank_comments(src)
    assert len(out) == len(src)
    assert out.count("\n") == src.count("\n")
    assert "nope" not in out


def test_tree_is_clean() -> None:
    """The tree emits no undefined badge class — the state the preflight gate pins.

    The file-count guard matters as much as the finding count: if the page walk
    ever collapsed to a handful of files this would pass for the wrong reason,
    which is the vacuous pass `.claude/rules/audits.md` § failure mode 3 names.
    """
    files = mod.collect_files(ROOT)
    assert len(files) > 100, "page walk collapsed — the scan would be vacuous"

    found: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        found += [f"{rel}:{h['line']} {','.join(h['emits'])}" for h in scan_hits(text, rel, path)]
    assert not found, "undefined badge classes:\n  " + "\n  ".join(found)


def scan_hits(text: str, rel: str, path: Path) -> list[dict]:
    return mod.scan_text(text, rel, ALLOWED | mod.local_classes(path, text))[0]
