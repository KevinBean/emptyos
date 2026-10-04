# Engine Implementation Record - Trust Loop

## Engine identity

- Implementation: `fault_current.py`
- Public function: `prospective_fault_current(...)`
- Method-registry id/version: `impedance_referral` / `2.0.0`
- Specification: `ALGORITHM.md`
- Runtime boundary: pure Python; no kernel, vault, HTTP, persistence, or model
  call in the calculation.

The reference app keeps the pure engine beside the app for a small public
showcase. New engineering calculators follow the shared `engines/<engine-id>/`
boundary described in `docs/ENGINEERING-APP-WORKFLOW.md`.

## Architecture

Inputs are normalized at the API boundary and validated again by the engine.
The engine returns the result, impedance contributions, provenance-relevant
method values, and every report step. The app renders those values without
recomputing them.

## Data flow

The section above is a *module map* — what each file is for. This one is the
path a number takes, which is a different question and the one no document in
this package could answer.

```
 spec.py ──────────────────┬──► ALGORITHM.md § 2 / § 3        (generated)
 the one declaration       ├──► GET /api/schema ──► the form  (UX-INP-01/02/03)
                           └──► app.py::_coerce_inputs        (the field set only)

 POST /api/calc   {"u_net": 20000, "vk_pct": 4, …}
   │
   ├─ `app.py::_coerce_inputs`     absent field ⇒ `_API_FALLBACK`, i.e. zero,
   │                               NOT the form's initial value — see below
   │
   ├─ `fault_current.py::_validate`   the refusals of ALGORITHM.md § 5
   │       └─ raises ValueError ─► {"error": …} at HTTP 200      (UX-REF-01)
   │
   ├─ `fault_current.py::prospective_fault_current`   pure, stdlib only
   │       ├─ `fault_current.py::network_impedance`   the current form (ALG-NET-01)
   │       ├─ `fault_current.py::transformer_impedance`          (ALG-TR-01)
   │       └─ `fault_current.py::_step` × 22          the working, recorded
   │
   ├─ `app.py::_anchor`            does the published case apply, and by how
   │                               much does it miss                (UX-RES-03)
   │
   └─ {result, anchor, provenance}
         │
         └─ JSON ─► `pages/trust-loop.js::render`      the number + provenance (UX-RES-01)
                    `pages/trust-loop.js::renderReport` formula · substitution (UX-RES-02)
                    `pages/trust-loop.js::renderScene` K² from `ratio_squared` (UX-RES-04)
```

Three things this diagram exists to state, none of which was written down
anywhere before.

**The form's default and the API's fallback are different numbers.** `spec.py`
declares `u_net = 20000`, and a POST omitting `u_net` gets `0.0`, which the
engine refuses. They look interchangeable and are not: single-sourcing the
fallback from the form's value would mean an empty request quietly computed the
published case and returned a confident answer to a question nobody asked.
`_API_FALLBACK` carries the one exception, `c`, because a code factor with a
standard value is not a plant measurement.

**A refusal travels the same path as a result.** It is not an exception escaping
to a 500 — `_validate` raises, `api_calc` catches, and the caller gets HTTP 200
with a named reason. The engine decides *what* is refused; the app decides only
that a refusal is an answer.

**The anchor is decided on this side of the JSON.** `_anchor` compares the
payload against `PUBLISHED_CASE` with an explicit tolerance and computes the
deviation. Both were in the page until 2026-08-13, which meant the interface
held its own copy of "are these the same inputs" and its own arithmetic for "how
far off is it" — a second implementation of an engineering judgement, sitting in
the one layer that is supposed not to make any.

## Traceability

| Requirement | Code symbol | Unit test |
|---|---|---|
| `ALG-REF-01` | `_referred` | `test_referral_round_trips`, `test_referral_ratio` |
| `ALG-NET-01` | `network_impedance` | `test_current_form_value`, `test_power_form_would_not_match` |
| `ALG-NET-02` | `prospective_fault_current` network split | `test_network_split_follows_the_stated_relationships` |
| `ALG-TR-01` | `transformer_impedance` | `test_transformer_rated_current`, `test_intermediate_impedances_match_the_printed_values` |
| `ALG-SUM-01` | `prospective_fault_current` totals | `test_pythagoras_on_the_totals` |
| `ALG-RESULT-01` | `prospective_fault_current` result | `test_reproduces_published_current` |
| `LIM-01` | specification boundary + UI disclosure | `test_algorithm_document_served` |
| `REFUSE-01` | `_validate` positive-domain guard | `test_nonpositive_refused` |
| `REFUSE-02` | `_validate` impedance guard | `test_negative_impedance_refused` |
| `REFUSE-03` | `transformer_impedance` consistency guard | `test_inconsistent_transformer_test_data_refused` |
| `VAL-ABB-01` | manifest conformance case | `test_reproduces_published_current`, `test_conformance_gate_passes` |
| `PROP-DOM-01` | contribution calculation | `test_transformer_dominates_this_topology` |
| `PROP-MONO-01` | engine behaviour | `test_more_impedance_never_raises_the_current` |
| `PROP-LIN-01` | voltage-factor path | `test_voltage_factor_is_linear` |
| `PROP-PYTH-01` | total impedance | `test_pythagoras_on_the_totals` |
| `REPORT-01` | returned `steps` | `TestCalculationReport` |

### Interface requirements

Traced on the same terms as any other, because Stage 5 carrying no executable
receipt is what lets an interface be one panel template repeated. Every test
below is in `tests/test_unit_trust_loop_ui.py`, which is static and needs no
daemon — the engine's requirements are held by tests that run in ten seconds,
and a contract enforced only at release is a contract that gets discovered
broken.

| Requirement | Code symbol | Unit test |
|---|---|---|
| `UX-JRN-01` | `p/index.html` `#btn-published` → `p/trust-loop.js::loadPublished` | `test_ux_jrn_01_the_published_case_loads_in_one_action` |
| `UX-JRN-02` | `p/trust-loop.js::showAlgorithm` → `app.py::api_algorithm` | `test_ux_jrn_02_the_specification_is_reachable_from_the_calculator` |
| `UX-JRN-03` | `p/trust-loop.js::init` `conformancePanel autorun/open` | `test_ux_jrn_03_the_gate_reports_without_being_asked` |
| `UX-INP-01` | `spec.py::FieldSpec.unit` → `p/trust-loop.js::renderForm` | `test_ux_inp_01_every_input_states_a_unit` |
| `UX-INP-02` | `spec.py::FieldSpec.minimum` ↔ `fault_current.py::_validate` | `test_ux_inp_02_the_declared_domain_is_what_the_engine_refuses` |
| `UX-INP-03` | `p/trust-loop.js::renderForm` `<label for>` | `test_ux_inp_03_every_input_is_programmatically_labelled` |
| `UX-INP-04` | `p/index.html` `#in-grid` (no field markup) | `test_ux_inp_04_the_page_declares_no_field_itself` |
| `UX-RES-01` | `app.py::api_calc` provenance → `p/trust-loop.js::render` | `test_ux_res_01_the_result_names_its_method_and_version` |
| `UX-RES-02` | `p/trust-loop.js::renderReport` | `test_ux_res_02_the_working_is_on_the_result_surface` |
| `UX-RES-03` | `app.py::_anchor` | `test_ux_res_03_the_anchor_verdict_is_the_servers` |
| `UX-RES-04` | `p/trust-loop.js` (arithmetic confined to chart geometry) | `test_ux_res_04_the_page_derives_no_engineering_quantity` |
| `UX-REF-01` | `app.py::api_calc` `except ValueError` | `test_ux_ref_01_a_refusal_is_an_answer_not_a_failure` |
| `UX-REF-02` | `p/trust-loop.js::init` `EOS_UI.errorState` | `test_ux_ref_02_a_transport_failure_is_not_an_empty_result` |
| `UX-ACC-01` | `p/trust-loop.js::renderScene`, `::renderChart` `role="img"` | `test_ux_acc_01_generated_svg_carries_a_text_alternative` |
| `UX-ACC-02` | narrow-layout CSS | `tests/test_sys_trust_loop.py::test_narrow_layout_does_not_trap_the_scroll` |

`p/` is `pages/`. `UX-ACC-02` is the one row whose test is a browser test rather
than a static one, and it is deliberate: whether a layout traps a scroll is a
rendered fact, and a static assertion about it would be an assertion about
markup that happens to correlate.

Four of these were defects at the moment the requirement was written down —
`UX-INP-02` reached no input at all, `UX-RES-04` was false in two places,
`UX-RES-03` was decided in the page, and `UX-REF-02` had no failure path to
render. That is the mechanism working. A requirement whose implementation
already exists costs nothing to state and proves nothing about the process that
stated it.

## Numerical implementation

- Python double-precision floating point is used internally.
- Intermediate values are not rounded before the final calculation.
- Display formatting is separate from stored numeric values.
- The 0.5% conformance band belongs to the validation contract, not the engine.
- The network R/X split follows the source relationships exactly rather than
  replacing them with a superficially equivalent identity.

## Dependencies

The calculation uses only the Python standard-library `math` module. Its
numerical behaviour therefore does not depend on an external solver package.

## Error and refusal mapping

| Condition | Engine behaviour |
|---|---|
| non-positive voltage, current, power, voltage factor, or transformer impedance percentage | `ValueError` with a named field reason |
| negative cable/network impedance component | `ValueError` |
| transformer loss implies resistance greater than impedance | `ValueError` describing inconsistent test data |
| total impedance is zero | `ValueError`; no infinite/plausible fallback |
| unknown sensitivity element | `ValueError` |

## Known deviations

The code implements the simplified ABB worked method, not a full current-edition
IEC 60909 study. That is a specification boundary, not a hidden implementation
shortcut.

## Human review

The implementation and tests are present and traceable. A durable review
receipt tied to a commit, reviewer, and date is not yet retained, so this stage
is implemented but not formally signed.
