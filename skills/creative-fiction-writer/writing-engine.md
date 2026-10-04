# Fiction Writer — Architecture & Writing Engine UI

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
