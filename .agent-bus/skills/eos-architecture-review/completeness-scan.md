# Step 2.5 — the app-completeness scan

Split out of `SKILL.md` 2026-09-12 (progressive disclosure). Filesystem-only, so
it runs with the daemon down — which is exactly when you want it. Read this at
Step 2.5 of check mode and run the script as-is.

The test-discovery half is an INDEX rather than a per-app grep on purpose; the
comment in the script explains why (app ids collide, and a prefix match credits
the wrong app — a false NEGATIVE, the harmful direction for a gap audit).

---

### Step 2.5: App Completeness Scan

Filesystem-only check (runs even if the daemon is down). Verifies every app has the canonical file set.

```bash
PYTHONIOENCODING=utf-8 python -c "
import sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path

# Page-less by design: backend/rooms-integration apps + scaffolds with no UI.
NO_PAGE_OK = {'tour', 'repo', 'skill', 'test-app', 'test-app-wiring'}
NO_TEST_OK = {'_example', 'tmpl', 'tests', 'test-app', 'test-app-wiring'}

# Test discovery is an INDEX, not a per-app grep, because app ids collide:
# 22 ids are underscore-prefixes of another id (`cable` vs `cable_bonding`,
# `kb` vs `kb_butler`, `work` vs `work_fit`). Matching a bare prefix would
# credit `cable` with `cable-bonding`'s tests — a false NEGATIVE, the harmful
# direction for a gap audit. So every file is claimed by the LONGEST matching
# id, and each app contributes BOTH spellings: 13 real test files are named
# `test_sys_<hyphenated-id>.py`, which an underscore-only match cannot see.
_TEST_CATS = ('sys', 'unit', 'sdk', 'dogfood', 'user', 'journey', 'e2e', 'api')

def build_tested_index(app_ids):
    keys = []
    for a in app_ids:
        for k in {a, a.replace('-', '_')}:
            keys.append((k, a))
    keys.sort(key=lambda kv: len(kv[0]), reverse=True)   # longest id wins
    tested = set()
    for p in Path('tests').rglob('test_*.py'):     # covers tests/personal/ too
        stem = p.stem[5:]                          # drop the leading `test_`
        for c in _TEST_CATS:                       # then the category token
            if stem.startswith(c + '_'):
                stem = stem[len(c) + 1:]
                break
        for k, a in keys:
            if stem == k or stem.startswith(k + '_') or stem.startswith(k + '-'):
                tested.add(a)
                break
    return tested

# Depth-agnostic discovery via the canonical loader helper: handles the
# public/<tier>/ + extension/<group>/ nesting, and excludes _retired/_archive
# (a shallow iterdir missed nested apps AND counted retired apps as gaps).
from emptyos.sdk.app_layout import iter_app_dirs

def info(name, d, tested):
    return {
        'id': name,
        'core_ok': (d/'app.py').exists(),
        'page': (d/'pages'/'index.html').exists(),
        'test': name in tested,
        'seed': (d/'demo'/'seed.py').exists(),
        'readme': (d/'README.md').exists(),
    }

# Two passes on purpose: the longest-id rule needs every app id in hand
# before any test file can be attributed, so discover first, then index.
found, seen = [], set()
for name, d in iter_app_dirs(Path('apps'), include_personal=True):
    if name in seen: continue
    seen.add(name)
    found.append((name, d))
tested = build_tested_index([n for n, _ in found])

core, personal = [], []
for name, d in found:
    rel = str(d).replace(chr(92), '/')
    (personal if ('/personal/' in rel or rel.endswith('/personal')) else core).append(
        info(name, d, tested))

def fmt_section(name, apps):
    n = len(apps); t = sum(a['test'] for a in apps); r = sum(a['readme'] for a in apps)
    print(f'{name}: {n} apps · {t} with smoke test · {r} with README')

print('=== App Completeness ===')
fmt_section('Core', core)
fmt_section('Personal', personal)
print()

gaps = []
for a in core + personal:
    if not a['core_ok']:
        gaps.append(f'- {a[\"id\"]}: missing app.py')
    if not a['page'] and a['id'] not in NO_PAGE_OK:
        gaps.append(f'- {a[\"id\"]}: missing pages/index.html')
    if not a['test'] and a['id'] not in NO_TEST_OK:
        norm = a['id'].replace('-', '_')
        gaps.append(f'- {a[\"id\"]}: missing test_sys_{norm}.py')
    if a['readme']:
        gaps.append(f'- {a[\"id\"]}: README.md present (anti-pattern, Rule 6)')

if gaps:
    print('Real gaps:')
    for g in gaps: print(g)
else:
    print('Real gaps: none')
print()
print('(Templates/scaffolding excluded:', ', '.join(sorted(NO_TEST_OK)) + ')')
"
```

If the user asks for the **full per-app table** (`--full-table` or "show the full app table"), expand the script to print one row per app with columns `id | core | page | test | seed | readme` for both `apps/` and `apps/personal/`. Default output stays scannable.

Report:
- Counts (apps, smoke-test coverage, README count)
- "Real gaps" — entries that fail the contract and aren't in the intentional-exception allowlist
- Anti-pattern: any `README.md` inside an app dir — Rule 6 says apps self-document via `eos app info`, not READMEs
