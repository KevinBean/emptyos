# Web-research surface — scoping (2026-07-20)

Scope for the three gaps surfaced by the `wigolo` comparison
(`docs/OPEN-SOURCE-BORROWING-PLAN.md` § Sunday-trending batch).

> **STATUS 2026-07-20 — items 1, 1b and 3 SHIPPED; item 2 deferred.**
> Decisions were delegated ("you decide"), so the ⚠ open questions below were
> resolved as: key name `snippet`; deterministic triage (not model-routed);
> robots honoured on automated paths only via an explicit `automated=` arg;
> modules `emptyos/sdk/web_politeness.py` (robots + pacing). Item 2 (fetch
> cache) was **deferred rather than decided** — it is the only item that adds a
> persistent store, and its eviction policy is a judgment call that shouldn't be
> made unilaterally. It now has a row in `docs/DEFERRED-WORK.md`.
>
> **LIVE VERIFICATION 2026-07-20 (sandbox `:9002`, real search + Playwright +
> openai-mini):** the pipeline runs clean through the new gates — 5 sources
> attempted, 4 read, 1 PDF failed as expected, 3 citations, no regression.
> Real robots.txt, real pacing (1.014s same-host, 0.000s cross-host) and the
> automated/interactive asymmetry all confirmed against live hosts.
> **But item 1b under-delivers and the reason is structural** — see the
> "Measured outcome" note appended to it. Live verification also found a real
> bug the unit tests could not (`read_web_source` closed a browser context it
> never opened on any gated URL); fixed + pinned.
>
> **Correction to this document:** §Item 1 "Risk" claimed the existing tests
> "keep passing". They did not — `test_ddg_search_dedupes_by_url_and_skips_empty`
> asserts exact dict equality, so the added key broke it. Updated in place; two
> snippet tests and three triage tests added alongside.

Consumers of the affected path (all four call `ddg_search` → `read_web_source`
on *every* hit):

| App | Call site | Top-N default |
|---|---|---|
| `apps/public/standard/assistant/research.py` | `:93`, `:135` | 5 |
| `apps/public/standard/voice-assistant/research.py` | `:82` | 3 |
| `apps/extension/plus/explore/app.py` | `:328`, `:399` | per-channel |
| `apps/extension/dev/kb-gap-miner/app.py` | `:583`, `:598` | `RESEARCH_TOP` |

---

## Item 1 — Snippets (S, ~1h incl. tests)

### The finding

`ddgs` **already returns** `body` (the result snippet). `ddg_search`
(`emptyos/sdk/web_search.py:46`) reads `href` and `title` and discards `body` on
the next line. Verified live 2026-07-20: `ddgs 9.14.4`, `DDGS().text()` returns
keys `['body', 'href', 'title']` with real snippet prose.

This is **not** the "consolidate the two DuckDuckGo implementations" project I
first described. It is one field.

### Correction to the earlier framing

I claimed `sdk/agent_tools/web_search.py`'s HTML scrape was "the path the SDK
docstring argues against" and implied it was fragile/broken. **Live-tested: it
returns HTTP 200 and parses 10/10 titles and snippets, no anomaly challenge.**
The SDK docstring's warning is specifically about *Playwright* hanging on
`html.duckduckgo.com`; a plain `aiohttp` POST is fine. So the second
implementation works, and consolidating it is **drift cleanup, not a bug fix** —
lower priority than stated. It is not part of this item.

### Change

```python
# emptyos/sdk/web_search.py::ddg_search
out.append({
    "url": url,
    "title": title,
    "snippet": (hit.get("body") or "").strip(),   # NEW
})
```

⚠ **Naming — needs sign-off.** Key name `snippet`:
- `snippet` — matches `agent_tools/web_search.py`'s existing dict key, and wigolo/
  Brave/Tavily convention. **Recommended.**
- `body` — matches ddgs upstream, but collides conceptually with page body text
  that `read_web_source` returns. Rejected for that reason.

### Cross-channel shape question

`web_search.py:314` states the vertical channels (`github/hn/arxiv/scholar/
openalex/wikipedia`) return the same `[{url, title}]` shape as `ddg_search` "so
pipelines can merge channels." Adding `snippet` to one channel diverges the shape.

**Proposal: `snippet` is an optional key, absent-or-empty tolerated everywhere.**
Consumers must treat it as `hit.get("snippet") or ""`. Several channels can
populate it cheaply from data already in their response (GitHub repo
`description`, arXiv `summary`, HN `story_text`) — do those in the same pass so
the key isn't misleadingly DDG-only. Semantic Scholar / OpenAlex abstracts are
available but larger; leave those for the triage item if wanted.

### Risk

Low. Additive key; no existing consumer reads it. Existing tests
(`tests/test_sdk_web_search.py:28,134`) stub `DDGS` and assert on `url`/`title` —
they keep passing. Add one test asserting `snippet` survives and one asserting a
missing `body` degrades to `""`.

**Note: shipping the snippet alone changes no behaviour.** It only becomes a win
when something *uses* it to skip a fetch — which is Item 1b.

---

## Item 1b — Snippet-based triage (M, ~half day) — the actual saving

Item 1 exposes the data; this spends it. Today every one of the top-N results
gets a full Playwright navigation. With snippets, a cheap pre-filter can decide
which URLs are worth fetching.

Two designs, **pick one**:

- **(a) Deterministic** — drop results whose snippet is empty *and* whose title
  has no query-term overlap. Zero LLM cost, no new failure mode, modest saving.
- **(b) Model-routed** — one cheap `select()` call ranking N snippets, fetch the
  top K. Bigger saving, but adds a model call to every research run and a new
  way to be wrong (discarding the one good source).

⚠ **Recommendation: (a) first**, measured, then (b) only if the fetch count is
still the bottleneck. `explore` already has a model-routed channel picker
(`_resolve_channels`) — a second router there compounds latency.

### Measured outcome (2026-07-20) — (a) shipped, and it is a no-op

Design (a) was built and driven live. **It does not fire on normal queries, and
the cause is structural, not a tuning problem:** a SERP snippet *is* the
fragment an engine chose because it contains the query match, so "snippet shares
a term with the query" is true for essentially every returned hit. Live results:
5/5 kept on `IEC 60287 cable current rating derating factors`, 8/8 on an earlier
query. The only case it bites is a merged multi-channel set where a vertical
channel contributed an off-topic hit carrying its own description.

So the recommendation "(a) first, measure, then (b)" was right as *method* and
wrong as *prediction*. The measurement is the deliverable: any real reduction in
fetch volume needs semantic judgement — design (b) — now deferred **with
evidence** rather than on a hunch. Do not reach for (b) reflexively; measure
whether Playwright navigation cost is actually felt first.

`triage_hits` stays as the cheap floor (it costs nothing and correctly protects
against the empty-result case), with the finding recorded in its docstring so
nobody reads it as a working optimisation.

Gate behind `feature.snippet-triage.enabled`, default off
(`project_feature_pipeline_flag_default_dark`). Land in `assistant/research.py`
first (single consumer, easiest to eyeball), not all four at once.

---

## Item 2 — Fetch cache (M–L, ~1 day)

### Shape

Content-addressed, keyed on the **normalised URL**, storing extracted text +
fetch timestamp + the `looks_blocked` verdict. `read_web_source` consults it
before calling `app.browse(...)`.

⚠ **Naming — needs sign-off.**
- Module: `emptyos/sdk/web_cache.py` (recommended) vs folding into `web_search.py`
  (already 496 lines and doing three jobs).
- Param: `read_web_source(..., max_age_s: int | None = None)` — `None` = always
  fetch, preserving today's behaviour byte-for-byte. Recommended over a bare
  `use_cache: bool` because staleness is per-call (a daily-brief wants fresh; a
  KB-gap-miner re-read does not).

### Storage decision

**`data/` not vault** — machine telemetry, high-frequency, regenerable
(CLAUDE.md § Storage & Vault). Proposed `data/web_cache/` with a SQLite index +
text blobs, mirroring `RunRegistry`'s folder discipline rather than inventing a
store.

### What NOT to build

wigolo pairs its cache with `sqlite-vec` embeddings and a `find_similar` tool.
**Out of scope.** We already have `sdk/embeddings.py` + `search?mode=embed`; if
semantic search over fetched pages is ever wanted it composes with those, and it
is a separate consumer with its own trigger.

### Open question (blocks estimate)

Eviction. Needs a TTL and a size cap, or `data/` grows unbounded. Simplest honest
answer: TTL-only (default 7d), swept by the existing app-analytics weekly cron,
with a documented max — mirroring the vault-structure `[retention]` convention.
Decide before implementation.

---

## Item 3 — robots.txt + rate limiting (M, ~half day)

We currently have **neither**, on any path. This is a politeness/liability gap
independent of any borrow, and the cheapest of the three to get right because
the surface is one function.

### robots.txt

Fetch + cache `/robots.txt` per host, honour `User-agent: *`, longest-prefix
match. Consult in `read_web_source` before the browse call.

⚠ **Policy decision — needs sign-off.** wigolo checks robots on `crawl` only,
never on single-URL `fetch`, reasoning that a user-directed fetch is not
crawling. Options:
- **(a) Honour on all fetches.** Safest, most conservative. Risk: breaks
  legitimate user-directed reads of sites that blanket-disallow (many news sites
  disallow `/` for non-Google agents), which would silently degrade `explore`.
- **(b) Honour on automated/background paths** (kb-gap-miner sweeps, daily-brief)
  **, skip for user-initiated single reads** (assistant answering a live
  question). Matches wigolo's reasoning and the actual ethical distinction.

**Recommendation: (b)**, with the automated/interactive split passed explicitly
by the caller — never inferred — so the choice is visible at each call site.

### Rate limiting

Per-host concurrency + min-delay, plus `Retry-After` parsing on 429 with a hard
cap (wigolo caps at 300s; a hostile `Retry-After: 99999` must not wedge a run).
`explore`'s bounded-parallel reads (`app.py:426`) already cap *total*
concurrency — this adds the *per-host* dimension it lacks, which is what actually
matters when 5 of 6 results share a domain.

⚠ Module: same `web_politeness.py` as robots, or inside `web_cache.py`? They
share the per-host state and the SQLite index; recommend **one module,
`emptyos/sdk/web_politeness.py`**, with the cache separate.

---

## Suggested order

1. **Item 1** (snippets) — 1h, zero risk, unblocks 1b. Ship standalone.
2. **Item 3** (robots + rate limit) — correctness/politeness gap we have *today*;
   independent of the other two.
3. **Item 1b** (triage) — measure before/after fetch counts.
4. **Item 2** (cache) — largest, and easiest to size correctly *after* 1b reduces
   what's being fetched in the first place.

Item 1 alone is not a win; 1+1b is the first real saving. Item 2 is the biggest
saving but also the only one that adds a persistent store, so it goes last.

## Not in scope

- Consolidating the two DuckDuckGo implementations (drift, both work — see
  Item 1 correction).
- Anything from wigolo's code (AGPL; see the borrow verdict).
- MCP sampling / tiered fetch escalation — already deferred rows in
  `docs/DEFERRED-WORK.md` with their own triggers.
