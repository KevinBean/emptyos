# People Manager — Templates

## Extended Frontmatter

```yaml
---
# === Basic Info ===
tags:
  - people/family | people/friend | people/professional | people/service
relationship: friend | family | colleague | mentor | service
location: City, Country
company:
title:
last_contact: YYYY-MM-DD

# === Relationship Analysis ===
met_date: YYYY-MM-DD
met_context: "work conference" | "mutual friend" | "university" | etc.
communication_style: direct | indirect | reserved | expressive
energy: gives | drains | neutral
trust_level: 1-10
reciprocity: high | medium | low
shared_interests: [hiking, AI, music]
boundaries: "doesn't discuss X"

# === Relationship Goals ===
relationship_goal: maintain | deepen | professional-network | reduce
contact_frequency: weekly | monthly | quarterly | yearly
next_action: "invite to coffee"
birthday: MM-DD
---
```

---

## Person Note Template

```markdown
[[_300 people MOC]]

---

## Quick Log
- YYYY-MM-DD: Brief interaction note

## Personality Profile
- **Communication style**:
- **Values**:
- **Triggers/Avoid**:
- **Love language**:

## What They Care About
- Current focus:
- Long-term goals:
- Challenges:

## Our Relationship
- **How we met**:
- **What we bond over**:
- **My role**:
- **Their role**:

## Relationship Health
| Date | Score | Notes |
|------|-------|-------|
| YYYY-MM | /10 | |

## Patterns I've Noticed
-

## Things to Remember
- Birthday:
- Family:
- Favorites:
- Sensitive topics:

## Notes
-

## Meetings
```dataview
LIST
FROM "50_Journal" OR "Timestamps/Meetings"
WHERE contains(file.outlinks, this.file.link)
SORT file.name DESC
```
```
