# Growth dimensions, REGROW, and the absorption process

Split out of `SKILL.md` 2026-09-12 (progressive disclosure). Reference material for
fix mode — read it at Phase 2 (identify growth opportunities) and Phase 3
(prioritize), where you need the dimension definitions to classify an opportunity.

---

## Growth Dimensions

6 dimensions the system grows along. **UI grows FROM every other dimension** — not separate.

```
1. BREADTH  — More apps
2. DEPTH    — Richer backends (more endpoints, deeper features)
3. LINKS    — More event connections (fewer unheard events, fewer orphans)
4. INFRA    — Shared platform services (VaultIndex, data layer, SDK modules, components)
5. ABSORB   — External services → plugins → native
6. REGROW   — Rethink from root, consolidate fragmented apps, simplify
```

Each growth session should:
- Touch at least 2 dimensions
- Prioritize daily-use apps
- Leave the system testable (see the health-probe rule in the spine's
  `### Verify After Fixes` — it is a safety rule and lives there, not here)

---

## Dimension 6: REGROW — Rethink from Root

Sometimes the right growth move isn't adding features — it's questioning whether the current structure is right.

### When to Regrow

- **User switches between 3+ apps for one workflow** — the apps should be one
- **Data is duplicated across apps** — same vault folder read by multiple apps
- **Composition app exists just to glue others** — the glue layer signals a missing unified app
- **The data structure changed** — vault-first data means the app should follow the data

### Regrow Process

1. **Notice friction** — "why do I need 4 apps for job applications?"
2. **Question structure** — "if I grew this from scratch, would it look the same?"
3. **Follow the data** — vault folders are the natural unit. One folder = one view in the app.
4. **Build infrastructure first** — if the regrow reveals a platform gap, build that before the app
5. **Consolidate** — merge apps into one with modules. Keep all endpoints, reorganize by user workflow.
6. **Retire old apps** — remove the fragments, redirect URLs

---

## Absorption Process

When absorbing an external service:
1. Audit the external service (endpoints, features, data)
2. Compare with existing EmptyOS apps (gap table)
3. Classify: absorb concepts vs keep external
4. Execute: enhance existing apps or create thin wrappers
5. Document the boundary in CLAUDE.md

Evolution path: WRAP → ABSORB → REPLACE → SHED

---
