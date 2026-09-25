/* Shared harness for the earthing page-payload tests.
 *
 * Extracted so the assertions (earthing_payload.test.mjs) and the payload dump
 * consumed by tests/test_unit_earthing_ui_wiring.py build the payload the SAME
 * way. Two copies of the stubs would let the Python cross-check pass against a
 * payload the JS test never saw — the drift this whole contract exists to stop.
 *
 * Not named *.test.mjs on purpose: tests/test_unit_js_suite.py globs that
 * pattern, and a helper with no tests would fail the runner.
 */

import fs from "node:fs";
import path from "node:path";
import { loadStatic, REPO_ROOT } from "./shim.mjs";

const APP_JS = "apps/extension/engineering/earthing/pages/earthing.js";
const present = fs.existsSync(path.join(REPO_ROOT, APP_JS));

/* A field registry standing in for the form. `getElementById` must return an
 * ELEMENT, never null: earthing.js runs `loadProjectList()` at load time and
 * assigns innerHTML, so a null would abort the load and hand every test an
 * empty context — the "passes for the wrong reason" shape this file exists to
 * kill. Values are whatever the test sets; anything unset reads "" like a real
 * empty input. */
function makeHarness(fields = {}) {
  const els = new Map();
  const el = (id) => {
    if (!els.has(id)) {
      const e = {
        id,
        value: fields[id] !== undefined ? String(fields[id]) : "",
        innerHTML: "",
        hidden: false,
        checked: false,
        style: {},
        classList: { add() {}, remove() {}, contains: () => false },
        getAttribute: () => null,
        setAttribute() {},
        appendChild() {},
        addEventListener() {},
        querySelector: () => null,
        querySelectorAll: () => [],
      };
      els.set(id, e);
    }
    return els.get(id);
  };

  const calls = [];
  const toasts = [];
  const ctx = loadStatic(APP_JS, {
    // earthing.js reads the query string at load time; without this the load
    // schedules async work that dies AFTER the test ends, which node reports
    // as an unhandledRejection rather than a clean failure.
    URLSearchParams,
    STATE: { faultCases: [] },
    EOS: {
      // Capture the request and answer with a canned success so the caller's
      // render path runs. `body` is the JSON string the page actually built.
      apiSafe: async (url, opts) => {
        calls.push({ url, body: JSON.parse(opts.body) });
        return canned(url);
      },
      api: async () => ({}),
      noteActions: () => "",
      normPath: (s) => s,
    },
    EOS_UI: {
      // DELIBERATELY NOT the real escapers. These are identity-ish so an
      // assertion can read the value that was passed in, which means a green
      // run here is NOT evidence that anything is escaped — do not read it as
      // XSS coverage. Escaping is covered where it can actually be observed:
      // EOS_UI.esc/escAttr/jsArg have their own tests in eos_components.test.mjs,
      // and the rendered round-trip (a quote-laden object name surviving an
      // editable row) is checked in the browser walk. Substituting the real
      // implementations here would test the shared bundle a third time and
      // still not exercise a parser, since this shim is not a DOM.
      esc: (s) => String(s == null ? "" : s),
      escAttr: (s) => String(s == null ? "" : s),
      jsArg: (v) => JSON.stringify(v),
      toast: (msg, ok) => toasts.push({ msg, ok }),
      errorState: (o) => `<ERR>${o.message}</ERR>`,
      statusBadge: () => "",
      lineChart: () => {},
      modal: () => {},
      confirm: () => {},
      formModal: () => {},
    },
    document: {
      readyState: "complete",
      body: el("__body"),
      head: el("__head"),
      documentElement: el("__html"),
      createElement: () => el("__tmp" + els.size),
      createTextNode: () => el("__txt"),
      addEventListener() {},
      removeEventListener() {},
      querySelector: () => null,
      // neKindChanged walks .ne-when; an empty list is honest here — this
      // file tests PAYLOADS. Visibility is the browser walk's job.
      querySelectorAll: () => [],
      getElementById: el,
      getAnimations: () => [],
    },
  });
  return { ctx, calls, toasts, el, els };
}

function canned(url) {
  if (url.indexOf("neutral-earthing") !== -1) {
    return {
      ok: true, i_f_a: 954.7756211341818, earth_fault_factor: 1.8043,
      neutral_displacement_v: 18358.6, healthy_phase_v: 34377.0,
      phase_voltage_v: { a: { mag: 0, deg: 0 }, b: { mag: 1, deg: 2 }, c: { mag: 3, deg: 4 } },
      arrangement: "resistance",
      fault_case: {
        label: "33 kV earth fault (resistance earthing)",
        fault_current_a: 954.7756211341818,
        notes: "earth-fault CURRENT derived from the neutral earthing arrangement",
      },
    };
  }
  return {
    ok: true, equivalent_disc_radius_m: 72.2, gpr_v: 10852, tolerable_touch_v: 1489,
    separation_distance_m: 337.6, all_pass: false,
    objects: [
      { name: "a", distance_m: 20, bonded: false, v_object_v: 0, v_feet_v: 10852,
        touch_v: 10852, own_earth_ohm: null, own_earth_modelled: false,
        utilisation: 7.29, band: "fail" },
      { name: "a", distance_m: 300, bonded: true, v_object_v: 10852, v_feet_v: 1679,
        touch_v: 9173, own_earth_ohm: null, own_earth_modelled: false,
        utilisation: 6.16, band: "fail" },
    ],
    governing: { name: "a", utilisation: 7.29 },
    model: "uniform-soil disc",
  };
}


export { makeHarness, canned, APP_JS, present };
