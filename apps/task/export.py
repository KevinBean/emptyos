"""Task — export hook.

Snapshots the live task list and intercepts the write paths the UI uses
(``POST /api/toggle``, ``POST /api/snooze``, ``POST /api/set-field``) plus the
cross-app ``add`` method called via ``EOS.callApp("task", "add", {...})``.

Offline, ``{file}:{line}`` is treated as an opaque id — we never re-resolve
into a vault file, since the vault doesn't exist in the bundle. Mutations
update the in-memory snapshot, persist via ``EOS_EXPORT.set``, and fan out
``task:*`` events on the in-page bus so other bundled apps can react.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import TaskApp


async def export_state(app: TaskApp) -> dict:
    rows = await app.list_all()
    return {
        "tasks": rows,
        "recent_adds": list(app._recent_adds),
    }


def stub_routes() -> dict:
    """GET endpoints derivable from the snapshot.

    ``/api/tasks`` and ``/api/list`` both serve the flat list. The shim
    falls through to generic IndexedDB for anything not listed here, which
    means writes-through-then-reads still work (the IDB row at the same
    path supersedes the snapshot).
    """
    return {
        "GET /task/api/tasks": "state.tasks",
        "GET /task/api/list": "state.tasks",
    }


def client_overrides() -> str:
    return r"""
// Task export — write handlers + cross-app add.
(function(){
  if (!window.EOS_EXPORT) return;

  var coll = window.EOS_EXPORT.registerCollection({
    appId: 'task',
    dataKey: '/task/api/tasks',
    mirrorKeys: ['/task/api/list'],
    idField: 'id',
    settableFields: ['done', 'due', 'text'],
    eventPrefix: 'task',
    onSetField: function(row, field, value){
      if (field === 'done') {
        var want = (typeof value === 'string')
          ? ['true','1','yes','x','done'].indexOf(String(value).toLowerCase()) !== -1
          : !!value;
        row.done = want;
        row.done_date = want ? new Date().toISOString().slice(0, 10) : '';
        if (want) window.EOS_EXPORT.emit('task:completed', { file: row.file, line: row.line });
      } else if (field === 'due') {
        row.due = String(value || '').trim();
      } else if (field === 'text') {
        var nt = String(value || '').trim();
        if (!nt) throw new Error('text must be non-empty');
        row.text = nt;
      }
    },
  });

  function _today() { return new Date().toISOString().slice(0, 10); }
  function _findByFileLine(rows, file, line) {
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].file === file && Number(rows[i].line) === Number(line)) return i;
    }
    return -1;
  }

  // ── Toggle (complete/reopen) — keyed by {file, line} not id ─────
  window.EOS_EXPORT.registerRoute('POST', '/task/api/toggle', async function(req){
    var body = req.body || {};
    var rows = await coll.load();
    var i = _findByFileLine(rows, body.file || '', body.line || 0);
    if (i < 0) return { error: 'task not found', offline: true };
    var t = rows[i];
    if (t.done) {
      t.done = false; t.done_date = '';
      window.EOS_EXPORT.emit('task:reopened', { file: t.file, line: t.line });
      await coll.save(rows);
      return { status: 'reopened', file: t.file, line: t.line };
    }
    t.done = true; t.done_date = _today();
    window.EOS_EXPORT.emit('task:completed', { file: t.file, line: t.line });
    await coll.save(rows);
    return { status: 'completed', file: t.file, line: t.line };
  });

  // ── Snooze ───────────────────────────────────────────────────────
  window.EOS_EXPORT.registerRoute('POST', '/task/api/snooze', async function(req){
    var body = req.body || {};
    var days = Number(body.days || 7);
    var rows = await coll.load();
    var i = _findByFileLine(rows, body.file || '', body.line || 0);
    if (i < 0) return { error: 'task not found', offline: true };
    var d = new Date(); d.setDate(d.getDate() + days);
    var newDue = d.toISOString().slice(0, 10);
    rows[i].due = newDue;
    await coll.save(rows);
    window.EOS_EXPORT.emit('task:snoozed', { file: rows[i].file, line: rows[i].line, new_due: newDue, days: days });
    return { status: 'snoozed', file: rows[i].file, line: rows[i].line, new_due: newDue };
  });

  // ── Set-field — wire to the helper but expose at /api/set-field too ──
  window.EOS_EXPORT.registerRoute('POST', '/task/api/set-field', async function(req){
    var body = req.body || {};
    return await coll.setField(body.id || '', body.field || '', body.value);
  });

  // ── Refresh (offline no-op) ──────────────────────────────────────
  window.EOS_EXPORT.registerRoute('POST', '/task/api/refresh', async function(){
    var rows = await coll.load();
    var open = rows.filter(function(t){ return !t.done; }).length;
    return { open: open, done: rows.length - open, status: 'refreshed', offline: true };
  });

  // ── Cross-app: EOS.callApp("task", "add", {text, due, project}) ─
  // Override the auto-bound default with an offline write that lands in IDB.
  window.EOS_EXPORT.registerAppMethod('task', 'add', async function(kwargs){
    var rows = await coll.load();
    var text = String((kwargs && kwargs.text) || '').trim();
    if (!text) return { error: 'empty text' };
    var due = String((kwargs && kwargs.due) || '').trim();
    var project = String((kwargs && kwargs.project) || 'inbox');
    var displayText = due ? (text + ' 📅 ' + due) : text;
    var task = {
      id: '(offline)/' + project + '.md:' + (rows.length + 1),
      file: '(offline)/' + project + '.md',
      line: rows.length + 1,
      text: displayText,
      done: !!(kwargs && kwargs.done),
      due: due,
      done_date: '',
      tier: 'fresh',
      focus_score: 0,
      overdue_days: 0,
      project: project,
    };
    rows.push(task);
    await coll.save(rows);
    window.EOS_EXPORT.emit(task.done ? 'task:completed' : 'task:added', task);
    return task;
  });
})();
"""
