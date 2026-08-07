// Trust Loop page — thin render layer over /trust-loop/api/calc.
// Convention: render from STATE (app-conventions-for-export §1); the page
// computes nothing — every number in the DOM came from the engine's response.

var STATE = { result: null, provenance: null, error: '' };

var FIELD_MAP = { rho: '#rho', t_s: '#ts', body_kg: '#body', rho_s: '#rhos', h_s: '#hs' };

var STAGES = [
  { n: 1, t: 'Knowledge system', d: 'The formula note the code implements', href: '/kb/#ieee-80-touch-step-voltages' },
  { n: 2, t: 'Algorithm doc', d: 'Equations, limits, acceptance target', href: '/kb/#ieee-80-touch-step-voltages' },
  { n: 3, t: 'Engine', d: 'Pure function — no model on the path', href: '/trust-loop/api/methods' },
  { n: 4, t: 'Conformance gate', d: "The standard's published case, run live", href: '#conformance' },
  { n: 5, t: 'App', d: 'Thin UI — the interface computes nothing', href: '#calc' },
  { n: 6, t: 'Report', d: 'The working, line by line', href: '#working' },
  { n: 7, t: 'End-to-end test', d: 'Browser tests walk this exact form', href: '#calc' },
];

function renderStrip() {
  var el = document.getElementById('loop-strip');
  el.innerHTML = STAGES.map(function (s) {
    return '<a class="tl-stage" href="' + escAttr(s.href) + '">' +
      '<div class="n">Stage ' + s.n + '</div>' +
      '<div class="t">' + esc(s.t) + '</div>' +
      '<div class="d">' + esc(s.d) + '</div></a>';
  }).join('');
}

var WORK_ROWS = [
  ['c_s',         'C_s',            'surface-layer derating factor', ''],
  ['rho_surface', 'ρ_surface', 'effective surface resistivity', ' Ω·m'],
  ['i_b_a',       'I_B = k/√t', 'tolerable body current',       ' A'],
  ['r_touch_ohm', 'R_touch = 1000 + 1.5·C_s·ρ_s', 'touch body-circuit resistance', ' Ω'],
  ['r_step_ohm',  'R_step = 1000 + 6·C_s·ρ_s',   'step body-circuit resistance',  ' Ω'],
];

function render() {
  var err = document.getElementById('out-err');
  var warn = document.getElementById('out-warn');
  var touch = document.getElementById('out-touch');
  var step = document.getElementById('out-step');
  var work = document.getElementById('out-work');
  var prov = document.getElementById('out-prov');

  err.hidden = !STATE.error;
  err.textContent = STATE.error || '';
  var r = STATE.result;
  if (!r) {
    touch.textContent = step.textContent = '—';
    work.innerHTML = '';
    prov.textContent = '';
    warn.hidden = true;
    return;
  }
  touch.textContent = r.e_touch_v.toFixed(2) + ' V';
  step.textContent = r.e_step_v.toFixed(2) + ' V';
  work.innerHTML = '<table>' + WORK_ROWS.map(function (row) {
    return '<tr><td class="sym">' + esc(row[1]) + '</td><td>' + esc(row[2]) + '</td>' +
      '<td class="val">' + esc(String(r[row[0]])) + row[3] + '</td></tr>';
  }).join('') + '</table>';
  warn.hidden = !(r.warnings && r.warnings.length);
  warn.textContent = (r.warnings || []).join(' ');
  var p = STATE.provenance || {};
  prov.textContent = p.method
    ? 'computed by ' + p.method + ' v' + (p.method_version || '?') + ' · deterministic · no model call'
    : '';
}

function gatherInputs() {
  var body = {
    rho: parseFloat(document.getElementById('rho').value),
    t_s: parseFloat(document.getElementById('ts').value),
    body_kg: parseInt(document.getElementById('body').value, 10),
  };
  var rhos = parseFloat(document.getElementById('rhos').value);
  var hs = parseFloat(document.getElementById('hs').value);
  if (rhos > 0 && hs > 0) { body.rho_s = rhos; body.h_s = hs; }
  return body;
}

async function calc() {
  var body = gatherInputs();
  if (!(body.rho > 0) || !(body.t_s > 0)) return;
  var data = await EOS.apiSafe('/trust-loop/api/calc', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (data && data.error) {
    STATE.error = data.error; STATE.result = null; STATE.provenance = null;
  } else if (data && data.result) {
    STATE.error = ''; STATE.result = data.result; STATE.provenance = data.provenance;
  }
  render();
}

var _debounce = null;
function calcSoon() { clearTimeout(_debounce); _debounce = setTimeout(calc, 250); }

function loadAnchorCase() {
  document.getElementById('rho').value = 100;
  document.getElementById('ts').value = 0.5;
  document.getElementById('body').value = '50';
  document.getElementById('rhos').value = '';
  document.getElementById('hs').value = '';
  calc();
}

function init() {
  renderStrip();
  ['rho', 'ts', 'body', 'rhos', 'hs'].forEach(function (id) {
    document.getElementById(id).addEventListener('input', calcSoon);
    document.getElementById(id).addEventListener('change', calcSoon);
  });
  document.getElementById('btn-anchor').addEventListener('click', loadAnchorCase);

  // KB citation "?" — feature-detected; renders nothing when kb is absent.
  var slot = document.querySelector('[data-kb-slot]');
  if (slot && window.EOS_UI && EOS_UI.kbInfoBtn) {
    slot.innerHTML = EOS_UI.kbInfoBtn('ieee-80-touch-step-voltages');
  }

  if (window.EOS_UI && EOS_UI.conformancePanel) {
    EOS_UI.conformancePanel({ mount: '#conf-panel', app: 'trust-loop', title: 'The gate' });
  }
  if (window.EOS_UI && EOS_UI.shareLink) {
    EOS_UI.shareLink({ mount: '#share-slot', fields: FIELD_MAP });
  }

  var pf = (window.EOS_UI && EOS_UI.prefillForm)
    ? EOS_UI.prefillForm({ fields: FIELD_MAP, onReady: calc })
    : { ran: false };
  if (!pf.ran) calc();
}

init();
