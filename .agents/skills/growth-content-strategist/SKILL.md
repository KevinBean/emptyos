---
name: growth-content-strategist
description: Cross-platform written + short-form content strategist — plans a platform-fit calendar, drafts hook/format templates for X, LinkedIn, and the blog, and runs the one-source → many-formats repurposing chain so a single piece of EmptyOS work becomes a coherent multi-channel push. Use when the user says "content strategy", "content calendar", "turn this into posts", "repurpose this", "what should I post", "hook for X/LinkedIn", or "blog this". NOT for YouTube/video-channel specifics (use `creative-youtube-channel` / `creative-youtube-ai-engineering`), NOT for music releases (use `creative-suno-*`), and NOT for the final announce-copy drafting/queueing (that's the `promote` app).
---

# Growth — Content Strategist

Adapted from the agency-agents social/content personas into an EmptyOS procedure.
Strategy + drafts the user reviews; the `promote` app owns the human-gated queue and
the `publish` app owns the blog. Defers all video-channel work to the existing
`creative-youtube-*` skills — this skill is for **written + short-form** (X, LinkedIn,
blog, short scripts).

## When to use / not

| Use when | Defer to |
|---|---|
| "content calendar", "what should I post this week" | — |
| "turn this devlog/feature into posts", "repurpose this" | — |
| "hook/format for X or LinkedIn", "blog this" | — |
| YouTube scripting, channel ops | `creative-youtube-channel`, `creative-youtube-ai-engineering` |
| Music / Suno releases | `creative-suno-*` |
| Final announce copy → review queue | `promote` app |

## 1. Source → formats (the repurposing chain)

EmptyOS content starts from *real work*, not invented topics. The chain:

1. **Primary source** — a shipped feature, a devlog section
   (`10_Projects/emptyos/log/`), a KB synthesis, or a long blog post.
2. **Anchor piece** — one substantial format: a blog post on `publish`, or a
   LinkedIn long-form / X thread.
3. **Adapt down** — cut the anchor into platform-native shorter forms.
4. **Measure** — track which angle landed; feed it back into the next round.

One unit of work → blog + LinkedIn post + X thread + (optional) short-video script.
Don't author each from scratch; derive them from the anchor so the message stays
coherent across platforms.

## 2. Platform fit

**Blog (publish site)** — the canonical, SEO-bearing long form (pair with
`growth-seo-specialist`). Everything else points back to it.

**LinkedIn** — authority + build-in-public. Long-form posts, "what I shipped + what I
learned" framing, industry insight. Higher word tolerance; lead with a concrete claim.

**X / Twitter** — threads and time-sensitive notes. Native, punchy, one idea per post.
Adapt the LinkedIn insight into a thread; don't cross-post verbatim.

## 3. Format templates

**X thread**
- Hook post: the surprising claim or result (no "a thread 🧵" filler — earn the scroll)
- 1 idea per post; concrete > abstract; show, don't summarize
- Last post: the takeaway + a single link back to the anchor

**LinkedIn post**
- Line 1: a specific, falsifiable hook (the "see more" cutoff is unforgiving)
- Body: context → what changed → what you learned (build-in-public beats marketing)
- Close: one question or one link, not five hashtags

**Blog post** — handled with `growth-seo-specialist` for title/meta/structure; this
skill supplies the narrative spine and the repurposing-down plan.

**Short-video script** (optional) — hook in first 2s, one idea, on-screen text carries
it; hand the actual production to `creative-*` skills if it's a channel piece.

## 4. Cadence

- Pick a sustainable rhythm tied to real shipping, not an arbitrary daily quota —
  empty cadence is worse than silence.
- Reference benchmarks (adapt, don't chase): LinkedIn ~3%+ post engagement; share
  rate ~15% for genuinely useful posts; steady follower growth over spikes.

## EmptyOS integration

- Pull sources from devlogs (`10_Projects/emptyos/log/`) and the `promote` app's
  "what's new" scan — `promote` already detects shippable changes.
- Hand finished drafts to the **`promote`** app's Apply/Reject queue; blog drafts to
  **`publish`**. Never auto-post.
- Use `growth-seo-specialist` for the blog anchor's on-page SEO and
  `growth-hacker` to decide which content bets are worth making.

## Quality bar — what NOT to do

- Don't cross-post the same text verbatim across platforms — adapt to each.
- Don't manufacture topics; EmptyOS content is downstream of real work.
- Don't pad threads/posts with filler ("🧵", hashtag walls) — it trains people to scroll past.
- Don't queue or post directly; output is reviewed drafts into `promote`/`publish`.
