---
tags: [grill-spec]
recipe: new-app
status: approved
date: 2026-07-16
---

# Chatbot Studio

## Verb

Set up and operate a managed shopping assistant for a business website.

## Data shape

The standalone chatbot service remains the source of truth. A site has origins,
knowledge sources, a normalized product catalogue, commerce policy, branding,
budgets, health, and aggregate analytics. Credentials live only in the service's
private data directory. Existing Publish sites are tagged `managed_by = "publish"`
and are read-only here; Studio-created sites use `managed_by = "studio"`.

## Surfaces

- `/chatbot-studio/`: portfolio dashboard, health, source freshness, usage and handoff totals.
- Setup wizard: business → website/origins → knowledge → catalogue → behavior → actions → test/install.
- Generated one-script embed snippet for any website.
- Controlled printer-store pilot that demonstrates search, comparison, stock,
  explicit add-to-cart, order lookup, return eligibility, and human handoff.

## Events

- `chatbot-studio:site_created`
- `chatbot-studio:site_enabled`
- `chatbot-studio:catalog_updated`

These are operator observability events; there are no cross-app listeners in v1.

## Capabilities

No EmptyOS model or vault capabilities. The app makes server-to-server requests
to the separately deployed chatbot service using operator-configured endpoint and
admin token settings.

## Sub-patterns

- Use the existing chatbot service and widget; do not create an agent framework.
- Commerce is opt-in per site and deterministic for price, stock, compatibility,
  action policy, and order/return data.
- Cart mutation requires a user click. Refunds, cancellations, returns, and
  account changes escalate rather than execute.
- Sensitive form values bypass prompts, Q&A logs, and analytics payloads.
- Remote content is size-bounded, SSRF-checked, and fenced as untrusted data.

## Name/id

- Name: Chatbot Studio
- App id: `chatbot-studio`
- Location: `apps/extension/dev/chatbot-studio`
- Route: `/chatbot-studio`
- Release tier: `dev`

## Scaffold checklist

- [x] `manifest.toml`: app metadata, web prefix, settings, emitted events, no capabilities.
- [x] `INTENT.md`: operator intent, service relationship, safety boundary.
- [x] `app.py`: admin proxy, CSV preview/import, snippet generation.
- [x] `pages/index.html`: dashboard and setup wizard using EOS design tokens/helpers.
- [x] `tests/test_sys_chatbot_studio.py`: at least ten route/validation tests.
- [x] Extend `services/chatbot` config and admin API without breaking legacy sites.
- [x] Add normalized catalogue, deterministic shopping replies, controlled actions,
  aggregate analytics, and secrets-at-rest boundary.
- [x] Upgrade `chatbot-widget.js` for typed commerce blocks and explicit actions.
- [x] Add controlled printer-store fixture and demo connector, disabled by default.
- [x] Add `chatbot-studio` to `[tiers.dev]` in `release.toml`.
- [x] Install in the Store registry and complete build → conform → walk → simplify → live-verify.

## Acceptance criteria

1. A managed operator can create, validate, test, enable, and monitor a Studio-owned site.
2. A legacy Publish site loads unchanged and cannot be overwritten by Studio.
3. A catalogue answer never invents price or stock; cards are sourced from normalized records.
4. Add-to-cart runs only after an explicit product-card click and is idempotent.
5. Order inputs and connector secrets never enter model prompts or stored analytics.
6. Commerce is disabled by default and the old `/chat` response contract remains valid.
7. The pilot answers printer discovery/comparison questions and demonstrates handoff.

## Test baseline (birth)
- date: 2026-07-16
- pytest: 11 passed, 0 skipped (`tests/test_sys_chatbot_studio.py`); 23 passed, 0 skipped (`services/chatbot/tests/`)
- acceptance criteria mapped: 7 criteria → 34 tests (see test-file docstrings)
