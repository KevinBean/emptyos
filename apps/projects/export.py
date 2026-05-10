"""Projects — export hook.

Snapshots the project list and intercepts the small set of write paths the
projects UI + boards layer use:
  - ``POST /api/projects/{id}/set-field`` (also exposed as cross-app set_field)
  - ``POST /api/projects/{id}/tasks/add`` (also exposed as add_task_to_project)
  - ``POST /api/refresh`` (offline no-op)

Anything not registered here falls through to auto-RPC + generic IndexedDB
via the shim. Project task-toggle by-line is intentionally not handled —
offline we don't keep the markdown body, so line numbers are meaningless.
Toggle the underlying task through the task app instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import ProjectsApp


async def export_state(app: ProjectsApp) -> dict:
    rows = await app.list_all()
    return {"projects": rows}


def stub_routes() -> dict:
    return {
        "GET /projects/api/list": "state.projects",
        "GET /projects/api/projects": "state.projects",
    }


def client_overrides() -> str:
    return r"""
// Projects export — write handlers + cross-app methods.
(function(){
  if (!window.EOS_EXPORT) return;

  var coll = window.EOS_EXPORT.registerCollection({
    appId: 'projects',
    dataKey: '/projects/api/list',
    mirrorKeys: ['/projects/api/projects'],
    idField: 'id',
    settableFields: [
      'status', 'stage', 'deadline', 'progress', 'type', 'description',
      'assignees', 'skills_required', 'blocks', 'blocked_by', 'deliverables',
    ],
    eventPrefix: 'project',
    // dataKey ends in /list but the snapshot key is "projects" (state.projects.projects).
    snapshotPath: function(state){
      return (state.projects && state.projects.projects) || state.projects || [];
    },
  });

  // ── Set-field via /api endpoint (mirrors the live route shape) ──
  window.EOS_EXPORT.registerRoute('POST', '/projects/api/projects/:id/set-field', async function(req, params){
    var body = req.body || {};
    return await coll.setField(params.id, body.field || '', body.value);
  });

  // ── Add task to project — delegates into bundled task app ───────
  async function _addTaskToProject(project_id, text, due, done) {
    var rows = await coll.load();
    var i = coll.findById(rows, project_id);
    if (i < 0) {
      // Bootstrap a thin row so the task has somewhere to live.
      rows.push({
        id: project_id,
        name: project_id.replace(/-/g, ' '),
        status: 'active',
        type: 'personal',
        tags: ['project'],
      });
      await coll.save(rows);
    }
    var taskRes = await window.EOS.callApp('task', 'add', {
      text: text, due: due || '', project: project_id, done: !!done
    });
    window.EOS_EXPORT.emit('projects:task_added', { id: project_id, text: text, done: !!done });
    return { ok: true, task: text, project: project_id, done: !!done, _task: taskRes };
  }

  window.EOS_EXPORT.registerRoute('POST', '/projects/api/projects/:id/tasks/add', async function(req, params){
    var body = req.body || {};
    return await _addTaskToProject(params.id, body.text || '', body.due || '', body.done);
  });

  // ── Refresh — offline no-op ──────────────────────────────────────
  window.EOS_EXPORT.registerRoute('POST', '/projects/api/refresh', async function(){
    var rows = await coll.load();
    return { count: rows.length, status: 'refreshed', offline: true };
  });

  // ── Cross-app: add_task_to_project (helper-registered list_all + set_field stay) ──
  window.EOS_EXPORT.registerAppMethod('projects', 'add_task_to_project', async function(kwargs){
    kwargs = kwargs || {};
    var text = String(kwargs.text || '').trim();
    if (!text) return { error: 'Task text required' };
    return await _addTaskToProject(kwargs.project_id || 'inbox', text, kwargs.due || '', !!kwargs.done);
  });
})();
"""
