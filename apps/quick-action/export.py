"""Quick-action — export hook.

Captures are the entry-point for the work-os bundle. Offline:
  - ``POST /api/add`` and ``POST /api/smart-add`` append to an in-browser list
    persisted via IndexedDB.
  - ``POST /api/to-task`` / ``/api/to-done-task`` route through the bundled
    task app via ``EOS.callApp("task", "add", ...)`` (so the new task lands
    in task's offline store and emits ``task:added``).
  - ``POST /api/to-journal`` calls ``EOS.callApp("journal", ...)`` if journal
    is bundled, else returns ``{offline: true, unavailable: true}``.
  - ``POST /api/smart-add`` skips the ``self.think`` classifier — falls back
    to plain add with whatever tag the body carries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import QuickActionApp


async def export_state(app: QuickActionApp) -> dict:
    captures = await app.list_captures(limit=200)
    return {"captures": captures}


def stub_routes() -> dict:
    return {
        "GET /quick-action/api/list": "state.captures",
    }


def client_overrides() -> str:
    return r"""
// Quick-action export — write handlers + cross-app routing.
(function(){
  if (!window.EOS_EXPORT) return;

  // Project routing — keep in sync with apps/quick-action/app.py _TAG_PROJECT.
  // Offline we re-declare it here so #dev still routes into the right project.
  var TAG_PROJECT = {
    'dev': 'emptyos-development',
  };

  async function _load() {
    var state = window.EOS_EXPORT_DATA || {};
    var stored = await window.EOS_EXPORT.get('/quick-action/api/list');
    if (Array.isArray(stored)) return stored;
    var rows = (state['quick-action'] && state['quick-action'].captures) || state.captures || [];
    await window.EOS_EXPORT.set('/quick-action/api/list', rows);
    return rows;
  }
  async function _save(rows) {
    await window.EOS_EXPORT.set('/quick-action/api/list', rows);
  }

  function _now() {
    var d = new Date();
    var pad = function(n){ return n < 10 ? '0' + n : '' + n; };
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth()+1) + '-' + pad(d.getUTCDate())
      + ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes());
  }

  async function _addCapture(text, tag) {
    text = String(text || '').trim();
    if (!text) return { error: 'text required' };
    var rows = await _load();
    var entry = { text: text, tag: tag || '', timestamp: _now(), dimension: '' };
    rows.unshift(entry);
    await _save(rows);
    window.EOS_EXPORT.emit('capture:saved', entry);
    return entry;
  }

  async function _removeCapture(timestamp, text) {
    var rows = await _load();
    var kept = rows.filter(function(r){
      return !(r.timestamp === timestamp && r.text === text);
    });
    if (kept.length !== rows.length) {
      await _save(kept);
      return true;
    }
    return false;
  }

  // ── Add (plain) ─────────────────────────────────────────────────
  window.EOS_EXPORT.registerRoute('POST', '/quick-action/api/add', async function(req){
    var body = req.body || {};
    return await _addCapture(body.text || '', body.tag || '');
  });

  // ── Smart-add — without `think` we just add with provided tag ───
  window.EOS_EXPORT.registerRoute('POST', '/quick-action/api/smart-add', async function(req){
    var body = req.body || {};
    if (!String(body.text || '').trim()) return { error: 'text required' };
    return await _addCapture(body.text, body.tag || '');
  });

  // ── To-task — promote capture into bundled task app ─────────────
  async function _toTask(body, done) {
    var text = String(body.text || '').trim();
    var tag = body.tag || '';
    if (!text) return { error: 'text required' };
    var project_id = TAG_PROJECT[tag] || null;
    var taskRes;
    try {
      if (project_id) {
        taskRes = await window.EOS.callApp('projects', 'add_task_to_project', {
          project_id: project_id, text: text, done: !!done
        });
      } else {
        taskRes = await window.EOS.callApp('task', 'add', { text: text, done: !!done });
      }
    } catch (e) {
      return { error: 'task creation failed: ' + e };
    }
    if (taskRes && taskRes.unavailable) {
      return { error: 'task app not bundled', offline: true };
    }
    await _removeCapture(body.timestamp || '', text);
    return {
      converted: true, text: text,
      done: !!done, project: project_id
    };
  }
  window.EOS_EXPORT.registerRoute('POST', '/quick-action/api/to-task', async function(req){
    return await _toTask(req.body || {}, false);
  });
  window.EOS_EXPORT.registerRoute('POST', '/quick-action/api/to-done-task', async function(req){
    return await _toTask(req.body || {}, true);
  });

  // ── To-journal — only works when journal is bundled ─────────────
  window.EOS_EXPORT.registerRoute('POST', '/quick-action/api/to-journal', async function(req){
    var body = req.body || {};
    var text = String(body.text || '').trim();
    if (!text) return { error: 'text required' };
    var d = new Date().toISOString().slice(0, 10);
    var res;
    try {
      res = await window.EOS.callApp('journal', '_add_entry', { d: d, text: text, mood: 'okay' });
    } catch (e) {
      return { error: 'journal write failed: ' + e };
    }
    if (res && res.unavailable) return { error: 'journal not bundled', offline: true };
    await _removeCapture(body.timestamp || '', text);
    return { converted: true, target: 'journal' };
  });

  // ── Dismiss / pending / clear ───────────────────────────────────
  window.EOS_EXPORT.registerRoute('POST', '/quick-action/api/dismiss', async function(req){
    var body = req.body || {};
    var ok = await _removeCapture(body.timestamp || '', body.text || '');
    return { dismissed: ok };
  });
  window.EOS_EXPORT.registerRoute('GET', '/quick-action/api/pending', async function(){
    var rows = await _load();
    return { pending: rows.length };
  });
  window.EOS_EXPORT.registerRoute('POST', '/quick-action/api/clear', async function(){
    await _save([]);
    return { ok: true, cleared: true };
  });

  // ── Cross-app: capture.add for voice intents / orchestrator ─────
  window.EOS_EXPORT.registerAppMethod('quick-action', 'add', async function(kwargs){
    kwargs = kwargs || {};
    return await _addCapture(kwargs.text || '', kwargs.tag || '');
  });
})();
"""
