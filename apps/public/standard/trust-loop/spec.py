"""The one declaration of what this calculator takes and returns.

`ALGORITHM.md` sections 2 and 3 are generated from this module by
`scripts/gen_trust_loop_tables.py`; `GET /api/schema` serves it to the page;
`app.py::_coerce_inputs` reads its defaults. The document, the form and the
request boundary are consumers of one declaration rather than three copies of
it.

## What this replaced

Eleven inputs, declared four times and agreeing by hand:

- `ALGORITHM.md` section 2 — symbols and units. It carried no column for the
  **field name an API caller actually sends**, so the wire format of the only
  endpoint was documented nowhere.
- `app.py::_coerce_inputs` — names and defaults, as a literal tuple.
- `pages/trust-loop.js` — `var FIELDS`, names again, driving three separate
  loops.
- `pages/index.html` — names a fourth time as `<input id=…>`, with the default
  in `value=`, the unit in the label prose, and the domain nowhere at all.

The domain is the one worth dwelling on. `fault_current.py::_validate` refuses
six inputs at zero and four at negative, and none of that reached the browser:
a form that accepts a negative reactance and then reports a refusal has spent
the user's attention to tell them something it knew before they typed. That was
a defect at the moment the requirement (`UX-INP-02`) was written down, which is
the mechanism working rather than failing.

## Two rules held at import

A unit outside `UNITS`, or a `derived_from` naming an input nobody supplies, is
an `ImportError` rather than a test failure. The engine is a pure module and the
document is generated from this one, so a spec that does not hold is not a
spec that produces a worse document — it is a spec that produces a confident,
wrong one.

`group` is checked against `CONTRIBUTION_ELEMENTS` for the same reason: the
group an input renders in and the row it appears as in the engine's
`contributions` are the same physical element, and a page that grouped them
differently from the result would be showing two topologies.

## Deliberately not a shared abstraction

`D:/prelim-sizing/engines/prelim_sizing/spec.py` solves the same problem with
its own `FieldSpec`. This one was written to this package's needs without
adopting that one's shape, because a shared primitive inferred from a single
example encodes that example's accidents. The comparison of the two is the
evidence `docs/TRUST-LOOP.md`'s adoption rule asks for; see that document
before extracting anything from either.

`emptyos/sdk/schema.py` is not used here either, and the reason is structural
rather than aesthetic: importing it executes `emptyos/sdk/__init__.py`, which
pulls `BaseApp` and some forty modules, and this file is imported by a pure
engine package and by a script that must run without a daemon. Its JSON-schema
key vocabulary (`x-eos-unit`, `minimum`, `description`) is reused verbatim so
that a future adapter is a rename and not a translation.
"""

from __future__ import annotations

from dataclasses import dataclass

from emptyos.fieldspec import render_domain, validate_declaration

#: Closed on purpose. A unit is how a reader knows what a number is, so a typo
#: in one should stop the import rather than render as a label nobody questions.
UNITS = frozenset({"V", "A", "Ω", "VA", "%", ""})

#: The four elements the engine reports impedance contributions for
#: (`fault_current.prospective_fault_current`). Input groups are named from
#: this set so the form and the result describe one topology.
CONTRIBUTION_ELEMENTS = ("Supply network", "MV cable", "Transformer", "LV cable")


@dataclass(frozen=True)
class FieldSpec:
    """One input the API accepts.

    `minimum` / `exclusive` restate `fault_current._validate`'s refusal rules so
    the browser can hint them. The hint is advisory and the refusal is
    authoritative — `min` on an `<input>` is a courtesy that a paste or a
    scripted client walks straight past, and the thin-app invariant says the
    interface collects and renders while the engine decides. Stating the domain
    twice is therefore not a duplicated rule; it is a rule and a hint, and
    `test_the_declared_domain_matches_what_the_engine_refuses` keeps them
    honest.

    `step` is presentation. It is here because generating the form needs it and
    nowhere else would own it, and it is not rendered into `ALGORITHM.md` — the
    specification has no opinion about spinner increments.
    """

    name: str
    symbol: str
    unit: str
    label: str
    group: str
    default: float
    step: float
    minimum: float | None = None
    exclusive: bool = False
    help: str = ""

    @property
    def domain(self) -> str:
        """The domain as a document reads it, or an em dash when unbounded."""
        return render_domain(self.minimum, exclusive=self.exclusive)


@dataclass(frozen=True)
class OutputSpec:
    """One scalar the engine returns.

    `derived_from` is the input set that actually reached this number, not
    every input to the call. `ratio` rests on two of the eleven, and a reader
    who has changed `x_lv` is owed the fact that it cannot have moved.
    """

    name: str
    symbol: str
    unit: str
    label: str
    derived_from: tuple[str, ...]
    headline: bool = False


INPUTS: tuple[FieldSpec, ...] = (
    FieldSpec("u_net", "U_net", "V", "Nominal voltage", "Supply network",
              default=20000.0, step=1000.0, minimum=0.0, exclusive=True),
    FieldSpec("i_k_net", "I_k_net", "A", "Fault current", "Supply network",
              default=14400.0, step=100.0, minimum=0.0, exclusive=True,
              help="or S_k_net, converted"),
    FieldSpec("c", "c", "", "Voltage factor c", "Supply network",
              default=1.1, step=0.05, minimum=0.0, exclusive=True),

    FieldSpec("r_mv", "R_CMV", "Ω", "R", "MV cable",
              default=0.360, step=0.01, minimum=0.0,
              help="at MV potential; referred by K²"),
    FieldSpec("x_mv", "X_CMV", "Ω", "X", "MV cable",
              default=0.335, step=0.01, minimum=0.0,
              help="at MV potential; referred by K²"),

    FieldSpec("s_n", "S_n", "VA", "Rated power", "Transformer",
              default=400000.0, step=10000.0, minimum=0.0, exclusive=True),
    FieldSpec("u_2n", "U_2n", "V", "Secondary", "Transformer",
              default=400.0, step=10.0, minimum=0.0, exclusive=True),
    FieldSpec("vk_pct", "v_k", "%", "vₖ", "Transformer",
              default=4.0, step=0.1, minimum=0.0, exclusive=True,
              help="voltage drop on test"),
    FieldSpec("pk_pct", "p_k", "%", "pₖ", "Transformer",
              default=3.0, step=0.1, minimum=0.0,
              help="load loss on test"),

    FieldSpec("r_lv", "R_CLV", "Ω", "R", "LV cable",
              default=0.000388, step=0.0001, minimum=0.0,
              help="at LV potential; not referred"),
    FieldSpec("x_lv", "X_CLV", "Ω", "X", "LV cable",
              default=0.000395, step=0.0001, minimum=0.0,
              help="at LV potential; not referred"),
)

_ALL = tuple(f.name for f in INPUTS)

OUTPUTS: tuple[OutputSpec, ...] = (
    OutputSpec("i_k3_ka", "I_k3", "", "Three-phase prospective fault current (kA)",
               _ALL, headline=True),
    OutputSpec("i_k3_a", "I_k3", "A", "Three-phase prospective fault current", _ALL),
    OutputSpec("r_total_ohm", "R_Tk", "Ω", "Total resistance at the LV base", _ALL),
    OutputSpec("x_total_ohm", "X_Tk", "Ω", "Total reactance at the LV base", _ALL),
    OutputSpec("z_total_ohm", "Z_Tk", "Ω", "Total impedance at the LV base", _ALL),
    OutputSpec("ratio", "K", "", "Referral coefficient U_net / U_2n",
               ("u_net", "u_2n")),
    OutputSpec("ratio_squared", "K²", "", "Referral divisor applied upstream of the LV base",
               ("u_net", "u_2n")),
    OutputSpec("transformer_i_2n_a", "I_2n", "A", "Transformer rated secondary current",
               ("s_n", "u_2n")),
)


def defaults() -> dict[str, float]:
    """Field name → default, for the request boundary and the form."""
    return {f.name: f.default for f in INPUTS}


def field_names() -> tuple[str, ...]:
    """Every input name, in declaration order."""
    return _ALL


def groups() -> tuple[str, ...]:
    """Group names in declaration order, deduplicated."""
    seen: list[str] = []
    for f in INPUTS:
        if f.group not in seen:
            seen.append(f.group)
    return tuple(seen)


def to_schema() -> list[dict]:
    """The form, as JSON.

    Keys follow `emptyos/sdk/schema.py`'s vocabulary (`x-eos-unit`, `minimum`,
    `description`) so an adapter later is a rename rather than a translation.
    """
    return [
        {
            "name": f.name,
            "symbol": f.symbol,
            "label": f.label,
            "group": f.group,
            "x-eos-unit": f.unit,
            "default": f.default,
            "step": f.step,
            "minimum": f.minimum,
            "exclusiveMinimum": f.exclusive,
            "description": f.help,
        }
        for f in INPUTS
    ]


def _validate() -> None:
    """The four rules, from `emptyos/fieldspec.py`.

    Was four hand-written loops here. They are shared with `cable-bonding` now
    because the two packages had drifted apart on which rules they carried at
    all -- this one had the duplicate-name check and that one did not -- and
    neither could see the other's gap from the inside. The reasons that are
    genuinely local stay local, passed in: a bad group here is not a
    mislabelled form section, it is a form and a result describing different
    topologies.
    """
    validate_declaration(
        INPUTS,
        OUTPUTS,
        units=UNITS,
        groups=CONTRIBUTION_ELEMENTS,
        group_attr="group",
        label="spec",
        group_reason=(
            "It is not an element the engine reports a contribution for, so the "
            "form and the result would be describing different topologies."
        ),
        derived_reason=(
            "A contributor set naming a field that does not exist is how a "
            "provenance claim becomes decorative."
        ),
    )


_validate()
