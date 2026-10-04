// eos-cad-ai-panel.js — shared proposed-action panel for CAD document surfaces.
//
// The panel owns AI chrome and proposal lifecycle only. Each consumer supplies
// its document, selection, transport, apply semantics, and proposal description.
// This keeps eos-cad/1, eos-draft/1, and eos-scene/1 domain rules in their owners.

const STYLE_ID = 'eos-cad-ai-panel-style';

const STYLES = `
  .eos-cad-ai { background:var(--bg-card, var(--panel)); border:1px solid var(--border);
    border-radius:10px; padding:10px; margin-bottom:16px; }
  .eos-cad-ai .cai-head { display:flex; align-items:center; gap:8px; margin:0 0 8px; flex-wrap:wrap; }
  .eos-cad-ai .cai-title { font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
  .eos-cad-ai .cai-pill { margin-left:auto; }
  .eos-cad-ai .cai-selchip { display:flex; align-items:center; gap:6px; font-size:11.5px; color:var(--text);
    background:var(--bg); border:1px solid var(--border); border-radius:8px;
    padding:4px 8px; margin-bottom:6px; }
  .eos-cad-ai .cai-selchip[hidden] { display:none; }
  .eos-cad-ai .cai-selx { margin-left:auto; cursor:pointer; border:0; background:transparent;
    color:var(--muted); padding:0 2px; min-height:24px; font-size:13px; border-radius:6px; }
  .eos-cad-ai .cai-selx:hover { color:var(--accent); border-color:transparent; }
  .eos-cad-ai .cai-abilnote { margin-bottom:6px; }
  .eos-cad-ai .cai-abilnote:empty { display:none; margin:0; }
  .eos-cad-ai textarea.eos-tool-field { width:100%; min-height:64px; margin-bottom:8px; box-sizing:border-box; }
  .eos-cad-ai .cai-row { display:flex; gap:6px; margin-bottom:8px; }
  .eos-cad-ai .cai-fill { flex:1; }
  .eos-cad-ai .cai-note { font-size:11px; color:var(--muted); line-height:1.5; margin-top:8px; }
`;

const MARKUP = `
  <div class="eos-cad-ai">
    <div class="cai-head">
      <span class="cai-title">AI</span>
      <span class="eos-tool-segmented" role="tablist" aria-label="AI mode">
        <button type="button" class="eos-tool-segment" data-cai-mode="new" title="Draft a new document">New</button>
        <button type="button" class="eos-tool-segment" data-cai-mode="edit" title="Draft changes to the current document">Edit</button>
      </span>
      <span class="cai-pill" data-cai-pill></span>
    </div>
    <div class="cai-selchip" data-cai-selchip hidden></div>
    <div class="cai-abilnote" data-cai-abilnote></div>
    <textarea class="eos-tool-field" data-cai-prompt rows="2" aria-label="AI CAD proposal prompt"></textarea>
    <div class="cai-row eos-tool-row">
      <button class="eos-tool-btn cai-fill" data-cai-propose title="Draft a proposal from the prompt">Draft</button>
    </div>
    <div data-cai-preview></div>
  </div>`;

export function resolveAiMode({ hasContent, lockedMode } = {}) {
  if (lockedMode === 'new' || lockedMode === 'edit') return lockedMode;
  return hasContent ? 'edit' : 'new';
}

export function buildProposePayload({ prompt, mode, doc, selection } = {}) {
  const payload = { prompt: String(prompt || '').trim() };
  if (mode !== 'edit') return payload;
  payload.base = doc || null;
  if (selection && Object.prototype.hasOwnProperty.call(selection, 'id')) {
    payload.selection = selection.id;
  } else if (selection && Object.prototype.hasOwnProperty.call(selection, 'ids')) {
    payload.selection = selection.ids;
  }
  if (selection && selection.label) payload.selection_label = selection.label;
  return payload;
}

function injectStyles() {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.appendChild(style);
}

export function createAiPanel(opts = {}) {
  let mount = null;
  let root = null;
  let lockedMode = null;
  let proposal = null;

  const q = (sel) => root && root.querySelector(sel);
  const status = (message, isError) => { if (opts.setStatus) opts.setStatus(message, isError); };
  const selection = () => (opts.getSelection ? opts.getSelection() : null);
  const mode = () => resolveAiMode({
    hasContent: opts.hasContent ? !!opts.hasContent() : false,
    lockedMode,
  });

  function paintSelection(currentMode) {
    const chip = q('[data-cai-selchip]');
    if (!chip) return;
    const sel = currentMode === 'edit' ? selection() : null;
    chip.innerHTML = '';
    if (!sel || !sel.label) { chip.hidden = true; return; }
    chip.hidden = false;
    const text = document.createElement('span');
    text.textContent = 'Editing: ' + sel.label;
    const clear = document.createElement('button');
    clear.type = 'button'; clear.className = 'cai-selx'; clear.textContent = '×';
    clear.title = 'Edit the whole document instead';
    clear.addEventListener('click', () => {
      if (opts.clearSelection) opts.clearSelection();
      refresh();
    });
    chip.append(text, clear);
  }

  function paintAbility() {
    const note = q('[data-cai-abilnote]');
    const pill = opts.pill || {};
    if (!note || !(window.EOS_UI && EOS_UI.abilityGate) || !pill.app) return;
    EOS_UI.abilityGate({ app: pill.app, domain: pill.domain, minAbility: pill.minAbility,
      onState: (res) => { note.innerHTML = res.meets ? '' : EOS_UI.abilityBannerHtml(res); } });
  }

  function mountPill() {
    const slot = q('[data-cai-pill]');
    const pill = opts.pill || {};
    if (!slot || slot._mounted || !(window.EOS_UI && EOS_UI.modelPill) || !pill.app) return;
    slot._mounted = true;
    EOS_UI.modelPill({ app: pill.app, mount: slot, domain: pill.domain, onSwitch: paintAbility });
  }

  function refresh() {
    if (!root) return;
    const currentMode = mode();
    root.querySelectorAll('[data-cai-mode]').forEach((button) => {
      button.classList.toggle('active', button.dataset.caiMode === currentMode);
    });
    const sel = currentMode === 'edit' ? selection() : null;
    paintSelection(currentMode);
    const prompt = q('[data-cai-prompt]');
    if (prompt) {
      const placeholders = opts.placeholders || {};
      prompt.placeholder = (sel && sel.label)
        ? ('Describe a change to ' + sel.label + '…')
        : (placeholders[currentMode] || placeholders.new || 'Describe the result you want');
    }
  }

  function paintFailure(message) {
    const slot = q('[data-cai-preview]');
    if (!slot) return;
    slot.innerHTML = '';
    const note = document.createElement('div');
    note.className = 'cai-note'; note.textContent = message;
    slot.appendChild(note);
  }

  async function propose() {
    const promptEl = q('[data-cai-prompt]');
    const prompt = String((promptEl && promptEl.value) || '').trim();
    if (!prompt) { status('Enter a brief first', true); return; }
    const currentMode = mode();
    const sel = currentMode === 'edit' ? selection() : null;
    paintFailure(currentMode === 'edit' ? 'Revising…' : 'Drafting…');
    try {
      const payload = buildProposePayload({ prompt, mode: currentMode,
        doc: opts.getDoc ? opts.getDoc() : null, selection: sel });
      const response = await opts.propose(payload);
      if (!response || !response.ok || !response.document) {
        paintFailure('Draft failed: ' + ((response && (response.error || (response.errors || []).join('; '))) || '?'));
        return;
      }
      proposal = { document: response.document, response, mode: currentMode, selection: sel };
      const slot = q('[data-cai-preview]');
      slot.innerHTML = '';
      const note = document.createElement('div');
      note.className = 'cai-note';
      note.textContent = opts.describeProposal
        ? opts.describeProposal(response.document, proposal)
        : 'Proposal ready — review then apply.';
      const row = document.createElement('div'); row.className = 'cai-row eos-tool-row';
      const apply = document.createElement('button');
      apply.className = 'eos-tool-btn eos-tool-btn-primary cai-fill'; apply.textContent = 'Apply';
      apply.title = 'Apply this proposal to the current document';
      apply.addEventListener('click', async () => {
        if (!proposal) return;
        try {
          if (opts.confirmApply && !(await opts.confirmApply(proposal.document, proposal))) return;
          await opts.applyDoc(proposal.document, proposal);
          proposal = null; slot.innerHTML = ''; promptEl.value = '';
          status(currentMode === 'edit' ? 'Applied change' : 'Applied draft');
          refresh();
        } catch (error) {
          status('Apply failed: ' + String(error), true);
        }
      });
      const discard = document.createElement('button');
      discard.className = 'eos-tool-btn cai-fill'; discard.textContent = 'Discard';
      discard.title = 'Discard this proposal without changing the document';
      discard.addEventListener('click', () => { proposal = null; slot.innerHTML = ''; });
      row.append(apply, discard); slot.append(note, row);
    } catch (error) {
      paintFailure('Draft error: ' + String(error));
    }
  }

  function mountInto(el) {
    teardown();
    mount = el;
    if (!mount) return;
    injectStyles();
    mount.innerHTML = MARKUP;
    root = mount.querySelector('.eos-cad-ai');
    root.querySelectorAll('[data-cai-mode]').forEach((button) => {
      button.addEventListener('click', () => { lockedMode = button.dataset.caiMode; refresh(); });
    });
    q('[data-cai-propose]').addEventListener('click', propose);
    // Opt-in ✨ field-suggest: stamping the attrs is enough — the auto-mounter in
    // eos-components.js observes the DOM and attaches the button itself.
    // Only apps declaring [[provides.field_suggest]] pass `suggest`.
    if (opts.suggest && opts.suggest.app && opts.suggest.field) {
      const prompt = q('[data-cai-prompt]');
      if (prompt) {
        prompt.setAttribute('data-suggest-app', opts.suggest.app);
        prompt.setAttribute('data-suggest-field', opts.suggest.field);
      }
    }
    mountPill(); paintAbility(); refresh();
  }

  function teardown() {
    proposal = null;
    if (mount) mount.innerHTML = '';
    mount = null; root = null;
  }

  return { mountInto, refresh, teardown };
}
