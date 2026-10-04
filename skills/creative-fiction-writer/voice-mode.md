# Fiction Writer — Voice Mode

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
