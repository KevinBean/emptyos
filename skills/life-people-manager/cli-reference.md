# People Manager — CLI Reference & Analysis Questions

## Analysis Questions Claude Can Answer

### Relationship Analysis
| Question | Data Sources |
|----------|--------------|
| "Who should I invest more time in?" | trust_level high + last_contact old + energy=gives |
| "Which relationships drain me?" | energy=drains + recent contact |
| "Am I maintaining reciprocity?" | reciprocity field + Quick Log analysis |
| "Who haven't I talked to in 3 months?" | last_contact field |
| "Whose birthday is coming up?" | birthday field |
| "Who do I need to follow up with?" | next_action field populated |

### Network Analysis
| Question | What It Does |
|----------|--------------|
| "Who can help with [topic]?" | Search contacts by expertise/industry |
| "Map my network" | Visual breakdown by sector, location, strength |
| "Where are my network gaps?" | Identify missing sectors/purposes |
| "How can I reach [company/role]?" | Find introduction chains |
| "Networking advice for [goal]" | Strategy + templates from [[Networking]] |

---

## Obsidian CLI (Quick People Lookups) — with Fallbacks

```bash
OBS="bash 80_Code/scripts/obs.sh"

# Who references this person? (instant backlinks)
$OBS backlinks "file=@Jun Ma"
$OBS backlinks "file=@Jun Ma" total
# FALLBACK: Grep for '\[\[@Jun Ma' across *.md

# Find all notes tagged #people
$OBS tag name=people verbose
$OBS tag name=people/friend verbose
# FALLBACK: Grep for '#people' or '#people/friend'

# Search for person mentions across vault
$OBS "search:context" "query=Jun Ma" limit=10
# FALLBACK: Grep for 'Jun Ma' across *.md

# Tasks related to a person
$OBS tasks todo "file=@Jun Ma"
# FALLBACK: Grep for '- \[ \]' in the person's note
```
