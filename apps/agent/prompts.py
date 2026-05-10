"""Prompt constants for the agent app.

Kept separate so app.py stays focused on the tool-use loop + API surface.
"""

from __future__ import annotations

SESSION_ARCHIVE_SYSTEM = """\
You are a senior software engineer writing a session-memory note.
Summarize the conversation into a concise, skimmable reference.
Output ONLY valid Markdown — no prose preamble, no code fences wrapping the whole response.
Do NOT invent details not present in the conversation."""

SESSION_ARCHIVE_PROMPT = """\
Summarize this agent session in a structured Markdown note with the following sections
(omit any section that has nothing to say):

## Goal
One sentence: what the user was trying to accomplish.

## What Was Done
Bullet list of the concrete actions taken (files created/edited, APIs added, bugs fixed).

## Key Decisions
Bullet list of non-obvious decisions or tradeoffs made.

## Artifacts
Bullet list of file paths created or significantly changed, with a one-line description each.
Use plain paths, not vault-viewer URI links.

## Outcome
One sentence: was the goal achieved? Any caveats or follow-up needed?

---
CONVERSATION (role: content):
{conversation}"""


CLASSIFY_SYSTEM = """\
You are a task classifier. Given a user request, output ONLY a JSON object — no prose, no fences."""

CLASSIFY_PROMPT = """\
Classify this request in one pass.

Request: {user_text}

Output ONLY:
{{"task_type": "debug|build|explain|refactor|review|other", "subject": "...", "scope": "file|module|system"}}

task_type rules:
  debug   — fixing broken behaviour or an error
  build   — adding a new feature, app, endpoint, or UI
  explain — understanding code or answering a question
  refactor — restructuring existing code without changing behaviour
  review  — assessing quality, correctness, or risks
  other   — anything else
subject: 3-6 words naming the thing being worked on.
scope: file (1-2 files), module (1 app/plugin), system (cross-app or architectural)."""

ORIENT_SYSTEM = """\
You are a senior EmptyOS architect. Given a classified task, its context, and the project rules,
output a short JSON object — nothing else, no markdown fences, no prose.
Be terse. Every field must fit on one line."""

ORIENT_PROMPT = """\
Task type: {task_type}
Subject: {subject}
Scope: {scope}

Request: {user_text}

{past_sessions}

Relevant CLAUDE.md rules ({task_type} tasks — focus on these):
{rules_text}

Relevant CLAUDE.md gotchas:
{gotchas_text}

Output ONLY a JSON object with these four keys (all required):
{{
  "relevant_rules": ["Rule N: ...", ...],   // 1-4 rules most relevant to this {task_type} task
  "investigation_plan": ["step 1", ...],    // 2-5 concrete first actions suited to {task_type}
  "success_criteria": "...",                // one sentence — what done looks like
  "risk_flags": ["...", ...]                // 0-2 gotchas likely to bite this {task_type} task
}}"""
