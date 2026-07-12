// eos-cad-views/part-right-tabs.js — tabbed right rail for the part-edit layout.
// It composes the existing inspector / params / part-extensions views so their
// CadStore subscriptions, APIs, and mutation paths stay unchanged.

import { defineView } from '/static/eos-cad-view.js';
import * as inspector from '/static/eos-cad-views/inspector.js';
import * as params from '/static/eos-cad-views/params.js';
import * as extensions from '/static/eos-cad-views/part-extensions.js';

const TABS = [
  { id: 'inspector', label: 'Inspector', title: 'Show selected feature properties', mod: inspector },
  { id: 'params', label: 'Parameters', title: 'Show named parameters', mod: params },
  { id: 'extensions', label: 'Extensions', title: 'Show CAD extension panels', mod: extensions },
];

const STYLES = `
  .cadv-prt { height: 100%; min-width: 0; min-height: 0; display: flex; flex-direction: column;
    border-left: 1px solid var(--border); background: var(--bg); }
  .cadv-prt-tabs { flex: none; display: flex; margin: 8px; }
  .cadv-prt-tabs .eos-tool-segment { flex: 1 1 0; }
  .cadv-prt-body { flex: 1; min-height: 0; min-width: 0; position: relative; }
  .cadv-prt-panel { height: 100%; min-height: 0; min-width: 0; }
  .cadv-prt-panel[hidden] { display: none; }
  .cadv-prt .cadv-insp, .cadv-prt .cadv-params, .cadv-prt .cadv-pext {
    border-left: 0; border-right: 0;
  }
`;

const MARKUP = `<div class="cadv-prt">
  <div class="cadv-prt-tabs eos-tool-segmented" role="tablist" aria-label="Right sidebar panels">
    ${TABS.map((t, i) => `<button type="button" class="eos-tool-segment${i === 0 ? ' active' : ''}"
      data-prt-tab="${t.id}" role="tab" aria-selected="${i === 0 ? 'true' : 'false'}"
      title="${t.title}">${t.label}</button>`).join('')}
  </div>
  <div class="cadv-prt-body">
    ${TABS.map((t, i) => `<div class="cadv-prt-panel" data-prt-panel="${t.id}" role="tabpanel"${i === 0 ? '' : ' hidden'}></div>`).join('')}
  </div>
</div>`;

function setActive(pane, id) {
  pane.querySelectorAll('[data-prt-tab]').forEach((tab) => {
    const active = tab.dataset.prtTab === id;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  pane.querySelectorAll('[data-prt-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.prtPanel !== id;
  });
}

async function mountChildren(vctx) {
  const mounted = [];
  for (const tab of TABS) {
    const pane = vctx.pane.querySelector('[data-prt-panel="' + tab.id + '"]');
    if (!pane || !tab.mod || typeof tab.mod.mount !== 'function') continue;
    await tab.mod.mount({ ...vctx, pane, config: { view: tab.id } });
    mounted.push(tab.mod);
  }
  vctx._partRightTabs = mounted;
}

function wireTabs(vctx) {
  vctx.pane.querySelectorAll('[data-prt-tab]').forEach((tab) => {
    tab.addEventListener('click', () => setActive(vctx.pane, tab.dataset.prtTab));
  });
}

const _v = defineView({
  id: 'part-right-tabs',
  styles: STYLES,
  markup: MARKUP,
  events: [],
  async mount(vctx) {
    wireTabs(vctx);
    await mountChildren(vctx);
  },
  teardown(vctx) {
    (vctx._partRightTabs || []).forEach((mod) => {
      try { if (mod.teardown) mod.teardown(); } catch (e) { /* best-effort */ }
    });
    vctx._partRightTabs = [];
  },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
