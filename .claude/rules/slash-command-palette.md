# Slash Command Palette Rule — keyboard-first power moves

A slash command palette is a single input affordance that fires named
verbs without leaving the keyboard. Apps build them when a chat-shaped
input grows enough actions that the chrome (buttons, menus) stops scaling.

**Reference implementation:** `apps/rooms/pages/index.html`
(`SLASH_COMMANDS` array + `_scanSlashAtCursor` + `_renderSlashPopup`
+ `tryRunSlashCommand`). 21 commands at v3.

## When to add one

You want a slash palette when **all** of these are true:

- The app has a primary text input (chat, capture, search).
- The app already has 4+ verbs the user wants to fire from anywhere
  (hidden in overflow menus, keyboard shortcuts, modal triggers).
- Some verbs take free-text args that don't map well to buttons
  (`/remind 2h follow up`, `/save mySnippet`, `/find query`).
- Users typing fast want the verbs without mouse contact.

**Skip** when the app's surface is buttons-and-pickers (boards, settings,
admin views). The palette earns its keep against a chat-shaped input.

## The pattern

### One array, one record per command

```js
var SLASH_COMMANDS = [
    {
        name: 'remind',
        desc: 'Schedule a reminder for this room',
        args: '<when> [note]',     // shown in the palette; '' for no-arg
        needsRoom: true,           // app-specific guard; check before run
        run: function(rest) { scheduleReminder(rest); },
    },
    // ...
];
```

Every command knows:
- `name` — lowercase, alphanumeric, no spaces. Becomes `/<name>`.
- `desc` — one short line shown in the popup.
- `args` — display hint; the actual parsing is in the `run` callback.
- `needsRoom` (or similar guard flag) — the dispatcher refuses to fire
  when the precondition isn't met, with a clear toast.
- `run(rest)` — receives the text after the command name. The function
  parses, calls existing API/UI, fires toasts.

**Do not** put parsing logic in the dispatcher. Each command owns its
parser. Keeps the dispatcher trivial; lets commands evolve their args
without touching shared code.

### Two parser functions, one popup

```js
// Trigger popup: input starts with `/<word>` and no space yet
function _scanSlashAtCursor(input) {
    var v = input.value || '';
    if (!v.startsWith('/')) return null;
    var sp = v.indexOf(' ');
    if (sp >= 0) return null;  // past command name — Enter fires it
    return {query: v.slice(1).toLowerCase()};
}

// Fire prefilled command: input starts with `/<known> <args>`
function _scanSlashPrefilled(input) {
    var v = (input.value || '').trim();
    if (!v.startsWith('/')) return null;
    var sp = v.indexOf(' ');
    var name = (sp >= 0 ? v.slice(1, sp) : v.slice(1)).toLowerCase();
    var cmd = SLASH_COMMANDS.find(function(c){ return c.name === name; });
    if (!cmd) return null;
    return {cmd: cmd, rest: sp >= 0 ? v.slice(sp + 1) : ''};
}
```

The first function gates the popup (open while typing the name). The
second gates the keyboard-fire (Enter on a fully-typed command).

### Mode-aware popup state

The same popup div serves multiple input affordances (slash, @-mention,
`[[wikilink`). One state object with a `mode` field:

```js
var _mention = { open: false, items: [], active: 0, mode: 'slash' };
```

Each mode has its own renderer + acceptor. Keyboard navigation
(↑/↓/Enter/Tab/Esc) is shared and dispatches based on `mode`.

### Enter behaviour

```js
function chatInputKeydown(event) {
    if (_mention.open) {
        // ↑/↓/Enter/Tab/Esc handled by current mode's renderer + acceptor
        return;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        // Fully-typed slash command? Fire it. Otherwise sendMessage.
        if (tryRunSlashCommand()) return;
        sendMessage();
    }
}
```

Three Enter outcomes in priority order:
1. Popup open → accept the highlighted item (prefill input, or fire if no args).
2. Input is `/<known> <args>` → fire the command, clear the input.
3. Anything else → submit as a regular message.

This single-handler pattern lets one `<textarea>` carry the chat
input AND the command palette without mode-switching keystrokes.

## What goes in slash commands vs. visible buttons

Three categories:

| Category | Surface | Examples |
|---|---|---|
| **High-frequency state-changing verbs** | Both — visible button + slash | `clear`, `archive`, `pin` |
| **Context-bound verbs** | Slash-only | `remind`, `schedule`, `remember`, `find`, `context` |
| **Inspection / read** | Slash-only | `help`, `snippets`, `discard` |

Don't double up frequently-used UI buttons in slash; users learn one
or the other. Do double up the destructive ones (clear, delete) so
keyboard users don't have to mouse to confirm.

## Discoverability

- Input placeholder telegraphs the affordances:
  `Message... · @ mention · / command · [[ link`
- `/help` is a command itself; opens a modal listing every registered
  verb with its desc.
- The palette opens on first keystroke (just typing `/`), so users
  discover commands by accident without reading docs.

## When to extract to SDK

Wait for the second consumer (per CLAUDE.md Rule 9 — "build specific first
in one app; extract to `sdk/` when a second app needs it"). When that
happens, the natural extraction shape is:

```
emptyos/sdk/slash_palette.py
├── SlashCommand dataclass
├── SlashRegistry({name → SlashCommand})
├── parse_slash_at_cursor(input)
├── parse_slash_prefilled(input, registry)
└── render_palette_popup(registry, query) -> HTML
```

Plus matching client-side JS in `emptyos/web/static/eos-slash-palette.js`
that the consumer page imports. Until then, the rooms implementation is
the canonical reference.

## Anti-patterns

- **Putting heavy logic in the dispatcher.** Each command's `run` should
  be short — it calls existing API/UI. If a command needs 30 lines of
  business logic, extract that to a named function and have `run`
  call it.
- **Overlapping commands.** `/save` and `/save-snippet` and `/snippet-save`
  for the same thing — pick one. Confusing palettes are unused palettes.
- **Slash commands that don't fit the input.** `/upload-image-from-url`
  doesn't belong in a chat input; that's a button.
- **Parsing complex grammars.** `/remind every monday at 9am unless holiday`
  is a programming language, not a slash command. Use a modal with
  proper inputs for anything beyond `<when> [note]`-style parsing.
