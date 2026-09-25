# EmptyOS — Canonical Messaging

> Single source of truth for the EmptyOS **platform** pitch. When marketing
> copy needs to be written or updated — README, the project site
> (eos.binbian.net), the in-app welcome surface, social posts, a new
> distribution's landing — start here so every surface says the same true thing.
>
> This file owns *positioning* (what the brand says). For *operations* — how the
> brand actually runs (the weekly draft → stage → approve loop, which surfaces
> draft content, the human approval gate) — see
> [`../docs/BRAND-OPS.md`](../docs/BRAND-OPS.md).

This file is the *bilingual source*. The English and 中文 copy live in two fully
separate sections below — they are **never mixed on a rendered surface**.

## Hard rules

1. **One language per surface.** A given page / post / landing renders English
   *or* 中文, never both interleaved. Pick the audience's language and ship that
   section whole. (Mirrors the English-source i18n model — see
   `project_i18n_translate_capability`: surfaces are single-locale; the other
   locale is a separate derived/curated artifact.)
2. **Distributions may narrow the hook.** Branded distributions built on EmptyOS
   (e.g. Plekto) intentionally use a sharper, audience-specific hook. Don't
   flatten them into this broad platform pitch — record the override in
   [§ Surface map](#surface-map).
3. **Accuracy is the rule.** Every number is verified against the codebase and
   drifts over time — re-check before reuse:
   - Public-clone apps (`core` + `standard`):
     `find apps/public/core apps/public/standard -name manifest.toml | wc -l` → **71** (checked 2026-08-22)
   - Plugins: `find plugins -maxdepth 2 -name manifest.toml | wc -l` → **36** (checked 2026-08-22)
   - Capabilities: **16** (CLAUDE.md § 16 Capabilities)
   - The live full system (demo / your machine) carries more apps than a public
     clone — the project site's auto-stats block shows that larger number. Use
     **57** ("what a fresh install ships"); never put a hard app-count next to
     the auto-stats block, where it would contradict.

## Voice & rules (carried from the `promote` app)

Mirrors `apps/extension/dev/promote/prompts.py` so generated and hand-written
copy share one voice. Applies to both languages.

- **Builder-to-builder.** Concrete, technical, understated. Let the work
  impress; don't editorialize that it's impressive.
- **Specific over grand.** Name the actual mechanism, not vague benefits.
- **Ground every claim.** If the source doesn't state a number, don't invent one.
- **No hype words** — "revolutionary", "game-changing", "supercharge", "10x".
- **No fabricated metrics, fake user counts, fake testimonials. No CTA begging.**
- **Branding (Rule 14):** provider names (Ollama/OpenAI/Claude) are fine in
  *marketing* copy as factual supported providers; NOT in in-product UI/prompts/
  errors (use generic terms there).

---

## Short-form social posts (the punchy single-angle pattern)

The platform pitch above is *broad* — right for a README or landing page, wrong
for a tweet/小红书/朋友圈 post. Short-form copy that travels uses a tight formula
(observed in the kind of "5MB Rust agent" / "精确到秒的分镜" posts that circulate):

1. **"vs the bloated incumbent" hook** — open against a category the reader knows.
   ("提到 AI 第二大脑你想到 Notion AI、各种笔记软件的 AI 插件，但 EmptyOS 不一样…")
2. **ONE sharp differentiator** — not the whole system. A short-form post that
   tries to describe all of EmptyOS reads as vague.
3. **Category positioning** — say what it *isn't*, sharply.
4. **Honest social proof** — see the hard rule below.
5. **Named audience** — who this is for, concretely.
6. **Link + low-key CTA** (no begging — voice rules above).

### The load-bearing discipline: one angle per post

EmptyOS has **no single-number hook** (it's a 150-app OS, not "a 5MB binary").
So each short post picks exactly ONE angle and ignores the rest. Rotate across
the honest angles below; never cram two into one post.

| Angle | The sharp line |
|---|---|
| **With you, not for you** | Reversible/internal actions auto-run (audit + one-click undo); irreversible/outbound/billing stay human-gated. Not "does it for you" — does it *with* you. |
| **Vault = external hard drive** | Your data is plain markdown, swappable, human-readable; usable even without the system. The AI never owns it. |
| **Conversation mode** | The system evolves by loading its own codebase as context — you grow it by talking to it. |
| **Local-first + consent gate** | Ollama by default; cloud is opt-in and your raw vault never leaves the machine without explicit consent. |
| **Own, not rent** | A workspace you own for years, not an agent you rent by the month. |

### Honest social proof — single-author, not a fake crowd

Posts in this genre often lean on "27+ contributors" / user counts. EmptyOS is a
**solo build-in-public** — so the credible proof is the *shipping cadence*, pulled
**live** from the `progress` app / `git log`, never hardcoded (numbers drift; the
doc's accuracy rule applies). Inventing a contributor count, user count, or
testimonial directly undercuts the NIW-credibility goal of the whole brand track
and violates the "no fabricated metrics" voice rule. The relentless-solo-shipping
story *is* the hook — don't fake the crowd version. (See `feedback_portfolio_no_invent`,
`project-emptyos-brand-ops`.)

### Worked example (中文, angle = "with you, not for you")

> 提到 AI Agent，你想到的可能是替你点外卖、替你发邮件、替你 push 代码——然后某天
> 发现它替你删了不该删的东西。
>
> EmptyOS 不一样。默认规则按**可逆性**分：可逆的内部动作自动跑（审计 + 一键撤销），
> 不可逆的 / 对外的 / 花钱的永远等你点确认。不是"帮你做"，是"跟你一起做"。
>
> 一个 markdown vault 当硬盘——外挂、可换、纯文本，数据永远是你的。本地模型优先，
> 上云要你同意。不是又一个套壳 Notion，是一个你能用对话本身去进化的系统。
> 开源，单人 build-in-public。
>
> 🔗 [link]

Note: naming competitors (LangChain / Notion / AutoGPT) in *marketing* copy is
fine — Rule 14's no-third-party-branding constraint applies to in-product
UI/prompt strings, not to outbound posts.

The `promote` app generates per-shipped-work social copy in this voice; this
section is the *form template* a human (or the drafter) reuses for a punchy,
single-angle post rather than the broad platform blurb.

---

## English

**Positioning (one sentence)**

EmptyOS is a local-first AI operating system you own: tasks, projects, notes,
knowledge base, creative work, and automation in one extensible system — not
another chat assistant bolted on.

The differentiator, in five words: **own a workspace, not rent an agent.**

**Taglines**

| Length | Copy |
|---|---|
| Motto | Think and create *with* you, not *for* you. |
| Short | A local-first AI operating system for your mind. |
| Hook | Own a long-term AI workspace, not a rented agent. |

**Feature-line pitch** (scannable form — one emoji per line, no more)

> 🧠 A markdown vault is the hard drive — your data stays human-readable, portable, and usable even without the system.
> ⚙️ 71 built-in apps + a plugin system — tasks, projects, journal, search, AI assistant, publishing, and creative workflows in one place.
> 🔁 An event bus + scheduler drive automation — capture → organize → review → self-fix closes the loop.
> 🤖 Ollama / OpenAI / Claude / any OpenAI-compatible provider — route models per task.
> 🔒 Cloud calls pass a consent gate — by default your raw vault never leaves the machine.
> 🛠️ Conversation mode drops an AI coding tool straight into the system's context — it keeps generating new apps, abstractions, and workflows.

**Full launch blurb**

> **EmptyOS: a local-first AI operating system for your mind**
>
> Not another chat assistant bolted on — tasks, projects, notes, knowledge
> base, creative work, and automation, all inside one extensible local OS you own.
>
> *(the six feature lines)*
>
> For people who want to **own** a long-term AI workspace, not rent an agent.

**Who it's for**

- Builders who want to **build** their own AI workspace, not buy someone else's.
- People who want their data in plain markdown, on their own hardware.
- Anyone who wants capability-level control — which model, local vs cloud, with
  explicit consent.
- Comfortable with Python / FastAPI / `pip install -e .`.

---

## 中文

**定位（一句话）**

EmptyOS 是一个你自己拥有的本地优先 AI 操作系统：把任务、项目、笔记、知识库、
创作和自动化都放进一个可扩展的本地 OS，而不是再接一个聊天助手。

核心差异，五个字：**拥有，而非租用。**

**标语**

| 长度 | 文案 |
|---|---|
| 座右铭 | 与你一起思考创造，而不是替你做。 |
| 短 | 本地优先的 AI 个人操作系统。 |
| 钩子 | 拥有自己的长期 AI 工作台，而不是只租一个 Agent 助手。 |

**特性列表**（可扫读形式 —— 每行至多一个 emoji）

> 🧠 markdown vault 作为硬盘，数据人可读、可迁移、可脱离系统继续使用。
> ⚙️ 71 个标准 app + 插件系统，任务、项目、日记、搜索、AI 助手、发布、创作工作流一体化。
> 🔁 event bus + scheduler 驱动自动化，捕获、整理、回顾、修复形成闭环。
> 🤖 支持 Ollama / OpenAI / Claude / OpenAI-compatible provider，可按任务路由模型。
> 🔒 云模型调用走 consent gate，默认不把 vault 原文发到云端。
> 🛠️ conversation mode 让 AI coding tool 直接进系统上下文，持续生成新 app、抽象和工作流。

**完整发布文案**

> **EmptyOS：本地优先的 AI 个人操作系统**
>
> 不是再接一个聊天助手，而是把任务、项目、笔记、知识库、创作和自动化都放进一个可扩展的本地 OS。
>
> *（六条特性）*
>
> 适合想拥有自己的长期 AI 工作台，而不是只租一个 Agent 助手的人。

**适合谁**

- 想**自己造**一个 AI 工作台，而不是买别人现成产品的人。
- 想把数据放进纯 markdown、留在自己机器上的人。
- 想要能力级控制 —— 用哪个模型、本地还是云、都要明确同意 —— 的人。
- 熟悉 Python / FastAPI / `pip install -e .`。

---

## Surface map

Where the message lives, which language, and which form each surface uses. Every
surface is single-language.

| Surface | File / location | Brand | Language | Form |
|---|---|---|---|---|
| Platform README | `README.md` | EmptyOS | EN | Long pitch + feature-line block |
| Project site | `eos.binbian.net` (vault `30_Resources/EmptyOS-Site/`) | EmptyOS | EN | Motto + long pitch |
| Public onboarding | `docs/GETTING-STARTED.md` | EmptyOS | EN | Motto + short pitch |
| In-app welcome | `apps/extension/portfolio/welcome/` | EmptyOS | EN | Motto only (programme listing) |
| Social copy generator | `apps/extension/dev/promote/` | — | per-post | Per-shipped-work, voice above |
| Chinese social / posts | (ad-hoc) | EmptyOS | 中文 | Ship the 中文 section whole |
| **Plekto landing** | `brand/plekto/site/index.html` | **Plekto (distribution)** | EN | **Narrower override — see below** |

### Distribution override — Plekto

Plekto is a curated **distribution** of EmptyOS for Claude Code users. Its
landing page leads with a sharper, narrower hook — keep it:

> **Give Claude Code a memory, a project, and a team.**
> Persistent memory · project workspaces · multi-agent rooms.

This is deliberately not the broad platform pitch ("specific over grand" makes
the narrow hook stronger). The page's *shared truth claims* still align with this
canon: local-first, consent-gated, AGPL-3.0, markdown data you own, "own not
rent". English-only, like the rest of the Plekto site.

---

## Maintenance

When the platform's shape changes (apps shipped, a capability added, a provider
chain changed), update this file first, then propagate to the surfaces above.
Re-run the count commands before reusing any number. Keep the two language
sections in sync in *meaning*, not word-for-word — each is curated copy, not a
machine translation of the other.

**Last accuracy check: 2026-08-22** (`eos-recency-check` — counts had drifted
from 57/26 to 71/36 since the last check on 2026-06-29; `README.md`'s copy of
the feature-line pitch updated in step.) Re-run before the next reuse rather
than trusting this note past its date.
