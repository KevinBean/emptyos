// Trust Loop page — thin render layer over /trust-loop/api/calc.
// Convention: render from STATE (app-conventions-for-export §1); the page
// computes nothing — every number in the DOM came from the engine's response.

var STATE = { result: null, provenance: null, error: '', inputs: {}, published: null,
              methods: null, gate: null };

var FIELDS = ['u_net', 'i_k_net', 'c', 'r_mv', 'x_mv', 's_n', 'u_2n', 'vk_pct', 'pk_pct', 'r_lv', 'x_lv'];
var FIELD_MAP = {};
FIELDS.forEach(function (f) { FIELD_MAP[f] = '#' + f; });

// The seven stages. `ev(state)` returns the card's evidence line — where the
// running system can answer for itself (the method it resolved, the gate's
// verdict, how many lines the report has), the card shows THAT rather than a
// claim about it. A stage that can only be asserted says so plainly.
// The seven stages. Each card says what it PRODUCES (which is the next
// stage's input — that is the chain), who did what, and what actually
// decides. The three-role split is the point: on most stages a model drafts
// and a human scopes, and on stage 4 neither of them decides anything.
// `ev(state)` returns live evidence where the system can answer for itself.
var STAGES = [
  {
    n: 1, t: 'Knowledge system', href: '#', onclick: 'showSource()',
    produces: 'the source, digested — its inputs, its answer, its limits',
    d: 'Before any code, the authority gets structured.',
    acts: [
      { who: 'human', what: 'picks the source, and vouches for it' },
      { who: 'ai', what: 'drafts the digest — clauses, the case, its limits' },
      { who: 'human', what: 'checks the clause text came over verbatim' },
    ],
    decides: 'the source itself — verbatim, plus an audit that walks every claim',
    ev: function () { return { text: 'ABB TAP No. 2 cl. 2.2 · open, no login' }; },
  },
  {
    n: 2, t: 'Algorithm document', href: '#', onclick: 'showAlgorithm()',
    produces: 'a specification, and the acceptance target named up front',
    d: 'What the calculation is, and what would prove it right.',
    acts: [
      { who: 'human', what: 'scopes it — what is in, what is out' },
      { who: 'ai', what: 'drafts the document' },
      { who: 'human', what: 'names the number that will judge the engine' },
    ],
    decides: 'the engineer',
    ev: function () { return { text: '11 inputs · 5 stated limits · anchor named first' }; },
  },
  {
    n: 3, t: 'Engine', href: '#', onclick: 'showMethods()',
    produces: 'a pure function — a candidate, not yet an answer',
    d: 'Deterministic, unit-testable, no model on the compute path.',
    acts: [
      { who: 'ai', what: 'writes most of the code, from the specification' },
      { who: 'human', what: 'reviews the draft against that specification' },
    ],
    decides: 'nothing yet — stage 4 does',
    ev: function (s) {
      var m = s.methods && s.methods.fault && s.methods.fault[0];
      return { text: m ? m.id + ' v' + m.version + ' · deterministic' : 'pure function' };
    },
  },
  {
    n: 4, t: 'Conformance gate', href: '#conformance', pivot: true,
    produces: 'a verdict — and on a fail, stage 3 goes round again',
    d: 'The engine must reproduce the published answer before it ships.',
    acts: [
      { who: 'auto', what: 'the case runs — on demand, and on every push' },
    ],
    decides: 'the published number. Neither of us gets a vote here.',
    ev: function (s) {
      var g = s.gate;
      if (!g) return { text: 'not run yet' };
      if (g.error) return { text: 'gate error: ' + g.error, bad: true };
      return g.passed
        ? { text: '✓ PASS  ' + g.got + ' A vs ' + g.expected + ' A · ' + g.dev + '% ≤ ' + g.tol + '%', ok: true }
        : { text: '✗ FAIL  ' + g.got + ' A vs ' + g.expected + ' A', bad: true };
    },
  },
  {
    n: 5, t: 'App', href: '#calc',
    produces: 'the surface you are using now',
    d: 'A thin interface over a gated engine.',
    acts: [
      { who: 'human', what: 'decides what a user needs to see' },
      { who: 'ai', what: 'drafts the interface' },
    ],
    decides: 'the engine — no model runs when you press calculate',
    ev: function (s) {
      return { text: s.provenance && s.provenance.method
        ? 'every number here came from ' + s.provenance.method : 'the interface computes nothing' };
    },
  },
  {
    n: 6, t: 'Report', href: '#report',
    produces: 'the derivation, line by line',
    d: 'An answer without its working is not a deliverable.',
    acts: [
      { who: 'ai', what: 'drafts the assembly' },
      { who: 'human', what: 'checks a line by hand — the whole point of it' },
    ],
    decides: 'the same source the engine was built from',
    ev: function (s) {
      var n = s.result && s.result.steps ? s.result.steps.length : 0;
      return { text: n ? n + ' lines · Given → I_k3' : 'inputs → result' };
    },
  },
  {
    n: 7, t: 'End-to-end test', href: '#calc',
    produces: 'proof on the path a user actually walks',
    d: 'Unit tests prove the maths; browser tests prove the product.',
    acts: [
      { who: 'human', what: 'names what must not break' },
      { who: 'ai', what: 'drafts the tests' },
    ],
    decides: 'the run — red or green',
    // Counted, not rounded — and honest about which of them CI actually runs:
    // push CI is `-m "api and not llm and not interactive"`, so the 13 API
    // tests gate every push and the unit + browser suites are local/on-demand.
    // A card on this page above all others must not decorate with test counts.
    ev: function () { return { text: '35 unit · 15 browser · 13 api gate every push' }; },
  },
];

function _roles(s) {
  // Ordered by WHEN, not by who. The sequence differs per stage and that is
  // the interesting part: on stage 2 a person scopes the work before a model
  // drafts it, on stage 6 the model drafts first and a person checks after.
  // Grouping by actor hid that.
  var steps = (s.acts || []).map(function (a, i) {
    return '<span class="r-n">' + (i + 1) + '</span>'
      + '<span class="r-k ' + a.who + '">' + esc(a.who === 'auto' ? 'runs' : a.who) + '</span>'
      + '<span class="r-v">' + esc(a.what) + '</span>';
  }).join('');
  return '<div class="tl-roles">' + steps
    // No glyph in the ordinal cell: U+2696 is not in the mono stack and
    // rendered as tofu. The rule and the label already carry the meaning.
    + '<span class="r-n de"></span>'
    + '<span class="r-k de">decides</span>'
    + '<span class="r-v de">' + esc(s.decides) + '</span></div>';
}

function focusStage(el, targetId) {
  var strip = document.getElementById('loop-strip');
  Array.prototype.forEach.call(strip.querySelectorAll('.tl-stage'), function (n) {
    n.classList.remove('on');
  });
  el.classList.add('on');
  var target = document.getElementById(targetId);
  if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderStrip() {
  document.getElementById('loop-strip').innerHTML = STAGES.map(function (s) {
    var e = s.ev(STATE) || { text: '' };
    var cls = e.ok ? ' ok' : (e.bad ? ' bad' : '');
    // An in-page anchor marks itself active and scrolls; a real destination
    // (the doc, a modal) is left alone.
    var act = s.onclick ? ' onclick="' + escAttr(s.onclick) + ';return false"'
      : (s.href.charAt(0) === '#' ? ' onclick="focusStage(this,&#39;' + escAttr(s.href.slice(1)) + '&#39;);return false"' : '');
    return '<a class="tl-stage' + (s.pivot ? ' pivot' : '') + '" href="' + escAttr(s.href) + '"' + act + '>' +
      '<div class="n">Stage ' + s.n + '</div>' +
      '<div class="t">' + esc(s.t) + '</div>' +
      '<div class="p"><span class="arrow">→</span>' + esc(s.produces) + '</div>' +
      '<div class="d">' + esc(s.d) + '</div>' +
      _roles(s) +
      '<div class="ev' + cls + '">' + esc(e.text) + '</div></a>';
  }).join('');
}

// Stages 1 and 2 open in place. Sending a reader to another app — or even to
// a document page — costs them the calculator they were looking at, and the
// loop is easier to follow when its artifacts surface beside the thing they
// produced. Each modal renders the SAME bytes its endpoint serves.

function showSource() {
  var p = STATE.published;
  if (!p || !p.inputs) { EOS.toast && EOS.toast('Source not loaded'); return; }
  var i = p.inputs;
  var rows = [
    ['Supply network', i.u_net / 1000 + ' kV, 500 MVA / ' + i.i_k_net / 1000 + ' kA'],
    ['Voltage factor', 'c = ' + i.c],
    ['MV cable', 'R = ' + (i.r_mv * 1000) + ' mΩ, X = ' + (i.x_mv * 1000) + ' mΩ'],
    ['Transformer', (i.s_n / 1000) + ' kVA, ' + (i.u_net / 1000) + '/' + (i.u_2n / 1000)
      + ' kV, vₖ ' + i.vk_pct + ' %, pₖ ' + i.pk_pct + ' %'],
    ['LV cable, 5 m', 'R = ' + (i.r_lv * 1000).toFixed(3) + ' mΩ, X = ' + (i.x_lv * 1000).toFixed(3) + ' mΩ'],
  ].map(function (r) {
    return '<tr><td style="padding:6px 8px;border-top:1px solid var(--border);color:var(--text-secondary)">'
      + esc(r[0]) + '</td><td style="padding:6px 8px;border-top:1px solid var(--border);font-family:var(--mono)">'
      + esc(r[1]) + '</td></tr>';
  }).join('');
  EOS_UI.modal({
    title: 'Stage 1 — the source, digested',
    width: '720px',
    body: '<p style="font-size:var(--fs-small);line-height:var(--lh-prose);color:var(--text-secondary)">'
      + 'ABB <em>Technical Application Paper No. 2 — MV/LV transformer substations</em>, clause 2.2. '
      + 'Freely downloadable, no login: '
      + '<a href="https://library.e.abb.com/public/2c522f583c884a4fbdf3968e1fdf1481/1SDC007101G0202.pdf" '
      + 'target="_blank" rel="noopener">1SDC007101G0202.pdf</a>. '
      + 'A published case is what lets a gate mean something: the anchor below is a number you can look '
      + 'up rather than one this app chose for itself.</p>'
      + '<h3 style="font-size:var(--fs-body);color:var(--text-heading);margin:var(--space-4) 0 var(--space-2)">Published inputs</h3>'
      + '<table style="width:100%;border-collapse:collapse;font-size:var(--fs-small)">' + rows + '</table>'
      + '<h3 style="font-size:var(--fs-body);color:var(--text-heading);margin:var(--space-4) 0 var(--space-2)">Published result</h3>'
      + '<p style="font-family:var(--mono);font-size:var(--fs-h2);color:var(--text-heading);margin:0">'
      + esc(String(p.published_result_a)) + ' A <span style="font-size:var(--fs-small);color:var(--text-muted)">= 14.95 kA</span></p>'
      + '<p style="font-size:var(--fs-small);color:var(--text-secondary);line-height:var(--lh-prose);margin-top:var(--space-3)">'
      + 'Reproduced independently in the knowledge base before the engine was written. '
      + 'The engine lands 0.06 % under, which is the paper dividing by its own rounded impedance — '
      + 'see <a href="#" onclick="EOS_UI.closeModal();showAlgorithm();return false">the algorithm document</a>, § 6.1.</p>',
  });
}

function showAlgorithm() {
  EOS_UI.modal({
    title: 'Stage 2 — the algorithm document',
    width: '900px',
    body: '<div id="algo-doc" class="doc-body"><p style="color:var(--text-muted)">Loading…</p></div>',
  });
  fetch('/api/appdoc?app=trust-loop&file=ALGORITHM.md')
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
    .then(function (md) {
      var el = document.getElementById('algo-doc');
      if (!el) return;                       // closed before it arrived
      el.innerHTML = EOS_UI.renderMarkdown(md);
      if (EOS_UI.typesetMath) EOS_UI.typesetMath(el);
    })
    .catch(function (e) {
      var el = document.getElementById('algo-doc');
      if (el) el.innerHTML = '<p style="color:var(--danger)">Could not load: ' + esc(String(e.message || e))
        + '</p><p><a href="/appdoc?app=trust-loop&file=ALGORITHM.md">Open the document page instead</a></p>';
    });
}

// Stage 3's destination. The registry as JSON is an API response, not
// something to hand a reader — same objection as dropping them into a raw
// markdown file. Rendered from the same endpoint the card reads.
function showMethods() {
  var m = STATE.methods && STATE.methods.fault;
  if (!m || !m.length) { EOS.toast && EOS.toast('No methods registered'); return; }
  var body = m.map(function (x) {
    var refs = (x.references || []).length
      ? '<div style="margin-top:var(--space-2);font-size:var(--fs-mono);color:var(--text-muted)">'
        + esc((x.references || []).join(' · ')) + '</div>' : '';
    return '<div style="border:1px solid var(--border);border-radius:var(--radius);'
      + 'padding:var(--space-3);margin-bottom:var(--space-3)">'
      + '<div style="font-family:var(--mono);color:var(--text-heading);font-weight:600">'
      + esc(x.id) + ' <span style="color:var(--text-muted);font-weight:400">v' + esc(x.version || '?') + '</span>'
      + (x.default ? ' <span class="eos-badge">default</span>' : '') + '</div>'
      + '<div style="color:var(--text-secondary);margin-top:4px">' + esc(x.label || '') + '</div>'
      + '<p style="margin:var(--space-2) 0 0;font-size:var(--fs-small);line-height:var(--lh-prose)">'
      + esc(x.description || '') + '</p>' + refs
      + '<div style="margin-top:var(--space-2);font-size:var(--fs-mono);color:var(--text-muted)">'
      + (x.available === false ? 'unavailable: ' + esc(x.disabled_reason || '') : 'available · deterministic · no model call')
      + '</div></div>';
  }).join('');
  EOS_UI.modal({
    title: 'Engine — registered methods',
    body: body + '<p style="font-size:var(--fs-small);color:var(--text-secondary);line-height:var(--lh-prose)">'
      + 'The method registry is what the conformance gate dispatches against, so the method named here '
      + 'is the one the published case was run through. Specification: '
      + '<a href="/appdoc?app=trust-loop&amp;file=ALGORITHM.md">the algorithm document</a>.</p>',
  });
}

function _shareOf(r, name) {
  for (var i = 0; i < r.contributions.length; i++) {
    if (r.contributions[i].name === name) return r.contributions[i].share_pct;
  }
  return '?';
}

// ── Schematic ──────────────────────────────────────────────────────────────
// The network the numbers describe, drawn: the chain of impedances between
// source and fault, and where the referral boundary sits. Geometry is
// illustration; every NUMBER printed on it comes from the engine response.
function renderScene() {
  var el = document.getElementById('scene');
  var cap = document.getElementById('scene-cap');
  var r = STATE.result;
  if (!el) return;
  if (!r) { el.innerHTML = ''; cap.textContent = ''; return; }

  var W = 620, H = 152, y = 76;
  var xs = [40, 165, 290, 415, 540];   // grid, mv cable, transformer, lv cable, fault
  var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Single-line diagram: supply network, MV cable, transformer, LV cable and the fault point, with each element impedance">';

  // referral boundary — everything to its left is divided by K squared
  s += '<line x1="290" y1="20" x2="290" y2="' + (H - 8) + '" stroke="var(--border-strong)" stroke-width="1" stroke-dasharray="3 3"/>';
  s += '<text x="284" y="14" text-anchor="end" fill="var(--text-muted)" font-size="10">referred by K&#178; = ' +
       esc(String(Math.round(r.ratio * r.ratio))) + '</text>';
  s += '<text x="296" y="14" fill="var(--text-muted)" font-size="10">LV base</text>';

  s += '<line x1="40" y1="' + y + '" x2="536" y2="' + y + '" stroke="var(--text-secondary)" stroke-width="2"/>';
  s += '<circle cx="40" cy="' + y + '" r="11" fill="var(--bg-card)" stroke="var(--text-secondary)" stroke-width="2"/>';
  s += '<text x="40" y="' + (y + 34) + '" text-anchor="middle" fill="var(--text-muted)" font-size="10">grid</text>';

  // transformer — two overlapping coils
  s += '<circle cx="282" cy="' + y + '" r="12" fill="none" stroke="var(--text-secondary)" stroke-width="2"/>';
  s += '<circle cx="298" cy="' + y + '" r="12" fill="none" stroke="var(--text-secondary)" stroke-width="2"/>';

  // the fault
  s += '<path d="M540 ' + (y - 15) + ' L532 ' + y + ' L544 ' + y + ' L536 ' + (y + 17) + '" fill="none" stroke="var(--series-x)" stroke-width="2.5"/>';
  s += '<text x="556" y="' + (y + 4) + '" fill="var(--series-x)" font-size="12" font-weight="600">' +
       esc(String(r.i_k3_ka)) + ' kA</text>';

  var order = ['Supply network', 'MV cable', 'Transformer', 'LV cable'];
  order.forEach(function (name, i) {
    var c = null;
    for (var j = 0; j < r.contributions.length; j++) {
      if (r.contributions[j].name === name) { c = r.contributions[j]; break; }
    }
    if (!c) return;
    var cx = (name === 'Transformer') ? 290 : (xs[i] + xs[i + 1]) / 2;
    s += '<text x="' + cx + '" y="' + (y - 28) + '" text-anchor="middle" fill="var(--text-secondary)" font-size="10">' + esc(name) + '</text>';
    s += '<text x="' + cx + '" y="' + (y - 16) + '" text-anchor="middle" fill="var(--text)" font-size="11">' +
         esc(c.z_ohm.toExponential(2)) + ' &#937;</text>';
    s += '<text x="' + cx + '" y="' + (y + 34) + '" text-anchor="middle" fill="var(--text-muted)" font-size="10">' +
         esc(String(c.share_pct)) + '%</text>';
  });
  s += '</svg>';
  el.innerHTML = s;
  cap.textContent = 'Everything left of the dashed line is referred to the LV base by K² before it is summed. '
    + r.dominant + ' dominates at ' + _shareOf(r, r.dominant) + '% of the total impedance.';
}

// ── Chart ──────────────────────────────────────────────────────────────────
// Stacked R and X per element. A bar chart because the job is comparing
// magnitudes across a handful of named categories, and the headline finding
// is which one dominates — which a bar shows and a lone number does not.
function renderChart() {
  var el = document.getElementById('chart');
  var cap = document.getElementById('chart-cap');
  if (!el) return;
  var r = STATE.result;
  if (!r) { el.innerHTML = ''; cap.textContent = ''; return; }

  var rows = r.contributions;
  var W = 720, rowH = 46, padL = 132, padR = 150, padT = 26;
  var H = padT + rows.length * rowH + 10;
  var maxZ = Math.max.apply(null, rows.map(function (c) { return c.z_ohm; })) || 1;
  var plotW = W - padL - padR;
  var sx = function (v) { return (v / maxZ) * plotW; };

  var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Impedance contribution of each element, split into resistance and reactance">';
  s += '<text x="' + padL + '" y="14" fill="var(--text-muted)" font-size="11">impedance at the LV base (&#937;)</text>';
  rows.forEach(function (c, i) {
    var y = padT + i * rowH;
    var wR = Math.max(sx(c.r_ohm), 0), wX = Math.max(sx(c.x_ohm), 0);
    s += '<text x="' + (padL - 10) + '" y="' + (y + 17) + '" text-anchor="end" fill="var(--text)" font-size="12">' + esc(c.name) + '</text>';
    // 2px surface gap between the two fills so the boundary reads at any size
    s += '<rect x="' + padL + '" y="' + y + '" width="' + wR.toFixed(1) + '" height="22" rx="4" fill="var(--series-r)"/>';
    s += '<rect x="' + (padL + wR + 2) + '" y="' + y + '" width="' + wX.toFixed(1) + '" height="22" rx="4" fill="var(--series-x)"/>';
    s += '<text x="' + (padL + wR + wX + 12) + '" y="' + (y + 16) + '" fill="var(--text-secondary)" font-size="11">' +
         esc(c.z_ohm.toExponential(2)) + ' &#937; &#183; ' + esc(String(c.share_pct)) + '%</text>';
  });
  s += '</svg>';
  el.innerHTML = s;
  cap.textContent = 'The transformer is not merely the largest term — at ' + _shareOf(r, 'Transformer')
    + '% it sets the answer, and the grid and both cables together move it by only a few percent. '
    + 'That is what the published case exists to demonstrate, and it is visible here without reading a number.';
}

// ── Calculation report ─────────────────────────────────────────────────────
// Stage 6: inputs through to result, every line showing formula,
// substitution and value, so a reviewing engineer can CHECK the arithmetic
// instead of trusting it. The steps come from the engine alongside the
// answer, so the report cannot drift from the number it explains.
function renderReport() {
  var el = document.getElementById('out-work');
  var r = STATE.result;
  if (!el) return;
  if (!r || !r.steps) { el.innerHTML = ''; return; }

  var html = '<table class="tl-report">';
  var group = null;
  r.steps.forEach(function (s) {
    if (s.group !== group) {
      group = s.group;
      html += '<tr class="grp"><td colspan="4">' + esc(group) + '</td></tr>';
    }
    var isGiven = s.substitution === 'given';
    html += '<tr' + (s.note ? ' class="has-note"' : '') + '>'
      + '<td class="sym">' + esc(s.symbol) + '</td>'
      + '<td class="fml">' + esc(s.formula) + '</td>'
      + '<td class="sub">' + (isGiven ? '<span class="given">given</span>' : esc(s.substitution)) + '</td>'
      + '<td class="val">' + esc(s.value_str) + (s.unit ? ' ' + esc(s.unit) : '') + '</td>'
      + '</tr>';
    if (s.note) {
      html += '<tr class="note"><td></td><td colspan="3">' + esc(s.note) + '</td></tr>';
    }
  });
  html += '</table>';
  el.innerHTML = html;
}

function render() {
  var err = document.getElementById('out-err');
  var ik = document.getElementById('out-ik');
  var verdict = document.getElementById('out-verdict');
  var work = document.getElementById('out-work');
  var prov = document.getElementById('out-prov');

  err.hidden = !STATE.error;
  err.textContent = STATE.error || '';
  var r = STATE.result;
  if (!r) {
    ik.textContent = '—';
    work.innerHTML = ''; prov.textContent = ''; verdict.textContent = '';
    renderScene(); renderChart();
    return;
  }
  ik.textContent = r.i_k3_ka + ' kA';

  // Only claim agreement when the inputs really are the published case —
  // an anchor that follows the user's edits around is not an anchor.
  var p = STATE.published;
  if (p && p.inputs && _isPublishedCase(STATE.inputs, p.inputs)) {
    var dev = Math.abs(r.i_k3_a - p.published_result_a) / p.published_result_a * 100;
    // Quote the source's printed amps, not our own kA rounding of them: the
    // paper prints 14 943 A and 14.95 kA, and (14943/1000).toFixed(2) is
    // 14.94 — a digit the source never wrote.
    verdict.innerHTML = '<span style="color:var(--success)">✓</span> published answer '
      + esc(String(p.published_result_a)) + ' A · deviation ' + dev.toFixed(3) + '%';
  } else {
    verdict.innerHTML = '<span style="color:var(--text-muted)">not the published case · '
      + 'no anchor applies to these inputs</span>';
  }

  renderReport();

  var pv = STATE.provenance || {};
  prov.textContent = pv.method
    ? 'computed by ' + pv.method + ' v' + (pv.method_version || '?') + ' · deterministic · no model call'
    : '';
  renderScene();
  renderChart();
  renderStrip();
}

function _isPublishedCase(a, b) {
  for (var i = 0; i < FIELDS.length; i++) {
    var k = FIELDS[i];
    var want = b[k] || 0;
    if (Math.abs((a[k] || 0) - want) > Math.abs(want || 1) * 1e-9) return false;
  }
  return true;
}

function gatherInputs() {
  var body = {};
  FIELDS.forEach(function (f) { body[f] = parseFloat(document.getElementById(f).value); });
  return body;
}

async function calc() {
  var body = gatherInputs();
  if (!(body.u_net > 0) || !(body.s_n > 0) || !(body.u_2n > 0)) return;
  STATE.inputs = body;
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

async function loadPublished() {
  var p = STATE.published || await EOS.apiSafe('/trust-loop/api/published-case');
  if (!p || !p.inputs) return;
  STATE.published = p;
  FIELDS.forEach(function (f) {
    if (p.inputs[f] !== undefined) document.getElementById(f).value = p.inputs[f];
  });
  calc();
}

function _summariseGate(gate) {
  // The per-method diff carries expected, got, the deviation and the
  // tolerance already — read it rather than re-deriving any of them here,
  // so the card cannot disagree with the gate it is reporting.
  if (!gate || gate.error) return { error: (gate && gate.error) || 'unavailable' };
  var res = (gate.results || [])[0];
  if (!res) return { error: 'no cases' };
  var mk = Object.keys(res.methods || {})[0];
  var m = mk ? res.methods[mk] : null;
  if (!m) return { error: 'no method ran' };
  if (m.error) return { error: m.error };
  var d = (m.diffs || [])[0];
  if (!d) return { passed: !!res.passed, got: '?', expected: '?', dev: '?', tol: '?' };
  return {
    passed: !!res.passed,
    got: d.got,
    expected: d.expected,
    dev: d.rel_pct,
    tol: d.tolerance_pct,
  };
}

async function init() {
  renderStrip();
  FIELDS.forEach(function (f) {
    var el = document.getElementById(f);
    el.addEventListener('input', calcSoon);
    el.addEventListener('change', calcSoon);
  });
  document.getElementById('btn-published').addEventListener('click', loadPublished);

  STATE.published = await EOS.apiSafe('/trust-loop/api/published-case');

  // Live evidence for the stage cards. Both are cheap (the engine is a pure
  // function), and both fail soft — a card that cannot be evidenced falls
  // back to stating its claim rather than showing a broken one.
  STATE.methods = await EOS.apiSafe('/trust-loop/api/methods');
  renderStrip();

  // The gate runs on load and shows expanded: on this app the gate IS the
  // subject, so making a reader click Run to see whether the calculator is
  // validated gets the emphasis backwards. onResults feeds the stage card from
  // this same run, so the card and the panel cannot disagree.
  if (window.EOS_UI && EOS_UI.conformancePanel) {
    EOS_UI.conformancePanel({
      mount: '#conf-panel', app: 'trust-loop', title: 'The gate',
      autorun: true, open: true,
      onResults: function (results) {
        STATE.gate = _summariseGate({ results: results });
        renderStrip();
      },
    });
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
