# Obsidian CLI (Quick Journal Queries) — with Fallbacks

```bash
OBS="bash 80_Code/scripts/obs.sh"

# Tasks from today's daily note
$OBS tasks todo daily
# FALLBACK: Read today's daily note, extract '- [ ]' lines

# Tasks due this week (from weekly note)
$OBS tasks todo "file=2026-W14"
# FALLBACK: Grep for '- \[ \]' in the weekly note file

# Read today's daily note content
$OBS "daily:read"
# FALLBACK: Read 50_Journal/YYYY/YYYY-MM-DD.md directly

# Search journal for a topic
$OBS "search:context" "query=career pivot" path=50_Journal limit=5
# FALLBACK: Grep across 50_Journal/**/*.md

# What's due today (frontmatter property check)
$OBS tasks todo total
# FALLBACK: python "80_Code/scripts/task-manager.py" --today
```
