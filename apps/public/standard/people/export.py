"""People — export hook.

Snapshots the people roster and intercepts the four write paths the directory
UI + boards layer use:
  - ``POST   /api/people``           (create)
  - ``PATCH  /api/people/{id}``      (multi-field update)
  - ``DELETE /api/people/{id}``      (archive — sets active=false)

Workload is derived live from cross-app assignments; offline we leave it at
whatever the snapshot recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import PeopleApp


async def export_state(app: PeopleApp) -> dict:
    rows = await app.list_all()
    return {"people": rows}


def stub_routes() -> dict:
    return {
        "GET /people/api/people": "state.people",
        "GET /people/api/list": "state.people",
    }


def client_overrides() -> str:
    return r"""
// People export — write handlers + cross-app methods.
(function(){
  if (!window.EOS_EXPORT) return;

  var SETTABLE = [
    'name', 'role', 'type', 'capacity_hours_per_week', 'active',
    'relationship', 'company', 'trust_level', 'energy', 'contact_frequency',
    'last_contact', 'phone', 'email', 'birthday',
    'skills', 'focus_areas',
  ];

  var coll = window.EOS_EXPORT.registerCollection({
    appId: 'people',
    dataKey: '/people/api/people',
    mirrorKeys: ['/people/api/list'],
    idField: 'id',
    settableFields: SETTABLE,
    eventPrefix: 'people',
  });
  var SETTABLE_SET = {};
  SETTABLE.forEach(function(k){ SETTABLE_SET[k] = 1; });

  function _slug(s) {
    return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'person';
  }

  // ── GET single person ───────────────────────────────────────────
  window.EOS_EXPORT.registerRoute('GET', '/people/api/people/:id', async function(req, params){
    var rows = await coll.load();
    var i = coll.findById(rows, params.id);
    return i < 0 ? { error: 'Person not found' } : rows[i];
  });

  // ── Create ──────────────────────────────────────────────────────
  window.EOS_EXPORT.registerRoute('POST', '/people/api/people', async function(req){
    var body = req.body || {};
    var name = String(body.name || '').trim();
    if (!name) return { error: 'name required' };
    var pid = String(body.id || _slug(name));
    var rows = await coll.load();
    if (coll.findById(rows, pid) >= 0) return { error: "person '" + pid + "' already exists" };
    var person = {
      id: pid, name: name,
      role: body.role || '', type: body.type || 'internal',
      capacity_hours_per_week: Number(body.capacity_hours_per_week || 40),
      skills: body.skills || [], focus_areas: body.focus_areas || [],
      active: body.active !== false,
      relationship: body.relationship || '', company: body.company || '',
      trust_level: body.trust_level || '', energy: body.energy || '',
      contact_frequency: body.contact_frequency || '', last_contact: body.last_contact || '',
      phone: body.phone || '', email: body.email || '', birthday: body.birthday || '',
      load_ratio: 0, band: 'ok',
    };
    rows.push(person);
    await coll.save(rows);
    window.EOS_EXPORT.emit('people:created', { id: pid, name: name });
    return { ok: true, id: pid, path: '(offline)/' + pid + '.md' };
  });

  // ── Patch (multi-field update) ──────────────────────────────────
  window.EOS_EXPORT.registerRoute('PATCH', '/people/api/people/:id', async function(req, params){
    var body = req.body || {};
    var rows = await coll.load();
    var i = coll.findById(rows, params.id);
    if (i < 0) return { error: 'Person not found' };
    var updates = {};
    Object.keys(body).forEach(function(k){ if (SETTABLE_SET[k]) updates[k] = body[k]; });
    if (!Object.keys(updates).length) return { error: 'No valid fields' };
    Object.keys(updates).forEach(function(k){ rows[i][k] = updates[k]; });
    await coll.save(rows);
    window.EOS_EXPORT.emit('people:updated', { id: params.id, updates: updates });
    return { ok: true };
  });

  // ── Archive (DELETE → active=false) ─────────────────────────────
  window.EOS_EXPORT.registerRoute('DELETE', '/people/api/people/:id', async function(req, params){
    var rows = await coll.load();
    var i = coll.findById(rows, params.id);
    if (i < 0) return { error: 'Person not found' };
    rows[i].active = false;
    await coll.save(rows);
    window.EOS_EXPORT.emit('people:archived', { id: params.id });
    return { ok: true };
  });
})();
"""
