---
name: knowledge-synthesis
description: Search vault intelligently and synthesize answers from multiple notes - go beyond keyword matching
---

# Knowledge Synthesis

A workflow skill for answering questions using vault knowledge. No special infrastructure - uses existing tools smartly.

## When to Use

User asks questions like:
- "What do I know about X?"
- "Summarize my thoughts on X"
- "What have I learned about X?"
- "How do X and Y relate?"
- "What are my goals for X?"

## The Workflow

### Step 1: Expand Search Terms

Don't just grep the exact words. Think of related terms AND **both languages** (Chinese + English):

| User Query | Search Terms |
|------------|--------------|
| "anxiety" | anxiety, anxious, worry, stress, CBT, therapy, panic, 焦虑, 压力 |
| "career goals" | career, job, work, profession, 职业, 工作, 目标 |
| "visa" | visa, immigration, PR, 189, 482, Australia, migrate, 签证, 移民 |
| "friendship" | friend, friendship, relationship, 友谊, 朋友, 关系 |
| "healing" | healing, therapy, CBT, recovery, 疗愈, 治愈, 康复 |

**Bilingual vault**: Notes and filenames may be in Chinese or English. Always search both.

### Step 2: Multi-Source Search

Search using multiple tools in priority order:

```bash
# 1. Obsidian CLI search (uses Obsidian's index — fast, ranked)
bash "99_Attachments/scripts/obs.sh" "search:context" "query=keyword" limit=10

# 2. Tag-based discovery (find notes tagged with related topics)
bash "99_Attachments/scripts/obs.sh" tag name=career verbose
bash "99_Attachments/scripts/obs.sh" tag name=healing verbose

# 3. Grep for content search (more thorough, regex support)
# Use Grep tool across priority folders:
#   10_Projects/ → 20_Areas/ → 30_Resources/ → 50_Journal/

# 4. Backlinks from key notes (find connected notes)
bash "99_Attachments/scripts/obs.sh" backlinks file="Key Note Name"
```

### Step 3: Use Link Relationships

After finding initial matches, expand via connections:

```bash
# What does this note link TO? (follow outgoing links)
bash "99_Attachments/scripts/obs.sh" links file="Found Note"

# What links TO this note? (find related context)
bash "99_Attachments/scripts/obs.sh" backlinks file="Found Note"
```

Fallback (if Obsidian not running): Read `99_Attachments/link-index.json`, check backlinks.

Example:
```
Found: "CBT.md" matches "anxiety"
Links to: [[Cognitive Distortions]], [[Thought Records]]
→ Read those too for fuller picture
```

### Step 4: Prioritize What to Read

Read notes in this order:
1. **Projects** (10_Projects) — active, current focus
2. **Areas** (20_Areas) — ongoing, maintained
3. **Resources** (30_Resources) — reference material
4. **Journal** (50_Journal) — personal reflections
5. **Archive** (40_Archive) — only if needed

Limit: Read 10-15 most relevant notes max.

### Step 5: Synthesize with Citations

When answering:
1. Combine information from multiple notes
2. Identify patterns/themes across notes
3. Note any contradictions or evolution of thought
4. **Always cite sources**: "According to your note [[Career Plan]]..."

## Example Interaction

```
User: What do I know about managing anxiety?

Claude thinking:
1. Search terms: anxiety, anxious, stress, CBT, therapy, calm, worry, panic
2. Grep in 20_Areas/Health/, 50_Journal/
3. Check link-index for related notes
4. Read top matches

Claude response:
Based on your notes, here's what you've documented about managing anxiety:

**CBT Techniques** (from [[cbt sessions/2024-03-20 Anxiety]]):
- Identifying cognitive distortions
- Fortune telling and mind reading patterns
- Challenging negative thoughts

**Personal Triggers** (from [[50_Journal/2024/2024-03]]):
- Work deadlines
- Social situations
- [specific patterns noted]

**What's Worked** (from [[Health/anxiety-management]]):
- [techniques that helped]

Sources: [[CBT]], [[2024-03-20 Anxiety]], [[anxiety-management]]
```

## Query Patterns

### "What do I know about X?"
→ Broad search, synthesize all knowledge

### "Summarize my X notes"
→ Find all X-related notes, create cohesive summary

### "What are my goals for X?"
→ Focus on Projects + Areas, extract goals/plans

### "How has my thinking on X evolved?"
→ Include Journal, order by date, track changes

### "Connect X and Y"
→ Search both, find notes that mention both, identify relationships

## Tips for Better Results

1. **Ask for topic expansion**: "What related terms should I search for?"
2. **Specify scope**: "Just in my journal" or "Only active projects"
3. **Ask for sources**: "Show me which notes this comes from"
4. **Iterate**: "That's helpful, but also check my 2023 notes"

## Integration with Other Skills

- **vault-index**: Use backlinks to find related notes
- **task-aggregator**: Find tasks related to a topic
- **cleanup-studio**: After synthesis, identify redundant notes to merge

## Limitations

- Obsidian CLI search may return empty if vault isn't fully indexed — fall back to Grep
- May miss notes using different terminology (use tag discovery + bilingual terms to compensate)
- Context limits if topic spans 50+ notes

For very broad topics, suggest:
"This topic appears in many notes. Want me to focus on a specific aspect, like 'career goals for 2026' instead of 'career'?"
