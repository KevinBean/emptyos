# Path-Scoped Rules — `paths:` frontmatter + `rules_for_paths()`

A rule file under `.claude/rules/` MAY carry a `paths:` frontmatter list of
globs naming the repo files it governs. Two consumers read the same data:

1. **Claude Code native loading** — a `paths:`-scoped rule loads only when the
   session touches matching files, cutting always-on token cost (the pilot in
   `project_claude_rules_path_scoping`).
2. **`emptyos.sdk.agent_bus.rules_for_paths(workspace, paths)`** — deterministic
   path→rule matching for LLM prompt builders: given the repo paths a change
   touches, returns the matched rules (`rules_menu_for_paths` renders the
   prompt-injectable menu). Borrowed 2026-06-12 from alibaba/open-code-review's
   hybrid thesis: *rule matching is a template-engine job, not an LLM job* —
   deterministic code picks which rules apply; the model keeps the judgment.

```yaml
---
paths:
  - "apps/extension/engineering/**"
  - "emptyos/web/static/eos-cad*"
---
```

Write patterns with `**` (real glob engines need it; the SDK's fnmatch treats
`*` and `**` alike — both cross `/`). First consumer: fix-agent injects the
matched menu into its fix system prompt, dark behind `[apps.fix-agent]
feature.path-rules.enabled` (`runs.py::_run_one` → `shared.py::
_extract_repo_paths` + `_build_fix_system_prompt(rules_block=)`). Second in
line: dogfood-agent's `_build_fix_prompt` (infer paths from the friction's app
id) and trace-miner's `_write_fix_prompt`.

Discipline:

- **Annotate on touch, not in bulk** — add `paths:` to a rule when you touch it
  and its scope is unambiguous. An unscoped rule stays always-on; that's the
  safe default. Never scope cross-cutting rules (testing, daemon-handling,
  debugging, dev-gotchas) — conditional loading of those is how regressions land.
- Rules without `paths:` never match in `rules_for_paths` — unscoped means
  "already in context", so injecting them would re-add the noise the resolver
  exists to cut.
- **45 of 75 rules are scoped today** (2026-08-31). Seeded pilots were
  `cad-extensions`, `cad-workspaces`, `geo`; a one-time bulk pass then scoped 35
  more when the always-loaded set reached 611KB (~153k est. tokens *per session*,
  80% of the whole context budget). That pass was a deliberate exception to the
  on-touch rule above, taken once because the cost had compounded past the point
  where incremental annotation could catch up — **it is not a precedent**. It
  touched only rules unambiguously scoped to one subsystem and left every
  cross-cutting rule always-on.
- **Second pass, 2026-09-25: a hard budget, not a preference.** Claude Code
  warns when the always-loaded instruction files exceed **150k chars**; the set
  had regrown to 34 files / 484k. 22 subsystem rules were scoped, and the four
  cross-cutting rules were **split rather than scoped**: each keeps its rules
  always-on and moves its measured case studies into a scoped sibling
  (`dev-gotchas` → `media-gotchas` + `chrome-extension-gotchas`; `audits` →
  `audits-casebook` + `conformance-anchors`; `testing` → `test-authoring`;
  `debugging` → `wedge-tooling`). CLAUDE.md sheds detail the same way and ends
  with a "Rules loaded on demand" index. Result: 12 always-loaded files,
  ~126k chars. **Keep the total under 150k** — measure before adding to an
  always-on file, and put a new case study in the scoped sibling, not the core.
- `eos bus ripple`/`import` syncs the frontmatter into `.agent-bus/` like any
  rule edit; the resolver reads native-first (what a spawned CLI itself reads).

Tests: `tests/test_sdk_agent_bus_paths.py`. Verdict + lineage: vault note
`30_Resources/Web-Clips/2026-06-12 alibaba open-code-review - repo review.md`.
