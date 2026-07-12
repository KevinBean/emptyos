

Projects is the **write endpoint**, task app is the **read-only aggregator**.

```
Capture #dev   → projects.add_task_to_project("emptyos-development")
Capture other  → task.add() → projects.add_task_to_project("inbox")
Task app       → scans entire vault for - [ ] lines (cross-project view)
```

- `task.add(text)` routes to inbox by default, or specific project via `project=` kwarg
- Quick-action tag → project routing: `_TAG_PROJECT` in `apps/quick-action/app.py`
- Task UI shows `[Project Name]` badges for tasks in `10_Projects/`
