---
name: eos-model-bench-scenario-audit
description: Audit every `self.think(...)`, `self.think_stream(...)`, and `self.think_compare(...)` call-site across EmptyOS apps and plugins, bucket each one into the canonical (domain, task_shape) taxonomy used by the Model Bench app, and flag any unbucketed shapes. Run this when the system grows new apps, when model-bench scenarios feel stale, or before reordering providers in `emptyos.toml`. Use when the user says "audit scenarios", "audit think calls", "refresh model-bench", "check bucket coverage", or asks whether the benchmark still reflects reality.
---

# EmptyOS Model-Bench Scenario Audit

Model Bench's scenarios are only useful if they mirror what `self.think(...)` actually does across the system. As apps are added or prompts change, the audit can drift — this skill refreshes the taxonomy.

Authoritative source of the taxonomy: the `BUCKETS` constant at the top of `apps/model-bench/app.py`. This skill compares that list against actual call-sites and proposes updates.

## When to Run

- A new app with `think` calls was added
- An existing app's prompts were rewritten (publish/*, dictionary/*, etc.)
- Before asking the user to reorder providers per bucket — the reorder is only honest if the buckets match reality
- User says "audit scenarios", "check bucket coverage", "are the model-bench scenarios still right?"

## Scope

Python files under:
- `D:/emptyos/apps/` (core apps)
- `D:/emptyos/apps/personal/` (user apps — may not exist)
- `D:/emptyos/plugins/`

**Skip:** `docs/`, `tests/`, `emptyos/sdk/`, `emptyos/capabilities/`, `scripts/`, any `__pycache__`, retired apps under `_retired/`, anything inside `.claude/`.

Only include **real invocations** — skip occurrences in comments, docstrings, or markdown examples.

## Procedure

### 1. Read the canonical bucket list

```bash
# Extract the BUCKETS constant from model-bench
```

Open `apps/model-bench/app.py` and read the `BUCKETS` list. That's the taxonomy. Record each `(id, domain, task_shape, description)` tuple.

### 2. Locate every call-site

Grep for:

```
self\.think\s*\(|self\.think_stream\s*\(|self\.think_compare\s*\(
```

Include 10 lines of context on each side so kwargs (`domain=`, `temperature=`, `system=`) and the prompt are visible.

### 3. For each call, extract

| Field | How to find it |
|---|---|
| File & line | From the grep hit |
| Call type | `think` / `think_stream` / `think_compare` |
| **Domain** | Value of `domain=` kwarg if passed; else `default` |
| Temperature | Value if passed; else `default` |
| **System prompt** | Name of the module-level constant used as `system=`; `inline` if inline string; `none` if no system kwarg |
| **User-prompt pattern** | The constant or f-string used for the user message — summarize in ≤8 words if inline |
| **Task shape** | Your judgment from reading the prompt — pick one of: `classify`, `json-extract`, `summarize`, `rewrite`, `draft`, `rank`, `qa`, `reason`, `code-gen`, or `other` (explain) |
| Input size | `tiny`<200, `small`<2KB, `medium`<10KB, `large`>10KB |
| Streaming? | yes if `think_stream`, else no |

### 4. Produce the audit table

Output a single markdown table sorted by (app, file, line). Columns: `File:Line | Call | Domain | System | User-prompt | Shape | Size`.

### 5. Produce the bucket roll-up

Group call-sites by `(domain, task_shape)`. For each bucket:

- Count
- 2–3 example call-sites
- Mark **✓ in taxonomy** if the bucket id (e.g. `text/qa`) exists in `BUCKETS`, or **✗ MISSING** if it doesn't

Format:

```
## Bucket roll-up

| Bucket             | Count | In taxonomy | Example call-sites |
|---|---|---|---|
| text/classify      | 2     | ✓           | apps/capture/app.py:78, apps/assistant/app.py:245 |
| text/qa            | 4     | ✓           | apps/assistant/app.py:436, ... |
| text/rewrite       | 3     | ✓           | apps/publish/app.py:757, ... |
| text/ranked-list   | 1     | ✗ MISSING   | apps/focus/app.py:40 |
```

### 6. Propose changes (only if needed)

If every observed bucket is in taxonomy and every taxonomy bucket has at least one real call-site, say so and stop — no action needed.

Otherwise:

- **Missing bucket** → observed shape has no entry in `BUCKETS`. Propose the new tuple `(id, domain, shape, description)` and the scenario prompt you'd add (use a real prompt from the codebase, not invented text). Ask the user before adding.
- **Orphan bucket** → `BUCKETS` entry with zero real call-sites. Flag it — may be obsolete since the audit was last run. Ask the user whether to drop it.
- **Concentrated call-site** → one bucket has ≥6 call-sites all from one app (e.g. all `json-extract` in `dictionary/`). Note it — the single-app dominance means the benchmark should pull the prompt *from that app* rather than a synthetic one.

### 7. If the user approves changes

- **Add bucket**: edit `BUCKETS` in `apps/model-bench/app.py`, add a matching `_prompt_<name>` method and register it in `_PROMPT_BUILDERS`. Use a real prompt from the identified call-site — import the constant or copy it verbatim with a comment pointing at the source file.
- **Remove bucket**: delete the `BUCKETS` entry, its `_prompt_*` method, and the `_PROMPT_BUILDERS` mapping.
- After edits, smoke-test: `python -c "import importlib.util,sys; spec=importlib.util.spec_from_file_location('m','apps/model-bench/app.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(len(m.BUCKETS))"` — make sure it imports and the count is what you expect.
- Suggest the user run `python -m emptyos start` and `eos model-bench run` to regenerate benchmark data against the updated taxonomy.

## Output Shape

Keep the whole report under ~250 lines:

1. A short 1-line summary ("N call-sites, M buckets observed, K in taxonomy, D missing, O orphan")
2. The audit table
3. The bucket roll-up
4. Proposed changes (or "no changes needed")

## Do Not

- Do not invent call-sites or pad the table with guesses — if a grep hit looks like a doc example, skip it
- Do not change `BUCKETS` without user approval
- Do not write synthetic prompts when a real one from the codebase would work
- Do not broaden scope beyond the listed directories — this skill is specifically about `self.think*` call-sites in apps/plugins
