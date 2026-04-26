"""Model Bench — prompt fixtures and builders.

Extracted from model-bench/app.py to keep the core under 800 lines (P4 Atomic).
Fixtures are module-level constants; builder methods are bound to ModelBenchApp
via attribute assignment in app.py.
"""

from __future__ import annotations

_FIXTURE_INBOX = "I should look into renewable energy certificates for the project"

_FIXTURE_TASKS = [
    "- [ ] Draft Q2 proposal for client review",
    "- [ ] Fix the broken deploy pipeline #dev",
    "- [ ] Call dentist about appointment",
    "- [ ] Read the paper on retrieval-augmented generation",
    "- [ ] Write blog post about vault-first workflow",
    "- [ ] Buy groceries",
    "- [ ] Pay electricity bill (due Friday)",
    "- [ ] Respond to Alex about the project scope",
]

_FIXTURE_NOTE = (
    "# Weekly Planning\n\n"
    "This week's focus: shipping the v2 of the publish app. Three sub-goals — "
    "finish the draft/publish flow, wire up the site-profile switcher, and land "
    "the cover-image generation pipeline. Everything else (podcast, vault cleanup, "
    "review features) is parked until v2 ships. Risk: the site switcher depends on "
    "a multi-site settings schema that isn't designed yet."
)

_FIXTURE_TITLES = [
    "Vault-first note-taking philosophy (vault/vault-first.md)",
    "Why I stopped using flashcards (notes/no-flashcards.md)",
    "Building EmptyOS — a mind companion (projects/emptyos.md)",
    "Cable sizing for industrial reticulation (30_Resources/cable-sizing.md)",
    "Retirement planning for expats (finance/retirement-expat.md)",
    "How local LLMs changed my workflow (tech/local-llm.md)",
    "Anxious attachment and self-soothing (healing/anxious.md)",
    "A debugging checklist I actually follow (dev/debug-checklist.md)",
    "Why atomic notes compound (notes/atomic-notes.md)",
    "One-shot cron vs. running daemon (systems/cron-vs-daemon.md)",
]

_FIXTURE_PARAGRAPH = (
    "The thing about local LLMs is that they're fast enough for classification "
    "and short transformations but they stumble on anything requiring careful "
    "multi-step reasoning. So the real skill is knowing which task goes where."
)

_FIXTURE_QA_CONTEXT = (
    "# Project: publish v2\n\n"
    "## Goals\n- ship draft/publish flow\n- site-profile switcher\n- cover-image pipeline\n\n"
    "## Risks\n- site switcher depends on undesigned multi-site schema\n"
    "- cover generation blocked by comfyui plugin latency\n\n"
    "## Deadline\n- internal demo: end of month\n"
)

_FIXTURE_USAGE = (
    "Recent LLM usage (last 7 days):\n"
    "- ollama: 420 calls, avg 1.8s, 100% local\n"
    "- openai: 52 calls, avg 2.3s, $0.47 total\n"
    "- claude-cli: 18 calls, avg 6.1s, free tier\n"
    "Top apps: assistant (38%), publish (21%), capture (14%)."
)

_FIXTURE_APP_BRIEF = (
    "App id: habit-tracker\n"
    "Description: Track daily habits with streak counts and a simple web UI. "
    "Uses read/write capabilities to store checks in a markdown file per month."
)


# ── Prompt builders ──────────────────────────────────────────

async def _prompt_classify(self) -> str:
    return (
        "Classify this inbox capture into ONE tag: idea, task, note, link, "
        "question, reminder, dev. Return ONLY the tag word, nothing else.\n\n"
        + _FIXTURE_INBOX
    )


async def _prompt_rank(self) -> str:
    tasks = await self._live_tasks() or _FIXTURE_TASKS
    return "Prioritize these tasks for today. Be brief — return a numbered list.\n\n" + "\n".join(tasks)


async def _prompt_json_extract(self) -> str:
    titles = await self._live_titles() or _FIXTURE_TITLES
    return (
        "These are notes from my vault. Suggest 5 that would make the best blog posts. "
        "Consider: technical depth, uniqueness, public interest, practical value.\n\n"
        'Return as JSON array: [{"title": "...", "pitch": "one-line why", '
        '"type": "blog|tutorial|project", "path": "original/path.md"}]\n\n'
        "Notes:\n" + "\n".join(f"- {t}" for t in titles)
    )


async def _prompt_summarize(self) -> str:
    note = await self._live_note() or _FIXTURE_NOTE
    return f"Summarize this note in 2-3 sentences:\n\n{note}"


async def _prompt_rewrite(self) -> str:
    return (
        "Improve this text: fix grammar, improve flow and clarity, "
        "keep the original meaning and tone. Do NOT add new content or change "
        "the argument. Return ONLY the improved text.\n\n" + _FIXTURE_PARAGRAPH
    )


async def _prompt_draft(self) -> str:
    return (
        "Write a single short paragraph (3-4 sentences) explaining what "
        "an atomic note is and why it's useful. Direct, no hedging. "
        "Return ONLY the paragraph."
    )


async def _prompt_qa(self) -> str:
    note = await self._live_note() or _FIXTURE_QA_CONTEXT
    return f"Based on this note, what are the main risks and the deadline?\n\n{note}"


async def _prompt_reason(self) -> str:
    return (
        "You are analyzing LLM usage data to suggest cost/quality tradeoffs.\n"
        + _FIXTURE_USAGE
        + "\n\nIn 3 bullets: (1) what should move to local, (2) what must stay cloud, "
        "(3) one concrete change to try this week."
    )


async def _prompt_code_gen(self) -> str:
    return (
        "Generate a manifest.toml for an EmptyOS app.\n\n"
        f"{_FIXTURE_APP_BRIEF}\n\n"
        "Rules:\n"
        "- Required sections: [app], [app.entry], [requires], [provides.cli], [provides.web]\n"
        "- app.entry must set module and class\n"
        "- requires.capabilities is a list (e.g. [\"read\", \"write\"])\n"
        "- Return ONLY the TOML content, no markdown fences, no commentary."
    )


PROMPT_BUILDERS = {
    "text/classify":     "_prompt_classify",
    "text/rank":         "_prompt_rank",
    "text/json-extract": "_prompt_json_extract",
    "text/summarize":    "_prompt_summarize",
    "text/rewrite":      "_prompt_rewrite",
    "text/draft":        "_prompt_draft",
    "text/qa":           "_prompt_qa",
    "text/reason":       "_prompt_reason",
    "code/code-gen":     "_prompt_code_gen",
}
