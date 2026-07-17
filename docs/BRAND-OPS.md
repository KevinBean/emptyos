# Brand Ops — running EmptyOS's brand from inside EmptyOS

> Positioning lives in [`brand/MESSAGING.md`](../brand/MESSAGING.md) (what the brand *says*).
> This doc is **operations** (how the brand *runs*): the weekly loop, which surfaces
> draft and stage content, and the approval gate that keeps a human on every
> outbound action. Keep the two separate — change the message in MESSAGING, change
> the machine here.

EmptyOS markets itself with the same machinery it ships: the `publish` app, the
`promote` app, the devlog/screenshot skills, the content generators, the `staff`
scheduler, and the `reactor`/`telegram` notification path. Nothing here is a new
subsystem — it's a wiring of existing parts.

## The thesis — the brand is the exhaust of the work

The content that runs the brand is a **byproduct of shipping**, not a separate
job. Each development session already produces a devlog; each shipped feature is
already a story. The loop below turns that stream into drafted, illustrated,
distribution-ready content automatically. The only recurring human cost is
approving the handful of outbound (irreversible) taps.

This is the CLAUDE.md reversibility split applied to marketing: **drafting and
staging are reversible and auto-run; posting to a third party is irreversible and
stays human-gated** (`.claude/rules/autopilot-grants.md` — outbound-to-third-party
is never autopilot-eligible). "Approve-only" is the correct design, not a
limitation.

## The weekly loop

```
WEEKLY (scheduled, low-traffic afternoon, system-local TZ)
  scheduler → staff "brand-editor" shift (or the promote weekly-drafter directly)
    OBSERVE  this week's devlog(s) + git history + shipped features
    DECIDE   pick 1–2 story-worthy items (lead with engineering judgment, not hype)
    ACT (auto — reversible, no gate)
      • promote app        → blog draft (routed to publish) + LinkedIn/X drafts
      • eos-screenshot      → privacy/branding-scanned hero images
      • (optional) podcast   → episode draft (pipeline, stop-after-script)
    STAGE (gated — outbound)  → pending review cards + a Telegram ping
  ──────────────────────────────────────────────────────────
  HUMAN (<1 hr): Telegram "📋 N brand items ready"
    → open → Apply / edit / Reject each card
    → blog deploys (you confirm publish.deploy), social copy paste-ready,
      audio/video ready to upload
  ──────────────────────────────────────────────────────────
  reactor ripples publish:deployed / podcast:generated → journal + Telegram "🚀 live"
```

## Surfaces it uses

| Surface | Path | Role in the loop |
|---|---|---|
| `promote` app | `apps/extension/dev/promote/` | Scans devlogs + git, drafts X/LinkedIn/what's-new copy + blog drafts (routes to `publish`). **Never auto-posts** — every draft is an Apply/Reject card. Weekly auto-drafter behind a dark flag. |
| `publish` app | `apps/public/standard/publish/` | Builds + deploys the static sites. Site profiles in `data/apps/publish/sites.json`. `publish.deploy` is the gated outbound step. |
| `eos-devlog-publish` skill | `.claude/skills/eos-devlog-publish/` | Rewrites a session log into a narrative blog draft (`publish: false`). |
| `eos-screenshot` skill | `.claude/skills/eos-screenshot/` | Captures UI assets; scans visible text against `.eos-personal` + `.eos-branding` and refuses to write on a leak. |
| `podcast` app | `apps/personal/podcast/` | Episode drafts via the resumable pipeline (`stop_after="script"` previews the script before any TTS spend). |
| `staff` app | `apps/personal/staff/` | The weekly orchestrator. A scheduled (cron) shift can only draft + stage — risky actions with no `pre_approved_risky` entry are auto-declined (`apps/personal/staff/shifts.py`). |
| `reactor` app | `apps/public/standard/reactor/` | Ripples `publish:deployed` / `podcast:generated` to the journal + a Telegram "it's live" ping. |
| `telegram` / `notifications` plugins | `plugins/telegram/`, `plugins/notifications/` | The approval hotline — pending items ping the phone. |

## The approval SOP (the <1 hr/week contract)

1. A weekly ping lands on Telegram: "N brand items ready."
2. Open the pending queue (the `promote` Apply/Reject cards + the publish Drafts tab).
3. For each item: **Apply** (good as-is), **edit then Apply**, or **Reject**.
4. The blog deploys only when you confirm `publish.deploy`. Social copy is
   paste-ready (or fires a configured webhook on your Apply). Audio/video is
   ready for you to upload.
5. `reactor` posts an "it's live" breadcrumb to the journal + Telegram.

Nothing posts to a third party without your explicit click. A scheduled shift
that finds no human on hand stages everything and posts nothing.

## Channel playbook

| Channel | Voice | Outbound gate | Mechanism |
|---|---|---|---|
| Owned site (`eos.binbian.net`) | Product | `publish.deploy` (you confirm) | `publish` app, site id `emptyos` |
| Personal blog (`binbian.net`) | Personal | `publish.deploy` (you confirm) | `publish` app, site id `default` |
| LinkedIn / X | Personal | manual paste, or a webhook on Apply | `promote` drafts → `promote.channel.<x|linkedin>.webhook_url` |
| YouTube (podcast/video) | Either | manual upload (you) | `podcast` / `music-studio` artifacts; no upload connector today |
| GitHub / HN / Reddit | Mixed | **manual post by design** | `promote` drafts release copy at release moments only |

Dev-community posts stay manual-approve **and** manual-post: community norms and
judgment make full automation a reputational risk, not a time saver.

### The tryable artifact (the one non-post channel)

Every channel above ships *words*. One slot ships a **thing**: a single standalone,
no-install, useful-in-60-seconds artifact, launched once and then linked from every
post forever. It is not a channel and not a product — it is the permanent asset that
converts strangers who will never read 2,000 words. Rationale (an artifact out-travels
the framework it advertises), the selection rules, and the current candidate live in
the private strategy layer; the deferred row + trigger are in `docs/DEFERRED-WORK.md`,
and the verdict that produced them in `docs/OPEN-SOURCE-BORROWING-PLAN.md` § PocketFlow
(2026-07-15). Hard rule: the artifact must be **on-thesis** — a generic AI dev-tool
dilutes the positioning and is explicitly refused, however easy it would be to ship.

## Voice discipline

Carried verbatim from `brand/MESSAGING.md` § Voice & rules — builder-to-builder,
specific over grand, ground every claim, no hype words, no fabricated metrics,
one language per surface. Lead with the engineering *judgment* behind a feature
(the decision, the hard part, the mechanism), not feature-announcement hype.

Both voices are now **written down as per-site voice notes** the publish app's
AI editorial actions inject automatically: `{vault}/<site source_folder>/_voice.md`
(Personal → `30_Resources/Published/_voice.md`, derived from the real post corpus,
incl. dual-brand positioning + technical-marketing article anatomy; Product →
`30_Resources/EmptyOS-Site/_voice.md`, extracted from MESSAGING.md + the
eos-devlog-publish voice guidelines). The `_` filename prefix keeps them out of
the built sites. The writer's `→ LinkedIn` action codifies the `.linkedin.md`
adaptation recipe. See `apps/public/standard/publish/writer.py::_voice_block`.

## Content guardrails (apply to every draft)

- **No proprietary or employer-conflicting material.** Public engineering content
  shows *method and rigor* on **illustrative or public data only** — never a live
  proprietary calculator or client data. (`.claude/rules/demo-mode.md`.)
- **Both scan gates stay on.** `scripts/check-personal.py` + `scripts/check-branding.py`
  run before any deploy; `eos-screenshot` self-scans every image.
- **Drafts are AI-authored until you edit them.** Mark provenance per
  `.claude/rules/authorship-boundary.md`; the human owns what ships.

## Audio (on-demand, pipeline-backed)

Brand audio is **on-demand, not weekly-automatic**. A weekly auto-podcast is the
wrong default: it spends cloud TTS on every run, it has no clean "this week's
eligible shipped work" source, and a scheduled agent that proposes one
auto-declines under cron anyway (no human on hand for a billing action). So:

- Produce a brand episode when a story genuinely warrants audio — `/eos-podcast`
  with the eligible story as context (same publishable-only discipline as the
  blog: no proprietary engineering).
- The podcast **pipeline** is enabled (`[apps.podcast] feature.pipeline.enabled`):
  generation runs as resumable stages with `stop_after="script"`, so you review
  the script **before** any TTS spend, and an interrupted run resumes without
  re-paying the audio pass.
- No `brand-editor` staff agent. The promote weekly cron already orchestrates the
  recurring blog/social drafting; a staff agent would duplicate it, can't observe
  the brand surfaces, and would auto-decline the one thing it'd uniquely do
  (propose a billing-cost podcast on a cron). Audio stays a deliberate human call.

## Dev community (release-moment, manual)

GitHub / HN / Reddit posts are **manual draft + manual post**, fired only at a
real milestone — never on every deploy (you deploy often; a Show-HN on a routine
increment is noise that burns the channel). The runbook:

1. At a notable release, open `/promote/` → create a campaign for the milestone.
2. The drafter produces the X / LinkedIn / what's-new copy; the what's-new blurb
   is the seed for a Show-HN / subreddit / release-note post.
3. Review, edit to taste, and **post by hand** in the community. The `hn` /
   `newsletter` channels are modelled as planned cells on purpose — there is no
   auto-generator and no auto-post for them.

## Build phases (status)

Most pieces exist; the work was *enable + configure*, not build.

| Phase | What | Status |
|---|---|---|
| 1 — Owned-site loop | `eos-devlog-publish` + `eos-screenshot` → `publish` build/deploy | ✅ proven; drafts staged, never auto-deployed |
| 2 — Social drafting + approvals | `promote.weekly-drafter.enabled`; Telegram approvals | ✅ live (runtime setting, no restart); Telegram configured |
| 2.5 — Auto-drafter eligibility filter | `run_weekly_draft` skips engineering/cable + personal candidates | ✅ built (`shared.py` helpers + `_candidate_eligible`); needs a restart |
| 3 — Audio | On-demand `/eos-podcast`, pipeline preview-before-spend; no staff agent | ✅ pipeline flag enabled; on-demand by design |
| 4 — Dev community | Release-moment manual draft + manual post | ✅ runbook above; intentionally manual |

Flags live in `emptyos.toml` (`[apps.podcast]`) and the runtime Settings service
(`promote.weekly-drafter.enabled`). Python/config changes need a daemon restart;
runtime settings take effect immediately. Per the dark-default convention each
capability ships off and is turned on once proven by hand.

## What not to do

- **Don't auto-post to a third party.** Outbound is never autopilot-eligible.
- **Don't fork the messaging.** `brand/MESSAGING.md` owns positioning; this doc
  owns operations.
- **Don't publish proprietary engineering.** Illustrative/public data only; both
  scan gates stay on.
- **Don't big-bang it.** Ship Phase 1, live with it, then add channels. The whole
  point is <1 hr/week.
- **Don't pre-approve outbound verbs in a scheduled agent.** Leave the
  `brand-editor`'s `pre_approved_risky` list empty so a cron shift can only stage.
```
