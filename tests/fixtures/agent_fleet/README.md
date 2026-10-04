# agent_fleet hook payload fixtures

**REAL captured payloads** from Claude Code on this machine (2026-07-17), not
synthetic. Content fields (`prompt`, `last_assistant_message`, `message`,
`transcript_path`) are redacted and session ids anonymised — the *shape* is
what matters and it is intact.

`claude-events.jsonl` — 84 events across 7 sessions: {"Notification": 21, "SubagentStop": 21, "UserPromptSubmit": 20, "Stop": 16, "SessionStart": 4, "SessionEnd": 2}

## What these proved (and why guessing was not good enough)

1. **`Stop` is a TURN end, not a session end.** A captured session runs
   `Notification -> SubagentStop -> UserPromptSubmit -> Stop -> Notification`
   — it keeps going *after* Stop. A reducer that ends a session on Stop is
   wrong. This is plan finding [R3], now confirmed against real data.
2. **There is no `turn_id`.** Claude sends **`prompt_id`**.
3. **`notification_type` is a native field**, values seen: `permission_prompt`
   and `idle_prompt`. Do NOT infer it from message prose — "Claude is waiting
   for your input" is `idle_prompt`, and a text heuristic reads it as a
   permission gate, producing false `blocked` alerts.
4. **`SessionStart.source`** is Claude's own field ("startup"/"resume"/"clear")
   and collides by name with the envelope's `source` (claude|codex). Unrelated.
5. **`SessionEnd` DOES fire** (resolved on the second capture — 2 observed).
   Both carried `reason: "clear"`, and `/clear` **retires the old session_id
   and opens a new one in the same second**. So marking the old record `ended`
   is correct: that conversation did end, and the human's still-open terminal
   reappears as the fresh session rather than a ghost.
   **But do not conclude the TTL is optional.** Both observations were graceful
   `/clear`s. An abruptly-killed terminal is not known to emit SessionEnd, and
   Codex has no documented SessionEnd at all — the TTL sweep remains the only
   guaranteed terminator for both sources.
6. Content fields confirmed present in real payloads: `prompt`,
   `last_assistant_message`, `transcript_path`, `message`. The envelope
   allowlist exists to keep every one of them out of the daemon.

Fields available but deliberately NOT in the frozen envelope (candidates for a
later, agreed extension — do not add unilaterally): `model` (e.g.
`claude-fable-5`, would make a nice fleet column), `permission_mode`,
`stop_hook_active`, `agent_type`, `effort`.
