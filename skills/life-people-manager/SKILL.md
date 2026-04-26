---
name: people-manager
description: Manage relationships - track contacts, analyze relationship health, personality profiling, network analysis
---

# People Manager

Comprehensive relationship management skill - beyond contact notes to understanding and nurturing relationships.

## Bilingual Search

Notes and logs may be in Chinese or English. When searching:
- 朋友/friend, 友谊/friendship, 关系/relationship
- 疗愈日志/healing log, 会议/meeting, 冲突/conflict
- 信任/trust, 联系/contact, 网络/network

Always search both languages when looking for people-related content.

## Location & Conventions

- **Folder**: `30_Resources/People/`
- **Naming**: `@FirstName LastName.md` (e.g., `@Jun Ma.md`, `@田海亭.md`)
- **MOC**: `[[_300 people MOC]]`

---

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

## Workflows

### Create New Contact

When user says "add contact" or "new person note":

1. Ask for: Name, Relationship type, Location (optional), Company (optional)
2. Generate filename: `@FirstName LastName.md`
3. Create in `30_Resources/People/`
4. Fill frontmatter with provided info
5. Add template structure
6. Link back to `[[_300 people MOC]]`
7. Offer to add to People MOC under appropriate group

**Example:**
```
User: Add a new contact - John Smith from Google

Claude: I'll create @John Smith.md in 30_Resources/People/

What's your relationship?
1. Professional/Colleague
2. Friend
3. Service provider
4. Other

User: Professional

Claude: [Creates note with professional tag, company: Google]
Done! Created @John Smith.md. Want me to add him to the People MOC under Professional Contacts?
```

### Find Contacts

Search contacts by various criteria:

```bash
# By company
grep -r "company: Google" "30_Resources/People/" --include="*.md"

# By location
grep -r "location: Sydney" "30_Resources/People/" --include="*.md"

# By relationship
grep -r "people/professional" "30_Resources/People/" --include="*.md"

# By name (partial match)
ls "30_Resources/People/" | grep -i "smith"
```

### Find Stale Contacts

Contacts you haven't interacted with recently:

```bash
# Find all last_contact dates and check which are > 3 months old
grep -r "last_contact:" "30_Resources/People/" --include="*.md" -A0
```

**When user asks "who should I catch up with?":**
1. Scan all people notes for `last_contact` field
2. Identify contacts where last_contact > 3 months ago
3. Prioritize by relationship type (friends > professional > service)
4. Suggest 3-5 people to reach out to

### Log Interaction

**Quick interaction (chat, message, call):**
1. Open person's note
2. Add entry to `## Quick Log` section:
   ```
   - 2025-01-03: Had coffee, discussed job market
   ```
3. Update `last_contact` in frontmatter

**Significant meeting:**
1. Create meeting note in `Timestamps/Meetings/YYYY-MM-DD Meeting with @Person.md`
2. Link to person with `[[@FirstName LastName]]`
3. The Dataview query in person note will auto-show it
4. Update `last_contact` in frontmatter

**Daily journal mention:**
- Just use `[[@Person]]` in daily note
- Dataview will pick it up

### Update People MOC

The MOC should be organized by groups:

```markdown
# People

## Family
- [[@Mom]]
- [[@Dad]]

## Friends - Australia
- [[@Friend Name]]

## Friends - China
- [[@朋友名]]

## Professional Contacts
- [[@Colleague Name]] - Company, Role

## Service Providers
- [[@Doctor Name]] - Specialty
- [[@Accountant Name]]
```

**When adding new contact to MOC:**
1. Determine appropriate group from relationship/location
2. Add link in correct section
3. Optionally add brief description

## Contact Logging Decision Tree

```
New interaction with @Person?
    │
    ├── Quick (< 5 min, casual)
    │   └── Add to Quick Log section in @Person note
    │
    ├── Significant (meeting, important call, event)
    │   └── Create separate note in Timestamps/Meetings/
    │       └── Link to @Person in that note
    │
    └── Just mentioning in daily reflection
        └── Use [[@Person]] in daily journal
```

---

## Relationship Analysis Workflows

### 1. Relationship Health Check

**User asks**: "How are my relationships doing?" / "Relationship health report"

**Claude workflow**:
1. Scan all people notes for `last_contact`, `trust_level`, `energy`, `reciprocity`
2. Categorize relationships:
   - **Neglected gems**: High trust (7+) + last_contact > 3 months + positive energy
   - **Energy drains**: energy=drains + contact in last month
   - **One-sided**: reciprocity=low + you initiated last 3 contacts
   - **Thriving**: Regular contact + high trust + gives energy
3. Generate report with actionable suggestions

**Example output**:
```
📊 Relationship Health Report

🌟 NEGLECTED GEMS (reach out soon!)
- @Jun Ma - Trust 8/10, last contact 4 months ago
- @田海亭 - Trust 8/10, last contact 3 months ago

⚡ ENERGY GIVERS (nurture these!)
- @Geemeng Chew - Regular contact, always energizing

⚠️ NEEDS ATTENTION
- @X - Low reciprocity, consider adjusting expectations

💡 Suggested actions:
1. Message Jun Ma this week
2. Schedule coffee with 田海亭
```

### 2. Person Deep Dive

**User asks**: "Tell me about @Jun Ma" / "Analyze my relationship with @Person"

**Claude workflow**:
1. Read person note thoroughly (frontmatter + all sections)
2. Search vault for all mentions:
   - Journal entries mentioning them
   - Meeting notes with their name
   - Tasks involving them
3. Synthesize:
   - **Relationship timeline**: How long, key milestones
   - **Interaction patterns**: Frequency, types of contact, who initiates
   - **Emotional patterns**: When do you feel good/drained after contact?
   - **What you've learned**: Personality insights, values, interests
4. Suggest next actions based on relationship_goal

### 3. Network Analysis

**User asks**: "Who can help me with X?" / "Who knows about Y?"

**Claude workflow**:
1. Parse query to identify need (skill, industry, location, etc.)
2. Scan people notes for:
   - `shared_interests` matching query
   - `company` or `title` relevant to query
   - Notes section mentioning relevant expertise
3. Filter by relationship quality:
   - Trust level 6+ (can ask for help)
   - Recent enough contact (or good reason to reconnect)
4. Suggest who to reach out to and how to frame the ask

**Example**:
```
User: Who can help me with job hunting in Sydney?

Claude: Based on your network:

1. @田海亭 (Trust 8/10)
   - Energy industry senior manager
   - Connection: Professional contact from NZ
   - Approach: Ask about industry landscape

2. @Jun Ma (Trust 8/10)
   - Based in Sydney area
   - Recently explored Ashfield/Burwood together
   - Approach: Casual catch-up, mention you're exploring options
```

### 3b. Network Mapping

**User asks**: "Map my network" / "Show my network by industry" / "Network overview"

**Claude workflow**:
1. Scan all people notes
2. Group by:
   - **Industry/Sector**: company field, title field
   - **Location**: location field
   - **Relationship type**: tags (professional, friend, family)
   - **Strength**: trust_level + recency of contact
3. Generate visual map (text-based)

**Example output**:
```
📊 NETWORK MAP

BY SECTOR:
├── Energy (2)
│   ├── @田海亭 ⭐⭐⭐ (Trust 8, last: 3mo ago)
│   └── [gap: need Australian energy contacts]
├── Tech/IT (1)
│   └── @researcher-unisa ⭐⭐ (Trust 6)
├── Academia (1)
│   └── @researcher-unisa
└── Other/Personal (15)
    └── Friends, board game group, etc.

BY LOCATION:
├── Sydney: @Jun Ma, ...
├── Beijing: @田海亭, @JU Li Beijing, ...
└── Adelaide: ...

NETWORK STRENGTH:
├── Strong (trust 8+): 3 contacts
├── Medium (trust 5-7): 5 contacts
└── Weak/Unknown: 12 contacts
```

### 3c. Network Gaps Analysis

**User asks**: "Where are my network gaps?" / "What's missing in my network?"

**Claude workflow**:
1. Define target areas based on user's goals (career, industry, location)
2. Scan existing contacts by sector/purpose
3. Identify gaps:
   - Industries with 0-1 contacts
   - Missing "purpose" contacts (mentor, referral source, etc.)
   - Geographic gaps if relevant
4. Suggest how to fill gaps

**Example output**:
```
🔍 NETWORK GAPS ANALYSIS

Based on your career goals:

GAPS IDENTIFIED:
1. Australian Energy Sector
   - You have: @田海亭 (Beijing-based)
   - Missing: Local Australian energy professionals
   - Action: Check Engineers Australia events, LinkedIn search

2. Mentorship
   - Current: No designated mentor
   - Action: Identify senior professional for guidance

3. Tech/Startup
   - You have: 0 contacts
   - Action: Attend meetups, connect via LinkedIn

WELL-COVERED:
✓ Friends in Sydney area
✓ Academic connections (UniSA)
```

### 3d. Introduction Chains

**User asks**: "How can I reach [person/company/role]?" / "Who can introduce me to X?"

**Claude workflow**:
1. Identify target (person, company, industry)
2. Search contacts for:
   - Direct connection to target
   - Works at target company
   - In same industry as target
3. Map introduction chain possibilities
4. Suggest approach and talking points

**Example**:
```
User: How can I reach someone at ElectraNet?

Claude: Introduction chain options:

1. DIRECT PATH:
   @田海亭 → Energy industry connections
   - They work in energy sector
   - Could ask: "Do you know anyone at ElectraNet or similar utilities?"

2. INDIRECT PATH:
   Engineers Australia → Industry events
   - ElectraNet engineers likely attend
   - Action: Check upcoming EA events in Adelaide

3. COLD OUTREACH:
   - See networking templates in [[Networking]]
```

### 3e. Networking Strategy Advisor

**User asks**: "How should I approach networking for X goal?" / "Networking advice for [situation]"

**Claude workflow**:
1. Understand the goal (job search, industry switch, building presence)
2. Reference [[Networking]] note for strategies
3. Analyze current network against goal
4. Provide personalized advice

**Example**:
```
User: I want to transition into data analytics

Claude: Networking strategy for data analytics transition:

1. LEVERAGE EXISTING:
   - @researcher-unisa has academic data background
   - Ask about industry contacts, recommended communities

2. BUILD NEW CONNECTIONS:
   - Join Adelaide Data Science meetup
   - Connect with data analysts on LinkedIn (personalize!)
   - Follow data analytics content creators

3. WARM UP DORMANT:
   - @Geemeng Chew (tech background) - haven't talked in 4 months
   - Good reconnection opportunity

4. TEMPLATES:
   - See [[Networking#Cold Outreach]] for approach templates

5. TRACK:
   - Set goal: 3 new data analytics contacts this month
   - Log in respective people notes
```

### 4. Relationship Retrospective

**User asks**: "How has my relationship with @Person evolved?" / "Retrospective on @Person"

**Claude workflow**:
1. Gather all data points:
   - Quick Log entries (chronological)
   - Meeting notes mentioning them
   - Journal entries
   - Relationship Health table scores
2. Analyze trends:
   - Contact frequency over time (increasing/decreasing?)
   - Trust level changes
   - Energy patterns
   - Key moments (positive and negative)
3. Present timeline and insights
4. Suggest: continue current pattern, invest more, or adjust expectations

### 5. Pre-Interaction Briefing

**User asks**: "I'm meeting @Person tomorrow" / "Prep me for seeing @Person"

**Claude workflow**:
1. Pull key info from person note:
   - Last interaction (what did you discuss?)
   - Things to remember (family names, sensitivities)
   - Current focus (what are they dealing with?)
   - Patterns (how do they prefer to communicate?)
2. Check for pending items:
   - Tasks related to them
   - Things you promised to do
   - `next_action` field
3. Generate briefing card

---

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

## Proactive Suggestions

Claude should proactively offer based on context:

| Trigger | Suggestion |
|---------|------------|
| Monthly review | "3 friends you haven't contacted in 3+ months" |
| After logging interaction | "Want to update their personality notes or trust level?" |
| Recurring patterns | "You've cancelled on @X twice recently - everything okay with that relationship?" |
| Before important dates | "@Jun's birthday is in 3 days" |
| After conflict logged | "Would a relationship retrospective help process this?" |
| Meeting scheduled | "Want a briefing card for your meeting with @Person?" |
| New person mentioned 3+ times | "You mention @NewPerson often - want to create a note for them?" |

---

## Obsidian CLI (Quick People Lookups) — with Fallbacks

```bash
OBS="bash 99_Attachments/scripts/obs.sh"

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

## Integration with Other Skills

- **knowledge-synthesis**: Find what you know about a person across all notes
- **task-aggregator**: Find tasks related to a person
- **cleanup-studio**: Identify stub person notes that need more info
- **obsidian-calendar-planner**: Birthday reminders, contact scheduling
