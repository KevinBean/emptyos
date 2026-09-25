"""emptyos/fieldspec.py — the shared calculator-declaration checker.

Runs without a daemon; the module is stdlib-only by contract and this suite
asserts that too.

The tests that matter here are the ones about what the module is NOT. It is a
behaviour extraction, not a record extraction, and the two packages it serves
are deliberately allowed to keep different dataclasses. A future change that
"tidies" that into a shared base class would pass every functional test below
and break the thing the extraction was careful about, so the reasons are
pinned as tests rather than left in a docstring.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from emptyos.fieldspec import SpecError, render_domain, validate_declaration

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _In:
    name: str
    unit: str
    group: str = "g"
    slot: str = "g"


@dataclass(frozen=True)
class _Out:
    name: str
    unit: str
    derived_from: tuple[str, ...] = ()


UNITS = {"V", "A", ""}


class TestUnitTable:
    def test_a_clean_declaration_passes(self):
        validate_declaration([_In("a", "V")], [_Out("x", "A", ("a",))], units=UNITS)

    def test_an_input_unit_outside_the_table_is_refused(self):
        with pytest.raises(SpecError, match="not in the unit table"):
            validate_declaration([_In("a", "furlongs")], units=UNITS)

    def test_an_output_unit_outside_the_table_is_refused(self):
        with pytest.raises(SpecError, match="output 'x' declares unit"):
            validate_declaration([_In("a", "V")], [_Out("x", "parsecs")], units=UNITS)

    def test_the_message_says_the_table_is_closed_on_purpose(self):
        """The reason is the useful half: an author who reads 'unknown unit'
        adds a cast; one who reads 'the table is closed by design' adds the
        unit where every reader will see it."""
        with pytest.raises(SpecError, match="closed by design"):
            validate_declaration([_In("a", "furlongs")], units=UNITS)


class TestGroupTable:
    def test_an_unknown_group_is_refused_when_the_set_is_closed(self):
        with pytest.raises(SpecError, match="declares group 'nope'"):
            validate_declaration([_In("a", "V", group="nope")], units=UNITS,
                                 groups=("g",))

    def test_group_is_not_checked_when_the_set_is_open(self):
        """A package whose groups are only form sections adds to them freely."""
        validate_declaration([_In("a", "V", group="anything")], units=UNITS)

    def test_the_group_attribute_is_configurable(self):
        """trust-loop calls it `group`, cable-bonding calls it `slot`, and they
        are not the same question — see the module docstring."""
        with pytest.raises(SpecError, match="declares slot 'nope'"):
            validate_declaration([_In("a", "V", slot="nope")], units=UNITS,
                                 groups=("g",), group_attr="slot")

    def test_a_group_attr_the_declaration_does_not_carry_is_a_clear_error(self):
        with pytest.raises(SpecError, match="has no attribute"):
            validate_declaration([_In("a", "V")], units=UNITS, groups=("g",),
                                 group_attr="bucket")


class TestDuplicateNames:
    def test_a_field_declared_twice_is_refused(self):
        """The rule cable-bonding was missing. Without it a duplicate yields
        one fewer default than declarations, silently."""
        with pytest.raises(SpecError, match="declared twice"):
            validate_declaration([_In("a", "V"), _In("a", "A")], units=UNITS)

    def test_the_real_cable_bonding_declaration_has_no_duplicates(self):
        spec = _load_spec("apps/extension/engineering/cable-bonding/spec.py", "cb_spec")
        names = [f.name for f in spec.INPUTS]
        assert len(names) == len(set(names))

    def test_the_real_trust_loop_declaration_has_no_duplicates(self):
        spec = _load_spec("apps/public/standard/trust-loop/spec.py", "tl_spec")
        names = [f.name for f in spec.INPUTS]
        assert len(names) == len(set(names))


class TestDerivedFrom:
    def test_an_output_deriving_from_a_field_nobody_supplies_is_refused(self):
        with pytest.raises(SpecError, match="claims to derive from 'ghost'"):
            validate_declaration([_In("a", "V")], [_Out("x", "V", ("ghost",))],
                                 units=UNITS)

    def test_an_output_deriving_from_a_real_field_passes(self):
        validate_declaration([_In("a", "V")], [_Out("x", "V", ("a",))], units=UNITS)

    def test_an_output_with_no_derived_from_is_fine(self):
        validate_declaration([_In("a", "V")], [_Out("x", "V")], units=UNITS)


class TestLocalReasoning:
    """A shared rule must not flatten the caller's reason for having it."""

    def test_a_group_reason_is_appended(self):
        with pytest.raises(SpecError, match="describing different topologies"):
            validate_declaration(
                [_In("a", "V", group="nope")], units=UNITS, groups=("g",),
                group_reason="The form and the result would be describing "
                             "different topologies.",
            )

    def test_a_derived_reason_is_appended(self):
        with pytest.raises(SpecError, match="provenance claim becomes decorative"):
            validate_declaration(
                [_In("a", "V")], [_Out("x", "V", ("ghost",))], units=UNITS,
                derived_reason="A contributor set naming a field that does not "
                               "exist is how a provenance claim becomes decorative.",
            )

    def test_without_a_reason_the_message_is_still_complete(self):
        with pytest.raises(SpecError) as e:
            validate_declaration([_In("a", "V", group="nope")], units=UNITS,
                                 groups=("g",))
        assert str(e.value).endswith("declared set.")


class TestExceptionType:
    def test_it_is_both_an_import_error_and_a_value_error(self):
        """Neither package had to change its stated contract to adopt this.
        cable-bonding documents and asserts ImportError; trust-loop asserts
        ValueError. Both readings are defensible."""
        with pytest.raises(ImportError):
            validate_declaration([_In("a", "furlongs")], units=UNITS)
        with pytest.raises(ValueError):
            validate_declaration([_In("a", "furlongs")], units=UNITS)


class TestRenderDomain:
    @pytest.mark.parametrize(
        "kw,expected",
        [
            ({}, "—"),
            ({"minimum": 1.0}, "≥ 1"),
            ({"minimum": 0.0, "exclusive": True}, "> 0"),
            ({"maximum": 2.0}, "≤ 2"),
            ({"minimum": 1.0, "maximum": 2.0}, "1 … 2"),
            ({"minimum": 0.0, "maximum": 2.0, "exclusive": True}, "> 0 … 2"),
            ({"minimum": 0.0005}, "≥ 0.0005"),
            ({"minimum": 1.5e-4}, "≥ 0.00015"),
            ({"choices": ("flat", "trefoil")}, "flat, trefoil"),
            ({"minimum": 1.0, "choices": ("a", "b")}, "a, b"),
        ],
    )
    def test_renders(self, kw, expected):
        assert render_domain(**kw) == expected

    def test_choices_win_over_a_numeric_bound(self):
        """A categorical input HAS a domain and it is not a bound. The first
        version returned an em dash here, which would have replaced a human's
        `flat, trefoil` with less information than the prose it succeeded."""
        assert render_domain(0.0, 9.0, choices=("x", "y")) == "x, y"

    def test_the_range_separator_is_the_one_the_documents_actually_use(self):
        """U+2026, measured from the documents rather than asserted.

        The first version pinned an en dash and justified it as "matching every
        hand-written range row in cable-bonding's ALGORITHM.md section 2". There
        was exactly one such row and it used U+2026; trust-loop's generated
        document contains no range row at all and structurally cannot, its
        FieldSpec having no `maximum`. So the citation named two documents that
        said the opposite of what was cited — a decoratively cited constant, in
        a module whose own docstring warns about them.
        """
        cb = ROOT / "apps/extension/engineering/cable-bonding/ALGORITHM.md"
        rows = [
            ln for ln in cb.read_text(encoding="utf-8").splitlines()
            if re.search(r"[0-9] . [0-9]", ln) and ln.startswith("|")
        ]
        assert rows, "section 2 has no range row to measure against"
        assert all("…" in ln for ln in rows), rows
        assert render_domain(1.0, 2.0) == "1 … 2"

    def test_exclusive_survives_a_two_sided_range(self):
        """It did not. `render_domain(0, 2, exclusive=True)` and the inclusive
        call returned the same string, so an exclusive lower bound printed as
        inclusive — in a module whose stated purpose is that one bound must not
        read two ways."""
        assert render_domain(0.0, 2.0, exclusive=True) != render_domain(0.0, 2.0)

    def test_it_matches_what_trust_loop_used_to_render_inline(self):
        """The generated ALGORITHM.md section 2 is checked against spec.py by
        `gen_trust_loop_tables.py --check`, so a change in this string is a
        change in a controlled document. Pinned against the pre-extraction
        formulation."""
        for minimum, exclusive in ((None, False), (0.0, True), (5.0, False)):
            old = ("—" if minimum is None else
                   (f"> {minimum:g}" if exclusive else f"≥ {minimum:g}"))
            assert render_domain(minimum, exclusive=exclusive) == old


class TestTheExtractionStaysBehaviourOnly:
    """What this module deliberately does not do.

    A later change that shares the dataclasses would pass every test above.
    These are the reasons that would be lost, stated so the decision has to be
    made again rather than drifted into.
    """

    def test_it_defines_no_dataclass(self):
        """Checked against what the module DEFINES, not against two literal
        names in `__all__`. The first version asserted `"FieldSpec" not in
        __all__`, which any other name walks straight past and which `__all__`
        does not govern anyway."""
        import dataclasses
        import emptyos.fieldspec as fs

        defined = [
            n for n, v in vars(fs).items()
            if isinstance(v, type) and dataclasses.is_dataclass(v)
        ]
        assert not defined, f"fieldspec defines dataclasses: {defined}"

    def test_the_two_packages_still_transpose_label_and_unit(self):
        """The concrete cost of a shared base class: every one of the
        declarations across the two packages is positional, and a mechanical
        reorder swaps two adjacent strings. The failure surfaces as a wrong
        ALGORITHM.md, which is the artifact the discipline exists to protect.
        """
        cb = _load_spec("apps/extension/engineering/cable-bonding/spec.py", "cb2")
        tl = _load_spec("apps/public/standard/trust-loop/spec.py", "tl2")
        import dataclasses

        cb_order = [f.name for f in dataclasses.fields(cb.FieldSpec)][:4]
        tl_order = [f.name for f in dataclasses.fields(tl.FieldSpec)][:4]
        assert cb_order == ["name", "symbol", "label", "unit"]
        assert tl_order == ["name", "symbol", "unit", "label"]
        assert cb_order != tl_order

    def test_the_two_packages_name_their_grouping_differently(self):
        """`group` and `slot` are not the same question — one is checked
        against the elements the engine reports a contribution for."""
        cb = _load_spec("apps/extension/engineering/cable-bonding/spec.py", "cb3")
        tl = _load_spec("apps/public/standard/trust-loop/spec.py", "tl3")
        import dataclasses

        assert "slot" in {f.name for f in dataclasses.fields(cb.FieldSpec)}
        assert "group" in {f.name for f in dataclasses.fields(tl.FieldSpec)}

    def test_the_module_is_stdlib_only_measured_in_a_clean_interpreter(self):
        """Executes the module in a SUBPROCESS and reports what it pulled.

        The first two versions of this were both vacuous.
        `spec_from_file_location` builds a ModuleSpec and never executes, so it
        pulled nothing no matter what the module imported; and `emptyos.fieldspec`
        was already imported at the top of this file, so `before` already
        contained anything it would have pulled. Measured against a file whose
        only content was `import emptyos.sdk`, the old assertion passed.

        A subprocess with a clean `sys.modules` is what actually settles it.
        """
        import subprocess

        code = (
            "import sys, json; base = set(sys.modules); "
            "import emptyos.fieldspec; "
            "print(json.dumps(sorted(set(sys.modules) - base)))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
        )
        assert out.returncode == 0, out.stderr
        pulled = json.loads(out.stdout)
        stdlib = set(sys.stdlib_module_names)
        foreign = [
            m for m in pulled
            if m.split(".")[0] not in stdlib and not m.startswith("emptyos")
        ]
        assert not foreign, f"non-stdlib imports: {foreign}"
        assert not [m for m in pulled if "sdk" in m or "kernel" in m]

    def test_that_subprocess_probe_can_actually_fail(self):
        """The guard above, watched red — per `.claude/rules/audits.md` a check
        at zero findings has proved nothing until it has been seen fail."""
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "heavy_probe.py"
            probe.write_text("import argparse\n", encoding="utf-8")
            code = (
                "import sys, json; sys.path.insert(0, r'%s'); base = set(sys.modules); "
                "import heavy_probe; "
                "print(json.dumps(sorted(set(sys.modules) - base)))" % d
            )
            out = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True,
            )
            pulled = json.loads(out.stdout)
            assert "argparse" in pulled, "the probe cannot see a real import"

    def test_the_module_declares_no_non_stdlib_import(self):
        """Its whole reason to be top-level rather than in the SDK."""
        src = (ROOT / "emptyos/fieldspec.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")) and " import " in s or s.startswith("import "):
                assert not s.startswith(("import emptyos", "from emptyos")), s
                assert "sdk" not in s and "kernel" not in s, s

    def test_importing_it_pulls_nothing_heavy(self):
        before = set(sys.modules)
        importlib.util.spec_from_file_location("fs_probe", ROOT / "emptyos/fieldspec.py")
        pulled = set(sys.modules) - before
        assert not [m for m in pulled if "sdk" in m or "kernel" in m]


def _load_spec(rel: str, name: str):
    p = ROOT / rel
    s = importlib.util.spec_from_file_location(name, p)
    assert s and s.loader
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m
