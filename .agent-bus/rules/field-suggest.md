# Field-Suggest Rule — ✨ vault-grounded AI suggestions on a form input

A **field-suggest** is a small ✨ button beside a form input that, on click, asks an
LLM — grounded in the user's own vault — to propose candidate values, then renders a
pick-list the user chooses from to fill the field. It turns a blank textarea into a
warm start without taking the keyboard away from the user.

**Reference (bespoke-grounding):** `apps/public/standard/reader` — the Premise field of
"Weave a story" (`pages/play.js`, `branching.py::suggest_premises`, careful Rule-19:
feeds the cloud only book *titles*). **Reference (generic helper):**
`apps/public/standard/viz` `prompt`, `apps/public/standard/forge` `design`,
`apps/public/standard/scroll` `topic_hint` (manifest-declared, no per-field backend code).

## The three moving parts

| Layer | Where | What |
|---|---|---|
| **Backend helper** | `BaseApp.suggest_field(...)` (`emptyos/sdk/base_app.py`) | Grounds a `think()` call in `vault_query(tags=…)` results, parses `{"suggestions":[…]}`, returns `list[str]`. **Rule-19 safe by default** (`title_only=True` → titles/frontmatter only, never bodies). Never raises. |
| **Platform endpoints** | `GET /api/sdk/suggest-apps`, `POST /api/sdk/suggest-field` (`emptyos/web/server.py`) | Read `[[provides.field_suggest]]` declarations; the POST calls the app's `suggest_field` with the declared grounding. |
| **Frontend component** | `EOS_UI.fieldSuggest(...)` + the auto-mounter (`emptyos/web/static/eos-components.js`) | Renders the ✨ button + popup pick-list; click fills (`mode:"fill"`) or comma-appends (`mode:"append"`). |

## Two wiring paths (declaring a field IS the opt-in — no dark flag)

### A. Manifest-declared (generic grounding — preferred for simple cases)

```toml
[[provides.field_suggest]]
field = "prompt"              # matches data-suggest-field / the formModal suggest key
instruction = "Propose vivid one-line visualization briefs riffing on the user's KB topics."
vault_tags = ["kb"]          # grounding source for vault_query
frontmatter_fields = ["status"]   # optional — surface these props alongside the title
title_only = true            # Rule-19: titles/frontmatter to cloud, never bodies
count = 5
mode = "fill"                # fill | append (append = comma-join, for list fields)
```

Then mount the button. **Raw-HTML input** — stamp two attributes; the auto-mounter does
the rest:
```html
<textarea id="prompt" data-suggest-app="viz" data-suggest-field="prompt"></textarea>
```
**formModal field** — add a `suggest` key to the field descriptor:
```js
{ key: 'topic_hint', label: 'Topic hint', suggest: { app: 'scroll', field: 'topic_hint' } }
```
`formHtml` stamps `data-suggest-*` on the `.eos-form-group`; the auto-mounter injects ✨.
No JS handler, no fetch wiring — the component reads the attributes and calls the platform
endpoint.

### B. Bespoke endpoint (when grounding is app-specific)

When the grounding isn't a plain tag query (reader grounds on filesystem book *titles*,
not VaultIndex tags), the app keeps its own route returning `{suggestions:[…]}` (or
`{premises:[…]}` — the component tolerates either) and points the component at it via
`endpoint`:
```js
suggest: { app: 'reader', field: 'premise', endpoint: '/reader/api/story/suggest-premise' }
```
No `[[provides.field_suggest]]` needed in this path. The backend method should still go
through `self.suggest_field(...)` where the grounding *is* a tag query; only reach for a
fully bespoke gather (like reader's) when `vault_query` can't express it.

## When to add a field-suggest

Add it where a candidate value genuinely **helps a blank/sparse field** and the user
still edits the result — i.e. **open, creative, generative** inputs:

- premise / topic / prompt / brief / scene description (viz, scroll, reader)
- a "smallest first version" or goal seed (forge)
- a free-prose description the LLM can draft from the user's own material
- a comma-list the user is filling (`mode:"append"` — e.g. target vocabulary, tags)

## When NOT to add one (the load-bearing half)

The affordance is a paid `think()` call and screen friction. **Do not** wire it onto:

| Field shape | Why not | Example |
|---|---|---|
| **The user's own draft text** | Suggesting it defeats the purpose — the value *is* their writing | writing-editor `draft`, learn `answer` |
| **Pick-an-existing-entity** | The user knows the value; an LLM inventing it hallucinates | jobs `company`/`recruiter`/`location` (use plain autocomplete from existing field values, not generation) |
| **Validated enums / one-right-answer** | There's a correct value, not a creative one | any `select`, `status`, `priority`, `type`, CEFR level |
| **Numbers / dates / passwords / currency** | No "suggestion" is meaningful or safe | `salary`, `deadline`, `price`, auth fields |
| **Already AI-served** | Don't double up | projects (conversational `aiFormFill`), focus (`/focus` slash suggestion) |
| **App-local / content-grounded, not vault-queryable** | The generic helper grounds on vault tags; this won't match | rooms snippets/agents (data/ JSON + room context) — would need a bespoke endpoint, defer until wanted |

When in doubt, leave it off — a missing ✨ costs nothing; a wrong one trains the user to
ignore it.

## Posture — propose, never autofill (三性 / autopilot alignment)

A field-suggest only ever **pre-fills an input the user still submits**. It is *not* an
autopilot-eligible action and does not go through the review gate — there is no state
change to gate; the form submit is the user's own act. This is the
propose-not-autofill discipline (`.claude/rules/proposed-action.md`) at the gentlest end:
the suggestion is a conditioned appearance (依他起), the user's pick is what makes it real.
Never auto-select a suggestion; never submit on behalf of the user.

## Distinct from `/api/sdk/ai-form-fill`

`ai-form-fill` (the `EOS_UI.aiFormFill` conversational modal) *extracts* field values from
a chat with the user across a whole form. Field-suggest *generates* candidate values for
**one** field from the **vault**. They compose (a form can use aiFormFill for creation and
field-suggest on individual inputs) but are different mechanisms — don't conflate them.

## Cost / ability posture

The suggest call respects the app's `EOS_UI.modelPill` provider override; no per-suggest
cost chip in v1. Pass `min_ability=` in a bespoke `suggest_field` call only if a field
needs a strong model (most suggestion tasks are bounded and fine on weak models — see
`.claude/rules/model-ability.md`; do **not** over-gate).

## Graduation notes

- **Static audit scanner** (`scripts/check-field-suggest.py`) — deferred per
  `.claude/rules/audits.md`: heuristic "is this a suggestable field" detection is
  >30%-false-positive-prone. Kept a skill-driven judgment check in
  `.claude/skills/eos-design-system-audit` instead.
- **Per-suggest provenance chip** — add `EOS_UI.provenance()` to the popup when a user
  asks to see which model produced a suggestion.
- **`append` dedup against existing tokens** — current append just comma-joins; dedup when
  a real list-field consumer wants it.

## Cross-references
- `emptyos/sdk/base_app.py` — `suggest_field` + `FIELD_SUGGEST_SYSTEM` + pure `_parse_suggestions`
- `.claude/rules/proposed-action.md` — propose/preview/confirm; this is its gentlest form
- `.claude/rules/three-natures-lens.md` — appearance (suggestion) vs reified truth (the pick)
- `.claude/rules/model-ability.md` — when (not) to gate a suggest behind a stronger model
- `.claude/rules/selector.md` — `select()` is the sibling for *closed-set routing*; this is *open generation*
- `.claude/rules/shared-frontend.md` — `EOS_UI.*` component conventions
