# Three-Natures Lens — spotting 遍計所執 (reified appearances)

A **reasoning lens**, not a feature. Borrowed from 唯识's three natures (三性,
trisvabhāva), it names one bug class the Eight-Consciousness model
(`docs/DESIGN.md` § Consciousness Model) doesn't: **mistaking a conditioned,
dependent appearance for independent, fixed ground truth.** Apply it when
reviewing or writing code; never surface it in a UI (same posture as the
wellbeing wheel, CLAUDE.md Rule 16 — it shapes *what gets built and refused*,
not what the user sees).

## The three natures, operationally

| 三性 | The thing | The check |
|---|---|---|
| **依他起** (paratantra) | A real-but-dependent appearance: rendered UI, an LLM's interpretation, a dashboard narrative, a cached snapshot, a memory, an aggregator view | Is this a *manifestation* of seeds, not the seeds themselves? (It almost always is.) |
| **遍計所執** (parikalpita) | The error: treating that appearance as fixed, independent, authoritative ground truth | **The bug.** Where is the code (or the LLM, or a future reader) about to trust a derived view as if it were the source? |
| **圓成實** (pariniṣpanna) | The fix: keep the appearance, remove the *imputation of fixity* — mark it, gate it, or check its freshness | Is the appearance marked/gated/staleness-checked so it can never be mistaken for the real? |

The fix is **never** "delete the appearance" — 依他起 is real and needed (you
need the cache, the narrative, the memory). The fix is to strip the false
*fixity*: provenance-mark it, gate it behind confirm, or check it's still current.

## The 遍計所執 bug class — where it shows up

When you catch yourself (or an app) doing any of these, that's the lens firing:

| Reified appearance | The existing discipline that strips the fixity |
|---|---|
| LLM says "Written to…" / "Done" without an actual write | `.claude/rules/proposed-action.md` — propose/preview/confirm |
| A cached snapshot used as live state | `.claude/rules/time-dimension.md` — staleness check before acting |
| A stale memory recommended as current truth | `scripts/check_memory_rot.py` + "verify before recommending from memory" |
| A read-only aggregator view edited as if authoritative | `.claude/rules/boards-as-view-layer.md` — `readonly` default + `SETTABLE_FIELDS` |
| Hardcoded `localhost`/path/port — a fixed self-nature on a relational thing | CLAUDE.md Rule 17 — host/port from `[network]` config |
| AI-authored content indistinguishable from user-authored | `.claude/rules/authorship-boundary.md` — `author:` + provenance chip |
| A grant/eligibility treated as a permanent fixed fact | `.claude/rules/verb-registry.md` — recomputed floor, never persisted |

All seven are the same move: **distinguish 依他起 (the conditioned appearance)
from 遍計所執 (the reified fixity), and apply 圓成實 (mark/gate/check) so the two
never collapse.** The lens is what lets you recognise a *new* instance as a
member of this class instead of a one-off.

## Live execution point

The lens is mostly a reasoning discipline, but it has **one check that actually
runs** and whose entire purpose is catching a 遍計所執 instance:

- **`scripts/check_memory_rot.py`** (preflight `--scope memory`, advisory) —
  flags auto-memory that recommends a *stale, dated appearance* as current
  truth (dead anchors, moved files, stale dates). Recommending from a decayed
  memory is exactly reifying a conditioned, time-bound view as fixed ground
  truth. It surfaces findings and **never auto-rewrites** — the human reconciles
  (圓成實 is *marking/checking*, not silently overwriting).

**A host-literal scanner was tried and dropped (2026-06-13).** Hardcoded
`localhost`/`127.0.0.1`/`:PORT` is a textbook 遍計所執 (a fixed self-nature on a
relational value, CLAUDE.md Rule 17), so a static `apps/**/*.py` scanner was
built and run per `.claude/rules/audits.md`. Verdict: **the codebase already
obeys Rule 17** — after tuning, the residue was ~100% false positives (display-
URL hints, regex parsing, loopback membership lists, config-template strings),
zero genuine connect-to-hardcoded-host bugs. Per audits.md (">30% FP after
tuning → don't ship"), it was not registered. Rule 17 here is convention-clean,
not scan-enforced; don't re-run this experiment without a real regression.

## When to apply it

- **Writing a `self.think()` consumer** — the model's output is 依他起. Does any
  downstream code trust it as fact without a gate/provenance mark? (Especially:
  does it *claim an action* the system didn't verify happened?)
- **Adding a cache, snapshot, memo, or aggregator** — is the derived view
  staleness-checked / marked readonly, or can a later reader mistake it for source?
- **Reviewing an audit/recommendation surface** — does it cite the live source
  (依他起 grounded in seeds), or recite a possibly-stale internal view (遍計所執)?
- **Seeing a hardcoded constant** — is it imputing a fixed nature to something
  that's actually conditioned (configurable, per-machine, relational)?

## When NOT to invoke it

- **Don't add a `three_natures` field, tag, score, or UI marker anywhere.** It's
  a lens, not a schema. The moment it becomes a visible feature it's decorative.
- **Don't relabel every existing rule as "三性".** The value is recognising a
  *new* bug as a member of the 遍計所執 class — not re-tagging the seven rules
  above. They already work; the lens just connects them.
- **Don't force the三-way split onto things that aren't appearances** — pure
  user-authored seeds (a journal entry, a hand-written note) aren't 依他起 of
  anything; they're 種子. The lens is about *derived* views, not source data.
- **Don't gate a genuinely reversible internal action** just because it produces
  an appearance — the autopilot pivot (CLAUDE.md north star) still governs *what*
  gets gated. 圓成實 says *mark/check* the appearance, which is cheaper than a
  gate and usually enough.

## Cross-references

- `docs/DESIGN.md` § 缘起性空 — the framework this operationalises (horizontal
  axis to the Eight-Consciousness vertical axis)
- `.claude/rules/proposed-action.md` — the strongest 圓成實 discipline (don't let
  an appearance claim it acted)
- `.claude/rules/authorship-boundary.md` / `EOS_UI.provenance()` — marking what's
  AI-derived so it isn't imputed as user truth
- `.claude/rules/time-dimension.md` — staleness is the temporal face of the same
  check (don't impute current-ness to a past snapshot)
- CLAUDE.md Rule 16 (wellbeing wheel) — the precedent for a reasoning-only lens
  that never becomes a UI feature
