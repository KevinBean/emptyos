# People Manager — Example Outputs

Example dialogues and output blocks moved out of the workflow step lists in SKILL.md. Read the relevant section when you need to see the expected shape of a response.

## Create New Contact — Example

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

## Relationship Health Check (Workflow 1) — Example output

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

## Network Analysis (Workflow 3) — Example

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

## Network Mapping (Workflow 3b) — Example output

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

## Network Gaps Analysis (Workflow 3c) — Example output

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

## Introduction Chains (Workflow 3d) — Example

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

## Networking Strategy Advisor (Workflow 3e) — Example

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
