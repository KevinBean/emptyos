# Fiction Writer — Project Scaffolds & Templates

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
