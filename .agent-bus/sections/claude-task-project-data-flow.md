

Projects is the **write endpoint**, task app is the **read-only aggregator**. `task.add(text)` routes to the inbox project by default, or a specific project via `project=`; capture `#dev` goes to `emptyos-development` (tag → project routing: `_TAG_PROJECT` in `apps/public/core/quick-action/app.py`). The task app scans the whole vault for `- [ ]` lines and shows `[Project Name]` badges for tasks in `10_Projects/`.
