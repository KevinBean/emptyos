# Trust Loop - Engineering Assurance Contract

> The operational companion to the public article *How to Build Trustworthy
> Engineering Software in the AI Era*. The article explains the argument; this
> document defines the reusable EmptyOS development contract.

The Trust Loop is the evidence spine inside the broader engineering delivery
workflow. It is not a second orchestrator and it does not replace Grill, App
Builder, Feature Pipeline, dogfood, or release. Those surfaces move work; the
Trust Loop defines what an engineering claim must carry as it moves.

```text
delivery: discovery -> planning -> [Trust Loop 1-7] -> packaging -> release
source -> specification -> engine -> conformance -> app -> report -> end-to-end
```

## The package contract

Each stage hands one package to the next. A package contains:

1. **Controlled documents** - reviewed engineering judgment and scope.
2. **Executable deliverables** - source files, code, fixtures, UI, or tests.
3. **Evidence receipts** - generated proof tied to an exact version and run.

Do not turn receipts into authored prose. Do not copy source facts into several
documents. The package index links to the one owner of each fact.

| Stage | Package | Controlled document | Executable deliverable | Authority / exit |
|---|---|---|---|---|
| 1 | Source Evidence | `SOURCE-PACK.md` plus KB `reference` / `clause` / `case` records | authoritative source snapshot + structured case data | engineer confirms faithful transcription |
| 2 | Calculation Specification | `ALGORITHM.md` | input/output and refusal contract | responsible engineer approves what will be implemented |
| 3 | Engine Implementation | `IMPLEMENTATION.md` | pure engine + unit/property tests | review confirms implementation; correctness is not yet claimed |
| 4 | Validation | `VALIDATION.md` | conformance fixtures + runner | published or independently established reference values |
| 5 | Application | `INTENT.md` | thin API/UI + acceptance tests | engine owns values; human owns what the user needs to see |
| 6 | Reporting | `REPORT-SPEC.md` | report schema/generator + example | same source as the engine plus a sampled hand check |
| 7 | Release Verification | `RELEASE-VERIFICATION.md` | unit/API/browser suites + packaged build | the real run is red or green; red blocks a verified release |

Every participating app also carries `ASSURANCE.md`, a short index of stage
status, claim boundary, document locations, latest receipts, and open
deviations. It links; it does not restate the packages.

## Every obligation carries a receipt

A stage is built well exactly when its obligation carries an artefact something
runs. Stage 5 is the measured case: it carried none, its only gate was that an
acceptance test passed, and a bare number field satisfies that - so all nine of
its obligations went unwritten and the interface came in at 8% of the build at
0.05-0.20 h per endpoint. Naming the obligation did not produce it. Giving it
`UX-*` ids traced to a code symbol and a test did.

Any obligation added to this contract answers three questions, in order:

1. **Which stage owns it?** One. An obligation owned by two stages is owned by
   neither.
2. **What generated or executed artefact proves it?** A table rendered from a
   declaration, a test named for the requirement, a receipt tied to a run.
3. **What runs that artefact?** A preflight scope, a release step, a test suite.

An obligation that answers the first two and not the third is not a contract, it
is a wish. It will pass review, ship, and drift - and the drift is invisible
precisely because the artefact exists and looks maintained. A `--check` mode
nothing calls is prose with extra steps.

## 1. Source Evidence Package

**Input:** an engineering question and candidate authorities.

**Controlled content:** source identity, publisher, document id, edition,
status/currentness, official URL, archive path, checksum, relevant clauses,
source conflicts, and human review status. Verbatim evidence belongs in KB
`kind: clause` records; interpretation is visibly separate. Published examples
belong in `kind: case` records.

**Executable content:** a stable source snapshot and inspectable case input data.

**Receipt:** source hash, checked clauses/cases, reviewer, review date, status,
and **how each transcribed table was read** — `checked_method: page-images`
(the rendered page) or `fulltext` (a text extraction, whose equations and
tables can be garbled). A transcribed table lives once, engine-side
(`engines/<pkg>/data/<std>.json`, per-row provenance validated by
`engines/provenance.py`); the KB note's table block is rendered from it
(`scripts/gen_kb_tables.py --check` runs it). A KB clause note that quotes a
formula or a table value carries `verified_against_pdf` + `source_pages` + a
`## Verification` section, derived to a tier by the kb app and listed when
unverified and engine-backed by `scripts/kb_verification_audit.py`.

**Exit:** answers "what does the authority say?" It makes no implementation
choice.

## 2. Calculation Specification Package

**Input:** an approved Source Evidence Package.

`ALGORITHM.md` defines purpose, scope, exclusions, normative evidence, symbols,
units, inputs, outputs, numbered requirements, equations, assumptions,
ambiguity decisions, applicability limits, refusal conditions, acceptance
targets, structural properties, report requirements, version, and approval.

Load-bearing requirements carry stable ids such as `ALG-NET-01`, `LIM-01`,
`REFUSE-02`, `VAL-ABB-01`, and `PROP-MONO-01`. These ids are the
traceability join.

**The input and output tables are the data dictionary, and they are generated.**
Their receipt is a renderer over the engine's single declaration of its fields,
plus a `--check` mode that exits non-zero when the document has gone stale. Two
rules separate a dictionary from a decoration. The table must carry the **field
name an API caller actually sends**, not only the maths symbol - a symbol table
names the algebra and leaves the wire format undocumented. And each output must
name the inputs that reached it, so that a requirement stated over the output
set is a requirement over something enumerated rather than over a set nobody
wrote down. Measured in the second package to adopt the loop: the same sixteen
inputs stood declared four times - in the document, in the engine, and twice in
the page - with a hand-written unit patch reconciling two of them.

Only the tables are generated. The prose around them is authored and is where
the reasoning lives; a generator that owned the prose would be a generator
nobody could argue with.

**Exit:** every implementation-affecting ambiguity is resolved or explicitly
blocks work. The engineer, not the drafting model, approves the contract.

## 3. Engine Implementation Package

**Input:** the approved calculation specification.

**Controlled content:** engine/spec versions, architecture boundary,
requirement -> code symbol -> unit-test traceability, numerical decisions,
dependencies, refusal mapping, deviations, and human review record.

**Executable content:** pure deterministic calculation code, validated inputs,
structured results and calculation steps, refusals, unit tests, and property
tests. No UI, vault access, HTTP, model call, or duplicated report calculation
sits in the mathematics.

**`## Data flow` is required, and it is not the module map.** The module map
says what each file is for. The data flow says the path a number takes: the
declaration it starts from, the transport shape, the boundary where a human's
claim about an input is checked, the pure engine, and the shape that reaches the
surface. It is the evidence for Stage 5's thin-app invariant, which is otherwise
asserted and never shown, and it is the one question about a package that its
other seven documents leave unanswerable.

Its receipt is the traceability table's discipline applied to a diagram: symbols
named in it are written `path::symbol` and must resolve, and requirement ids
named on its edges must be ids the package states. An author writes the
qualified form for the hops they intend to be checked, and a section that
qualifies nothing proves nothing - so at `demonstration` and above at least one
qualified symbol is required.

**Exit:** a reviewed candidate. Its own tests cannot grant conformance.

## 4. Validation Package - first gate

**Input:** a candidate engine and independent anchors from Stage 1.

`VALIDATION.md` defines the claim, applicability domain, reference case matrix,
independence, tolerances and rationale, boundary/extreme/unit/rounding coverage,
structural checks, acceptance rule, failure routing, and claim limit.

**Executable content:** inspectable fixtures, expected results, tolerance
definitions, `[[provides.conformance.<endpoint>]]`, and the runner. Every
case names `source` — where its expected numbers were read: a
page/table/figure/equation/example locator, or `<data>.json#<source-id>`
resolving to a record cited from page images. A case carries only its free
variables; published constants come from the one transcription.
`scripts/check_engineering_assurance.py` runs it: a hard error for a
participating app, an advisory count elsewhere.

**Receipt:** app/engine/spec versions, commit, case/source id, `source`,
inputs, expected and computed values, deviation, tolerance, verdict,
environment, and time.

**Exit:** every mandatory case passes. Source errors return to Stage 1,
specification errors to Stage 2, and implementation errors to Stage 3. Every
downstream gate reruns after repair.

One published case demonstrates a gate; it does not validate a method across
its full input domain.

## 5. Application Package

**Input:** a conforming engine.

`INTENT.md` owns user/purpose, supported and refused uses, user journeys, input
labels/units/ranges, result and provenance presentation, validation-anchor
behaviour, error/refusal experience, accessibility, and acceptance criteria.

**Executable content:** manifest, engine adapter, API, unit boundary, UI,
method/version provenance, validation-scope display, and API/browser tests.

**Invariant:** the interface collects, transmits, and renders values. It never
recalculates an engineering result.

**This interface is the test bench.** Its job is to make a result checkable,
for the person developing and verifying the calculator. An interface for a
user who only needs an answer is a different thing with a different reader:
the **product face**, built afterwards by `docs/PRODUCT-LOOP.md` (stages
P1-P5), which reads the same contract and never replaces this one.

**These obligations carry ids like any other.** `UX-*` ids are stated in
`INTENT.md` and traced in `IMPLEMENTATION.md` to a code symbol and a test, on
the same terms as `ALG-*`. Without them Stage 5 is the one stage of the loop
carrying no executable receipt, and its only gate is that an acceptance test
passes — which a bare number field satisfies. Measured in the first package to
adopt the loop: all nine obligations above went unwritten, and the interface
took 8% of the build at 0.05-0.20 h per endpoint — the signature of one panel
template repeated, not of seven interfaces designed for seven questions.

Not all nine are machine-checkable, and the split is the point. That a unit is
present, a domain is declared, output is announced, or a transport error does
not render identically to an empty result — those hold statically, cheaply,
with no browser. Whether a journey is *followable* does not, and belongs in
Stage 7's hand walk. State both; never let the testable half stand in for the
other.

## 6. Reporting Package

**Input:** one engine execution, including structured calculation steps.

`REPORT-SPEC.md` defines audience, report/run identity, required inputs,
formula/substitution/intermediate lines, units, assumptions, source citations,
ambiguity decisions, validation statement, rounding policy, formats, and the
sampled hand-check procedure.

**Executable content:** report schema, generator, templates/renderers, example,
and report-to-engine consistency tests. The generator consumes engine-returned
steps; it does not repeat formulas.

**Receipt:** input/report hashes, engine/method version, consistency verdict,
sampled lines, reviewer, and date.

## 7. Release Verification Package - second gate

**Input:** the real packaged application and all upstream packages.

`RELEASE-VERIFICATION.md` defines release identity, included component
versions, unit/API/browser matrix, end-to-end journeys, environment matrix,
packaging checks, limitations, deviations/skips, failure routing, and decision.

The mandatory journey loads a reference case through the real UI, crosses the
real API/engine boundary, inspects the result, generates the report, checks
report/result agreement, changes an input, and confirms the anchor no longer
follows the changed result.

**Receipt:** release/commit/build ids, suite results, conformance receipt links,
package inventory/checksum, environment, limitations, and approval.

**Exit:** all required checks pass against the packaged version. Red blocks the
"verified" label and returns the defect to its owning stage.

## Machine-readable opt-in

An engineering app participates through manifest `[assurance]`:

```toml
[assurance]
method = "trust-loop-v1"
status = "draft" # draft | demonstration | verified | released
index = "ASSURANCE.md"
source_pack = "SOURCE-PACK.md"
algorithm = "ALGORITHM.md"
implementation = "IMPLEMENTATION.md"
validation = "VALIDATION.md"
app_spec = "INTENT.md"
report_spec = "REPORT-SPEC.md"
release_verification = "RELEASE-VERIFICATION.md"
```

`scripts/check_engineering_assurance.py` validates the declaration, document
contract, requirement traceability, the data-flow resolve join, calculator
manifest surface, and test-file presence. Three of its rules exist only to close
a vacuous pass, where a set difference over an empty set reports success for
work nobody did: a package must state requirement ids, a web-facing package must
state `UX-*` ids, and a data-flow section must qualify at least one symbol.
Structural compliance does not promote an app to `verified`.
`verified` and `released` additionally require current generated receipts;
until that durable receipt store exists, apps remain `draft` or
`demonstration`.

## Storage and publication

| Material | Owner/location | Reason |
|---|---|---|
| method contract | `docs/TRUST-LOOP.md` | shared authored doctrine |
| source archive and verbatim evidence | external vault KB | private/licensed evidence stays outside the product repo |
| per-app controlled documents | beside the app | reviewed with the implementation |
| code, fixtures, tests | app/engine/test tree | executable product truth |
| run receipts | generated evidence store / release artifact | generated facts are not hand-maintained |
| public narrative | published site | explanation, not operational truth |

Public apps may ship source identities, lawful short extracts, derived
specifications, and public-case data. They must not copy licensed standards out
of the private vault merely to make the package self-contained.

## Adoption rule

`trust-loop` is the first local implementation. Do not extract another SDK
layer or generic assurance UI until a second real engineering calculator has
adopted the same package and the two shapes have been read-verified.

**Read-verified means two independent implementations, not one copied.** An
abstraction inferred from a single example encodes that example's accidents, and
no amount of internal review separates the two from the inside. The same holds
for the gates in this contract: a gate authored in the same session as its only
subject cannot be distinguished from an overfitted one, so each rule is
calibrated against a package its author did not write before it gates. Running
one repository's checker against another's package is that calibration, and it
has already earned its keep twice - once at 11 findings, 0 real defects, every
one a heading synonym, and once more when it surfaced a genuine defect
(`_test_exists` demanded an exact filename while the documented convention is a
`test_unit_{slug}*.py` glob; the reference package passed only because it
happens to carry the exact name).

### Read-verify verdict, 2026-08-13 - do not extract yet

The second calculator exists: `D:/prelim-sizing`'s bid-stage collector sizing,
seven endpoints, engine under `engines/prelim_sizing/`. Both packages now
declare their I/O once and generate the Stage 2 tables from that declaration.
Reading the two shapes against each other:

| | Shared | Divergent |
|---|---|---|
| **Output record** | all 6 fields, one differing only in name (`kpi` / `headline`) | none |
| **Input record** | 6 of 14: `name`, `symbol`, `unit`, `label`, `help`, `default` | `display_unit`, `range`, `produced_by`, `optional` / `group`, `step`, `minimum`, `exclusive` |
| **Import-time validation** | closed unit table; contributor set must name real inputs | the rules themselves |
| **Generator** | `BEGIN` / `END` / `BLOCKS` / `splice` / `main`, named identically by accident | every renderer - the column sets differ |

The output record arriving byte-comparable from two independent authorings is
real evidence. The input record is the opposite: its divergent tail is not
stylistic, it is two different questions. One models a domain as *the extent
over which reference data is tabulated*; the other as *the boundary the engine
refuses at*. A shared field that means both is a field that means neither.

**So the extraction is deferred, and the reason is arithmetic rather than
caution.** The genuinely common core is six dataclass fields and one
`splice()`. The two packages live in separate repositories with no shared
package, so extracting means a third vendored file kept in sync by hand - the
same burden already carried for the checker's helpers, for less than half the
code. Note the constraint that forces vendoring rather than an SDK import:
`emptyos/sdk/__init__.py` pulls `BaseApp` and some forty modules, and both
declarations are imported by pure engine code whose whole claim is that its
test suite needs no daemon.

Revisit when a third calculator adopts the loop, or when two of them land in one
repository. See `docs/DEFERRED-WORK.md` for the row.

### Read-verify verdict, 2026-08-28 - extract the behaviour, not the record

Both revisit clauses fired at once. `cable-bonding` is the third calculator
**and** it sits in this repository beside `trust-loop`, which removes the
vendoring cost the 2026-08-13 deferral turned on.

The third independent authoring reproduced the earlier finding rather than
overturning it. `name`, `symbol`, `unit`, `label` are byte-identical in all
three. `default` and `minimum` are shared but not with the same optionality -
`trust-loop` requires every input to carry a default, while 12 of
`cable-bonding`'s 36 inputs have none and two of those are `required=True`. The remaining tail differs from both predecessors again:
`slot`, `description`, `maximum`, `required`, `choices` against `group`,
`help`, `step`, `exclusive`. On the output record, 5 of 6/7 shared.

So the record stays local and the **behaviour** is extracted:
`emptyos/fieldspec.py`, stdlib-only and top-level for the same reason
`frontmatter.py` and `nethost.py` are. Two decisions worth recording.

**Why not the dataclass.** Beyond the verdict's own principle that a field
meaning two things means neither, the two positional orders transpose `label`
and `unit`. All 63 declarations across the two packages are positional (44 in
`cable-bonding`, 19 in `trust-loop`, counting `OutputSpec` as well as
`FieldSpec`), so a shared base forces a mechanical reorder whose failure mode
is two adjacent strings swapping - invisible in review, surfacing as a wrong
`ALGORITHM.md`. The output records transpose the same pair, so excluding them
understated the risk by 20 declarations.
The four shared field names are trivial to restate; the risk of restating them
is not.

**What the extraction actually bought.** Not tidiness. The two packages had
drifted on which rules they carried: `trust-loop` refused a duplicate input
name and `cable-bonding` did not, so a field declared twice there silently
SHADOWED the earlier declaration - `defaults()` resolving that name to the
later default, `json_schema()` returning one property fewer than there are
declarations, the first declaration's domain and description gone with
nothing raised. Adoption is not symmetric: `trust-loop` consolidated four
hand-written loops into one call, while `cable-bonding` layered - its
per-field `__post_init__` still fires first, so only the two collection-level
rules do new work there. Neither could see the other's gap
from the inside, which is the same argument that made read-verify a
requirement in the first place. A second gap surfaced on adoption:
`cable-bonding`'s `ALGORITHM.md` Domain column was hand-written and unchecked,
and pinning it against `render_domain` is what caught the third mistake
below.

Three things the adoption had to be careful about, and they all cut against
extracting eagerly. The first `render_domain` returned an em dash for a
categorical input, which would have replaced a human's `flat, trefoil` with
**less** information than the prose it succeeded; `choices` is checked before
any numeric bound for that reason. It silently discarded `exclusive` whenever
a maximum was present, so an exclusive lower bound printed as inclusive - in
a helper whose stated purpose is that one bound must not read two ways. And
it was pinned to an en dash on the stated grounds that this matched "every
hand-written range row" - when the sole range row in either package used
U+2026 and `trust-loop`'s generated document has no range row at all. That
citation named two documents that said the opposite of what was cited, which
is the decoratively-cited constant this repository's review discipline exists
to catch, committed inside the module that warns about it. The renderer now
matches the document.

## Cross-references

- `docs/ENGINEERING-APP-WORKFLOW.md` - broader calculator delivery wrapper.
- `docs/PRODUCT-LOOP.md` - the sequence after this one: test bench -> product face.
- `emptyos/sdk/conformance.py` - method/conformance registry and live gate.
- `.claude/rules/test-fix-verify-loop.md` - repair and re-verification roles.
- `.claude/rules/self-audit-loops.md` - deterministic scanner registration.
- `apps/public/standard/trust-loop/` - reference implementation.
