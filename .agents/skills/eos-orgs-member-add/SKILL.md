---
name: eos-orgs-member-add
description: Add a member (human or persona) to an existing Real Org in `apps/company/`. Extracts the person's name, role, and reporting line from the current conversation, links to an existing `apps/people/` person record when one matches by name, and POSTs to `/orgs/api/members`. Use when the user says "add <person> to <org>", "<name> joined <org>", "<name> is now <role> at <org>", or "make <name> a member of <org>".
---

# EmptyOS Orgs — Add Member

Link a person (or persona) to an existing org via `POST /orgs/api/members`. The skill prefers linking to an existing `apps/people/` record by name match, so the org member and the people-app contact share a single source of truth. Falls back to `mode=human` with a bare name when no people record exists.

This skill does **not** create the org — use `eos-orgs-create` first if the org doesn't exist. It also does **not** create the person in the people app — that's `life-people-manager`'s job. The skill is a *linker*.

## When to Use

- User says: "add Sarah to Acme Corp", "Sarah Lee joined as VP Engineering", "Bob is now Phoenix team lead", "make Alice a member of Build Club Sydney"
- Conversation has surfaced a person joining or being newly tracked under an org
- **Not** for re-roling an existing member — the v1 API doesn't have an update-role endpoint; advise the user to remove + re-add, or edit via the web UI

## Schema (`POST /orgs/api/members`)

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `org_id` | string | **yes** | — | Must already exist. |
| `mode` | enum | no | `human` | `human` (links to `apps/people/`) or `ai` (AI persona — discouraged from this skill) |
| `person_id` | string | **yes (mode=human)** | — | `apps/people/` person id. **API does not validate**; the skill must verify it exists before POSTing to avoid dangling links. |
| `name` | string | **yes (mode=ai)** | — | Display name. Only used when `mode=ai`. Ignored when `mode=human`. |
| `role` | string | no | `""` | Job title or function. |
| `dept` | string | no | `""` | Department / squad. |
| `reports_to` | string | no | `""` | Another member's id within the same org (skip unless explicitly stated). |
| `joined` | string | no | today | ISO date. |
| `emoji` | string | no | `""` | Single emoji for the org chart. |
| `model` | string | no | `""` | Only for `mode=ai`. |
| `system_prompt` | string | no | auto | Only for `mode=ai`; auto-generated from name + role if blank. |

## Process

### Step 1 — Resolve `org_id`

Three paths in order:

1. **Skill arg.** If invoked as `/eos-orgs-member-add <org_id>`, use it directly. Confirm with `GET /orgs/api/orgs/<org_id>` — if 404, abort and tell the user.
2. **Conversation name match.** Extract the org name from the user's message (e.g. "add Sarah to **Acme Corp**"). `GET /orgs/api/orgs?reality=real`, fuzzy-match against `name` and `id`. If exactly one hit, use its id. If multiple or zero, fall through.
3. **Ask.** Present the org list to the user via AskUserQuestion with up to 4 best candidates by recency, and an "Other" option that takes the org id as free text. Validate the chosen id exists before continuing.

### Step 2 — Resolve the person

Extract the person's name from the conversation. Then, in order:

1. **People app lookup (mandatory).** `GET /people/api/people?q=<name>` (auth Bearer). The endpoint returns `{people: [...]}` with each entry carrying `id`, `name`, and other fields.
2. **Exactly one match** → capture `person_id`, set `mode=human`. Show the user a compact preview ("linking to existing person record: Sarah Lee · sarah-lee") so they can correct before commit.
3. **Multiple matches** → AskUserQuestion with each candidate (label = "name · id"). The user picks one.
4. **No matches** → AskUserQuestion with two options:
   - **Add the person first, then re-run** *(recommended)* — invoke `life-people-manager` to create the record properly. Stop this skill; the user re-invokes it once the person exists.
   - **Add as AI member instead** — switch to `mode=ai`, use the conversation name as `name`. A synthetic system_prompt will be generated server-side. Suitable for personas / placeholder members; not for real humans.

The org API does **not** validate `person_id` against the people app — passing a non-existent id silently creates a dangling member. The skill's lookup step is what keeps the cross-app link useful. Never POST a `mode=human` member without confirming `person_id` resolves to a real person record.

Never create a people-app record from this skill. Cross-app writes through specialised skills (one verb each).

### Step 3 — Extract role, dept, reports_to from conversation

Pull verbatim phrases where present:

- "as VP Engineering" → `role = "VP Engineering"`
- "in the platform team" → `dept = "Platform"` (capitalise consistently)
- "reports to Alice" → `reports_to = <alice's member id, resolved by GET /orgs/api/orgs/<org_id> + match by name>`

If `reports_to` resolution fails (no matching member), leave blank rather than guess. Manager links can be wired in the web UI later.

### Step 4 — Confirm

Render the proposed member and ask once:

```
Proposed member:
  org:         <org_name> (<org_id>)
  person:      <name> (<person_id or "no link">)
  mode:        <human|ai>
  role:        <or "(blank)">
  dept:        <or "(blank)">
  reports_to:  <or "(none)">
  joined:      <today>
```

AskUserQuestion: **Add**, **Edit fields**, **Cancel**.

### Step 5 — POST

```bash
TOKEN=$(grep -E '^auth_token' D:/emptyos/emptyos.toml | head -1 | sed 's/.*= *"\(.*\)".*/\1/')
curl -s -X POST http://127.0.0.1:9000/orgs/api/members \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '<JSON>'
```

Use a Python `json.dumps()` for the body if the role / dept contains unicode.

### Step 6 — Verify + report

`GET /orgs/api/orgs/<org_id>` should now include the new member in the response. If not, surface the POST response and the GET response side-by-side and ask the user to diagnose.

Report:

```
✓ Member added: <name> → <org_name>
  member id:  <mid>
  role:       <role or "(none)">
  open web:   http://localhost:9000/company/?org=<org_id>
```

## Anti-patterns

- **Don't** create a Persona member from this skill. Personas come out of the web UI's Persona tab where the sim context is rich enough to write a useful system prompt.
- **Don't** silently choose between multiple people-app matches. Ambiguous names always go through AskUserQuestion.
- **Don't** invent `reports_to` from "they probably report to the CEO". Only link when the user explicitly states the reporting line *and* the manager exists as a member already.
- **Don't** auto-create the person in the people app. The skill family is intentionally narrow — one verb each.
