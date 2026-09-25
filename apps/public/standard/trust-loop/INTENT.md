# Intent - Trust Loop

> Living design doc for `apps/public/standard/trust-loop/`. Edit as the app
> evolves. Birth certificate: the published "How to Build Trustworthy
> Engineering Software in the AI Era" article. Changelog:
> `10_Projects/emptyos/log/app-development.md`.

## Why

Trust Loop is the public reference implementation of EmptyOS's seven-stage
engineering-assurance contract. It lets a reader run a deterministic
three-phase fault-current calculation, inspect the checked specification and
working, and re-derive a conformance verdict against ABB Technical Application
Paper No. 2 clause 2.2. It deliberately implements only that simplified radial
method; the full private engineering study engines remain outside this public
demonstration.

## Supported uses

- Demonstrate how source, specification, code, conformance, UI, report, and E2E
  evidence connect.
- Reproduce the ABB 400 kVA worked example.
- Inspect sensitivity and the contribution of each series impedance.
- Demonstrate explicit refusals and validation-claim boundaries.

## Refused or out-of-scope uses

- Final equipment-duty or protection-design decisions.
- Full current-edition IEC 60909 maximum/minimum studies.
- Motor/converter contribution, unbalanced faults, or meshed networks.
- Treating the one published case as validation of the full input domain.

## User journeys

1. Load the published case and see the deterministic answer and provenance.
2. Run the conformance gate and inspect expected, computed, deviation, tolerance,
   and verdict.
3. Read the source package and algorithm specification beside the calculator.
4. Inspect every formula, substitution, intermediate value, and unit.
5. Change an input and see both the result and the validation scope change.
6. Print or download the checkable working.

Three of those carry ids, because they are the ones a build can lose without
anyone noticing:

- **`UX-JRN-01`** — the published anchor loads in one action. A reader who has
  to retype eleven numbers to reach the case the gate is about will not check
  it, and an unchecked anchor is a claim rather than evidence.
- **`UX-JRN-02`** — the specification the engine was written against is one
  action from the calculator. Stage 2 exists whether or not it is reachable;
  making it reachable is what stops "written against a spec" being a card.
- **`UX-JRN-03`** — the gate's verdict is on screen without being asked for. On
  this app the gate *is* the subject, so requiring a click to learn whether the
  calculator is validated gets the emphasis backwards.

Journeys 4-6 are not idded here. Whether a derivation is *followable* is not a
property markup can carry, and Stage 7's hand walk owns it; stating an id whose
test could only assert that some element exists would be the testable half
standing in for the other.

## Thin-app invariant

The page computes no engineering value. It collects inputs and renders the
response from `fault_current.py`. The report consumes the calculation steps
returned by that same engine execution. No model call is on the runtime path.

## Input and result presentation

### Inputs

**Every input states its unit** (`UX-INP-01`), in the units `ALGORITHM.md`
defines. A number without one is not an engineering input, it is a number. The
one dimensionless input, the voltage factor `c`, declares that explicitly rather
than by omission — otherwise "no unit shown" would mean both "dimensionless" and
"nobody wrote it down".

**A domain the engine refuses on is declared to the browser** (`UX-INP-02`).
`fault_current.py::_validate` rejects six inputs at zero and four at negative,
and until 2026-08-13 none of that reached the form: a field that accepts a
negative reactance and then reports a refusal has spent the reader's attention
telling them something it knew before they typed. The `<input min>` is a hint
and the engine's refusal is authoritative — a paste or a scripted client walks
past the hint, which is why the rule is stated in both places and joined by a
test rather than moved to one.

**Every input is programmatically labelled** (`UX-INP-03`) — a `<label for>`,
not adjacent text. Placeholder-as-label is the failure mode this excludes.

**The page declares no field itself** (`UX-INP-04`). The form is built from
`GET /api/schema`, which serves the same declaration `ALGORITHM.md` section 2 is
generated from. Before that, the eleven inputs were written down four times and
kept in agreement by hand.

### Results

**The result names the method and version that produced it** (`UX-RES-01`).
"14.95 kA" is not an engineering answer; "14.95 kA, computed by `native`
v1.0.0, deterministic, no model call" is.

**The working is on the result surface** (`UX-RES-02`) — formula, substitution
and value per line, not behind a download. A derivation a reader must ask for is
a derivation most readers will not read.

**The validation anchor is claimed only where it applies, and the claim is the
server's** (`UX-RES-03`). Whether these inputs *are* the published case, and by
how much the result misses it, are decided by `/api/calc` and rendered verbatim.
The page used to decide both, with its own comparison tolerance and its own
deviation arithmetic — a second implementation of "how far off is it" is a
second answer waiting to disagree with the first.

**The interface derives no engineering quantity** (`UX-RES-04`). This is the
thin-app invariant with a test attached, and stating it found two live defects:
the single-line diagram squared the referral coefficient to label the boundary,
and the verdict line recomputed the deviation from the published answer sixty
lines below a comment saying the gate summary must not. Both now come from the
engine. Arithmetic in the page is confined to chart geometry, where the quantity
being computed is a pixel.

### Refusals

**A refusal renders as a result, not as a failure** (`UX-REF-01`). "This
transformer's test data is inconsistent" is an engineering answer and arrives as
HTTP 200 with a named reason; conflating it with a transport error would train a
caller to retry it.

**A transport failure does not render as an empty result** (`UX-REF-02`). The
distinction matters more on a calculator than most places: a form showing no
inputs and no complaint reads as one that has nothing to ask rather than one
that is broken.

## Relationships

**Calls into** (`self.call_app(...)`):

- (none) - the compute path is deliberately self-contained and deterministic.

**Emits:**

- (none)

**Listens for** (`@on_event`):

- (none)

**Optional apps:**

- `kb` - source/formula popovers may resolve mounted evidence records; absence
  is tolerated.

## Acceptance criteria

- `POST /trust-loop/api/calc` returns the engine result plus method provenance.
- `GET /trust-loop/api/methods` lists the deterministic method.
- `GET /trust-loop/api/conformance` lists the published case.
- `POST /trust-loop/api/conformance/run` re-derives its verdict.
- Changed inputs never retain the published-case validation claim.
- The calculation report is derived from engine-returned steps.
- The real page, report, conformance panel, narrow layout, and refusal paths are
  exercised by `tests/test_sys_trust_loop.py`.

## Accessibility

The calculator supports keyboard-reachable controls, semantic form labels, a
non-sticky narrow layout, and no horizontal scroll trap.

**The diagram and the chart carry a text alternative** (`UX-ACC-01`). Both are
generated SVG and both are load-bearing — the single-line diagram is where the
impedance chain becomes a topology, and the contribution chart is where
"dominant element" stops being a word. A reader using a screen reader gets the
same two facts or the page has told them less than it told everyone else.

**The narrow layout does not trap the scroll** (`UX-ACC-02`). Traced to a
browser test rather than a static one, because whether a layout traps a scroll
is a rendered fact and markup cannot answer it.

Semantic form labels are `UX-INP-03` above, stated once with the inputs rather
than restated here.

## Open questions

- Which second independent public reference case is suitable for broadening the
  validation demonstration?
- Where should durable assurance receipts live so release artifacts and the
  mounted vault can both reference them without making runtime telemetry the
  only copy?

## Future

- Persist versioned conformance, hand-check, and release-verification receipts.
- Expose the package index and receipt freshness through a shared assurance view
  after a second engineering app proves the same shape.
