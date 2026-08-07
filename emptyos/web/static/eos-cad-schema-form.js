// eos-cad-schema-form.js — dotted-path scalar schema → editable form markup.
//
// Pulled out of object-inspector.js (the generic typed-object schema editor)
// when corridor-inspector.js became its second consumer (the cable-run ampacity
// spec editor) — the two had near-identical getPath/setPath/inputFor/markup
// functions, which is exactly the CLAUDE.md rule-9 extraction trigger. Kept
// pure (no store, no DOM events) so it's testable under plain node — see
// tests/js/cad_schema_form.test.mjs. Each view still owns its own commit()
// wiring (attaching listeners, calling store.patchObject/regenerateObject) —
// that part stayed view-specific enough (different oid source, different
// status messaging) not to force into one shared closure.

// esc()/escAttr() duplicated (not imported) from eos-cad-view.js / eos.js's
// global — a browser-absolute `/static/...` import isn't resolvable by plain
// node, and a page global doesn't exist there either. This module's whole
// point is being node-testable like eos-cad-checks-core.js. Keep in sync if
// either's escaping rules ever change.
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escAttr(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;');
}

export function getPath(root, path) {
  return path.split('.').reduce((v, p) => (v == null ? undefined : v[p]), root);
}

export function setPath(root, path, value) {
  const parts = path.split('.');
  let cursor = root;
  parts.slice(0, -1).forEach((part, i) => {
    const next = parts[i + 1];
    if (cursor[part] == null) cursor[part] = /^\d+$/.test(next) ? [] : {};
    cursor = cursor[part];
  });
  cursor[parts.at(-1)] = value;
}

export function inputFor(field, value) {
  const common = ' data-key="' + escAttr(field.key) + '"' +
    (field.min != null ? ' min="' + escAttr(field.min) + '"' : '') +
    (field.max != null ? ' max="' + escAttr(field.max) + '"' : '');
  if (field.type === 'boolean') return '<input type="checkbox"' + common + (value ? ' checked' : '') + '>';
  if (field.type === 'select') {
    // A leading empty option so an optional select can be cleared back to
    // "unset" — without it, once a value is picked there is no way back.
    return '<select' + common + '><option value=""' + (value == null || value === '' ? ' selected' : '') + '>—</option>' +
      (field.options || []).map((o) =>
        '<option' + (String(o) === String(value) ? ' selected' : '') + '>' + esc(o) + '</option>').join('') + '</select>';
  }
  return '<input type="' + (field.type === 'number' ? 'number' : 'text') + '"' + common +
    (field.type === 'number' ? ' step="any"' : '') + ' value="' + escAttr(value == null ? '' : value) + '">';
}

// Renders every field grouped by `field.group`, reading current values from
// `props` via the dotted `field.key`. `field.hint` (if present) becomes the
// label's tooltip — optional on every existing schema, so this is additive.
export function fieldsMarkup(props, fields) {
  const groups = new Map();
  fields.forEach((field) => {
    const g = field.group || 'Properties';
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(field);
  });
  return [...groups].map(([group, items]) => '<fieldset><legend>' + esc(group) + '</legend>' +
    items.map((field) => '<label title="' + escAttr(field.hint || '') + '"><span>' + esc(field.label || field.key) + '</span>' +
      inputFor(field, getPath(props, field.key)) + '<span class="unit">' + esc(field.unit || '') + '</span></label>').join('') +
    '</fieldset>').join('');
}
