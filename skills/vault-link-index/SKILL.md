---
name: vault-index
description: Manage vault links - find broken links, orphans, hub notes, and backlinks. Includes link-doctor for repairs.
---

# Vault Link Index & Link Doctor

This skill manages the backlink index and provides link health tools for the vault.

## Files

- **Script**: `99_Attachments/scripts/generate-link-index.py`
- **Index**: `99_Attachments/link-index.json`

## Commands

### Smart Index Update
Default behavior - only regenerates if stale (>24h old):
```bash
python "99_Attachments/scripts/generate-link-index.py"
```

### Check Freshness Only
```bash
python "99_Attachments/scripts/generate-link-index.py" --check
```

### Force Regenerate
```bash
python "99_Attachments/scripts/generate-link-index.py" --force
```

### Find Broken Links (Link Doctor)
Show broken wikilinks with fuzzy-match suggestions:
```bash
python "99_Attachments/scripts/generate-link-index.py" --broken
```
Output example:
```
📄 20_Areas/Career/@data analyst.md
   ❌ [[&DataScienceHub 101]] → 💡 [[DataScienceHub 101]] (97% match)
```

### Show Orphan Notes
List notes with no incoming links, grouped by folder:
```bash
python "99_Attachments/scripts/generate-link-index.py" --orphans
```

## Link Doctor Operations

### Fix a Broken Link
When user says "fix this link" or you identify a fixable broken link:

1. Read the source file
2. Replace the broken link with the suggested fix
3. Confirm the change

```python
# Example: Fix prefix typo
old = "[[&DataScienceHub 101]]"
new = "[[DataScienceHub 101]]"
# Use Edit tool to replace
```

### Common Broken Link Patterns

| Pattern | Cause | Fix |
|---------|-------|-----|
| `[[&Note]]` vs `[[Note]]` | Prefix typo | Remove `&` |
| `[[note]]` vs `[[Note]]` | Case mismatch | Match existing case |
| `[[Old Name]]` | Renamed note | Update to new name |
| `[[2022-01-01]]` | Missing daily note | Create note or remove link |

## Obsidian CLI (Fast Queries) — with Fallbacks

Try CLI first for instant results. If Obsidian is not running, fall back to Python/Grep.

```bash
OBS="bash 99_Attachments/scripts/obs.sh"

# Check if Obsidian is available
$OBS --status

# Backlinks — what links TO this note?
$OBS backlinks file="Note Name"
$OBS backlinks file="Note Name" total          # count only
# FALLBACK: Grep for '\[\[Note Name' across *.md files

# Outgoing links — what does this note link TO?
$OBS links file="Note Name"
# FALLBACK: Read the file, extract [[...]] wikilinks

# Orphan notes (no incoming links)
$OBS orphans total
# FALLBACK: python "99_Attachments/scripts/generate-link-index.py" --orphans

# Dead-end notes (no outgoing links)
$OBS deadends total
# FALLBACK: no equivalent (would need full scan)

# Unresolved (broken) links
$OBS unresolved total
$OBS unresolved verbose                         # with source files
# FALLBACK: python "99_Attachments/scripts/generate-link-index.py" --broken
```

**When to use CLI vs Python script:**
| Need | Tool | Fallback |
|------|------|----------|
| Quick backlink lookup for 1 note | CLI `backlinks` | Grep `\[\[Note Name` |
| Count orphans/broken links | CLI totals | Python `--orphans` / `--broken` |
| Before moving/renaming a note | CLI `backlinks file=X` | Grep for wikilink |
| Broken links with fuzzy-match suggestions | Python `--broken` | (no CLI equivalent) |
| Full JSON index for bulk analysis | Python script | (always use Python) |
| Hub notes (most-referenced) | Python `top_linked` | (always use Python) |

## Query Operations

### Query Backlinks
**Try CLI first** (instant, live index):
```bash
bash "99_Attachments/scripts/obs.sh" backlinks file="Career-Development-Plan"
```

**Fallback** (if Obsidian not running):
1. Read `99_Attachments/link-index.json` → look up `backlinks["Note Name"]`
2. Or Grep for `\[\[Career-Development-Plan` across vault `*.md` files

### Find Hub Notes
Read the Python index, report `top_linked` (notes with most backlinks).

### Check Note Before Moving
1. CLI `backlinks file=X total` for instant count
2. If non-zero, show full backlink list
3. Proceed only after user confirms

## When to Use

| Situation | Action |
|-----------|--------|
| User asks "fix broken links" | Python `--broken` (has fuzzy suggestions) |
| Before refactoring files | CLI `backlinks` for quick check |
| Looking for orphaned notes | CLI `orphans total`, then Python `--orphans` for grouped list |
| Understanding vault structure | Python index hub notes |
| Moving/renaming a note | CLI `backlinks file=X` (instant) |
| Weekly maintenance | CLI `unresolved total` + `orphans total` for quick health check |
| "What links to X?" | CLI `backlinks file=X` |

## Index Structure

```json
{
  "generated": "ISO timestamp",
  "stats": {
    "total_files": 2379,
    "total_links": 8808,
    "orphan_count": 935,
    "broken_count": 2514
  },
  "orphans": ["path/to/orphan.md", ...],
  "top_linked": [["NoteName", 182], ...],
  "backlinks": {"NoteName": ["linking/file.md", ...]},
  "broken_links": {"source.md": ["BrokenTarget", ...]},
  "file_paths": {"NoteName": "actual/path.md"}
}
```

## Proactive Usage

Claude Code should invoke this skill:
- Before any batch file operations
- When user asks "what links to X?"
- When user asks about vault health/orphans/broken links
- Before archiving projects (check nothing links to them)
- After major file moves (run `--broken` to check for issues)
