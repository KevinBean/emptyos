# EmptyOS website chatbot → shopping assistant research

**Date:** 2026-07-16
**Scope:** What EmptyOS already has, how current website-chatbot products work, and the smallest credible first target for an Officeworks-like shopping site.
**Decision type:** Product/architecture research only. This is not a borrow-verdict and proposes no external framework.

## Executive verdict

EmptyOS already has a real website-chatbot stack. It is not merely an idea:

- `services/chatbot/` is a standalone FastAPI service with origin locking, per-IP rate limits, daily cost caps, FAQ and curated-answer fast paths, embedding/BM25 retrieval, cited answers, streaming, and a SQLite Q&A ledger.
- `apps/public/standard/publish/` emits `corpus.json`, injects a drop-in widget, exposes per-site model/persona/budget/starter settings, and provides a Q&A review/promote-to-FAQ loop.
- The widget persists a session, streams replies, and renders source chips.

The current product is a **cited content-Q&A bot**, not a commerce assistant. Its corpus contains prose chunks and FAQs only. It has no typed product catalog, live price or inventory, rich product cards, cart actions, authenticated order access, or human handoff. Adding a new site also still requires a manual VPS edit before the existing settings sync can work.

**Recommendation:** evolve the existing Lane-1 chatbot service; do not build a second chatbot engine. The first target should be a controlled, Officeworks-like **printer finder** for a store we own or have permission to integrate, not Officeworks itself. It should answer policy questions from the existing cited corpus and recommend at most three products from a structured, freshness-stamped catalog.

## 1. EmptyOS baseline

### What is already shipped

| Capability | Evidence | Grade |
|---|---|---|
| Public, no-vault chatbot service independent of the daemon | `services/chatbot/README.md`, `docs/DEPLOYMENT.md` | read-verified |
| FAQ → curated cache → retrieved context → LLM answer path | `services/chatbot/main.py`, `corpus.py` | read-verified |
| Embedding retrieval with BM25 fallback and a low-confidence refusal path | `services/chatbot/main.py`, `embed.py` | read-verified |
| Origin allowlist, input/output caps, per-IP limits, per-site/global spend caps | `services/chatbot/main.py`, `ledger.py`, `config.py` | read-verified |
| SSE streaming and validated citation IDs | `services/chatbot/main.py` | read-verified |
| Drop-in floating widget, starter prompts, session history, source chips | `apps/public/standard/publish/static/chatbot-widget.js` | read-verified |
| Publish-site build emits a chunked `corpus.json` and injects widget metadata | `apps/public/standard/publish/builder.py` | read-verified |
| Operator can review, edit, reject, curate, and promote answers to `faqs.toml` | `apps/public/standard/publish/chatbot.py` and Publish UI | read-verified |
| Existing local Publish profiles point at `chat.binbian.net` but currently have chatbot disabled | `data/apps/publish/sites.json` inspected 2026-07-16 | read-verified current local state |

### Where the present setup stops

| Gap | Why it matters for retail | Grade |
|---|---|---|
| New service sites cannot be created through the admin API; name, allowed origins, and corpus URL require manual SSH/TOML editing | It prevents a true self-service setup wizard | read-verified (`main.py` explicitly says “sync, not create”) |
| `corpus.json` has prose fields (`id`, `type`, `title`, `section`, `tags`, `url`, `text`) but no product fields | Price, stock, variants, compatibility, and hard filters cannot be trusted as live structured facts | read-verified |
| Widget renders text and source chips only | A shopping assistant needs images, prices, availability, compare, and product/card actions | read-verified |
| No customer identity or commerce actions | No cart-aware recommendations, order lookup, returns, or account-specific answers | read-verified |
| No live-agent inbox/handoff | Failed or sensitive requests have nowhere to go beyond a refusal | read-verified |
| Ledger tracks requests, cost, and Q&A curation—not assisted product views, clicks, cart adds, or task completion | It cannot yet show commercial usefulness | read-verified |
| Only OpenAI provider is implemented despite a provider interface | Model portability is designed but not presently delivered | read-verified |
| No dedicated chatbot-service tests are present in the current test tree | Commerce changes would lack an existing regression harness | counted/read-verified |

The go-live note still lists RAG/embeddings as future work even though current code implements it. That documentation is stale and should be corrected if implementation begins.

## 2. What similar products teach us

This comparison uses vendor documentation for current capabilities. Marketing outcome claims are intentionally excluded from the recommendation.

| Product | Setup/data pattern | Commerce capability worth learning from | Strategic reading |
|---|---|---|---|
| **Shopify Inbox** | Near-zero setup because Shopify already owns the catalog, inventory, policies, customer identity, cart, and orders | Catalog-aware answers, product recommendations, order updates, customer/cart context, live chat | The best setup experience comes from owning the data plane; EmptyOS must recreate that clarity through an explicit feed/API contract |
| **Gorgias Shopping Assistant** | Connects Shopify catalog + website content + merchant-added product facts and guidance | Context-aware similar/complementary/popular recommendations, visual cards, add-to-cart, promotion/exclusion controls | Product facts and selling guidance are separate concepts; merchant control matters as much as retrieval |
| **Tidio Lyro** | Connects Shopify, WooCommerce, product feed, or API; installs by plugin or one JS line | Follow-up questions, product cards, real-time stock/variant sync, cart-aware recommendations, Shopify add-to-cart | A platform-neutral product feed is the right first connector; native store adapters can follow |
| **Intercom Fin** | Syncs websites/content and adds data connectors/workflows | Shopify order lookup, escalation rules, human handoff, multi-channel deployment, reporting | Content Q&A becomes customer service only when live data, procedures, and escalation exist |
| **Botpress** | Crawls a domain/sitemap, accepts documents/tables/API sources, then provides an embed script and widget settings | Structured tables alongside unstructured knowledge; allowed origins, feedback, proactive messages, storage controls | The setup tool should visibly separate website knowledge, structured catalog data, widget configuration, and deployment |

### Shared product pattern

The credible products converge on this setup flow:

1. **Connect knowledge** — website, sitemap, help centre, documents, policies.
2. **Connect structured business data** — catalog/feed/API, inventory, customer/order data where authorised.
3. **Define behaviour** — voice, rules, exclusions, escalation, allowed actions.
4. **Test before live** — preview conversations and inspect the evidence/actions used.
5. **Install** — native platform app where possible; otherwise one script snippet with domain verification.
6. **Operate** — review failures, feedback, task completion, assisted revenue, and stale sources.

EmptyOS presently covers parts of 1, 3, 5, and 6 for its own Publish sites. The missing retail core is 2 plus rich rendering/actions.

## 3. Recommended first target

### Target definition

Build one **pre-purchase printer finder** for an owned or explicitly authorised Officeworks-like demo store, initially with roughly 100–500 products.

Why printers:

- The choice is constrained by objective attributes: budget, colour/mono, laser/inkjet, print volume, duplex, scanner, Wi-Fi, paper size, and ink/toner compatibility.
- It forces the assistant to ask a useful clarifying question rather than merely semantic-search descriptions.
- It supports honest comparison and complementary recommendations (ink/toner, paper, cables) without needing customer identity.
- Hard-filter accuracy is testable deterministically.

Officeworks is a useful UX benchmark because its site exposes a large catalog, store-dependent availability, Click & Collect, delivery, returns, and order tracking. It should **not** be used as the first production integration without permission and stable product/inventory/cart APIs. Public-page scraping cannot guarantee current price, local stock, or cart behaviour.

### The first complete customer journey

> “I need a home-office printer under $300, automatic duplex, Wi-Fi, and cheap running costs.”

The assistant should:

1. Ask at most one necessary clarifying question, such as colour vs monochrome or approximate pages per month.
2. Apply hard constraints deterministically to the structured catalog.
3. Return no more than three **currently eligible** products as cards with image, price, availability timestamp, and concise reasons.
4. Let the shopper compare those products.
5. Deep-link to the product page; add-to-cart is enabled only when a supported store adapter exists.
6. Answer delivery/returns/policy questions from the existing cited content corpus.
7. Say it does not know when catalog freshness or evidence is insufficient.

### Explicitly out of scope for the first slice

- Order status, returns execution, refunds, address changes, or any authenticated account action.
- Omnichannel email, SMS, WhatsApp, Instagram, or voice.
- Proactive discounting, shopper profiling, purchase-history personalisation, or autonomous upsell.
- A full live-agent help desk.
- Scraping Officeworks production pages as a substitute for an authorised feed.

Those capabilities are valuable, but each introduces identity, PII, irreversible actions, integration-specific semantics, or an operational support queue. They would obscure whether the product-finding core works.

## 4. Smallest honest architecture

### Reuse, do not replace

Keep `services/chatbot/` as the public runtime and extend its existing config, retrieval, ledger, admin, and widget contracts. Keep the service no-vault and independent of the EmptyOS daemon.

Do **not**:

- create another generic agent runner;
- put commerce logic into the in-daemon conversation backends;
- treat product pages as prose and let the LLM invent filters;
- add an SDK/decorator abstraction before two real, same-shape store adapters exist.

### Add one typed product feed

Introduce a platform-neutral catalog contract alongside the existing content corpus. Minimum product record:

```text
id, title, url, image_url, price, currency, availability,
category, attributes, variants, related_product_ids, updated_at
```

The service should perform hard filters and availability checks in code. The model may ask questions, rank eligible results, and explain trade-offs, but it must not generate price, stock, variant, or compatibility facts.

Start with a static JSON/CSV-to-JSON feed owned by the demo store. A second real merchant can justify a connector boundary; Shopify or WooCommerce would be the obvious next adapters based on market demand, but that is an **inference**, not yet a repo-grounded requirement.

### Turn setup into an actual tool

The setup surface should guide an operator through:

1. Site name, production origin, and ownership/domain check.
2. Knowledge source: existing EmptyOS `corpus.json`, sitemap/site crawl, or explicit URLs.
3. Product source: validated JSON/CSV feed or later a store connector.
4. Behaviour: role, tone, allowed categories, promoted/excluded products, refusal and escalation text.
5. Widget: colours, launcher, welcome text, starter questions, preview.
6. Test bench: fixed shopping prompts, expected constraints/products, policy questions, and off-topic/adversarial prompts.
7. Deploy: create the service-side site profile, generate/copy one embed snippet, verify origin/health/source freshness.
8. Operate: unanswered questions, wrong-result feedback, product clicks, compare use, cart adds, cost, and stale-feed warnings.

The first UI may remain in Publish for an EmptyOS-built demo. If the product goal is arbitrary third-party websites from day one, a separate control app is justified—but it must call the same chatbot-service admin API rather than duplicate runtime logic. Final app/product naming should be approved before implementation.

### Security and trust gates

- Admin site creation must require strong admin authentication; browser `Origin` is an abuse gate, not operator identity.
- Verify or explicitly approve fetched domains and block private/link-local addresses to prevent SSRF.
- Treat crawled/site content and product descriptions as untrusted data, never as instructions.
- Render price/availability only from structured results with `updated_at`; stale data gets a visible warning or is withheld.
- Keep order/account work deferred until an authenticated, least-privilege customer-data contract exists.
- Preserve hashed-IP/minimal-retention defaults and avoid logging customer PII in prompts or the Q&A ledger.

## 5. Suggested delivery slices

### Slice 0 — benchmark before UI

- Create a representative printer catalog fixture and 30–50 deterministic queries.
- Grade hard-filter compliance, compatible-supply correctness, refusal quality, and latency.
- Record the existing content-Q&A behaviour as a regression baseline.

### Slice 1 — self-service site + catalog setup

- Add safe admin creation/update for complete site profiles.
- Validate source URLs and freshness.
- Provide setup preview, snippet generation, and live health checks.
- Remove the one-time SSH/TOML requirement for normal onboarding.

### Slice 2 — shopping response

- Add typed catalog ingest and deterministic filtering.
- Add rich product cards and compare state to the existing widget.
- Keep action to product-page deep-link only.

### Slice 3 — one store action + analytics

- Add one reversible cart adapter for the controlled demo store.
- Record product impressions, clicks, compares, cart attempts/successes, unanswered intents, and cost.
- Run the test bench plus a real browser walk.

Only after these slices should human handoff, authenticated order lookup, or additional commerce platforms be considered.

## 6. Acceptance criteria for the first target

These are proposed product gates, not industry benchmarks:

- Setup from a valid site + catalog feed to a working preview in **under 15 minutes**, with no server shell access.
- **100% hard-constraint compliance** on the deterministic benchmark (budget, required features, availability).
- **Zero model-authored prices, stock states, variants, or compatibility claims**.
- At least **80% task completion** on the curated printer-finder journeys.
- Every policy answer has a valid source or refuses.
- Every product card links to a valid current product record and shows freshness.
- Off-topic and prompt-injection cases cannot alter assistant policy or source configuration.
- Operator can see unanswered intents and correct/promote knowledge without database or shell access.

## 7. Decision summary

| Decision | Verdict | Evidence grade |
|---|---|---|
| Does EmptyOS already have a website chatbot? | Yes—substantial runtime, widget, Publish setup, and review loop exist | read-verified |
| Is it currently a shopping assistant? | No—it is content Q&A only | read-verified |
| Build or buy for the first experiment? | Build the smallest commerce extension on the existing service; use competitors as behaviour references, not code/framework dependencies | inferred from read-verified architecture + vendor comparison |
| First target | Owned/authorised Officeworks-like printer catalog; pre-purchase discovery only | inferred/recommended |
| First architectural addition | Typed catalog feed + deterministic filters + product cards | inferred/recommended |
| First setup improvement | Admin-created site profiles and no-SSH wizard | read-verified gap + inferred solution |
| What not to build yet | Generic omnichannel help desk, order/refund agent, broad connector SDK | inferred/recommended |

## Sources

### EmptyOS primary sources

- `services/chatbot/README.md`, `main.py`, `config.py`, `corpus.py`, `embed.py`, `ledger.py`
- `apps/public/standard/publish/app.py`, `builder.py`, `chatbot.py`, `pages/index.html`, `static/chatbot-widget.js`
- `docs/DEPLOYMENT.md`, `docs/PUBLISHING.md`, `docs/CONVERSATION-STACK.md`
- Git history: initial chatbot stack `924ccd21`; retrieval expansion `5d9b28a5`

### Current product/vendor sources

- Shopify Inbox: <https://apps.shopify.com/inbox> and <https://help.shopify.com/en/manual/inbox>
- Gorgias Shopping Assistant: <https://docs.gorgias.com/en-US/how-shopping-assistant-recommends-products-4996877> and <https://www.gorgias.com/ai-agent>
- Tidio Lyro product recommendations: <https://www.tidio.com/ai-agent/product-recommendations/> and installation: <https://help.tidio.com/hc/en-us/articles/5378348485660-Install-Tidio-on-Your-Website>
- Intercom Fin FAQ/setup: <https://www.intercom.com/help/en/articles/7837535-fin-ai-agent-faqs>, website sync: <https://www.intercom.com/help/en/articles/9357945-sync-and-manage-websites>, Shopify data connectors: <https://www.intercom.com/help/en/articles/9909996-use-data-connector-templates>
- Botpress knowledge sources: <https://www.botpress.com/docs/studio/concepts/knowledge-base/add-sources/> and webchat quickstart: <https://botpress.com/docs/webchat/get-started/quick-start/>
- Officeworks benchmark context: <https://www.officeworks.com.au/>, delivery/Click & Collect: <https://www.officeworks.com.au/help-centre/delivery>, contact/order support: <https://www.officeworks.com.au/contact-us>
