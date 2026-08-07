# Conversation source-archive contract

## Why this contract exists

An archived original and a digest serve different jobs. A digest is optimized
for retrieval; a source archive preserves evidence. Calling a tightened summary
"raw" destroys that distinction.

## Required frontmatter

```yaml
---
record_kind: conversation-source
archive_schema: eos-ai-conversation-v1
author: both
provider: claude
source_conversation_id: a99580db-a7d2-4a6a-b5f8-00d208eb9b80
source_url: https://claude.ai/chat/a99580db-a7d2-4a6a-b5f8-00d208eb9b80
title: Tomago cable design challenges
conversation_started: 2026-07-21
conversation_ended: 2026-07-22
captured_at: 2026-07-23T12:00:00+10:00
capture_method: chrome-rendered-dom
capture_fidelity: verbatim-text
raw_status: complete
message_count: 102
content_sha256: <sha256 of normalized Markdown body>
tags:
  - conversation
  - conversation-source
related:
  - "[[2026-07-21-tomago-cable-design-challenges]]"
---
```

For sensitive content add `private` and use the private archive path.

## Fidelity vocabulary

| Value | Meaning |
|---|---|
| `export-native` | Provider export retained with its native text and metadata |
| `verbatim-text` | Message text copied in order; provider UI chrome omitted |
| `partial` | One or more messages, branches, attachments, or code blocks unavailable |
| `legacy-unverified` | Older turn-by-turn note whose completeness cannot be proven |
| `source-unavailable` | Only a digest/summary survives |

`verbatim-text` is not a byte-for-byte provider export. It promises message
text fidelity, order, and explicit attachment gaps.

## Binary attachments

For provider-native exports, preserve every recoverable binary referenced by
the conversation at:

`<source-root>/assets/<provider-id-short>/<ordinal>-<safe-original-name>`

Write bytes only through `POST /api/vault/write-bytes` with the caller-computed
SHA-256. Existing identical bytes are reused; an existing different payload is
an immutable conflict. Read the asset back through `/api/vault/file` and
compare its SHA-256 before the source may claim `export-native`/`complete`.

The source frontmatter and body must record the provider asset ID, Vault
wikilink, byte size, and SHA-256. If the ZIP omits a referenced payload, retain
the provider metadata and explicit unavailable placeholder and keep the source
`partial`. Do not copy unreferenced ZIP binaries merely because they are
present.

## Body format

```markdown
# <visible title>

> Source capture. Message wording is preserved; provider UI chrome is omitted.

## Messages

### 001 · User · 2026-07-21 09:14

<exact message text>

### 002 · Claude · 2026-07-21 09:15

<exact message text>
```

Preserve code fences, tables, URLs, equations, typos, repetitions, and later
corrections. Use an explicit placeholder for unavailable non-text content:

`[Attachment unavailable: visible filename/type/description]`

## Hash definition

Hash the body only, beginning with `# <visible title>`. Normalize CRLF/CR to LF,
encode UTF-8 without a byte-order mark, then calculate SHA-256.

After the API write:

1. read the note back
2. split frontmatter from body
3. normalize the body
4. compare SHA-256
5. count message headings

If either check fails, set `raw_status: partial` or repair the write before
continuing. Do not mark `complete`.

## Immutability and recapture

Do not edit source message text after capture. If a better provider export or a
previously hidden branch becomes available, create a new capture with a
distinct filename/capture timestamp, link the captures, and mark which has the
highest fidelity in the ledger. Never silently replace historical evidence.
