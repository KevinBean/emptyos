"""Shared validation discipline for calculator I/O declarations.

A Trust Loop calculator declares its inputs and outputs once, in a `spec.py`,
and three things consume that declaration: `ALGORITHM.md` sections 2 and 3, the
form, and the request boundary. This module holds the rules that make such a
declaration *hold* — nothing else.

Top-level and stdlib-only, for the same reason `emptyos/frontmatter.py` and
`emptyos/nethost.py` are: both consumers are pure declaration modules imported
by test suites whose whole claim is that they need no daemon, and
`emptyos/sdk/__init__.py` pulls `BaseApp` and some forty modules.

Why this exists now, and not on 2026-08-13
------------------------------------------
`docs/TRUST-LOOP.md` § Read-verify verdict deferred a shared declaration layer
and named its own revisit condition: *"a third calculator adopts the loop, OR
two of them land in one repository (which removes the vendoring cost and makes
the six-field core worth a module)"*. `cable-bonding` is the third calculator
**and** it is in the same repository as `trust-loop`, so both clauses fired.

The read-verify against a third independent authoring reproduced the earlier
finding rather than overturning it: `name`, `symbol`, `unit`, `label` are
byte-identical in all three; `default` and `minimum` are shared but not with
the same optionality; and each author's remaining tail differs from both others.

What is deliberately NOT here: the dataclasses
----------------------------------------------
This module extracts the **behaviour**, not the record. Two reasons, and the
second is the one that would have cost real money.

The verdict's own principle is that a shared field meaning two things means
neither. `trust-loop.group` and `cable-bonding.slot` are close but not the same
question — one names an element the engine reports a contribution for and is
checked against that set, the other names a section of a five-part form. Same
for `help` and `description`. Forcing one word on both is exactly the
"abstraction inferred from one example encodes that example's accidents"
failure the deferral was protecting against.

And the two positional orders **transpose `label` and `unit`**: trust-loop
declares `name, symbol, unit, label` and cable-bonding `name, symbol, label,
unit`. Every one of the 43 declarations across the two packages is positional.
A shared base class forces a mechanical reorder of all of them, where the error
is two adjacent strings swapping — invisible in review, and surfacing as a
wrong `ALGORITHM.md`, which is the exact artifact the whole discipline exists
to keep honest. The four shared field names are trivial to restate; the risk of
restating them is not.

What IS here is the part that would rot silently. The two packages carried
different subsets of the same four rules and neither could see the other's gap:
`trust-loop` refused a duplicate input name and `cable-bonding` did not.

Be precise about what adoption changed on each side, because it is not
symmetric. `trust-loop` genuinely consolidated -- its `_validate()` was four
hand-written loops and is now one call. `cable-bonding` LAYERED: its
`__post_init__` still validates unit and slot per field, which is the better
error because it fires at construction and names the field, so the shared
checker's unit, group and `derived_from` branches are unreachable from that
side. Only the two collection-level rules -- duplicate input name, duplicate
output name -- do new work there. The gain is real and it is those two.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol, Sequence

__all__ = [
    "SpecError",
    "HasName",
    "HasUnit",
    "validate_declaration",
    "render_domain",
]


class SpecError(ImportError, ValueError):
    """A declaration that does not hold.

    Inherits from **both** on purpose. A declaration is validated at import
    time, so `ImportError` is the honest category — this module cannot be
    imported — and `cable-bonding` states that reasoning in its own docstring
    and asserts it in three tests. `trust-loop` raises `ValueError`, the
    conventional bad-value category, and asserts that. Both readings are
    defensible and neither package should have to change its contract to adopt
    a shared checker, so the exception satisfies both.
    """


class HasName(Protocol):
    name: str


class HasUnit(Protocol):
    name: str
    unit: str


def _attr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except AttributeError as exc:  # pragma: no cover - programmer error
        raise SpecError(
            f"spec: {obj!r} has no attribute {name!r}; validate_declaration was "
            f"given a group_attr this declaration does not use"
        ) from exc


def validate_declaration(
    inputs: Sequence[Any],
    outputs: Sequence[Any] = (),
    *,
    units: Iterable[str],
    groups: Iterable[str] | None = None,
    group_attr: str = "group",
    label: str = "spec",
    group_reason: str = "",
    derived_reason: str = "",
) -> None:
    """Check a whole I/O declaration. One entry point, four rules.

    Deliberately not four public functions. A package that calls three of four
    is a package silently missing a check, and that is not hypothetical: before
    this module existed, `cable-bonding` validated units, slots and
    `derived_from` per field but had no duplicate-name rule, so a field declared twice silently SHADOWS the earlier one: `defaults()` resolves that name to the later declaration's default, `json_schema()` returns one property fewer than there are declarations, and the first declaration's domain and description vanish with nothing raised.
    `trust-loop` had the rule. Neither could see the other's gap.

    An earlier draft of this paragraph said "36 defaults from 37 declarations",
    which cannot happen -- `defaults()` filters out fields with no default and
    12 of 36 have none, so it returns 24 either way. That number came from a
    probe that measured a dict comprehension written inside the probe rather
    than the real `defaults()`. The claim was measuring its own assumption.

    `groups=None` skips the group check for a declaration whose grouping is open
    (a form section a package adds to freely); pass the closed set to check it.

    `group_reason` and `derived_reason` append the package's own *why* to those
    two messages. The shared checker knows what the rule is and cannot know why
    it matters here — `trust-loop` groups inputs by an element its engine
    reports a contribution for, so a bad group means "the form and the result
    would be describing different topologies", which is worth saying and is not
    true of a package whose groups are only form sections. Extracting the rule
    while dropping the reasoning would make the shared error worse than the two
    it replaced.
    """
    unit_set = frozenset(units)
    group_set = frozenset(groups) if groups is not None else None
    seen: set[str] = set()

    for f in inputs:
        if f.unit not in unit_set:
            raise SpecError(
                f"{label}: input {f.name!r} declares unit {f.unit!r}, which is "
                f"not in the unit table. The table is closed by design — add it "
                f"there, or the label is a unit nobody agreed to."
            )
        if group_set is not None:
            g = _attr(f, group_attr)
            if g not in group_set:
                raise SpecError(
                    f"{label}: input {f.name!r} declares {group_attr} {g!r}, "
                    f"which is not in the declared set."
                    + (f" {group_reason}" if group_reason else "")
                )
        if f.name in seen:
            raise SpecError(f"{label}: input {f.name!r} is declared twice")
        seen.add(f.name)

    seen_out: set[str] = set()
    for o in outputs:
        # The same rule as for inputs, and for the same reason: ALGORITHM.md
        # section 3 is generated from OUTPUTS exactly as section 2 is from
        # INPUTS. The first version guarded only the input half, which left the
        # precise failure this module cites to justify its own existence
        # unguarded on the other one.
        if o.name in seen_out:
            raise SpecError(f"{label}: output {o.name!r} is declared twice")
        seen_out.add(o.name)
        if o.unit not in unit_set:
            raise SpecError(
                f"{label}: output {o.name!r} declares unit {o.unit!r}, which is "
                f"not in the unit table."
            )
        for d in getattr(o, "derived_from", ()):
            if d not in seen:
                raise SpecError(
                    f"{label}: output {o.name!r} claims to derive from {d!r}, "
                    f"which no input supplies."
                    + (f" {derived_reason}" if derived_reason else "")
                )


def render_domain(
    minimum: float | None = None,
    maximum: float | None = None,
    *,
    exclusive: bool = False,
    choices: Sequence[str] = (),
) -> str:
    """The domain as a document reads it, or an em dash when unbounded.

    Shared because the string appears in a generated `ALGORITHM.md` § 2 column
    in one package and a hand-written one in the other, and two renderings of
    one bound is how the same constraint comes to read two ways.

    `choices` comes first because a categorical input HAS a domain and it is
    not a numeric bound. Checking the first version of this against
    `cable-bonding`'s hand-written column found four rows where the human had
    written `flat, trefoil` and `boolean` and the renderer would have replaced
    them with an em dash — adopting it would have made the document less
    informative than the prose it was replacing, which is the same failure the
    module docstring warns about one level up.
    """
    if choices:
        return ", ".join(choices)
    lo = None
    if minimum is not None:
        lo = f"> {minimum:g}" if exclusive else f"≥ {minimum:g}"
    hi = f"≤ {maximum:g}" if maximum is not None else None
    if lo and hi:
        # `exclusive` must survive a two-sided range. The first version dropped
        # it here -- `render_domain(0, 2, exclusive=True)` and the inclusive call
        # returned the same string -- so an exclusive lower bound printed as
        # inclusive in a controlled document. Not live in either package today
        # (cable-bonding has no `exclusive` field, trust-loop passes no
        # `maximum`), but this module's stated purpose is that one bound should
        # not read two ways, and it had one.
        open_lo = "> " if exclusive else ""
        return f"{open_lo}{minimum:g} … {maximum:g}"
    return lo or hi or "—"
