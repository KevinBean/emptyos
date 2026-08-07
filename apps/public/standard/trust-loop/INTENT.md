# Intent — Trust Loop

> Living design doc for `apps/public/standard/trust-loop/`. Edit as the app evolves.
> Birth certificate: the "How to Build Trustworthy Engineering Software in the
> AI Era" article (vault: `30_Resources/Published/posts/drafts/`), whose seven-stage
> loop this app demonstrates live. Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why

The public demo had zero engineering surface, while the article that links to
it walks a seven-stage verification loop with a concrete anchor — IEEE Std 80
tolerable touch/step voltages (188.65 V / 262.48 V, ±0.5%). This app lets an
article reader *run* the loop's artifacts instead of reading screenshots: a
calculator over a pure engine, a live conformance gate pinned to the standard's
published case, KB citations behind every input, and the working shown line by
line. It deliberately implements only the textbook closed forms — the full
grid-analysis engines stay private; nothing here touches them.

## Relationships

**Calls into** (`self.call_app(...)`):
- (none) — the compute path is the pure in-app `ieee80.py` module by design

**Emits:**
- (none)

**Listens for** (`@on_event`):
- (none)

**Optional apps:**
- `kb` — "?" popovers cite the `ieee-80-touch-step-voltages` formula note;
  absence is tolerated (buttons feature-detect)

## Open questions
- Should a second public-safe method join the gate table so the conformance
  panel shows more than one anchored row? (Deferred until the first row has
  demonstrated its value on the live demo.)

## Future
- Link the loop strip to the published article URL once the post ships
- A downloadable one-page PDF of the working (stage 6 made literal) via
  `self.render_pdf` — only if demo visitors actually ask for it
