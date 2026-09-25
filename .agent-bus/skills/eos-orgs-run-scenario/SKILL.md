---
name: eos-orgs-run-scenario
description: Run a Persona Sim or Real Org scenario (critique / workshop / interview) and get back a multi-lens decision digest. Use when the user says "run a critique with <org>", "what would <team> think about X", "workshop this with <org>", "interview my <team> about <topic>", "get a team digest on X", "ask my panel", "run a scenario on <org>". Wraps `POST /orgs/api/scenario/run` in `mode="in-room"`, which now produces a synthesized digest in one LLM call via `multi_lens_analyze` (cheap default) and leaves the room open for follow-up via `rooms.run_panel` if the user wants turn-by-turn debate. NOT for creating the org or its members (use eos-orgs-create / eos-orgs-member-add) and NOT for dogfood use-case scenarios (use eos-new-usecase).
---

# EmptyOS Orgs — Run Scenario

Fire a scenario (critique / workshop / interview) at one of the user's orgs and surface the digest immediately. The orgs `_run_in_room` path was upgraded 2026-05-25 to call `multi_lens_analyze` after creating the debate room — one LLM call, every member as a lens, agreement / disagreement / recommendation block. The heavy turn-by-turn `rooms.run_panel` stays available on the returned room id when the user explicitly wants the conversational artifact.

## When to Use

- "Run a critique on this proposal with <org>"
- "What would <team> think about <X>?"
- "Workshop this idea with <org>"
- "Interview the panel about <topic>"
- "Get a team digest on <X>"
- User has a concrete proposal / draft / question and an existing org with ≥2 AI members
- **Not** for free-form chat with the org — direct them to `/orgs/?org=<id>` for that
- **Not** for cutting a CLI debate — use `rooms.run_panel` on a room instead
- **Not** when no org exists yet — use `eos-orgs-create` first

## Pre-flight

This skill writes through the daemon HTTP API, so the daemon must be reachable first:

- **Daemon up** — `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9000/orgs/` should print `200`. If not, ask the user to run `restart.bat` — never start/restart the daemon yourself (`.claude/rules/daemon-handling.md`).
- **Auth (private mode)** — read `auth_token` from `emptyos.toml` `[network]` and send `Authorization: Bearer <token>` on every request (`.claude/rules/environment.md`).
- **Non-ASCII bodies** — POST via Python `urllib`, not `curl -d` (Windows cp1252 mangles em-dash / CJK).

## Schema

`POST /orgs/api/scenario/run` body:

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `org_id` | string | **yes** | — | Slug id from `GET /orgs/api/orgs`. |
| `scenario_type` | enum | **yes** | — | `critique` / `workshop` / `interview` |
| `prompt` | string | **yes** | — | The proposal / question / topic. One paragraph max. |
| `mode` | enum | no | `in-room` | `in-room` (creates room + digest) or `headless` (no room, no digest). Default to `in-room` — that's the value-add this skill exists for. |

## Scenario semantics

Pulled from `apps/public/standard/company/scenarios/`:

- **critique** — Members react to a proposal from their roles. Best for "tear this apart" / "what's wrong with this." May emit `[DO:]` revision tasks (review-gated).
- **workshop** — Members co-develop or push something forward. Best for "help me improve this" / "what should I add."
- **interview** — Members ask the user questions (instead of answering one). Best for "what would they need to know?" / "stress-test my pitch."

If the user's phrasing doesn't make the scenario obvious, default to **critique** (most common ask) and surface the choice in the preview.

## Process

### Step 1 — Read auth token

```bash
TOKEN=$(grep -E '^auth_token' D:/emptyos/emptyos.toml | head -1 | sed 's/.*= *"\(.*\)".*/\1/')
```

### Step 2 — Resolve the target org

Scan the conversation for an org name. If the user named one explicitly ("ask Phoenix team"), look it up:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9000/orgs/api/orgs" \
  | python -c "import sys,json; d=json.load(sys.stdin); [print(o['id'],'|',o.get('name','')) for o in d.get('orgs',[])]"
```

Match the user's phrasing against `name` (case-insensitive, fuzzy). If exactly one match: use that `id`. If none or multiple, ask via AskUserQuestion with the candidates as options.

Confirm the org has ≥2 AI members — `GET /orgs/api/orgs/<id>/members` and count `mode=ai` entries. If <2, refuse with: *"<Org> has <N> AI member(s). Scenarios need at least 2."* and suggest `eos-orgs-member-add`.

### Step 3 — Extract the prompt

Pull the actual proposal / question / topic from the conversation. If the user pasted a draft, that's the prompt. If they asked a question like "what would they think about X," X is the prompt. Keep it under ~500 chars — long prompts erode the multi-lens output.

Do NOT invent or expand the prompt. If you can't extract one cleanly, ask via AskUserQuestion for it.

### Step 4 — Pick the scenario type

If the user's phrasing maps cleanly to one (critique / workshop / interview), use it. Otherwise default to `critique` and note the choice in the preview.

### Step 5 — Render the preview

```
Proposed scenario:
  org:           <name> (<id>)
  scenario:      <critique|workshop|interview>
  mode:          in-room  (will produce a multi-lens digest)
  prompt:        <first 200 chars… or full if shorter>
  members:       <N AI members will weigh in>
```

### Step 6 — Confirm once

Use AskUserQuestion with three options:
- **"Run as proposed"** — POST and surface the digest.
- **"Edit the prompt"** — accept a rewritten prompt, re-render, re-ask.
- **"Cancel"** — abort, no API call.

Do not split into per-field questions.

### Step 7 — Fire the scenario

```bash
curl -s -X POST "http://127.0.0.1:9000/orgs/api/scenario/run" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"org_id":"<id>","scenario_type":"<type>","prompt":"<prompt>","mode":"in-room"}'
```

Times out after ~60s in practice (one `multi_lens_analyze` call). If the daemon takes longer, fall back to: tell the user the scenario was *started* (the run record exists), and offer to fetch it later with `GET /orgs/api/runs/<run_id>`.

### Step 8 — Render the result

The response is the run record. Show:

```
✓ Scenario complete — <scenario> on <org>
  run id:  <id>
  room:    http://localhost:9000/rooms/?room=<room_id>

DIGEST
------
<record.digest.text>

Provenance: <record.digest.provenance>  (e.g. multi_lens · 4 lenses)
```

If `digest.text` starts with `(digest unavailable: ...)`, show that verbatim and stop — don't pretend it succeeded.

### Step 9 — Offer the follow-up

End with one short line offering the heavy turn-by-turn version *only if* the user might want it:

> *Want the turn-by-turn debate too? I can fire `rooms.run_panel` on this same room — slower (9 calls, minutes) and on a single underlying model it's mostly the same content as the digest above. Use it if you want the conversational artifact or if you've set heterogeneous models on the seats.*

Honest framing — don't push the panel as automatically better. The empirical A/B (2026-05-25) showed the single-call digest matches the 9-call panel on a same-model panel.

## Failure modes

- **Daemon down on `:9000`** — report and stop. Don't try to restart.
- **Org has <2 AI members** — refuse with the count + `eos-orgs-member-add` suggestion.
- **`multi_lens_analyze` import fails (old daemon code)** — `digest.text` will read `(digest unavailable: ImportError: ...)`. Tell the user to restart `:9000` to pick up the new SDK helper.
- **Long prompt (>2k chars)** — warn and suggest trimming; long prompts erode lens-by-lens distinction.

## Doesn't do

- Doesn't chain scenarios — for `critique → workshop → critique-of-revision` loops, use `POST /orgs/api/scenario/chain` directly or surface the option for the user.
- Doesn't create the org or add members — composes with `eos-orgs-create` + `eos-orgs-member-add`.
- Doesn't run the turn-by-turn panel — that's `rooms.run_panel`, intentionally separate. This skill is the cheap default.
