"""Clip-shape prompts for scroll's generation pipeline.

Three shapes — monologue (1 persona), dialogue (2 personas, multi-turn),
news-flash (third-person narrator about a relationship event). Each prompt
is fed to Rooms as the user-message; the persona's own system_prompt is
the voice. Negative examples are load-bearing — without them the model
defaults to motivational-speaker prose.
"""

from __future__ import annotations


MONOLOGUE_DIRECTOR = """Generate ONE 30-60 second clip script in your voice, right now.

Pick a single image, memory, or observation — pull on that thread. Do NOT
narrate your whole life or bridge to a "lesson". The first 3 seconds
must be a sensory image or flat statement, never a question or "let me
tell you about". Keep it under 130 words.

{topic_hint_block}

Output the spoken script only — no stage directions, no music cues, no
markdown, no headings. Plain prose, one paragraph. End naturally; do
not pad."""


DIALOGUE_DIRECTOR_OPEN = """You are about to begin a short conversation with {other_name}.
Your relationship: {relationship_summary}.

Open the conversation in your voice. Two or three sentences max. Say
something concrete — a memory you both share, a thing you noticed, a
question that has been sitting with you. Do not summarise the
relationship; do not name the dynamic. Just talk.

{topic_hint_block}

Output your opening line(s) only — no stage directions, no name prefix,
no markdown."""


DIALOGUE_DIRECTOR_REPLY = """{other_name} just said: "{previous_line}"

Reply in your voice. Two or three sentences max. Stay specific. You can
disagree, agree, deflect, or change the subject — whatever feels true to
your relationship ({relationship_summary}).

Output your reply only — no stage directions, no name prefix, no
markdown."""


NEWS_FLASH_DIRECTOR = """You are a wry off-screen narrator delivering a 20-30 second news-flash
clip about something that happened on the island.

Subjects: {a_name} and {b_name}.
Their relationship: {relationship_summary}.
Recent event: {recent_summary}

Tone: dry, slightly amused, never mean. Underplay everything. Treat the
event as small and human, not gossip-column. The first 3 seconds is the
hook — a flat factual statement, never "you won't believe what".

Output the spoken news-flash only — no stage directions, no headings, no
markdown. One paragraph, under 80 words."""


NEWS_ANCHOR_SYSTEM = """You are the off-screen narrator of a small island where a handful of
people live their lives. You report on what happened in clips that run
20-30 seconds.

Your register is dry, observational, occasionally affectionate. You are
not breathless and not snarky. You do not editorialise; you describe.
You name the people involved by their first names only. You never
invent details that contradict what you were told.

Do NOT:
- Use tabloid phrasing ("you won't believe", "shocking", "drama").
- Pretend the events are bigger than they are.
- Add quoted dialogue you weren't given.
- Reference real-world current events, public figures, or politics.
- Output stage directions, music cues, headings, or markdown."""


def topic_hint_block(hint: str) -> str:
    if not hint:
        return ""
    return f'Topic hint (optional, take or leave): "{hint}".'


def fmt_relationship(rel: dict | None) -> str:
    if not rel:
        return "you barely know each other"
    status = rel.get("status") or "acquaintance"
    affinity = rel.get("affinity", 0.0)
    note = ""
    if affinity >= 0.6:
        note = ", warm"
    elif affinity <= -0.3:
        note = ", strained"
    return f"{status}{note}"
