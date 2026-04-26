---
name: fiction-writer
description: Fiction writing workflow — novel, memoir, short story. Hybrid approach with Obsidian files + Writing Engine visual UI + Claude Code AI brain.
---

# Fiction Writer

Structured creative writing using **Obsidian files** (source of truth) + **Writing Engine UI** (visual workspace) + **Claude Code** (full-context AI) + **Voice API** (hands-free dictation & TTS).

## Architecture

```
┌─────────────────┐         ┌─────────────────┐
│  Writing Engine  │         │   Claude Code    │
│   (Web UI)       │◄──────►│   (AI Brain)     │
│  Port 7800       │  same   │   Terminal       │
│                  │  files  │                  │
│  - Visual editor │  on     │  - Draft scenes  │
│  - Character map │  disk   │  - Revise prose  │
│  - Progress view │         │  - Continuity    │
│  - Mood/theme    │         │  - Full context  │
│  - 🎤 Voice mode │         │                  │
└────────┬────────┘         └─────────────────┘
         │                          │
         │  ┌─────────────────┐     │
         └──│  Local Voice API │     │
            │  Port 8601       │     │
            │  - STT (Whisper) │     │
            │  - LLM (OpenAI)  │     │
            │  - TTS           │     │
            └─────────────────┘     │
                    │               │
                    └───── 10_Projects/<Title>/ ────┘
                           (markdown files)
```

**Key principle**: Files are the API. All three tools read/write the same markdown files. The UI auto-refreshes on file changes.

### Three AI Brains, Three Strengths

| Tool | Best For | Context |
|------|----------|---------|
| **Claude Code** (terminal) | Full-context drafting, continuity checks, structural revision | Reads entire project |
| **Voice API** (mic button) | Hands-free scene generation, dictation, TTS proof-listening | UI packages context into system_prompt |
| **Writing Engine AI panel** (Ollama/OpenAI) | Quick in-editor actions: expand, compress, grammar | Current scene + codex selection |

## Reference

- **Memory**: `.claude/skills/fiction-writer/fiction-memory.md`
- **Writing Engine**: `10_Projects/writing-engine/` (web UI on port 7800)
- **Projects**: `10_Projects/<Title>/` (each novel/memoir/story is a project folder)

---

## Trigger Table

| User Says | Mode | Action |
|-----------|------|--------|
| "写小说" / "new novel" / "start a novel" | `new-project` | Scaffold novel project |
| "写回忆录" / "new memoir" | `new-project` | Scaffold memoir project |
| "写短篇" / "new short story" | `new-project` | Scaffold short story project |
| "写大纲" / "outline" / "plan the story" | `outline` | Create/refine story outline |
| "写场景" / "draft scene" / "write next scene" | `draft` | Write scene with full context |
| "扩展" / "expand this" | `revise:expand` | Expand passage with detail |
| "压缩" / "compress" | `revise:compress` | Tighten prose |
| "对话润色" / "dialogue pass" | `revise:dialogue` | Improve dialogue voice |
| "文风统一" / "voice pass" | `revise:voice` | Consistent narrative voice |
| "连续性检查" / "continuity check" | `continuity` | Check consistency |
| "角色档案" / "new character" | `character` | Create/update character |
| "世界设定" / "worldbuilding" | `world` | Create/update world note |
| "组装章节" / "assemble chapter" | `assemble` | Compile scenes → chapter |
| "项目状态" / "story status" | `status` | Progress report |
| "打开写作界面" / "open writer" / "writing UI" | `ui` | Launch Writing Engine |
| "语音写作" / "voice mode" / "talk to write" | `voice` | Activate voice mode in UI |
| "读给我听" / "read it back" / "TTS" | `voice:tts` | TTS playback of current scene/chapter |

---

## Project Types & Scaffolds

### Novel
```
10_Projects/<Title>/
├── _brief.md                # Premise, themes, audience, tone
├── _outline.md              # Act/chapter structure with scene beats
├── _progress.md             # Scene status tracker, word counts, threads
├── _project.yml             # Writing Engine metadata (template, language)
├── characters/
│   └── (character files)
├── world/
│   └── (setting/lore files)
├── manuscript/
│   ├── act1/
│   │   ├── ch01-scene01.md
│   │   └── ...
│   └── ...
└── revisions/
    └── (revision notes, editor feedback)
```

### Memoir
```
10_Projects/<Title>/
├── _brief.md
├── _outline.md              # Thematic or chronological structure
├── _progress.md
├── _project.yml
├── people/                  # Real people profiles
├── places/                  # Location descriptions
├── manuscript/
│   ├── part1/
│   │   ├── entry-01.md
│   │   └── ...
│   └── ...
└── revisions/
```

### Short Story
```
10_Projects/<Title>/
├── _brief.md
├── _outline.md
├── _project.yml
├── characters/
├── manuscript/
│   ├── scene-01.md
│   └── ...
└── revisions/
```

---

## Templates

### _brief.md

```markdown
---
title: <Title>
type: novel|memoir|short
genre:
language: zh|en|both
target_words:
audience:
---

# <Title>

## Premise
(One paragraph — who wants what, why they can't have it, what's at stake)

## Themes
- Theme 1
- Theme 2

## Tone & Style
- POV: first|third-limited|third-omniscient
- Tense: past|present
- Voice notes: (e.g., "lyrical but grounded", "sharp and dry")

## Audience
(Who is this for? What comparable works exist?)
```

### _outline.md (Novel)

```markdown
---
title: <Title> — Outline
acts: 3
chapters:
---

# Outline

## Act 1 — Setup

### Chapter 1: <Title>
- **Scene 1**: [beat] — POV: [character], Setting: [where]
- **Scene 2**: [beat]

### Chapter 2: <Title>
- **Scene 1**: [beat]

## Act 2 — Confrontation

### Chapter 3: <Title>
- ...

## Act 3 — Resolution

### Chapter N: <Title>
- ...

## Open Questions
- (Plot decisions not yet made)
```

### _progress.md

```markdown
---
title: <Title> — Progress
updated: YYYY-MM-DD
---

# Progress

## Stats
- **Total Words**: 0
- **Scenes Written**: 0 / [total]
- **Current Phase**: outlining | drafting | revising

## Scene Status

| Scene | Status | Words | POV | Notes |
|-------|--------|-------|-----|-------|
| ch01-scene01 | draft | 0 | — | — |

## Open Threads
- (Plot threads that need resolution)

## Continuity Flags
- (Issues found during checks)
```

### Scene File

```markdown
---
title: Scene Title
chapter: 1
scene: 1
pov: Character Name
status: draft
wordcount: 0
summary: One-line beat summary
---

[scene content]
```

Memoir entry variant — additional frontmatter:
```yaml
date_event: YYYY-MM-DD
location: Place Name
people_involved:
  - Person A
  - Person B
```

### Character File

```markdown
---
name: Character Name
role: protagonist|antagonist|supporting|minor
---

# Character Name

## Core
- **Want**: What they consciously pursue
- **Need**: What they actually need (often opposite of want)
- **Flaw**: Central weakness
- **Arc**: beginning state → end state

## Voice
- Speech patterns, vocabulary level, verbal tics
- Sample dialogue: "..."

## Relationships
- [[Other Character]] — nature of relationship

## Key Scenes
- Ch X: [pivotal moment]
```

### World File

```markdown
---
name: Setting/Concept Name
type: location|culture|magic-system|technology|organization
---

# Name

## Description
(Sensory details — what you see, hear, smell, feel)

## Rules
(How this element works — constraints, logic)

## Significance
(Why it matters to the story)

## Appears In
- [[ch01-scene01]] — first introduced
```

### _project.yml (Writing Engine Integration)

```yaml
template: novel        # novel|memoir|short
language: zh           # zh|en|both
pov: third-limited     # first|third-limited|third-omniscient
tense: past            # past|present
```

This file tells the Writing Engine UI which template to load (codex categories, AI actions, metadata fields).

---

## Core Workflow

```
new-project → outline → [DRAFT LOOP] → assemble → revision passes
```

### Draft Loop (per scene)

```
1. Claude reads context (see Context Loading Protocol)
2. Claude drafts scene → writes to manuscript/<path>.md
3. User reviews in Writing Engine UI or Obsidian
4. User annotates / requests changes via Claude Code
5. Claude revises
6. Every 3-5 scenes: continuity check
7. Update _progress.md
```

---

## Context Loading Protocol

**Before writing ANY scene**, Claude MUST read (in order):

1. **`_brief.md`** — premise, themes, tone, audience
2. **`_outline.md`** — find the current scene's beat/purpose
3. **`_progress.md`** — what's done, open threads, continuity flags
4. **Previous 2 scenes** — for continuity and flow
5. **POV character file** — voice, speech patterns, arc position
6. **Other characters in scene** — relationships, tensions
7. **Relevant world file** — if new setting introduced

This is what makes Claude Code superior to the Writing Engine's generic AI panel: **every scene is written with full project context**.

### Context Loading — Quick Reference

```
READ _brief.md          → tone, themes
READ _outline.md        → this scene's beat
READ _progress.md       → what's happened, open threads
READ prev 2 scenes      → continuity, voice
READ character files    → POV voice, relationships
READ world files        → setting details (if needed)
```

---

## Mode Details

### `new-project`

1. Ask user for: title, type (novel/memoir/short), premise, language
2. Create project folder in `10_Projects/<Title>/`
3. Scaffold all folders and template files
4. Create `_project.yml` for Writing Engine compatibility
5. Write initial `_brief.md` from user's premise
6. Update `fiction-memory.md` with new active project

### `outline`

1. Read `_brief.md` for premise and themes
2. Propose act/chapter structure with scene beats
3. Iterate with user until outline is solid
4. Write to `_outline.md`
5. Generate initial `_progress.md` scene table

### `draft`

1. **Execute full Context Loading Protocol** (see above)
2. Identify next scene from `_progress.md` (or user specifies)
3. Write scene matching: outline beat, character voice, tone, previous flow
4. Save to `manuscript/<path>/<scene>.md`
5. Update `_progress.md` (status → draft, word count)

### `revise:expand`

Read the scene, expand thin passages with:
- Sensory detail (sight, sound, smell, touch, taste)
- Internal thought (for POV character)
- Environmental texture
- Micro-actions and body language

### `revise:compress`

Read the scene, tighten by:
- Removing redundant descriptions
- Cutting filler words and phrases
- Combining sentences where possible
- Preserving voice and key imagery

### `revise:dialogue`

Read the scene + all character files for characters present:
- Make each character sound distinct
- Remove "said" synonyms overuse
- Add subtext (what's unsaid)
- Ensure dialogue advances plot or reveals character

### `revise:voice`

Read `_brief.md` tone notes + sample of earlier scenes:
- Flag voice inconsistencies
- Normalize POV distance
- Harmonize tense usage
- Suggest specific fixes

### `continuity`

Scan recent 3-5 scenes + character files + world files:
- **Character consistency**: behavior matches profile? contradictions?
- **Timeline**: chronological logic? impossible overlaps?
- **Plot threads**: which are open? any dropped?
- **Setting details**: physical descriptions consistent?
- **Tone/voice**: has narrative voice drifted?

Output: Update `_progress.md` with findings under "Continuity Flags"

### `character`

1. Ask: name, role, want/need/flaw/arc
2. Create file in `characters/` (or `people/` for memoir)
3. Link to relevant scenes in outline
4. If updating existing: read current file, preserve existing info, add new

### `world`

1. Ask: name, type, description, rules, significance
2. Create file in `world/` (or `places/` for memoir)
3. Link to scenes where it appears

### `assemble`

1. Read all scenes for target chapter (sorted by scene number)
2. Check transitions between scenes
3. Add chapter-level framing if needed
4. Calculate chapter word count
5. Write assembled chapter to `revisions/ch01-assembled.md`

### `status`

Report on demand:
- Total word count (by chapter/act)
- Scene completion status (draft/revised/final)
- Open plot threads
- Character appearance tracker
- Continuity flags
- Estimated completion %

### `ui`

Launch the Writing Engine:
```bash
cd "10_Projects/writing-engine" && python server.py &
```
Then open `http://localhost:7800` in browser.

**Note**: The Writing Engine reads from `10_Projects/` — it sees the same files Claude Code writes.

---

## Writing Engine Integration

The Writing Engine UI (`10_Projects/writing-engine/`) provides the visual creative workspace:

### What the UI Does
- **Scene editor** with metadata bar (POV, status, word count)
- **Manuscript tree** sidebar (expand/collapse, status dots)
- **Codex panel** (characters, locations — populated from project's `characters/` and `world/` folders)
- **AI actions** panel (continue, rewrite, grammar, describe, dialogue, brainstorm, translate, feedback)
- **Export** (HTML, Markdown, JSON)
- **TTS** (listen to scenes read aloud)

### What Claude Code Does (that the UI can't)
- **Full-context drafting** (reads brief, outline, previous scenes, character files)
- **Continuity checking** (cross-references all project files)
- **Structural revision** (voice pass, dialogue pass across multiple scenes)
- **Progress tracking** (updates `_progress.md` with comprehensive status)
- **Outline management** (iterative story structure refinement)

### How They Work Together
1. User scaffolds project via Claude Code (`new-project` mode)
2. User opens Writing Engine UI for the visual workspace
3. User asks Claude Code to draft scenes → files appear in UI
4. User edits in the UI → Claude Code sees changes when asked to revise
5. Claude Code runs continuity checks periodically
6. User uses UI's quick AI actions (expand, compress) for small edits
7. Claude Code handles structural passes (voice, dialogue) across chapters

---

## Voice Mode

### Overview

The Writing Engine UI includes a mic button that connects to the **Local Voice API** (port 8601) for hands-free writing. The voice API has STT (Whisper) + LLM (OpenAI GPT-4o) + TTS — the same pipeline used by TalkBuddy, repurposed for fiction.

### Voice API Endpoint

```
POST http://localhost:8601/v1/converse/stream
Body: { system_prompt, history: [{role, content}] }
Returns: streaming audio + text
```

The Writing Engine UI packages novel context into `system_prompt` before each voice call.

### Two Explicit Modes

To avoid "dictation vs. instruction" ambiguity, the UI provides two voice modes:

#### 🎤 Dictate Mode (push-to-talk → append)
- User speaks prose directly
- STT transcript is **appended** to the current scene editor as raw text
- No LLM processing — pure dictation
- Fast, no latency beyond STT
- Use case: first-draft bursts, capturing dialogue ideas, stream-of-consciousness

#### 🗣️ Command Mode (push-to-talk → LLM → confirm → save)
- User speaks an instruction (e.g., "Write the next scene where Sarah finds the letter")
- UI packages context into `system_prompt`:
  - Current scene content
  - Brief summary from `_brief.md` (tone, themes — compact)
  - POV character profile (from codex/characters)
  - Previous scene's last 500 words (continuity)
  - The outline beat for this scene
- Transcript + context → Voice API → GPT-4o generates prose
- **Result shown as proposed diff** — user confirms before saving to file
- Use case: scene generation, revision requests

#### 💬 Conversation Mode (multi-turn voice interview)
- Open-ended, multi-turn dialogue with the LLM — like talking to a writing partner
- The voice API's `history` parameter accumulates turns naturally
- **Nothing is saved automatically** — conversation is exploratory
- UI shows a running transcript panel alongside the editor
- User can **cherry-pick** insights from the conversation:
  - "Save that to outline" → appends to `_outline.md`
  - "Save as character note" → appends to character file
  - "Use that as the scene beat" → updates `_progress.md`
- Use cases:
  - **Story development interview**: "Tell me about your protagonist" → "What's her deepest fear?" → "How does that connect to the theme?" → building character depth through dialogue
  - **Plot brainstorming**: "What if she never opens the letter?" → "But then how does act 2 work?" → "Go back, I liked the first idea better" — exploring branches conversationally
  - **World-building Q&A**: "Describe the magic system" → "What are its limits?" → "How would a poor person use it differently?" — the LLM asks questions back, draws out details you haven't thought of
  - **Unsticking**: "I'm stuck on chapter 5, the pacing feels off" → back-and-forth diagnosis → "Try splitting it into two shorter chapters" — collaborative problem-solving
- The LLM should **ask follow-up questions** (system prompt instructs it to interview, not just answer)
- Conversation history persists within the session; cleared on mode exit

### Context Packaging

The UI assembles a compact `system_prompt` (~1000 tokens) before each voice call. Different prompts per mode:

**Command Mode:**
```
You are a fiction writing assistant for "{title}".
TONE: {brief.tone} | THEMES: {brief.themes}
POV: {pov_character.name} — {pov_character.voice}
CURRENT BEAT: {outline_beat}
PREVIOUS: {last_500_words_of_prev_scene}

Write in {language}, matching the established voice and style.
The user will give you an instruction. Follow it precisely.
```

**Conversation Mode:**
```
You are a creative writing partner for "{title}" ({type}).
PREMISE: {brief.premise}
THEMES: {brief.themes}
CHARACTERS: {character_names_and_roles}
CURRENT STATE: {progress.current_phase}, {progress.total_words} words written

You are having a voice conversation with the author. Your role:
- Ask follow-up questions to draw out ideas
- Challenge weak logic, suggest alternatives
- Build on what the author says, don't replace their vision
- Be concise — this is a spoken conversation, not an essay
- When the author says something worth keeping, flag it:
  "That's a great insight — want me to save that to your outline?"
```

### TTS Proof-Listening

Separate from voice input — this is **output only**:

- **Read current scene**: TTS reads the active scene aloud
- **Read selection**: TTS reads highlighted text only
- **Read chapter**: TTS reads assembled chapter
- Use case: catch awkward rhythm, test dialogue flow, hear prose cadence
- Endpoint: existing `/api/tts` in Writing Engine (OpenAI TTS)

### Voice Commands Reference

| You Say | Mode | What Happens |
|---------|------|-------------|
| (speaking prose directly) | Dictate | Raw transcript appended to editor |
| "Write the next scene. Sarah finds the letter..." | Command | LLM generates scene → show diff → confirm → save |
| "Rewrite this dialogue to be more tense" | Command | LLM revises current scene → show diff → confirm |
| "Tell me about the protagonist's motivation" | Converse | Multi-turn interview begins, LLM asks follow-ups |
| "What if we kill off Marcus?" → "How would act 3 change?" → "Go back, I liked the first idea" | Converse | Ongoing dialogue, no auto-save, cherry-pick insights |
| "I'm stuck on this chapter" | Converse | Collaborative diagnosis, LLM suggests approaches |
| "Save that to outline" / "Save as character note" | Converse | Cherry-picks from conversation → appends to file |
| "Read me back this scene" | TTS | TTS plays current scene audio |
| "Read chapter 3" | TTS | TTS plays assembled chapter |

### Safety Guardrails

1. **Command mode always shows diff before saving** — never auto-overwrites
2. **Dictate mode only appends** — never replaces existing text
3. **Version backup**: before any voice-triggered save, copy current file to `revisions/`
4. **Visual indicator**: UI shows which mode is active (🎤 red = dictate, 🗣️ blue = command)
5. **Escape hatch**: pressing Esc or clicking "Cancel" discards the LLM output

### Implementation Plan (for Writing Engine UI)

**Phase 1 — Mic button + Dictate** (minimal):
1. Add mic button to editor toolbar
2. Use `navigator.mediaDevices.getUserMedia()` for browser audio capture
3. Send audio to `localhost:8601` STT endpoint
4. Append transcript to editor textarea
5. ~2 hours work

**Phase 2 — Command Mode** (voice → LLM → diff):
1. Add mode toggle (Dictate / Command / Converse)
2. Build context packager (reads brief, outline, character, prev scene via existing APIs)
3. Send system_prompt + transcript to `/v1/converse/stream`
4. Display LLM response as proposed diff
5. Confirm button saves to file
6. ~4 hours work

**Phase 2b — Conversation Mode** (multi-turn interview):
1. Add conversation transcript panel (scrollable, alongside editor)
2. Maintain `history` array of turns, send with each voice call
3. Add "Save to..." buttons on each LLM response (outline, character, progress)
4. Interview-style system prompt that asks follow-up questions
5. "Clear conversation" button to reset
6. ~3 hours work

**Phase 3 — TTS Proof-Listening**:
1. Add "Read Aloud" button to editor toolbar
2. Send scene text to existing `/api/tts` endpoint
3. Play audio in browser with pause/stop controls
4. ~1 hour work (TTS endpoint already exists)

---

## Writing Principles

These guide Claude's drafting and revision:

1. **Show, don't tell** — action and sensory detail over abstract statements
2. **Character voice distinction** — each character sounds different
3. **Scene purpose** — every scene must advance plot OR reveal character (ideally both)
4. **Tension on every page** — conflict, mystery, or emotional stakes
5. **Specific over general** — "a cracked blue mug" not "a cup"
6. **Subtext in dialogue** — what's unsaid matters more
7. **End scenes with forward momentum** — reader wants to turn the page

---

## Post-Action

After any mode execution:
1. Update `_progress.md` if scene status changed
2. Update `fiction-memory.md` if new project or significant craft insight
3. Report what was written/changed and next suggested action
