#!/usr/bin/env python3
"""scripts/preflight.py — run EmptyOS's static self-audit suite by scope.

The runtime aggregator for the graduated `check-*.py` / `*_audit.py` family
(see .claude/rules/self-audit-loops.md). Each check is treated as a black-box
subprocess (`python scripts/<script> [args]`) so the runner is robust to their
differing interfaces; this file owns only the *registry* (which scope each check
belongs to, whether its non-zero exit is a hard gate) and the orchestration.

Consumers, one source of truth:
  - the `/preflight` skill calls this for the static scans (judgment steps —
    git / daemon / bus / env — stay in the skill),
  - CI runs `--scope docs` on every push (`.github/workflows/tests.yml`) — so
    a docs-scope script missing on disk is a red CI gate, not a silent skip,
  - `--scope release --gate-only` is the pre-release set, run by hand before
    `/eos-release-public`. `scripts/release-public.py` does NOT invoke this
    runner — it hand-calls nine scripts of its own (measured 2026-09-05:
    zero references to preflight there, so the release scope's 32 rows /
    25 gates are not what a release actually runs),
  - `--all` has no caller anywhere; its first full run was 2026-09-05.

Usage:
  python scripts/preflight.py --scope ui,kb     # run the ui + kb checks
  python scripts/preflight.py --scope always    # default safe set
  python scripts/preflight.py --all             # every registered check
  python scripts/preflight.py --list            # show the registry
  python scripts/preflight.py --scope ui --gate-only   # only hard-gate checks

Exit code = number of gate-failing checks (0 = clean / advisory-only).
Scopes: always, ui, kb, vault, memory, skills, docs, topology, apps, security,
loops, tests, git, release, deferred, export.  (Authoritative list: ALL_SCOPES
below — and every scope a CHECKS row names must appear there, or `--scope X`
answers "unknown scope" for a check that exists. `deferred` and `export` were
missing from the list for weeks; tests/test_unit_preflight_registry.py pins
the two sets equal.)

Adding a check: append one row to CHECKS. No edits to the check script itself.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Registry of static, read-only, fast scanners. `gate=True` means a non-zero
# exit fails preflight (the hard floor). Heavy/live checks that need the *user's*
# daemon or a browser (check-clickable, ui_walk_audit) stay out of the static
# runner — they belong in release-public.py / dedicated invocations.
#
# One exception: check_snapshot_boot.py (release scope) spawns its own throwaway
# daemon on an ephemeral port with an isolated data dir + vault. It never touches
# :9000/:9001, and only runs in the release scope, where a ~30s boot is worth it.
CHECKS: list[dict] = [
    # always — cheap repo-wide hygiene
    # --include-untracked: a leak's riskiest moment is in a NEW file that is
    # written but not yet staged — scanning tracked-only reports CLEAN while it
    # sits there (observed 2026-08-14). Untracked-but-not-ignored is exactly the
    # about-to-be-committed set; gitignored paths stay excluded, which is right
    # because those (apps/personal/, tests/personal/) are where personal data
    # legitimately lives. Costs ~22 extra files here, 0 false positives.
    {"script": "check-personal.py",        "scope": ["always", "security", "release"], "gate": True,
     "args": ["--include-untracked"]},
    {"script": "check-branding.py",        "scope": ["always", "security", "release"], "gate": True,
     "args": ["--include-untracked"]},
    {"script": "check-asyncio-blocking.py","scope": ["always", "security"],            "gate": False},
    {"script": "check-swallowed-exceptions.py","scope": ["always", "security"],         "gate": False},
    {"script": "check-vault-rmw-race.py",  "scope": ["always", "vault"],               "gate": False},
    {"script": "check_call_app_declared.py","scope": ["always", "release"],             "gate": True},
    # A bump is a hand edit of two files and release.py only reads the version,
    # so this drifted across seven releases with the pytest assertion red the
    # whole time — it just never ran on the release path. Now it does.
    {"script": "check_version_sync.py",    "scope": ["always", "release"],             "gate": True},
    {"script": "check_dark_flags.py",      "scope": ["always", "release"],             "gate": False},
    # The registry sibling of check_dark_flags: a dark flag asks "was this ever
    # turned on?", a deferred row asks "did its trigger ever fire?". Advisory,
    # and deliberately NOT in "always" — deferring is a valid outcome, so an
    # aging row is information for a periodic readiness pass (eos-insights §9),
    # never a reason to block a session. Reports age + table-shape drift only;
    # judging readiness stays with a human because triggers are prose.
    {"script": "check_deferred_work.py",   "scope": ["deferred", "release"],           "gate": False},
    # A detached task holding no strong reference can be garbage-collected
    # mid-flight (asyncio weak-refs running tasks) and fails silently.
    # Advisory: a legitimate detached task in a class with no app in reach is
    # not a build break, and the opt-out is an inline marker at the call site.
    {"script": "check_bare_create_task.py","scope": ["always", "apps"],                "gate": False},
    # Runtime NameErrors from the multi-module split: a helper's __globals__ is
    # its own module, so an import left in the spine is simply absent. Gates,
    # because both classes it reports are unambiguous runtime bugs with no
    # legitimate use, and the tree is at zero (1444 files, ~2s). Its second pass
    # catches what pyflakes structurally cannot — a TYPE_CHECKING-only name used
    # at runtime, which is how shadowing's worst audio bug stayed invisible.
    {"script": "check_undefined_names.py", "scope": ["always", "apps", "release"],     "gate": True},
    # Multi-module apps re-bind module-level helpers in the class body; a missing
    # binding line is an AttributeError only on the branch that runs it, and
    # several call sites sit inside `except Exception`, so it degrades silently.
    # An unbound @web_route is worse: the endpoint just never exists. Gates
    # because the rule (takes self AND (self-called OR decorated OR a call_app
    # verb)) measures 0 findings across 235 helper modules on a healthy tree —
    # the two looser rules that flagged 36% / 9% were false positives.
    {"script": "check_helper_bindings.py", "scope": ["apps", "release"],               "gate": True},
    # `require_path_segment` raises by design (it is the path-builder form), so
    # inside a @web_route an id like `nul` or `foo..` becomes a 500 while a plain
    # unknown id answers `{"error": ...}` — one mistake, two shapes. Gates because
    # the rule is narrow (route + no try + no path_segment_error, argument shape
    # ignored) and measured 4 findings across 2 apps, all real, then 0. Both
    # directions pinned in tests/test_unit_check_route_500.py; ~0.7s.
    {"script": "check_route_500.py",       "scope": ["always", "apps", "release"],     "gate": True},
    # Every core @server.* route is classified operator-only (emptyos/posture.py
    # OPERATOR_ROUTES) or user-ok (the reviewed set in the check), and every
    # committed hosted/demo config resolves to `user` trust posture. Gates: a new
    # core route that exposes host/config/network/plugins/install/dispatch is a
    # hole the moment it is added and forgotten (docs/AUTH.md § Operator vs user).
    # 0 findings on a healthy tree; both directions pinned in
    # tests/test_unit_check_route_posture.py.
    {"script": "check_route_posture.py",   "scope": ["always", "apps", "security", "release"], "gate": True},
    # `[provides.report]` vs what the app does. Advisory while adoption is
    # partial (nine pre-existing report routes have not migrated). Deliberately
    # has NO path-based detector: 29 routes repo-wide match `report` in the URL
    # and 19 are something else, so a path heuristic runs at 66% false
    # positives. Every rule keys on the declaration or an exact call.
    # Both directions pinned in tests/test_unit_check_app_reports.py; ~0.4s.
    {"script": "check_app_reports.py",     "scope": ["apps", "release"],               "gate": False},
    # The sibling of check_dark_flags: a feature unreachable NOT because it is
    # behind a flag, but because nothing can set the field that selects it.
    # Advisory — an app may legitimately declare a field its own machinery
    # fills, and only the author knows which. Gating on "code-only" would fire
    # on healthy code; the script's own exit code separates the two severities.
    {"script": "check_field_authors.py",   "scope": ["always", "apps"],                "gate": False},
    # Advisory, not a gate: it asks a question ("is this box yours?") that only
    # the operator can answer, and a legitimate private-LAN service would
    # otherwise break the build until annotated.
    {"script": "check_provider_trust.py",  "scope": ["always", "security"],            "gate": False},
    # loop registry ↔ reality reconciliation: registered components resolve +
    # dark flags appear in code (its own exit code); dark-but-live-here is
    # informational. Advisory here — unregistered-loop detection is deliberately
    # NOT automated (ambiguous, per audits.md); the register mandate is doctrine.
    {"script": "check_loops.py",           "scope": ["loops", "release"],              "gate": False},
    # A proactive kind missing from the mute catalog still delivers — it just
    # cannot be silenced, because the catalog IS the mute UI and /api/mute
    # refuses an unlisted kind. Found 22 such kinds (expense-budget: 251
    # deliveries). Gates: detection is narrow (a literal-kind *notify* call, a
    # proactive_source dict), 0 findings on THIS tree; both directions pinned
    # in tests/test_unit_check_proactive_kinds.py. Two caveats: CI runs only
    # --scope docs, so the real CI coverage is that file's live-tree test, not
    # this row; and two counted emitters live under gitignored apps/personal/,
    # so a fresh clone sees fewer emitters (which can only lower findings,
    # never raise them — no precondition guard needed).
    {"script": "check_proactive_kinds.py", "scope": ["apps", "release"],               "gate": True},
    # suites.toml (suite catalog) member ids must resolve to real manifests —
    # deterministic (typo/retired-app drift only), so it gates. Unassigned
    # FEATURE apps (public core/standard/labs + extension engineering/dev/
    # english-learning) are an advisory inside the check, never a gate.
    {"script": "check_suites.py",          "scope": ["always", "release"],             "gate": True},
    # A test hardcoding apps/<track>/<group>/<id> breaks on every promote/regroup
    # and surfaces as a pytest COLLECTION error — reddening the whole CI gate,
    # not one file. Deterministic (flags only paths that no longer resolve), so
    # it gates. Four historical breakages: explore, daily-brief, publish, devices.
    {"script": "check-test-app-paths.py",  "scope": ["always", "release"],             "gate": True},
    # topology — full dataflow graph audit (call_app/emit/capability edges +
    # dead listeners). Hard floor = dangling call_app; debt categories advisory.
    {"script": "dataflow_audit.py",        "scope": ["topology"],                      "gate": False},
    # event wiring — drift (listener with no emitter) + phantom (declared but
    # never emitted) are exit-code signals; dead events stay informational.
    {"script": "check_event_wiring.py",    "scope": ["always", "topology"],            "gate": False},
    # ui / frontend
    {"script": "check-contrast.py",        "scope": ["ui"],            "gate": True},
    {"script": "check-text-tokens.py",     "scope": ["ui"],            "gate": True},
    {"script": "check_hardcoded_hex.py",   "scope": ["ui"],            "gate": True},
    # `var(--x)` naming a token defined nowhere: the declaration is dropped at
    # computed-value time, silently. Shipped advisory on 2026-09-03 while the
    # tree still carried the 285 instances it found; those were migrated to real
    # tokens on 2026-09-05, so it now gates. Only the *provable* class (the token
    # is defined nowhere in the repo) affects the exit code — the "defined but
    # unreachable" class stays an advisory note, since that may be a resolver gap
    # rather than a defect. Mutation-pinned in
    # tests/test_unit_check_phantom_tokens.py.
    {"script": "check_phantom_tokens.py",  "scope": ["ui", "release"], "gate": True},
    {"script": "check-design-md.py",       "scope": ["ui"],            "gate": True},
    {"script": "check-absolute.py",        "scope": ["ui"],            "gate": False},
    {"script": "check-attr-escaper.py",    "scope": ["ui", "security"],"gate": False},
    {"script": "check-esc-phantom.py",     "scope": ["ui", "security"],"gate": True},
    {"script": "check-app-nav.py",         "scope": ["ui"],            "gate": False},
    {"script": "check-ios-safe-area.py",   "scope": ["ui"],            "gate": False},
    # An icon-only button with no accessible name: a screen reader announces
    # "button" and nothing else. Gates from 2026-09-06 (audit F7), after the
    # check was narrowed and the tree cleared. It previously flagged EVERY
    # button lacking a title=, which fired on 87% of apps — 1971 of those 2069
    # had a visible label that already IS their accessible name. The broad
    # number survives as an advisory line; only the icon class is a finding,
    # and `aria-label` now counts (the old rule flagged 37 buttons that were
    # already labelled correctly). Both directions mutation-pinned in
    # tests/test_unit_check_button_tips.py.
    {"script": "check-button-tips.py",     "scope": ["ui"],            "gate": True, "args": ["--strict"]},
    {"script": "check_ui_structure.py",    "scope": ["ui"],            "gate": False},
    {"script": "check_ui_consistency.py",  "scope": ["ui"],            "gate": False},
    # JSON.stringify interpolated into an onclick="" closes the attribute early,
    # so the handler never binds — the control renders and silently does nothing.
    # Four shipped that way across three apps. Gates: 0 findings on a healthy
    # tree, and every hit is a dead affordance with one mechanical fix (jsArg).
    {"script": "check_onclick_args.py",    "scope": ["ui", "release"],  "gate": True},
    # A hand-assembled `eos-badge-*` class the stylesheet never defines: the word
    # renders, the status colour silently does not. Two ways in — a token outside
    # the shared set, or double-prefixing what statusVariant already prefixed —
    # and one provable question settles both (does the CSS define this class?).
    # Gates: 0 findings on a healthy tree (the 19 it found at registration were
    # all fixed the same day), and every hit is a badge whose colour is silently
    # missing, with one mechanical fix. Runtime-valued concatenations it cannot
    # read statically are reported separately as advisory and never counted.
    {"script": "check_badge_class.py",     "scope": ["ui", "release"],  "gate": True},
    # A hub.panel naming a renderer hub.js lacks paints a red "Unknown renderer"
    # box on the home screen; a duplicate panel id collides in the DOM and makes
    # lazy hydration fetch the wrong panel. Both are provable from the manifest +
    # hub.js RENDERERS map, and both are silent on a healthy tree, so they gate.
    # The ambient band (priority >= 150, 63 of 102 panels) is deliberate and only
    # ever printed as an advisory — gating it would fail on a healthy tree.
    {"script": "check_hub_panels.py",      "scope": ["ui", "release"], "gate": True},
    # An app's ⚙ panel hand-mirrors its manifest [provides.settings] schema, so a
    # new setting silently reaches /settings and nowhere else. Advisory: an
    # omission can be deliberate (dark flag, secret) — mark it with
    # `settings-panel-drift: ignore <key>`, or pass `app:` to derive the fields.
    {"script": "check-settings-panel-drift.py", "scope": ["ui"],       "gate": False},
    # A manifest [provides.settings] key the app reads only through app_config():
    # the panel writes to the settings service, the read looks in emptyos.toml,
    # so the toggle does nothing. Provable from manifest + source. First run
    # (2026-10-01) found 32 keys in 11 apps, eleven weeks after a hand-fix of four;
    # 0 on a healthy tree, so it gates. A deliberate TOML-only read opts out at
    # the call site: `# settings-dead-toggle: ignore <key>`.
    {"script": "check_settings_dead_toggle.py", "scope": ["apps", "release"], "gate": True},
    # A catch block painting a failure in the muted vocabulary reserved for
    # "there is nothing here" — EOS_UI.errorState exists and 24 pages use it.
    # Worst measured case rendered "No tasks found" from a catch: not merely
    # unstyled, but false. Advisory and must stay so — whether a silent degrade
    # was deliberate is a judgment no scanner can make, so a page that has made
    # the call says `// error-state: intentional — <why>` at the call site.
    {"script": "check_error_state.py",     "scope": ["ui"],            "gate": False},
    # An option EOS_UI.confirm cannot read is dropped in silence — no error, no
    # console warning, and the dialog still opens. Two sites were rendering a
    # bare "Are you sure?" on destructive actions and one delete button ran no
    # callback at all. The component now reads every alias the 134 call sites
    # actually use, so a healthy tree is silent and this gates.
    {"script": "check_confirm_keys.py",    "scope": ["ui", "release"], "gate": True},
    # export-surface inline handlers must stay inside the eos-csp-bridge
    # grammar (MV3 extension packaging) — needs node; skips when absent
    {"script": "check-csp-inline.py",      "scope": ["ui", "export"],  "gate": False},
    # kb / engineering notes (EmptyOS KB + the personal vault KB corpus)
    # Engineering assurance packages are opt-in through manifest [assurance].
    # Structural/document/traceability failures are deterministic. Promotion
    # to verified/released is also blocked unless every receipt is declared
    # and present; engineering correctness still belongs to the live gates.
    {"script": "check_engineering_assurance.py", "scope": ["apps", "docs", "release"], "gate": True},
    # The data dictionary is generated from the calculator's spec, so a stale
    # ALGORITHM.md is drift rather than an editing oversight. Registered here
    # because a `--check` mode nothing calls is prose with extra steps — the
    # sibling generator in D:/prelim-sizing went stale within four commits of
    # being written for exactly that reason.
    {"script": "gen_trust_loop_tables.py", "scope": ["apps", "docs", "release"], "gate": True,
     "args": ["--check"], "label": "gen_trust_loop_tables (drift)"},
    # Same reasoning, different artifact: the standalone Vowel Crosswalk is
    # published outside EmptyOS and cannot fetch its data (artifact CSP blocks
    # every external host), so it inlines a copy of dictionary/crosswalk.py.
    # Not in `release` — the artifact lives under apps/extension/, which the
    # public snapshot drops.
    {"script": "gen_vowel_crosswalk_artifact.py", "scope": ["apps"], "gate": True,
     "args": ["--check"], "label": "vowel_crosswalk_artifact (drift)"},
    # Third instance of the same shape, and the one whose upstream *moves*:
    # worklog's CPEng roll-up copies the 16 EA elements so the standalone offline
    # bundle can name them (an exported HTML has no server and no vault). Names
    # are stable; the per-element Strength is Kevin's live assessment and changes
    # the moment an element gets evidenced — so the copy drifts toward advertising
    # a gap that has closed. Its first run found exactly that: elements 5 and 7
    # were Adequate in the note and absent from the code's focus map.
    # Skips with exit 0 when no vault is configured, so a public clone still passes.
    {"script": "gen_competency_focus.py", "scope": ["apps", "vault"], "gate": True,
     "args": ["--check"], "label": "competency_focus (drift)"},
    # The checker above enforces that a stated requirement *has* a traceability
    # row; it never opens the row. So a rewrite can delete the test a row cites
    # and still report `0 error(s)` — in D:/prelim-sizing 17 of 20 UX rows
    # pointed at tests that no longer existed, and the requirement most visibly
    # lost regressed in the same commit that deleted its guard. Advisory,
    # because a row may legitimately cite something this resolver cannot see;
    # opt one out with a trailing `<!-- traceability: external -->`.
    {"script": "check_traceability_targets.py", "scope": ["apps", "docs", "release"], "gate": False,
     "label": "traceability targets resolve"},
    # Sibling of the row above, one layer out: that one opens a traceability row's
    # target, this one opens every backticked repo path cited anywhere in prose.
    # Nothing checked those, so the app-track reorganisation invalidated hundreds
    # of citations silently — and a stale path is worse than a missing one, since
    # it reads as authoritative and sends the next reader (or agent) to a file
    # that isn't there. Reports two bands: `renamed` is provable (the path is gone
    # and exactly one existing path has its tail) and `--fix` rewrites it;
    # `unresolved` needs prose judgment — a deleted app, a deliberately
    # prospective reference, a negative example. Advisory because of that second
    # band, and because the first is red on arrival; gate the `renamed` band via
    # `--band renamed` once its backlog is cleared. Opt out at the call site with
    # `<!-- doc-paths: ignore [path] -->`.
    {"script": "check_doc_paths.py", "scope": ["docs", "release"], "gate": False,
     "label": "doc path citations resolve"},
    {"script": "kb_claim_audit.py",        "scope": ["kb"],            "gate": False},
    {"script": "kb_link_audit.py",         "scope": ["kb"],            "gate": False},
    {"script": "kb_claim_audit.py",        "scope": ["kb"],            "gate": False,
     "args": ["--root", "30_Resources/KB"], "label": "kb_claim_audit (personal)"},
    {"script": "kb_link_audit.py",         "scope": ["kb"],            "gate": False,
     "args": ["--root", "30_Resources/KB"], "label": "kb_link_audit (personal)"},
    # Which numeric clause/case notes has nobody compared to the print, and
    # which of those does an engine lean on. Advisory: a transcribed note is the
    # honest state until someone opens the page (kb-fact-integrity T5).
    {"script": "kb_verification_audit.py", "scope": ["kb"],            "gate": False},
    # The KB tables note's Table I-III blocks are rendered from the engine's
    # one transcription; a hand edit inside a block is drift. Gates; skips with
    # exit 0 when no vault is configured (kb-fact-integrity T8).
    {"script": "gen_kb_tables.py",         "scope": ["kb", "apps"],    "gate": True,
     "args": ["--check"], "label": "gen_kb_tables (drift)"},
    # test-suite hygiene: a `test_*` nested inside another `test_*` is never
    # collected, yet the enclosing test still passes — so the coverage loss is
    # invisible and CI is green. Cost 24 dead tests on 2026-07-31. Zero false
    # positives across the tree, so it gates.
    {"script": "check_nested_tests.py",    "scope": ["always", "tests", "release"], "gate": True},
    # vault hygiene
    {"script": "check_vault_test_leak.py", "scope": ["vault"],         "gate": False},
    {"script": "check_vault_structure.py", "scope": ["vault"],         "gate": False},
    # MV prompt + asset library (YouTube-Music-Channel/library): vocabulary,
    # hashes, paths, verdict evidence, pattern counts. Advisory — the rows are
    # backfilled from hand-written project ledgers, so a finding is a record to
    # reconcile, not a broken build. Skips with exit 0 when no vault/library.
    {"script": "check_mv_library.py",      "scope": ["vault"],         "gate": False},
    # Prose tone over the two corpora that have a standing home: published posts,
    # and every `spoken: true` note. Advisory — tone is a judgment the human
    # reconciles, and the spoken family's sentence-length rule is `low` by design.
    # It is here because it had NO runner: the linter existed, and the one
    # contraction-free recruiter email it ever saw was linted by hand, once. The
    # `spoken: true` marker is what makes an unattended run honest — without it
    # there is no way to tell an interview answer from a CV, and a blanket career
    # scan would fire register rules on 42 trackers (audits.md).
    {"script": "check_prose_tone.py",      "scope": ["vault"],         "gate": False,
     "args": ["--vault"]},
    # session memory hygiene (Claude-Code auto-memory)
    {"script": "check_memory_rot.py",      "scope": ["memory"],        "gate": False},
    # The inbound half of the same loop: memory_rot asks "has a stored fact
    # decayed?", this asks "is there a correction that should have been stored?".
    # Advisory forever — a cluster is a question for a human, and per audits.md
    # an ambiguous signal must never gate.
    {"script": "mine_corrections.py",      "scope": ["memory"],        "gate": False},
    # session-plan hygiene. In `always` despite reading the vault — unlike the
    # two vault scans above it reads ~3 small files, and the signal it carries
    # (a stale row claim) is a mutual-exclusion claim that silently BLOCKS
    # its task for every other session until a human clears it. Degrades silently with no
    # vault configured, so a fresh clone stays quiet. Advisory: a plan is a
    # human artifact and every finding here is "someone should look".
    {"script": "check_plan_staleness.py",  "scope": ["always", "vault"], "gate": False},
    # The project model (vault `projects/areas.md`: area vocabulary, start,
    # deadline, `## Goal`, parent covers its children, plans/tracks resolve).
    # The Projects app validates only what IT writes; a hand edit or a note that
    # predates the model never meets that gate. Reuses the app's own validators.
    # `vault` only — it reads ~100 notes. Advisory: every finding is a vault
    # edit for a human, and a stray note must not block an unrelated commit.
    {"script": "check_projects.py",        "scope": ["vault"],         "gate": False},
    # Commits that never left the machine. The nested `apps/personal` repo is
    # gitignored by this one, so it is invisible to every other check here — four
    # of its commits (including the one adding the whole System Icon Library) sat
    # unpushed overnight on 2026-09-01 with nothing watching. Reports only past a
    # session boundary (12h), because "has unpushed commits" is the normal state
    # of an active session and reporting that would fire on every run. Fetches at
    # most every 30 min and degrades to the last-known ref when offline — which
    # over-reports, never under-reports. Advisory: holding a commit back is
    # sometimes deliberate, so this is "someone should look", never a gate.
    {"script": "check_unpushed.py",        "scope": ["always", "git"],   "gate": False},
    # skill-authoring contract (frontmatter trigger + boundary + preflight)
    # release: repo store only (a release must not gate on the machine's personal skills)
    {"script": "check_skills.py",          "scope": ["release"],       "gate": True},
    # skills scope: also lint the user-global store (~/.claude/skills); skipped on a
    # clone with no user skills (the flag no-ops when the dir is absent)
    {"script": "check_skills.py",          "scope": ["skills"],        "gate": True,
     "args": ["--user-skills"], "label": "check_skills (+ user store)"},
    # skill-content security scan (vendored SkillSpector static pass — advisory)
    {"script": "check_skill_security.py",  "scope": ["skills", "security"], "gate": False},
    # a skill marked `vault_sync: true` must match its vault copy byte-for-byte.
    # Gates because the marker is opt-in: only a human-declared pair is checked,
    # so a finding is never ambiguous. Unmarked skills legitimately differ (the
    # tracked copy carries {vault}/{home} placeholders per rule 13).
    {"script": "check_skill_vault_sync.py", "scope": ["skills", "vault"], "gate": True},
    # a skill's .agents/ and .claude/ Python copies must be byte-identical — both
    # are tracked mirrors of the same runner. Gates, and unlike the vault check
    # needs no opt-in marker: rule 13's placeholder rationale cannot apply when
    # both copies are in git, and the tree measured 27/27 identical once synced.
    # A .claude copy 162 lines behind its twin silently discarded a spec key.
    {"script": "check_skill_script_sync.py", "scope": ["skills", "always", "release"],
     "gate": True},
    # The bundled<->user-global pair — the one that decides what a fresh clone
    # ships, and the only skill mirror nothing watched until 2026-09-12. The
    # checker is the sync script's own --check mode rather than a new scanner:
    # it already owns the scrub (personal paths -> {vault}/{home} placeholders),
    # so a separate byte-comparison would have to duplicate that and would drift
    # from it. Advisory, not a gate — the user-global store is per-machine, it
    # is absent on a clone (the script exits 0 and says so), and a divergence
    # there is a human call about direction, exactly as check_skill_vault_sync
    # documents for its own pair.
    {"script": "sync_user_skills.py", "scope": ["skills"], "gate": False,
     "args": ["--check"], "label": "bundled<->user skill sync"},
    # Repo paths named in a skill BODY that no longer resolve — the class the
    # three sync checkers structurally cannot see, since they only compare
    # copies against each other. Advisory: whether a `scripts/x.py` is repo-
    # relative, vault-relative, or a file the skill creates is a judgment a
    # regex does not settle, so it must not gate (audits.md). Opt out at the
    # file with `<!-- skill-refs: ignore — why -->`.
    {"script": "check_skill_refs.py", "scope": ["docs"], "gate": False},
    # skills scope also reads the user-global store, following the
    # `check_skills.py --user-skills` precedent above. Measured 2026-09-12:
    # the default run reports 2 findings, `--user` reports 4 — the extra two
    # are real rows the audit named. Kept out of the `docs` scope because CI
    # runs that one and has no user store.
    {"script": "check_skill_refs.py", "scope": ["skills"], "gate": False,
     "args": ["--user"], "label": "check_skill_refs (+ user store)"},
    # docs — generated docs must match the code (APPS.md, TIERS.md, SKILLS.md)
    {"script": "generate_apps_doc.py",  "scope": ["docs", "release"], "gate": True, "args": ["--check"]},
    {"script": "generate_tiers_doc.py", "scope": ["docs", "release"], "gate": True, "args": ["--check"]},
    {"script": "generate_skills_doc.py", "scope": ["docs", "skills", "release"], "gate": True,
     "args": ["--check"]},
    # DESIGN.md is generated FROM theme.css and is the machine-readable contract
    # external AI tools read. It silently drifts on every token edit — nothing ran
    # this until 2026-07-20, when a theme accent change reached theme.css, the app
    # UI, and published sites while DESIGN.md kept serving the old hex.
    # In "ui" as well as "docs" because the *cause* is a theme.css edit: whoever
    # touches tokens runs --scope ui, and that is exactly where it was missed.
    # (check-design-md.py is a different gate — it validates refs/hex/cycles, and
    # a stale-but-well-formed file passes it.)
    {"script": "gen-design-md.py", "scope": ["ui", "docs", "release"], "gate": True,
     "args": ["--check"]},
    # apps — whole-system 8-dimension quality scorecard (App Optimizer). Advisory;
    # --check prints the summary without writing a vault snapshot.
    {"script": "app_optimizer_scan.py", "scope": ["apps"], "gate": False, "args": ["--check"]},
    # apps — [storage] manifest-declaration hygiene (docs/CLOUD-ARCHITECTURE.md);
    # advisory: exit = invalid declarations only, undeclared apps are fine.
    {"script": "check_storage_decl.py", "scope": ["apps"], "gate": False},
    # apps — AI-native scorecard (backend think vs assistant reach vs page AI-UI);
    # advisory: exit = "dark AI" apps (think but no chip + no reach). Judgment
    # layer / triage = the eos-ai-native-audit skill; exceptions go in DARK_OK.
    {"script": "check_ai_native.py", "scope": ["apps"], "gate": False},
    # apps — market gap-analysis registry coverage/staleness (vault
    # 30_Resources/EmptyOS/gap-analysis/). Advisory: exit = stale + missing
    # (capped 99); judgment layer = the eos-app-gap-analysis skill.
    {"script": "check_gap_freshness.py", "scope": ["apps"], "gate": False},
    # apps — [[provides.verbs]] args declarations: unsupported type tokens
    # (enforce nothing) + declared args the target method can't accept (would
    # TypeError once [verbs] arg_gate is on). Advisory; opt out with an inline
    # `# verb-args-check: ignore` in the manifest.
    {"script": "check_verb_args.py", "scope": ["apps"], "gate": False},
    # release packaging
    # Also in "apps": release-only is how `cable-bonding` shipped unreachable —
    # it was absent from its tier for a whole session and nothing said so until
    # release time, by which point the drift was already committed.
    {"script": "check-tier-folder.py",     "scope": ["apps", "release"], "gate": True},
    # The app->plugin edge its sibling cannot see: check-tier-folder validates
    # that a tier's plugin ids EXIST, never that a shipped app's declared
    # plugin is in that app's own tier. A plugin absent from the tier is
    # PRUNED from the snapshot, so the feature is dead while the manifest
    # still claims it.
    {"script": "check_tier_plugin_reach.py", "scope": ["apps", "release"], "gate": True},
    # Product lines (trunk + branches): every tier declares `product_line`,
    # extends only its own line or the trunk, and a direct-allowlist (hosted)
    # tier stays a subset of one tier in its line.
    {"script": "check_tier_lines.py",      "scope": ["apps", "release"], "gate": True},
    # export-groups.toml ships publicly: a group naming a closed app must be
    # private (the release drops private groups). Editions M12.
    {"script": "check_export_groups.py",   "scope": ["apps", "release"], "gate": True},
    {"script": "check-licenses.py",        "scope": ["release"],       "gate": False},
    # Compile every module, then actually boot the tree in a throwaway daemon.
    # The only check here that runs the code rather than reading it — the class
    # that cost releases v0.2.7-v0.2.10 (compiles clean, dies on a fresh boot).
    # Boot capped well below the runner's 180s per-check timeout.
    {"script": "check_snapshot_boot.py",   "scope": ["release"],       "gate": True,
     "args": ["--timeout", "60"]},
]

ALL_SCOPES = ["always", "ui", "kb", "vault", "memory", "skills", "docs", "topology", "apps", "security", "loops", "tests", "git", "release", "deferred", "export"]


def _select(scopes: set[str], gate_only: bool) -> list[dict]:
    # A registered script that is missing on disk is NOT filtered out here:
    # `_run_one` reports it (FAIL for a gate, error otherwise). Filtering it
    # silently meant a deleted or renamed checker vanished from every run with
    # no line of output — the run got one check shorter and stayed green.
    out = []
    for c in CHECKS:
        if not (scopes & set(c["scope"])):
            continue
        if gate_only and not c.get("gate"):
            continue
        out.append(c)
    return out


def _run_one(c: dict, timeout: int) -> dict:
    script = c["script"]
    t0 = time.time()
    if not (REPO / "scripts" / script).exists():
        # Fail closed for a gate, same as a timeout: a check that cannot run
        # has not certified anything.
        state = "FAIL" if c.get("gate") else "error"
        return {**c, "rc": -1, "state": state,
                "summary": f"script missing on disk: scripts/{script}",
                "detail": [], "dt": 0.0}
    try:
        r = subprocess.run(
            [sys.executable, str(REPO / "scripts" / script), *c.get("args", [])],
            cwd=REPO, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",  # children emit UTF-8; don't let cp1252 mojibake it
        )
        rc = r.returncode
        tail = (r.stdout or r.stderr or "").splitlines()
        while tail and not tail[0].strip():
            tail.pop(0)
        while tail and not tail[-1].strip():
            tail.pop()
        summary = tail[-1] if tail else ""
        detail = tail
    except subprocess.TimeoutExpired:
        # A timeout means "cannot verify clean within budget" — for a gate
        # check that must fail closed (FAIL), not silently downgrade to a
        # non-fatal warn/error the way an ungated check's timeout does.
        state = "FAIL" if c.get("gate") else "error"
        return {**c, "rc": -1, "state": state, "summary": f"timeout >{timeout}s",
                "detail": [], "dt": timeout}
    except Exception as e:  # never let one check crash the runner
        return {**c, "rc": -1, "state": "error", "summary": str(e)[:80],
                "detail": [], "dt": round(time.time() - t0, 1)}
    if rc == 0:
        state = "ok"
    elif c.get("gate"):
        state = "FAIL"
    else:
        state = "warn"
    return {**c, "rc": rc, "state": state, "summary": summary[:SUMMARY_CHARS],
            "detail": detail, "dt": round(time.time() - t0, 1)}


_MARK = {"ok": "✓", "warn": "·", "FAIL": "✗", "error": "!"}

# Width of the one-line summary. Named so the detail block can ask "was this
# line cut?" against the same number the clamp uses.
SUMMARY_CHARS = 90

# How many output lines to show under a check that did not pass.
#
# Measured 2026-08-30 across the routine scopes (sizes: release 27, ui 19,
# always 19, apps 11 — a first calibration sampled `apps` alone, called it "the
# widest", and so never saw the top of the range). Non-ok output spans three
# orders of magnitude:
#
#     check-button-tips   2384   (ui)
#     check_field_authors   39   (always, apps)
#     app_optimizer_scan    21   (apps)
#     check_ai_native       18   (apps)
#     check_gap_freshness    9   (apps)
#     check_verb_args        7   (apps)
#     check_storage_decl     1   (apps)
#
# That spread is the argument for a low cap, not a high one: 24 prints all but
# two checks whole, and clips the 2384-line case to something a terminal can
# still hold. The remainder is always reported, never dropped silently.
DETAIL_LINES = 24


def detail_block(res: dict) -> list[str]:
    """The indented finding lines to print under one check result.

    Pure so it can be tested — the rendering, not just the data behind it, is
    what a one-line-per-check view was hiding. Empty for a passing check, so a
    healthy run stays exactly as compact as it was.

    Shown when there is more than one line, OR when the single line was cut by
    the 90-char summary clamp: a lone over-long finding was still being
    truncated with no marker, which is the silent cap this block exists to end.
    """
    if res.get("state") == "ok":
        return []
    body = [ln for ln in res.get("detail", []) if ln.strip()]
    if not body:
        return []
    if len(body) == 1 and len(body[0]) <= SUMMARY_CHARS:
        return []           # the summary line already showed it in full
    if len(body) > 1 and body[-1][:SUMMARY_CHARS] == res.get("summary", ""):
        body = body[:-1]        # the summary line already sits on the check's own row
    out = [f"      {ln}" for ln in body[:DETAIL_LINES]]
    if len(body) > DETAIL_LINES:
        cmd = f"python scripts/{res.get('script', '<check>')}"
        if res.get("args"):
            cmd += " " + " ".join(res["args"])
        out.append(f"      … {len(body) - DETAIL_LINES} more line(s) — run: {cmd}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Run EmptyOS static self-audit checks by scope.")
    ap.add_argument("--scope", default="always", help="comma-separated: " + ", ".join(ALL_SCOPES))
    ap.add_argument("--all", action="store_true", help="run every registered check")
    ap.add_argument("--gate-only", action="store_true", help="only hard-gate checks")
    ap.add_argument("--list", action="store_true", help="list the registry and exit")
    ap.add_argument("--timeout", type=int, default=180, help="per-check timeout seconds")
    args = ap.parse_args()

    if args.list:
        print(f"{'check':28} {'scope':28} gate")
        for c in CHECKS:
            print(f"  {c.get('label', c['script']):26} {','.join(c['scope']):28} {'gate' if c.get('gate') else '-'}")
        return 0

    scopes = set(ALL_SCOPES) if args.all else {s.strip() for s in args.scope.split(",") if s.strip()}
    bad = scopes - set(ALL_SCOPES)
    if bad:
        print(f"unknown scope(s): {', '.join(sorted(bad))}; valid: {', '.join(ALL_SCOPES)}", file=sys.stderr)
        return 2
    selected = _select(scopes, args.gate_only)
    if not selected:
        print(f"preflight: no checks for scope {sorted(scopes)}")
        return 0

    print(f"preflight — scope {sorted(scopes)} · {len(selected)} checks\n")
    fails = warns = 0
    for c in selected:
        res = _run_one(c, args.timeout)
        if res["state"] == "FAIL":
            fails += 1
        elif res["state"] in ("warn", "error"):
            warns += 1
        print(f"  {_MARK[res['state']]} {res.get('label', res['script']):26} {res['state']:5} {res['dt']:>5}s  {res['summary']}")
        # A one-line summary hides every finding but the last. Healthy checks stay
        # one line; anything not ok prints its real output, capped and with the
        # truncation stated (never a silent cap — .claude/rules/audits.md).
        for line in detail_block(res):
            print(line)
    print(f"\n{len(selected)} checks · {fails} FAIL · {warns} warn/err")
    if fails:
        print("Hard-gate check(s) failed — see .claude/rules/self-audit-loops.md + audits.md.")
    return fails


if __name__ == "__main__":
    sys.exit(main())
