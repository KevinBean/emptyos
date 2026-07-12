"""worklog-capture — prompt constants + registry declaration.

Pure constants only (per .claude/rules/prompt-management.md); the /prompts app
imports this module standalone, so no import side effects.
"""
from __future__ import annotations

from emptyos.sdk.prompt_registry import declare_prompts

# Distinct from worklog's SMART_LOG_SYSTEM: that prompt refines ONE clean
# user-authored sentence and forbids rewriting. Capture input is noisy — OCR
# text + window chrome + a cross-checked activity trail — so this prompt
# SUMMARIZES a cluster of evidence into one worklog line and infers the
# project/employer from what the whole cluster shows.
CAPTURE_DRAFT_SYSTEM = (
    "You draft ONE work-log entry from a cluster of evidence about what the "
    "user was doing at a point in time: an on-screen text excerpt (OCR, may be "
    "noisy), the active app and window title, an optional browser URL, and an "
    "optional trail of concurrent AI-coding sessions and web visits.\n\n"
    "Output STRICT JSON only:\n"
    '{"project": str, "text": str, "status": str, "employer": str, '
    '"confidence": str}\n\n'
    "Rules:\n"
    "- project: choose ONLY from the provided known-projects list; if none "
    'clearly fits, use "General". Never invent a project name.\n'
    "- text: one concise past/near-present work line (<= 140 chars) describing "
    "what was actually being worked on — summarize the evidence, do not quote "
    "raw OCR or window chrome. No first person, no filler.\n"
    "- status: one of complete, in-progress, todo, next, waiting, review, "
    "blocked. Default in-progress.\n"
    "- employer: choose ONLY from the provided known-employers list, or \"\" if "
    "the evidence gives no clear signal. When unsure, prefer \"\" — a wrong "
    "employer pollutes timesheet exports.\n"
    '- confidence: "high" | "medium" | "low". Use "low" when the evidence '
    "sources disagree (e.g. the screenshot and the session trail point at "
    "different projects) and name the conflict in the text if it matters.\n\n"
    "Do NOT:\n"
    "- Output anything except the JSON object.\n"
    "- Invent work, projects, or employers not supported by the evidence.\n"
    "- Include the raw OCR text, URLs, or file paths verbatim in the text."
)

PROMPTS = declare_prompts(
    "worklog-capture",
    capture_draft_system=CAPTURE_DRAFT_SYSTEM,
)
