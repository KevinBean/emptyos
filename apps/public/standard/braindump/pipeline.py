"""braindump — the transcribe → summarize → extract_actions pipeline.

Three module-level stage callables (taking a ``StageContext``, reaching the app
via ``ctx.app``) driven through ``emptyos.sdk.pipeline.Pipeline`` — the same
shape as ``apps/personal/podcast/pipeline.py``. Owns: the stage definitions, the
two LLM prompts (named UPPERCASE constants, CLAUDE.md rule 12), and the
``_run_pipeline`` / ``_extract_to_proposals`` drivers bound onto the app.

The pipeline pauses after ``summarize`` (``stop_after="summarize"``) so the user
reviews + edits the summary before any action items are extracted. On resume the
edited summary flows in via ``inputs["summary_override"]`` and the completed
``transcribe``/``summarize`` stages are skipped (resume-from-partial).

Nothing here writes vault state. ``extract_actions`` returns a plan; the app's
``_extract_to_proposals`` files each action through the rooms review gate via
``emptyos.sdk.capture_routes.propose_capture`` (the shared task/journal/kb
destination table, also read by quick-action) — the user clicks Apply.

Cross-module callers reach these via ``self.X`` after re-binding in app.py.
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk.capture_routes import propose_capture
from emptyos.sdk.pipeline import Pipeline, Stage
from emptyos.sdk.utils import parse_llm_json

if TYPE_CHECKING:  # for type hints only
    from .app import BrainDumpApp  # noqa: F401


# ─── Bind to BrainDumpApp class as ───────────────────────────────────────────
#   _run_pipeline          = _pipeline._run_pipeline
#   _resume_pipeline       = _pipeline._resume_pipeline
#   _run_status            = _pipeline._run_status
#   _extract_to_proposals  = _pipeline._extract_to_proposals
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────────────


CAPTURE_SUMMARY_SYSTEM = """You are a faithful note-taker. You receive a raw \
spoken or typed brain-dump and condense it into a tight summary the user will \
read back as their own note.

Write 2–5 sentences in the user's own register. Preserve concrete names, dates, \
and numbers verbatim. Neutral first-person-of-the-user voice — as if THEY wrote it.

Do NOT:
- invent facts, commitments, or dates that are not in the dump
- editorialize, advise, or add a "next steps" / "action items" section (a later \
step extracts those, and the user reviews them)
- address the user as "you" — this is their note
- pad to length. A one-line dump stays one line."""


CAPTURE_EXTRACT_SYSTEM = """You turn a brain-dump into a SMALL set of concrete, \
reviewable action items. Return STRICT JSON and nothing else:

{"actions": [ ... ]}

Each action is exactly one of:
- {"type": "task",    "text": "<imperative>", "due": "<YYYY-MM-DD|today|tomorrow|>"}
- {"type": "journal", "text": "<single-line reflection>", "mood": "<great|good|okay|low|bad>"}
- {"type": "kb",      "title": "<short>", "kind": "<concept|lesson|formula|case|reference>", "body": "<the durable fact/insight>", "topic": "<optional>"}

Rules:
- Return AT MOST {MAX_ACTIONS} actions; prefer 2–4. An empty array is a correct, \
valid answer when the dump has no actionable content.
- Do NOT emit an action the dump does not clearly support.
- Do NOT invent due dates; set "due" only when the user stated a time, else "".
- A reflection/feeling is a journal entry, NOT a task. A durable fact or insight \
worth keeping is a kb note. Do not classify everything as a task.
- kb "kind" must be one of the listed kinds; default to "lesson" when unsure."""


# --- Stage callables (module-level; reach the app via ctx.app) ---

async def _cs_transcribe(ctx) -> dict:
    """Audio mode → run the completed recording through the listen chain; text
    mode → passthrough. The capability chain self-routes + consent-gates; this
    stage never picks a provider."""
    app, i = ctx.app, ctx.inputs
    mode = i.get("mode") or "text"
    if mode == "audio":
        # Live recording or uploaded file — both land as a raw.<ext> artifact.
        name = i.get("audio_artifact") or "raw.webm"
        p = ctx.handle.artifact(name)
        if not p.exists():
            raise RuntimeError(f"audio mode but no {name} artifact in the run")
        audio_bytes = p.read_bytes()
        transcript = (await app.listen(audio=audio_bytes) or "").strip()
    else:
        transcript = (i.get("text") or "").strip()
    if not transcript:
        raise RuntimeError("empty transcript")
    ctx.write_artifact("transcript.txt", transcript)
    import asyncio
    asyncio.create_task(app.emit("braindump:transcribed",
                                 {"run_id": ctx.handle.run_id, "chars": len(transcript)}))
    return {"transcript": transcript, "mode": mode, "chars": len(transcript)}


async def _cs_summarize(ctx) -> dict:
    """One faithful-condensation think call. This is the stop_after pause point —
    the app drives start(stop_after="summarize") and the UI shows the result in
    an editable box before extract_actions runs."""
    app = ctx.app
    transcript = (ctx.result("transcribe") or {}).get("transcript", "")
    summary = (await app.think(transcript, domain="text", system=CAPTURE_SUMMARY_SYSTEM,
                               temperature=0.3) or "").strip()
    if not summary:
        summary = transcript[:600]  # degrade, never block the run
    ctx.write_artifact("summary.md", summary)
    return {"summary": summary}


async def _cs_extract_actions(ctx) -> dict:
    """Extract typed action items from the (possibly user-edited) summary +
    transcript, then file each through the review gate. Prefers an edited
    summary passed on resume via inputs["summary_override"]."""
    app, i = ctx.app, ctx.inputs
    summary = (i.get("summary_override")
               or (ctx.result("summarize") or {}).get("summary", "")).strip()
    transcript = (ctx.result("transcribe") or {}).get("transcript", "")
    max_actions = int(app.setting("braindump.max_actions", 6) or 6)

    system = CAPTURE_EXTRACT_SYSTEM.replace("{MAX_ACTIONS}", str(max_actions))
    user = f"SUMMARY:\n{summary}\n\nFULL TRANSCRIPT:\n{transcript}"
    raw = await app.think(user, domain="reason", system=system, temperature=0.2)
    parsed = parse_llm_json(raw, fallback={"actions": []})
    actions = parsed.get("actions") if isinstance(parsed, dict) else parsed
    if not isinstance(actions, list):
        actions = []
    actions = actions[:max_actions]

    proposed = await app._extract_to_proposals(actions)
    import asyncio
    asyncio.create_task(app.emit("braindump:proposed",
                                 {"run_id": ctx.handle.run_id, "count": len(proposed)}))
    return {"proposed": proposed, "count": len(proposed)}


_CAPTURE_STAGES = [
    Stage("transcribe", _cs_transcribe, weight=3),
    Stage("summarize", _cs_summarize, weight=2),
    Stage("extract_actions", _cs_extract_actions, weight=2),
]


# --- Drivers bound onto the app ---

def _pipe(self) -> Pipeline:
    return Pipeline(app=self, name="braindump", stages=_CAPTURE_STAGES)


async def _run_pipeline(self, inputs: dict, *, run_id: str | None = None) -> dict:
    """Start a run, pausing after summarize for review. Returns the run summary.

    Pass ``run_id`` when the caller already minted a handle to pre-write an
    artifact (audio mode writes ``raw.webm`` before the pipeline runs)."""
    return await _pipe(self).start(inputs, run_id=run_id, stop_after="summarize")


async def _resume_pipeline(self, run_id: str, *, summary_override: str = "") -> dict:
    """Continue a paused run through extract_actions, applying a user-edited summary."""
    extra = {"summary_override": summary_override} if summary_override else None
    return await _pipe(self).resume(run_id, inputs=extra)


def _run_status(self, run_id: str) -> dict | None:
    """Poll read of the persisted run summary (None if the run doesn't exist)."""
    return _pipe(self).get(run_id)


async def _extract_to_proposals(self, actions: list) -> list[dict]:
    """File each typed action through the rooms review gate. Returns a light list
    of {action_id, type, verb, summary} for the UI. Nothing auto-applies — every
    item lands in the pending queue for explicit Apply."""
    out: list[dict] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        row = await propose_capture(self, a.get("type", ""), a, source="braindump")
        if row:
            out.append(row)
    return out
