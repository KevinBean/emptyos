---
name: eos-vault-study-pack
description: Turn any source content (an infographic image, pasted article, conversation excerpt, URL, or existing vault note) into a complete study pack — distilled KB notes + a Learn course with read/quiz lessons (SRS-scheduled) + an optional viz artifact. Use when the user says "make a study pack", "turn this into learning materials", "make notes + flashcards from this", "help me learn this", or hands over content and asks for "better notes / visualisation / learning materials". NOT for digesting a whole reference PDF into citation sources (use vault-source-digest) or archiving a raw chat transcript (use vault-ai-conversation-digest) — this skill is for *learnable* distillation, not archival.
---

# Study Pack — content → KB notes + Learn course + viz

Chains three existing daemon surfaces into one flow. **No new daemon code** —
everything goes through live APIs, so this works without a restart.

```
source content
   │ distill (you, in-conversation)
   ▼
KB notes (kind: concept/lesson)      POST /kb/api/notes
   │ reference by slug
   ▼
Learn course (read + quiz lessons)   POST /learn/api/courses/save
   │ quizzes auto-generate per lesson; SM-2 SRS scheduling comes free
   ▼
optional viz artifact                POST /viz/api/generate
```

## Prerequisites (read first)

- **The daemon must be running** at `http://127.0.0.1:9000` — this skill
  drives three live daemon surfaces (KB, Learn, viz) and Playwright; it has
  no offline path.
- **Never `localhost`** from Python — use `127.0.0.1` (localhost resolves to
  `::1`; the daemon binds IPv4 only).
- Auth: private mode needs `Authorization: Bearer <token>`; token at
  `emptyos.toml [network] auth_token`.
- POST bodies with non-ASCII (中文 etc.): use Python `urllib`, never
  `curl -d` (Windows mangles to cp1252).
- Probe `GET /api/health` first. If the daemon is down, STOP and tell the
  user — do not write notes via raw file I/O as a fallback (the APIs apply
  vault-map paths, validation, and events).

Helper shape for all calls:

```python
import urllib.request, json, tomllib
tok = tomllib.load(open(r'D:/emptyos/emptyos.toml','rb')).get('network',{}).get('auth_token','')
def api(path, payload=None, method=None):
    req = urllib.request.Request(
        "http://127.0.0.1:9000" + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method or ("POST" if payload is not None else "GET"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {tok}"} if tok else {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)
```

## Step 1 — Gather + scope the content

Accept any of: an image already in the conversation (read it directly — you
have vision), pasted text, a vault note path, or a URL (WebFetch it; for
YouTube use `vault-yt-digest` first and study-pack its output note).

Then decide scope **before writing anything**:

- Identify 2–6 distinct learnable units. One KB note per unit. Fewer,
  meatier notes beat many thin ones — each note must stand alone because
  the quiz generator reads ONLY that note's body.
- One short line to the user: "I'll cut this into N notes: …" — then
  proceed (don't block on approval unless the source is ambiguous or
  the user asked for something specific).

## Step 2 — Create KB notes

`POST /kb/api/notes` per unit:

```json
{"kind": "concept", "title": "Hybrid Search in RAG",
 "domain": "ai", "topic": "rag",
 "body": "...", "source": "<where this came from>",
 "related": ["<sibling-slug>", "..."]}
```

- `kind`: `concept` for explanatory units, `lesson` for practical/hard-won
  guidance. Never `clause`/`reference` (that's vault-source-digest's job).
- Body discipline — **write for the quiz generator**: each note needs
  enough concrete substance (definitions, contrasts, the *why*, 1–2
  examples) that 3 fair MCQs can be generated from it alone. A note that's
  just headers + one-liners produces garbage quizzes.
- Wikilink sibling notes in bodies (`[[slug]]`) and set `related:` — notes
  join the KB graph, not an island.
- Capture the response `slug` of every note — Step 3 needs them.
- Same `domain`/`topic` across the pack so they cluster in /kb/.

## Step 3 — Create the Learn course

`POST /learn/api/courses/save`:

```json
{"course_id": "", "title": "RAG Production Pipeline",
 "description": "...", "level": "intermediate",
 "domain": "ai", "topic": "rag", "duration_min": 30,
 "lessons": [
   {"slug": "hybrid-search-in-rag", "title": "Hybrid Search", "kind": "read", "duration_min": 5},
   {"slug": "hybrid-search-in-rag", "title": "Quiz: Hybrid Search", "kind": "quiz", "duration_min": 3}
 ]}
```

- Empty `course_id` → derived from title; response returns the real id.
- Default pattern: a `read` lesson then a `quiz` lesson **per slug**, in
  teaching order. Quizzes are generated on demand by the learn app and
  scored quizzes auto-schedule the slug into SM-2 SRS review — you get
  spaced repetition for free, don't build flashcards separately.
- For 5+ notes, consider one final quiz over the keystone note instead of
  quiz-per-note — quiz fatigue is real.

## Step 4 — Optional viz artifact

Only when the content has a *shape* worth seeing (a pipeline, a hierarchy,
a comparison, a network). Skip for purely verbal content — a forced diagram
is noise.

`POST /viz/api/generate {"prompt": "...", "shape": "<shape>"}`:

| Content shape | viz shape |
|---|---|
| Process / pipeline / decision flow | `mermaid` (cheapest, works on weak models) |
| Concept map, labelled anatomy | `svg-diagram` |
| Numeric comparison | `chart` |
| Entity-relationship web | `network-graph` |
| Step-by-step teaching deck | `slide-deck` |

Write a **self-contained prompt** (the viz app sees only the prompt, not
the KB notes): include the actual stage names / relationships / numbers.
The response carries the artifact `id` — viewable at `/viz/` and at
`/viz/api/html/<id>`. Link it from the keystone KB note with a `## Visual`
section. There is no HTTP API for appending a section to a kb note, so
either generate the viz *before* the notes and include the link in the
body at creation time, or append the section directly to the note file on
disk (safe here — the file was created by this same flow seconds ago;
path comes back in the create response).

**Verify the render before reporting it.** Generated diagram code can carry
syntax errors (mermaid v10 rejects `color:#hex` in `linkStyle` and
`direction TD` inside subgraphs). Load the artifact headlessly and check:

```python
from playwright.sync_api import sync_playwright
# goto the artifact's scene.html (file:// path from the viz outputs dir,
# avoids the auth gate), wait ~4s, then assert:
#   "Syntax error" not in page.inner_text("body")
#   page.query_selector(".mermaid svg .node")  # real nodes, not the error bomb
```

If broken, fix the persisted `scene.html` under
`{vault}/30_Resources/EmptyOS/viz/outputs/<id>/` directly and re-verify —
don't re-roll the LLM and hope.

## Step 5 — Report

End with the pack's surfaces, all clickable:

- **Course**: `http://127.0.0.1:9000/learn/` → "<title>" (N lessons)
- **Notes**: each slug with its /kb/ link
- **Viz** (if made): `/viz/` artifact link
- Remind: quizzes generate on first open; scored quizzes feed the SRS
  review queue (`/learn/` Review tab + hub panel).

## Quality bar (what NOT to do)

- Don't transcribe the source verbatim — distill. The source's structure
  is rarely the right teaching structure.
- Don't create a note per bullet point. A note = one self-contained idea
  big enough to quiz.
- Don't write quiz questions yourself — the learn app generates them; your
  job is note bodies rich enough to quiz from.
- Don't generate a viz for content with no visual shape.
- Don't fall back to raw vault file writes when the daemon is down — stop
  and say so.
- If the source contradicts itself or you correct it during distillation
  (e.g. critiquing an infographic), the notes carry the *corrected*
  understanding, with a "common misconception" line where useful — that's
  the highest-value part of the note.
