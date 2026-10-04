---
name: eos-send-preflight
description: Verify a finished draft before it goes out under Kevin's professional name — pick the right one against the brand framework, confirm it hasn't already been posted, check every link resolves, run the prose-tone gate. Use when the user says "post this", "send this", "is this ready to publish", "check this draft before I post", or asks which draft to put out. NOT for writing or editing a draft (that is the writing-editor / promote apps), NOT for deploying the blog (use eos-devlog-publish).
---

# Send preflight

A draft that is *finished* is not the same as a draft that is *safe to send*. This is the
last check before something reaches a named human with Kevin's name on it.

It exists because on 2026-08-16 the top-recommended LinkedIn draft turned out to carry a
**404 CTA link** and a "yesterday" that had been false for four months — and separately, the
content had already been posted in April under a different title. All three would have been
sent. None was visible without checking.

## When to use

- A `.linkedin.md` companion, recruiter reply, outreach DM, or any public post about to go out
- The user asks which draft to send
- Any message carrying a technical number under Kevin's professional name

## When NOT to use

- Drafting or rewriting — that is `writing-editor` / the `promote` app
- Deploying the blog itself — `eos-devlog-publish`
- Internal-only writing that no external human will read

## The hard boundary

**This skill prepares. It never sends, schedules, queues, or tracks.**

`feedback_builder_trap_do_not_build_the_fix` rules out a distribution app, outreach tracker,
scheduler, post queue, publishing dashboard, or nagging cron — because the measured failure
was never a tooling gap. Every input to the send already existed; the button was the gap. If
running this skill ever starts to feel like it needs a queue or a dashboard, that is the trap,
and the answer is to send the thing instead.

## The checklist

### 1. Pick the right draft — and confirm it is unsent

**Folder location is the only signal for whether a LinkedIn companion has been posted.**

| Location | Meaning |
|---|---|
| `posts/published/<slug>.linkedin.md` | **Already posted.** It shipped with its parent article. |
| `posts/drafts/<slug>.linkedin.md` | Not posted. These are the candidates. |

A `.linkedin.md` is simply *the LinkedIn version of that post* — not a proposal to write one.
When the post ships, its companion ships, and both move to `published/`.

**The frontmatter carries no signal, and reading it as one is a trap this skill exists to stop.**
`type: social-draft` and `publish: false` appear on posted and unposted companions alike:
`social-draft` is the permanent artifact *kind*, and `publish: false` means "do not render this
as a page on the blog" — a LinkedIn companion is never a blog page, posted or not. On
2026-08-16 that misreading produced two wrong recommendations in one session, both to re-post
something already public, the second *after* being corrected on the first.

So: pick from `drafts/`, and only from `drafts/`.

- Choose against `{vault}/30_Resources/Published/_framework.md` (the spine + audiences), not by
  recency or convenience. The energy×software pieces are what that file calls "the NIW payload";
  prefer them when the goal is career conversion.
- The parent article must be live before its companion goes out — the companion links to it.
  If the parent is still in `drafts/`, the blog post ships first (`eos-devlog-publish` or the
  Publish app), not the LinkedIn version.
- `project_linkedin_posting_experiment` logs post *times and results*, not whether something
  shipped. Use it to record outcomes, not to decide what to send.

### 2. Every link must resolve

```bash
curl -s -o /dev/null -w "%{http_code}\n" -L --max-time 12 "<url>"
```

Expect `200`. The house CTA form is `binbian.net/posts/<slug>.html` — a bare
`binbian.net/<slug>` **404s**. Check the real site structure rather than assuming a slug:

```bash
curl -s -L https://binbian.net/ | grep -oE 'href="posts/[^"]*"' | sort -u
```

### 3. Prose-tone gate — mandatory

```bash
python scripts/check_prose_tone.py "<vault>/30_Resources/Published/posts/<file>"
```

Must read `clean` (0 findings). This is the same gate `reference_linkedin_share_pipeline`
requires; it blocks hype vocabulary, announce-openers, engagement bait, AI filler, and the
"X isn't Y. It's Z." pivot.

### 4. Verify every number against its source

Any figure in a public claim gets traced to code, a test, or a published document **before** it
goes out. `_voice.md` bans fabricated metrics outright, and a wrong number under a professional
byline is expensive to walk back.

Worked example: the ABB receipt in the trust-loop post traces to
`PUBLISHED_RESULT_A = 14943.0` in `apps/public/standard/trust-loop/app.py`, is pinned by
`tests/test_sys_trust_loop.py` and `tests/test_unit_trust_loop.py`, and the full-precision
14,934.6 figure is recorded in that app's `VALIDATION.md`.

### 5. Re-read time-bound and version-bound claims

A draft ages in place. Check any "yesterday" / "last week" / "this month", any model or version
name, and any "currently" against the draft's own `date:` frontmatter versus today. Fix by
removing the time reference rather than updating it — the claim rarely needs it.

### 6. Hand over

Output the **exact paste-ready body**, with the timing from
`reference_linkedin_publish_timing` (Tue/Wed 21:30–22:00 AEST for the LinkedIn companion).
Do not paraphrase it into chat prose — the user should be able to copy it directly.

### 7. Tell the user to log it afterwards

The one LinkedIn post that did go out (2026-04-22) was logged "results pending, ask in ~48h"
and nobody ever asked. Remind the user to add the outcome to
`project_linkedin_posting_experiment` (post time, topic, then impressions/reactions at ~48h),
and to record any human contact in the week note's **Sent to a human** row
(`.claude/skills/eos-week-review/SKILL.md`).

## Cross-references

- `reference_linkedin_share_pipeline` — companion format, prose gate, share-card sizing
- `reference_linkedin_publish_timing` — the Tue/Wed 21:30–22:00 AEST slot and why
- `feedback_builder_trap_do_not_build_the_fix` — why this stops at preparation
- `{vault}/30_Resources/Published/_framework.md` · `_voice.md` · `_distribution.md`
- `.claude/skills/eos-week-review/SKILL.md` — the **Sent to a human** counter this feeds
