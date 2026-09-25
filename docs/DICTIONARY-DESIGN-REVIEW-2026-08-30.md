# EOS Design Review — dictionary, five tabs

`apps/extension/english-learning/dictionary/pages/index.html`
Walked live on `:9000`, theme `tatami`, 2026-08-30. Mode: **review** (nothing edited).

## Archetype

Mixed, and that is the root of most findings. Each tab is a different archetype
sharing one shell:

| Tab | Archetype | Height |
|---|---|---|
| Today | list | 2,886 px |
| Look up | generator + detail | 1,240 px |
| My words | dashboard + list | **51,127 px** |
| Pictures | list (browse) | 8,745 px |
| Practice | instrument (mode picker) | 1,242 px |

A 41× density spread across sibling tabs is the headline. Nothing is wrong with
a long list; what is wrong is that the longest surface is the one with no
filter chrome and the shortest is the one with no content.

## Mechanical passes

| Check | Result |
|---|---|
| Hex colours (DL-1) | **0** — token-clean |
| `scripts/check-ios-safe-area.py` | **clean** |
| Theme bootstrap | present |
| Em-dashes | 24 total, **~9 user-facing** (rest are code comments) |
| Mobile breakpoints | one, at 720 px (canonical is 640) |
| Inline `<style>` | **260 lines** vs the ~60-line S-2 budget |

## Findings

Tags per the seven-question rubric.

| # | Tag | Issue | Fix (tokens/scale only) |
|---|---|---|---|
| 1 | density | My words is 51,127 px — 618 word buttons, **zero** search/filter/sort | Add a filter input above Threads; it is the one surface that most needs the chrome Pictures already has |
| 2 | affordance | Filter chrome is **inverted** across tabs: Pictures has sub-tabs + 2 chip rows + search; My words (504 words) has none | Move the search pattern from Pictures to My words |
| 3 | hierarchy | `.vocab-stats` is `display:block`, so four shared `.eos-stat-card`s stack full-width and burn ~350 px | `display:grid; grid-template-columns:repeat(4,1fr); gap:8px` + `repeat(2,1fr)` at the breakpoint — verbatim the `#cal-stats` house pattern |
| 4 | rhythm | Look up stacks **seven** identical uppercase micro-labels (CHINESE / DEFINITION / EXAMPLE / SYNONYMS / ANTONYMS / ETYMOLOGY / USAGE NOTES) | Drop CHINESE and EXAMPLE (the 中文 glyphs and the italic + left-rule already say it). Keep SYNONYMS/ANTONYMS — chips are not self-labelling |
| 5 | hierarchy | Look up puts CHINESE **above** DEFINITION — the translation outranks the definition in an English dictionary | Definition first, Chinese after |
| 6 | weight | ETYMOLOGY is the only section with a filled card — heaviest treatment on the most tertiary field | Match the other sections; plain body text |
| 7 | weight | My words gives `0 MASTERED` the same tile weight as `504 TOTAL`, and `336 LEARNING` / `336 DUE TODAY` are the same number twice | Consider `--muted` variant for a zero stat; drop one of the duplicate 336s |
| 8 | affordance | Today's harvested rows render a raw vault path as **plain text** (`from: 30_Resources/Web-Clips/…md`) — widest element in the row, and not clickable | `EOS.noteActions(path)` — shared-frontend rule says vault paths are always clickable |
| 9 | hierarchy | The same entity (a word) has **four** row anatomies: Today's-words rows, harvested rows, Look-up detail, My-words chips. Today's-words puts 中文 on its own line; harvested inlines it after an em-dash | Converge the two Today variants at minimum |
| 10 | density | Pictures renders ~3/4 of its grid as empty placeholder cells at full card height | Default the browse filter to entries that have an image, or render image-less entries in a denser text row |
| 11 | weight | Practice offers three equal-weight ghost buttons and no primary; `336 due today` — known one tab over — is invisible here | Promote one mode to primary; surface the due count on this tab |
| 12 | affordance | 14 distinct emoji across five tabs (📖 🌿 💡 📈 🗂 🃏 ❓ ✍️ 🔤 ⬆ ⬇ 🎧 💾 🔊) mixed with geometric controls | Converge on one family; the DL prefers drawn SVG over emoji |
| 13 | restraint | ~9 user-facing em-dashes, two doing structural work (`definition — 中文`; `—` as an empty-cell placeholder) | Replace with a colon, a line break, or an empty cell |

## Platform finding (bigger than this app)

**`.eos-model-pill` wraps instead of truncating.**
`emptyos/web/static/eos-components.css:1078` sets `max-width: 260px` with no
`white-space: nowrap`. Measured on `/dictionary/`: the pill is exactly **260 px
wide and 43 px tall** — "openai-mini" and "gpt-5.4-mini" each break across two
lines, in an 860 px header with room to spare.

It is content-conditional, which is why it went unnoticed: `/viz/` resolves to
`claude-cli` with no model subtitle (177 × 25 px, no wrap). Any app pinned to a
provider whose name + model exceeds 260 px wraps the same way.

Fix, in the shared component: `white-space: nowrap` on the pill, plus
`overflow:hidden; text-overflow:ellipsis; min-width:0` on `-name` / `-sub` so
the existing `max-width` truncates as intended. No colour involved, so it is
theme-safe by construction.

## Refused / out of scope

- **Pictures' empty grid** — whether those entries should be filtered, generated
  or deleted is a content decision, not a CSS one.
- **618 buttons for 504 words** — topic threads repeat the same word across
  cards (`animals` and `mammals` share 11). That is a data-shape question.
- **260-line inline `<style>`** — over the S-2 budget, but extracting it is a
  `multi-module-apps` split, not a design pass.

## Recommended order

Smallest blast radius first:

1. **#3** `.vocab-stats` grid — one CSS rule, ~350 px reclaimed, copies a sibling app.
2. **Platform pill** — one CSS rule in the shared bundle, fixes every pinned app.
3. **#4 / #5 / #6** Look-up label pruning and section order — markup-local.
4. **#8** clickable vault path — uses an existing helper.
5. **#13** em-dashes — text-only.
6. **#1 / #2** My-words filter — the largest win, and the largest change.
7. **#9 / #12** row-anatomy and icon convergence — cross-tab, do last.

Items #7, #10, #11 need a product call before a design one.
