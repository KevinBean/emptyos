# Product Loop - From a Verified Calculator to an Interface People Can Use

> The sequence that runs **after** the Trust Loop. The Trust Loop
> (`docs/TRUST-LOOP.md`) proves a calculation is right. The Product Loop builds
> the interface a real user works in and makes it easy to use. It is about the
> interface, not about how it ships. They answer different questions, are
> built for different readers, and must not share stage numbering: Trust Loop
> stages are 1-7, Product Loop stages are P1-P5. (These are stage ids, not the
> principle numbers P1-P10 scored in `docs/DESIGN.md`.)

```text
Trust Loop:    1 source -> 2 spec -> 3 engine -> 4 validation -> 5 test bench -> 6 report -> 7 release verification
Product Loop:                                     (gate green) -> P1 users -> P2 contract -> P3 face -> P4 usability review -> P5 user test
```

The Product Loop branches off after the Validation gate and ends when a person
who did not write the tool can use the face unaided. It ends at an interface,
not at a package: whether that interface is served as a page, wrapped as an
app, packaged as an executable or hosted is a later and separate choice (see
**Delivery is separate**, below).

## Why a second loop

The Trust Loop's Stage 5 interface exists to make a result checkable: every
calculation reachable on its own, every method selectable, the working, the
provenance, the conformance run and the raw payload all on the page. That is
the right interface for the person developing and verifying the calculator,
and the wrong one for the person who only needs an answer. Hiding the checking
controls behind a toggle does not turn the first into the second: the page
still asks the user to know which calculation to open, in what order, and which
of its numbers matter.

This was first seen on a calculator built outside this repository to the same
contract; no app here has reached that point yet, so it is stated as the reason
for this document and not as a measurement of this tree. The fix is not a
better toggle. It is a second interface, designed for a different reader, built
by a sequence with its own stages and its own gate.

## Two names, never mixed

| Name | Built by | Reader | Purpose |
|---|---|---|---|
| **Test bench** | Trust Loop Stage 5 | the developer, the checking engineer | reach every calculation, method, working step and conformance case; prove a number |
| **Product face** | Product Loop P3 | the user who did not write the tool | one journey from a question to an answer and a report |

"Workbench" is deliberately not one of the two: it already names the
`basic-engineer` tier (a bundle of calculators) and several full-bleed design
surfaces. An interface whose job is checking is a **test bench**.

The test bench is never retired. It is where a result seen in the product face
is opened and checked.

## Entry condition

The Product Loop starts only when the app's Trust Loop has passed its first
gate: Validation is green and `[assurance] status` is at least `demonstration`.
A product face over unproven numbers makes them look more finished than they
are. That is the one thing this sequence must never do.

## The joining rule

**The product face shows only numbers the test bench can reproduce.** It
computes nothing, and every result it shows links to the same saved case opened
in the test bench. This is the Trust Loop's Stage 5 invariant ("the interface
collects, transmits, and renders values. It never recalculates an engineering
result.") applied to a second interface, plus the one thing a second interface
adds: a way back to the first.

**The face stays inside the claim.** The test bench can reproduce a number;
the Trust Loop's claim says which numbers are *proven* (`ASSURANCE.md`
§ Current claim and § Claim boundary). A face drawn over a calculation outside
that boundary makes it look finished, which is what the entry condition exists
to stop, arriving one calculation at a time. So the P2 contract names, for each
answer section, the result it reports on, and each must be inside the claim
boundary. A section outside it is either left out or rendered with a visible
note that it is outside the verified claim. The test that pins the contract
reads the boundary, so moving it moves the test.

This holds for a page that serves the product-face reader whether or not this
loop ran on it. The earthing app shows the failure it prevents: its whole-site
page is the best-laid-out page it has, and it is outside its own claim
(`ASSURANCE.md` lists the whole-site model under *Outside*), while the page
inside the claim is the one being rebuilt.

Both faces read the same contract (the app's routes and its declared inputs
and outputs) and the same saved records. A product face **never keeps its own
copy of a case** (`.claude/rules/engineering-tier-membership.md` § "A product
face beside a test bench").

**Ownership is split by face, not by topic.** The Trust Loop's `INTENT.md`
owns users, journeys, labels and acceptance for the *test bench*. `PRODUCT.md`
owns the same four things for the *product face*. Neither document states the
other's, so no obligation has two owners.

## The package contract

Same shape as the Trust Loop: a controlled document, an executable deliverable,
and a receipt that something runs. One document per app, `PRODUCT.md`, with one
section per stage.

| Stage | Package | `PRODUCT.md` section | Executable deliverable | Authority / exit |
|---|---|---|---|---|
| P1 | Users and journeys | Users: who, what decision, the one main journey, what is out of scope | none | the owner approves the journey before anything is built (a human decision; nothing runs it) |
| P2 | Face contract | Contract: flow steps, answer sections, plain labels, display precision | a face block in the app's declarations, served with its schema | tests pin the block against the Trust Loop's declared inputs and outputs |
| P3 | Product face | Face rules: what the face may and may never do | the face page or pages | browser tests: no number on the page that is not in the API response |
| P4 | Usability review | Review record: the lenses used, each finding with its severity, where it is and a screenshot, and whether it was fixed or deferred with a reason | a scripted walk of the P1 journey at desktop and phone width, run by the app's test suite, and a browser test pinning each fix | every *blocks* finding fixed, none deferred (a judgement recorded by hand; the fixes are tests) |
| P5 | User test | Test record: date, performer, journey, outcome, every point of friction | none beyond P4's | **gate:** a person who did not write the tool completes the P1 journey unaided. Each point of friction becomes a P4 finding, and the test is repeated with a person new to the tool (recorded by hand; nothing verifies the record) |

### P1. Users and journeys

Name one reader and one decision. A product face that serves two readers is two
faces. State the main journey as the user would describe it, and what the face
deliberately leaves to the test bench (method choice, working, conformance,
comparison against a source).

If the test bench's journeys contradict the product face's (co-equal panels
there, one flow here), say so in both documents: each stance holds for its own
face.

### P2. Face contract

The product face needs five things the test bench does not, and they are
declared beside the app's existing input and output declarations, not in the
page:

1. **Flow**: the ordered steps of the journey, each naming the declared inputs
   it collects, and which are required or defaulted.
2. **Answer**: the ordered sections of the result, each naming declared outputs.
3. **Plain labels**: a label for every key a user can see. A raw key reaching
   the page (`r_ohm_km`) is a contract failure, not a styling one.
4. **Display precision and plain sentences**: how a number is shown and the
   sentence that carries it. Rounding is a rendering step declared here; the
   face never rounds a value it then sends back.
5. **The check behind each section**: the result each answer section reports
   on, which is what its status reads and what must lie inside the Trust
   Loop's claim boundary (**The face stays inside the claim**, above).

Extend the app's one declaration format; do not start a second
(`docs/FRONTEND-DESIGN-LANGUAGE.md` § 13: converge, not multiply). Pin with
tests that every answer reference is a declared output, that the flow covers
every required input of the calculation it drives, and that every visible key
has a label.

### P3. Product face

A second page in the same app: both faces serve the same decision and the
same saved cases, so it is not a separate face app
(`.claude/rules/engineering-tier-membership.md` § "A product face beside a test
bench").

The face **may**: call the app's existing routes, format numbers at the
declared precision, link to the test bench.

The face **may never**:

- compute an engineering number;
- carry a value from one calculation into another (only the engine chains);
- rank options or mark one "recommended" unless the calculation itself selects;
- hide a refusal, a limit, or how firm an input is;
- keep its own copy of a case;
- offer a comparison-only or checking method.

A default shown in the face is shown as an assumption until the user confirms
it. All of `docs/FRONTEND-DESIGN-LANGUAGE.md` applies: five states, the same
page at both densities, no category-specific stylesheet.

**Start from a reference face, not a blank page.** Before building, name the
existing page in this tree whose layout the face will follow, and say what it
takes from it and what it does not. The exemplar today is the earthing app's
whole-site page (`pages/site.html`):

- the case's main picture (the drawing, the plan) is the main surface;
- the result sits beside it in a narrow tabbed panel, and the first tab
  carries the verdicts, each a word in a badge, never colour alone;
- a table row and its element on the picture point at each other;
- every empty state offers the action that fills it;
- the report opens as a rendered document, not as raw text.

What not to copy from it: its header gives five actions equal weight, so
nothing marks the one to press. A face keeps one primary action.

**Then review the design before building it.** Apply the P4 lenses to the
planned layout (a sketch, or screenshots of the reference with the face's own
steps written over them) and record what they find. The cost of skipping this
was measured: the first product face built to this document (the private
fork's, below), built from a blank page, met its P4
review with thirteen findings, one of them *blocks* (no verdict at the top of
the answer). The fix for that one was this exemplar's layout, adopted after the
face had been built once.

### P4. Usability review

A face that is correct can still be hard to use. P4 looks at the real page the
way its reader would, before any reader is asked to.

Walk the P1 journey on the live page at desktop and phone width, as the P1
reader: someone who has never seen the tool. Each finding names the lens it
fails, so a reviewer can tell a judgement from a preference:

- **Usability heuristics** - is the status visible, are the words the
  reader's, can a step be undone, is an error prevented or explained, does the
  page ask the reader to remember what it could show.
- **`docs/FRONTEND-DESIGN-LANGUAGE.md`** - five states, density at both widths,
  plain labels, status never by colour alone.
- **The domain** - is it clear what is firm and what is a placeholder, what was
  refused and why, what to do next, and what the tool does *not* assess.

Grade each finding by what it costs the reader: it **blocks** the journey, it
**slows** it, or it is **polish**. The owner picks which to fix. Each fix gets
a browser test that fails without it; the walk is repeated and the record
shows before and after.

P4 is done when every *blocks* finding is **fixed**. A *slows* or *polish*
finding may be deferred with a written reason; a *blocks* finding may not —
deferring it leaves P4 open, because the reader it blocks is the reader P5
puts in front of the face.

The walk is a script the app's test suite runs, not a one-off: it drives the
journeys in a browser and repeats the round trip - the journey end to end,
then "open in the test bench" on the result, asserting the same case and the
same numbers on both faces. A review by eye on the live page is the review;
the scripted walk is what keeps its fixes fixed.

### P5. User test - the gate

A person who did not write the tool completes the P1 journey unaided. Recorded
by date, performer, journey and outcome, with every point where they hesitated,
asked or went wrong. A scripted walk or a review cannot stand in for it:
whether a journey is *followable* is not machine-checkable, the same split the
Trust Loop states for its own hand walk.

Each point of friction becomes a P4 finding. Fix, then test again with a
person who has not used the tool: someone who saw the earlier version has
learnt it, and can no longer show whether it is followable unaided.

## Delivery is separate

The Product Loop produces an interface. How that interface reaches people is a
separate decision, taken after it and changeable without re-running it: a page
served by the daemon, an app, a packaged executable, a hosted service.

Whatever the shape, three things hold, and none of them is a product-loop
stage:

- the delivery opens on the product face, with the test bench reachable from
  it (for a packaged product, `start_url` in `products/<id>/product.toml`; see
  `.claude/rules/product-packaging.md`, where a `welcome_url` would open first);
- a delivery build is a new build, so it re-runs the Trust Loop's Stage 7 on
  itself and never inherits an earlier build's receipt;
- the delivery owns its own checks - that it boots and that it opens on the
  face - and writes them for that delivery. They are not added to the face's
  tests, and they are not optional: the shared product launcher's health probe
  confirms the daemon answers, never which page the window opened.

## What is and is not built yet

Written 2026-10-03, before the first product face existed; updated the same
day. Stated plainly so this document does not read as more than it is:

- **No app in this repository has completed this loop.** A private fork built
  from this document alone (it shares no code with this repository) has
  completed P1-P4 for one product face; its P5 user test is open. The earthing
  calculator, the planned first consumer here, has since started the Trust
  Loop (`INTENT.md`, and an `ASSURANCE.md` at **draft**), so it does not yet
  meet the entry condition. Its whole-site page already serves the
  product-face reader, outside its claim (**The face stays inside the claim**,
  above).
- **Three records nothing verifies.** P1's approval, P4's judgement that no
  *blocks* finding is open, and P5's user test are recorded by hand. P2, P3
  and each P4 fix are tests a suite runs.
- **No cross-app checker and no manifest opt-in exist.** A `[product]` manifest block
  (`method = "product-loop-v1"`, `status = draft | reviewed | user-tested`) and a
  product-loop checker script are built when a **second** product face
  lands in this repository, on the same extract-on-second-use rule as every
  shared helper. Until then every obligation here that no app test runs is a
  wish, by the Trust Loop's own definition.
- **No shared kit.** A form renderer, a face-block validator and an answer
  card are extracted only when a second caller exists.
- **No shared landing check for a delivery.** Each packaged product writes
  its own check that it opens on its face; the shared launcher does not
  check which page opened, and nothing makes a product write one.

## Cross-references

- `docs/TRUST-LOOP.md` - the assurance contract this follows; Stage 5 builds
  the test bench.
- `docs/ENGINEERING-APP-WORKFLOW.md` - the delivery wrapper around both.
- `.claude/rules/engineering-tier-membership.md` - a face never forks its
  host's data; a product face is a second page, not a face app.
- `.claude/rules/product-packaging.md` - delivery as a packaged product; `start_url`.
- `docs/FRONTEND-DESIGN-LANGUAGE.md` - every page rule applies to both faces.
