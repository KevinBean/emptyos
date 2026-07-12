---
name: eos-ui-walk
description: Act as Kevin — the user of EmptyOS — and walk the product by hand through a real browser, performing real use-case workflows end to end (capture a task, write a journal entry, look something up in the KB, add a job application, run a learn session…). Screenshot the meaningful steps, judge each one (works / slow / confusing / broken), and log it all into a self-contained HTML report to show the user. Designed to be re-run on a /loop ("dogfood the UI"). Use when the user says "UI walk", "dogfood the UI", "act as me and use the app", "walk the real use cases", "screenshot the steps", "test it like a user", or loops this skill. NOT for visual/design polish (use eos-page-design-review / eos-design-system-audit), NOT for backend correctness — async-loop wedges, vault read-modify-write races (use eos-bug-audit), NOT for architecture/wiring (use eos-architecture-review).
---

# EmptyOS UI Walk — dogfood as the user

**Be Kevin.** Don't load pages to check for 404s — *use the product the way its
owner uses it*, complete real tasks end to end, and notice the friction only a
real user feels: a step that's slow, a button whose result is invisible, a flow
that needs one click too many, an empty state that doesn't tell you what to do,
a form that loses your input. The deliverable is a **screenshot-backed HTML
report** the user can open and skim.

This is dogfooding, not a test suite. Your judgment per step is the signal:
`pass` (it just worked), `slow` (worked but made me wait), `confusing` (worked
but I had to think / hunt), `fail` (broke / dead end). Be honest — a clean
report that says "everything worked" is a fine and valuable outcome.

The automated scanners (`check-js-errors.py`, `check-clickable.py`) are NOT the
job here — they're a 60-second optional pre-pass for crude breakage. The job is
the **hand-walk of real use cases**.

## This is a loop

Built to run under `/loop dogfood the UI as me`. One iteration = pick a fresh
set of use cases → walk them as Kevin → screenshot + judge each step → render
the HTML report → tell the user where it is and the headline findings. Re-running
rotates to **different** use cases (don't re-walk the same five forever) until
coverage of the real workflows is broad, then say so and stop.

## Step 0 — Preflight (every iteration)

```bash
curl -s -m 5 http://127.0.0.1:9000/api/health     # daemon up? apps count
git status --short                                 # pre-existing drift (leave others' files alone)
```

- **Daemon down** → surface it and stop. NEVER restart `:9000`/`:9001` from a
  tool (`.claude/rules/daemon-handling.md`).
- This is mostly a *read* activity that produces an artifact under `data/`
  (gitignored). You only commit if you actually fix a bug (rare) — and then ONLY
  your own files by explicit path (Step 7).

## Step 0.5 — Pick a browser backend (this walk is NOT bound to any one browser)

The walk drives **generic** browser verbs (`browser_navigate`, `browser_click`,
`browser_type`, `browser_snapshot`, `browser_take_screenshot`). Any MCP browser
backend that provides them works:

| Backend | Tools | When |
|---|---|---|
| **Playwright MCP** | `mcp__plugin_playwright_playwright__browser_*` | **Default.** Headless, always available, no user setup. |
| Claude-in-Chrome | `mcp__claude-in-chrome__*` | When the user wants to watch in their own Chrome, or a flow needs their logged-in session. |

**"The Chrome extension isn't connected" is NEVER a valid reason to skip this
walk.** Fall through to the Playwright MCP and keep going. This walk is the ONLY
mechanism that catches the perceptual/affordance class of bug — panels that blur
together, a list you can't tell scrolls, a missing readout, a control that
doesn't explain itself. Every *automated* rendered check targets a narrower axis
(`check_readability.py`=contrast, `check-clickable.py`=occlusion,
`check_ui_affordance.py`=overflow + tab-roles, `check_ui_structure.py`=EOS_UI
adoption). Skipping the hand-walk silently drops perceptual coverage to **zero**
— which is exactly how a batch of CAD-workspace UX bugs shipped on 2026-07-11
behind a green `node --check` + `pytest` + `curl`.

Corollary: **a rendered-UI change is not verified until it has been looked at.**
Static checks passing is not the same as the surface being usable.

## Step 1 — Authenticate the live browser (once per run)

The daemon is password-gated, so the MCP browser starts unauthenticated and every
page 302s to `/login`. Sign it in with a one-time token deep-link — the server
sets the `eos_session` cookie and strips the token from the URL:

```bash
python -c "import tomllib;print((tomllib.load(open('emptyos.toml','rb')).get('network',{}).get('auth_token')) or '')"
```

Then `browser_navigate` to `http://127.0.0.1:9000/?token=<TOKEN>` **once**. After
the redirect you're authenticated for the whole session — every subsequent
`browser_navigate` to an app works. (If `auth_token` is empty, the daemon has no
gate; navigate straight to `/hub/`.) Override `EOS_URL` if walking a remote
daemon (Tailscale/LAN), and adjust the navigate base accordingly.

Set up the run folder + step-log path:

```
data/ui-walk/usecases/<YYYY-MM-DD-HHMM>/      # screenshots land here
data/ui-walk/usecases/<YYYY-MM-DD-HHMM>/steplog.jsonl
```

## Step 2 — Pick the use cases (act as Kevin)

Choose **5–8 real workflows** this iteration, weighted toward what Kevin actually
does. Don't script rigidly — follow curiosity the way a real user would, and let
one step suggest the next. Starter catalog (rotate across iterations; combine /
deviate freely):

- **Capture → task → done** — `/hub/` capture box → type a task → confirm it lands
  in Today / `/task/` → mark it done → confirm it disappears.
- **Daily journal** — `/journal/` write an entry + set a mood → confirm it shows on
  the hub heatmap / streak.
- **KB lookup (cable engineering)** — `/kb/` search a real concept (e.g. IEC 60287,
  ampacity) → open a `clause`/`formula` note → follow a citation/backlink.
- **AI search / ask** — `/search/` ask a vault question → read the answer → pin it.
- **Job application** — `/jobs/` (personal) add an application → set status →
  open the board view → confirm the card moved.
- **Learn / vocab** — `/learn/` start an SRS review, rate a card; or `/dictionary/`
  look up a word, save it.
- **Projects** — `/projects/` open a project detail → check the 4D timeline drawer.
- **People / expense / reminders** — add one record, confirm it aggregates.
- **A generator** — `/viz/` or `/designer/` describe an artifact → generate → look
  at the result (judge "did I get something usable?", not pixel quality).
- **Home glance** — `/hub/` cold: does the digest tell me my next move at a glance?

Prefer flows that **cross apps** (capture → task → project ripple) and flows that
**write then read back** (did my entry actually persist and surface?) — that's
where real friction hides.

## Step 3 — Walk each use case, step by step

For each meaningful step:

1. **Do the action** — `browser_navigate`, `browser_click`, `browser_type`,
   `browser_fill_form`, `browser_select_option`, `browser_press_key`. Use
   `browser_snapshot` to find elements to act on.
2. **Let it settle** — `browser_wait_for` (text/time) so the screenshot catches
   the rendered result, not a spinner.
3. **Screenshot** — `browser_take_screenshot` with an explicit `filename` like
   `uc1-s2.png`. **Record the saved path** the tool returns.
4. **Judge + log** — append one line to `steplog.jsonl`:

```json
{"usecase":"Capture a task and see it in Today","step":2,"action":"Type 'call plumber' in capture, hit Enter","status":"slow","note":"toast confirmed but took ~2s; task appeared in Today only after manual reload","shot":"/abs/path/uc1-s2.png","url":"http://127.0.0.1:9000/hub/"}
```

`status` ∈ `pass | slow | confusing | fail | skipped | info`. The `note` is the
point — write what a real user would mutter: "couldn't tell if it saved",
"had to scroll to find the button", "empty and no hint what to do". Screenshot
the **payoff** steps (the result of an action), not every navigation.

Also opportunistically catch the cheap breakage classes while you're there: a
visible JS error, a button that 404s, a dead dropdown (route returns nothing) —
log those as `fail` with the detail.

## Step 4 — Triage (false-positive discipline — `.claude/rules/audits.md`)

Before calling anything a `fail`:

- **Data-volume slowness on the real vault is NOT a code bug.** `/task/` taking a
  beat (whole-vault `- [ ]` scan) or `/vault-graph/` straining (rendering every
  node) is the user's large real vault, not a defect. Log it `slow` with a note,
  flag it as a perf/design thread for the user — don't blind-fix against real data.
- **Transient one-off** that doesn't reproduce on a second try → drop or `info`.
- A friction that's really *design* (flat panel, weak spacing) → note it briefly
  and route to the design skills; don't manufacture an interactive "bug".

What's left — a reproducible broken flow, a dead feature, a genuinely confusing
step — is the real signal.

## Step 5 — Render the HTML report

```bash
python scripts/ui_walk_report.py \
  --steplog data/ui-walk/usecases/<run>/steplog.jsonl \
  --out data/ui-walk/usecases/<run>/report.html \
  --title "EmptyOS UI walk — <date>" --persona Kevin
```

It groups by use case (worst-first), base64-embeds every screenshot into one
self-contained file, badges each step, and rolls up pass/slow/confusing/fail
counts. Output is under `data/` (gitignored) — an artifact to **show the user**,
not committed. A missing screenshot renders a placeholder, never a crash.

## Step 6 — Fix only a clear, root-caused bug (optional)

Most iterations produce a report, not a code change. But if the walk surfaced a
real, reproducible interactive bug with an obvious root cause:

- Fix at the source (`.claude/rules/debugging.md`); prefer the platform fix (one
  bug in `eos.js` over N pages — `feedback_platform_fix_for_n_app_bugs`).
- Reuse `EOS_UI` / `EOS.*` helpers; match surrounding code.
- **Verify**: static change (`pages/*.html`, `static/*.js`, CSS) hot-reloads — re-walk
  the step and confirm. Python change is NOT live until the user restarts `:9000`
  (you can't) — verify on a leased sandbox (`.claude/rules/sandbox-driven-testing.md`)
  or `py_compile` + tell the user a restart is needed.

## Step 7 — Commit a fix (only when you actually changed code)

- Commit ONLY files you changed, by explicit path: `git commit -o path/to/file -m "..."`.
  NEVER `git add -A` / `git add .` (parallel-session drift — `.claude/rules/environment.md`).
- `apps/personal/` is gitignored — a fix there is local-only; say so.
- Conventional-commit message; end with the `Co-Authored-By` trailer. Then
  `git log --oneline -3` to confirm your commit landed.
- The report HTML itself is **never committed** (it's under `data/`).

## Step 8 — Report + loop decision

Tell the user:
- **Where the report is** (the `report.html` path) and how to open it.
- **Headline findings** — the `fail`/`confusing`/`slow` steps in plain language
  ("adding a task works but doesn't show in Today without a reload").
- **Anything fixed** (with commit hash) and anything **flagged-not-fixed** (the
  data-volume/perf/design class) and why.

If this pass walked its use cases and the remaining real workflows are already
well-covered by prior iterations, say coverage is broad and **end the loop** —
don't re-walk the same flows forever, and don't invent friction to keep going.
Otherwise the next iteration rotates to fresh use cases.

## Cross-references

- `scripts/ui_walk_report.py` — the use-case step-log → HTML renderer (this skill's artifact half).
- `scripts/_eos_browser.py` — auth/token + base-URL resolution shared by the walkers.
- `scripts/check-js-errors.py` / `scripts/check-clickable.py` — the optional crude-breakage pre-pass.
- `scripts/ui_walk_audit.py` — the older *per-app* screenshot walk (one shot per app); complementary, not this.
- `.claude/rules/audits.md` — false-positive discipline.
- `.claude/rules/debugging.md` — root-cause-before-fix.
- `.claude/rules/daemon-handling.md` — never restart `:9000`/`:9001`.
- `.claude/rules/sandbox-driven-testing.md` — verify Python fixes off `:9000`.
- `eos-bug-audit` (backend correctness), `eos-page-design-review` / `eos-design-system-audit` (visual/design) — the adjacent skills this one is deliberately NOT.
