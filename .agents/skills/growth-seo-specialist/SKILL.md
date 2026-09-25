---
name: growth-seo-specialist
description: Technical + content SEO strategist for the EmptyOS publish site (eos.binbian.net) and any markdown-vault-backed site. Runs technical-SEO audits, a mandatory cannibalization check, on-page optimization, keyword/topic-cluster planning, and link-authority strategy — turning findings into copy the `promote` / `publish` apps surface for human review. Use when the user says "SEO", "optimize for search", "why isn't my post ranking", "keyword research", "technical SEO audit", "on-page check", or "improve organic traffic". NOT for AI-engine citability (use `eos-geo-audit`, the GEO sibling) and NOT for publishing copy drafting itself (that's the `promote` app).
---

# Growth — SEO Specialist

Adapted from the agency-agents SEO persona into an EmptyOS procedure. This is
expertise *I* apply when working the publish site or a vault-backed site; output is
always recommendations the user reviews — never auto-applied. Pairs with
`eos-geo-audit` (AI-engine citability) and feeds the `promote` app (which drafts
the announce copy) and `publish` app (which builds the site).

## When to use / not

| Use when | Not for |
|---|---|
| "SEO audit", "why isn't this ranking", "on-page check" | AI-engine citability → `eos-geo-audit` |
| "keyword research", "topic cluster for X" | Drafting announce copy → `promote` app |
| "improve organic traffic", "technical SEO" | Pure backlink outreach execution (advice only) |

The publish site's built output lives at `data/apps/publish/sites/<id>/site/`. Read
`data/apps/publish/sites.json` to resolve the target site (same resolution as
`eos-geo-audit`). Work from the built HTML, not the source markdown.

## 1. Technical SEO audit

**Crawlability & indexation**
- robots.txt: intentional blocks vs critical-path access
- XML sitemap: declared, and indexed/total URL coverage ratio
- Crawl waste: parameter URLs, faceted nav, thin/duplicate pages

**Architecture**
- Max clicks homepage → deepest page (target **<3 hops**)
- Orphaned pages with zero internal links
- Internal-link distribution; top-10 most-linked pages

**Core Web Vitals thresholds**

| Metric | Target |
|---|---|
| LCP | <2.5s |
| INP | <200ms |
| CLS | <0.1 |

**Structured data**: schema types present (Article, FAQ, HowTo, Organization),
validation errors, missing-schema opportunities.

## 2. Cannibalization check (MANDATORY before any on-page change)

Two pages competing for one query split clicks and depress both. Do this first.

1. **Map**: pull query→page report (search-console-style: dimensions = page + query)
   for the topic. Table: Query | Page A (pos/clicks) | Page B (pos/clicks) | conflict?
2. **Assign owner**: per query with 2+ pages ranking, award to highest clicks +
   closest semantic fit + designated pillar/satellite role.
3. **De-conflict**: strip the competing primary keyword from non-owners; add internal
   links *from* non-owner *to* owner for the disputed query; never duplicate the same
   primary keyword in title/H1 across cluster pages; self-referencing canonicals only.

Signal: multiple pages in the top-20 for one query at similar positions = active
cannibalization to resolve before further content work.

## 3. Keyword & topic-cluster plan

- **Pillar**: head term + volume + difficulty (0–100) + intent
  (Informational/Commercial/Transactional/Navigational) + SERP features + target URL.
- **Satellites**: table Keyword | Volume | Difficulty | Intent | Target URL | Tier.
- **Gap analysis**: competitor keywords with no presence; "low-hanging fruit" at
  positions 4–20; weak-answer featured-snippet targets; segment by intent → format.

## 4. On-page checklist

- Title: primary keyword + modifier + brand, **50–60 chars**
- Meta description: keyword + CTA, **150–160 chars**; self-referencing canonical; OG tags
- Single H1 with primary keyword aligned to intent; H2/H3 covering subtopics + PAA
- Primary keyword in first 100 words; word count competitive with top-5
- Alt text on images; images **<100KB** WebP/AVIF; FAQ section targeting PAA
- Schema matching content + breadcrumb + author (E-E-A-T)

## 5. Link authority (advice, not autopilot)

- Profile: referring-domain count, quality distribution, toxic ratio (disavow if >5%)
- Channels: original research/data stories → journalist outreach; guides + free tools +
  calculators; broken-link reclamation; unlinked-mention → linked citation.

## EmptyOS integration

- Resolve target via `data/apps/publish/sites.json`; audit built HTML under
  `data/apps/publish/sites/<id>/site/`.
- Hand the announce/title/meta copy to the **`promote`** app for Apply/Reject drafting
  — never post or deploy directly.
- Run alongside **`eos-geo-audit`** for the AI-engine half; they produce a hybrid view.
- Write any saved audit into the vault next to GEO audits:
  `30_Resources/EmptyOS/publish/audits/YYYY-MM-DD-seo-<site>.md` (same dated-overwrite
  convention as `eos-geo-audit`).

## Quality bar — what NOT to do

- Don't propose on-page edits before the cannibalization check (Step 2).
- Don't chase keyword volume against the wrong intent — a transactional page won't win
  an informational query.
- Don't recommend keyword-stuffing, cloaking, or link-buying — they're the failure mode.
- Don't auto-edit live pages; SEO output is reviewed copy, not an applied change.
