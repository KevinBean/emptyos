# Untrusted-Content Fencing — fetched text is data, not instructions

Any pipeline that interpolates **fetched external text** (web pages, search
results, RSS items, transcripts) into a `think()` prompt lets a hostile page
speak with the same authority as the app's own task ("ignore previous
instructions, instead…"). The fence makes the trust boundary explicit at the
prompt layer: each excerpt is wrapped in markers, and the system prompt carries
a clause telling the model the fenced content is data-only.

**Lineage:** borrowed 2026-06-12 from Odysseus (`src/prompt_security.py`,
pewdiepie-archdaemon/odysseus) via `/eos-repo-extract`. We kept the discipline
(user-role placement, one choke-point, marker escaping), not their byte-exact
markers or their prose.

**Shared core:** `emptyos/sdk/web_search.py` — `source_fencer(app)` →
`SourceFencer` (the consumer-facing kit: reads the dark flag once, then
`.system(base)` appends the clause and `.wrap(text, label=)` fences a block;
both no-op when disabled). Underneath: `untrusted_block(content, label="")`
(pure fence + defang) + `UNTRUSTED_SOURCE_CLAUSE` (the system-prompt clause).
All re-exported from `emptyos.sdk`. Tests: `tests/test_sdk_web_search.py`.

**Consumers (all dark-flagged):** `apps/assistant/research.py`,
`apps/voice-assistant/research.py`, `apps/explore/` (`_source_prompt_block`
takes the fencer — synthesize, digest, stance), `apps/daily-brief/`
(`_distill` headline block).

## The four moves (all required together)

1. **Untrusted content goes in the user message, never the system prompt.**
   Injecting fetched text into the system role is a security bug, fenced or
   not. The clause is the only injection-related text that belongs in
   `system=`.
2. **One choke-point.** Go through `source_fencer(app)`; don't hand-roll
   per-app header strings or flag reads — they drift and the clause stops
   matching the markers.
3. **Fence + clause are a pair.** The markers mean nothing unless the system
   prompt carries `UNTRUSTED_SOURCE_CLAUSE` naming them. `SourceFencer`
   guarantees the pairing — `.system()` and `.wrap()` flip together.
4. **Marker escaping is built in.** `untrusted_block` defangs any literal
   `<<<eos:` inside the payload (and strips angle brackets from the label) so
   content can't close its own fence or open a fake one. Don't bypass it by
   concatenating markers manually.

## Usage shape

```python
from emptyos.sdk.web_search import source_fencer

fencer = source_fencer(self)            # reads the dark flag once per request
system = fencer.system(MY_SYSTEM)       # + clause iff enabled
for s in sources:
    text = fencer.wrap(s["text"], label=site_label(s["url"]))  # fence iff enabled
    prompt_parts.append(f"[{s['n']}] {s['title']}\n{text}")
```

## Dark flag (per `feature_pipeline_flag_default_dark`)

Every consumer gates on `[apps.<id>] feature.untrusted-wrap.enabled`
(default false, read via `app_config`). Flag off → prompts are
**byte-identical** to pre-fence output; that's the regression contract.
Rationale for shipping dark: the fence changes prompt shape, which can shift
synthesis quality per model — flip per-app after eyeballing output. Once
proven across the research apps, collapse the flag and make fencing
unconditional (a later session).

## Scope honesty — what this is NOT

- **Not a sandbox.** It's a prose-level defense that raises the bar; a
  sufficiently steered model can still ignore it. Action-layer protection is
  the real floor: the review gate (`.claude/rules/room-review-gate.md`), the
  autopilot eligibility floor (`.claude/rules/autopilot-grants.md`), and SSRF
  guards (`is_public_web_url`). Those gates do not relax because content is
  fenced.
- **Not for vault content.** The vault is trusted (SECURITY.md § Threat
  model); fencing the user's own notes adds noise, not safety. KB digest of
  user-pasted text is semi-trusted — the user curates the paste — and stays
  unfenced today.
- **Not for tool-loop output.** Agent/rooms tool results have their own
  trust path (review gate on actions). If a future audit wants tool output
  fenced too, that's a separate consumer of the same helper, not a new
  mechanism.

## When to fence a new consumer

Fence when **all** hold: the text arrives from outside the machine (web, feed,
email, third-party API), the app didn't author it, and it lands inside a
`think()` prompt. New web-research consumers should call the helper from day
one (behind the same flag until the flag collapses).

Don't fence: capability *outputs* the user already reviews (proposed-action
previews), per-item classification of user-owned data, or anything where the
"source" is EmptyOS itself.

## Cross-references

- `emptyos/sdk/web_search.py` — helper + clause + the SSRF/anti-bot guards
  that compose with this (different axis: where the browser may go vs what
  the model may believe).
- `SECURITY.md § Threat model` — prompt injection via fetched content listed
  as a cared-about threat; this rule is the mitigation record.
- `.claude/rules/autopilot-grants.md` / `room-review-gate.md` — the action
  layer that holds when prose defense fails.
