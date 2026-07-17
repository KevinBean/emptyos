# EOS Markdown Profile — vault storage and rendering contract

This is the contributor contract for Markdown that must survive a round trip
through the EmptyOS vault. Read it before adding a vault-writing app, changing
frontmatter parsing, or promising that syntax renders the same on a new surface.

The profile consolidates existing behavior. It does not introduce a second note
format: the implementations and conformance tests remain authoritative.

## Decision

Markdown is the durable, human-owned format for authored vault knowledge. It is
not the store for every other concern:

| Concern | Authority |
|---|---|
| Human-authored or recovery-critical knowledge | Vault Markdown |
| Typed interpretation of app-managed frontmatter | `VaultModel` soft contracts |
| Private-vault query state | Rebuildable `VaultIndex` / search indexes |
| Published/shared copy, owner, visibility, ACL | Commons database |
| Live concurrent edits | Co-doc CRDT state; a Markdown snapshot is the durable vault record |
| Machine telemetry | Existing `data/` SQLite/JSON stores |

A frontmatter field such as `visibility: private` is metadata, not access
control. Never use a plaintext vault field as a security boundary; see
`docs/AUTH.md`.

## Note envelope

A vault note is a UTF-8 `.md` file with an optional frontmatter block followed by
a Markdown body:

```markdown
---
title: Cable bonding
tags:
  - kb
  - concept
created: 2026-07-15
related:
  - sheath-losses
---

# Cable bonding

See [[sheath-losses|sheath losses]].
```

The opening `---` must be the first line and the closing `---` must occupy its
own line. `VaultIndex` intentionally implements a small, defensive frontmatter
parser rather than the full YAML language.

### Frontmatter rules

1. **Keep it flat.** Use scalar values and block-style lists. Do not write nested
   YAML maps or lists of maps.
2. **Write tags as a block list.** The parser accepts legacy inline arrays, but
   new writes use `tags:` followed by one `- value` per line.
3. **Encode nested payloads explicitly.** Use
   `BaseApp.vault_encode_json(value)` / `vault_decode_json(...)`; use a sidecar
   `.json` or domain file when the payload is large or independently useful.
4. **Treat folders as placement, not type.** Tags identify note types; folder is
   only the default creation location.
5. **Use ISO dates.** Prefer `YYYY-MM-DD` for authored dates and explicit
   timestamp fields when time-of-day matters.
6. **Preserve unknown fields.** App-managed types use `VaultModel` with
   `extra="allow"`; human additions must survive read→write cycles.
7. **Keep secrets and permissions out.** Tokens belong in ignored config;
   enforceable ACLs belong in Commons.

See `docs/SOFT-SCHEMA.md` for typed coercion and migration discipline.

## Body syntax

The portable core is ordinary Markdown: headings, paragraphs, emphasis, lists,
blockquotes, fenced code, links, images, tables, and footnotes. EmptyOS also
stores these vault-native extensions:

| Syntax | Meaning | Shared Python renderer |
|---|---|---|
| `[[Note]]` / `[[Note|label]]` | Wikilink | Link when published; readable text when private/unresolved |
| `![[image.png]]` | Vault image embed | Resolves through the supplied assets prefix |
| `> [!note] Title` | Callout | Renders as a typed callout block |
| `- [ ]` / `- [x]` | Task | Stored as Markdown; task semantics belong to task-aware consumers |

`emptyos/sdk/markdown_render.py` is the shared Python Markdown→HTML renderer. It
guarantees wikilinks, image embeds, and callouts in addition to its configured
Markdown extensions. Client-side `EOS_UI.renderMarkdown()` is a separate
implementation for live app surfaces: do not assume byte-identical HTML, only
the documented syntax contract.

PDF output has additional presentation behavior for `%%comments%%`,
`==highlight==`, task glyphs, mastheads, and themes. Those are output rules, not
new vault storage rules; see `.claude/rules/pdf-markdown.md`.

## Source documents and derived Markdown

For a note written by a person, the Markdown file is the source of truth. For an
imported PDF, Office document, image, or archive, keep the original artifact and
treat extracted Markdown as a readable/searchable derivative.

Today the PDF/OCR path can emit page markers and KB reference notes can retain a
`source_file` / `local_text` pointer. The MarkItDown provider converts Office and
structured files to Markdown but returns text only; a caller that wants a durable
paired record must persist the source reference and conversion provenance. Never
delete or silently replace an original during conversion.

## Sharing and collaboration

The private and shared authorities stay separate:

- One vault belongs to one user and one daemon.
- Publishing creates/updates a shared copy in Commons; Commons owns that copy's
  owner, visibility, ACL, and shared body.
- Co-doc owns ephemeral concurrent editing state. Snapshot-back produces an
  `author: both` Markdown note in the owner's vault.
- Multi-user growth happens through per-user daemons plus shared services, never
  by placing several users inside one plaintext vault.

See `docs/AUTH.md` and `docs/CLOUD-ARCHITECTURE.md` for the pinned boundaries.

## Conformance

`tests/test_sdk_eos_markdown_profile.py` pins one representative note across the
frontmatter parser, nested-payload helpers, wikilinks, private-link fallback,
callouts, tables, and image embeds. Narrow regression suites continue to own
edge cases such as quoted empty strings, frontmatter injection, image path forms,
and PDF-specific cleanup.

When changing the profile:

1. Change the owning parser/renderer first.
2. Add or update a regression test that proves the behavior.
3. Update this map only after the behavior exists.
4. Do not broaden every renderer merely to make them byte-identical; document a
   surface-specific extension when the difference is intentional.

