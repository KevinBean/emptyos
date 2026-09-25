// portal-attach.js — attachments + vault context in the chat-first composers
// (desktop GUI plan B3). Runs only when PortalChat.isOn(); with the flag off
// both "+" buttons stay the disabled "coming soon" placeholders they were.
//
// Two composers, two slots: `hero` (a new chat) and `chat` (a reply in an
// open chat). Each holds the files picked for its next send — vault notes,
// uploads, pasted screenshots — as chips, plus a "Vault" toggle that asks the
// server to ground the answer in the user's notes. Picking and uploading go
// through EOS_UI.attachmentPicker (the assistant's vault search + upload
// routes, which portal already depends on). The server (agent app) turns the
// paths into image parts / extracted text, and refuses to send any of it to a
// cloud model without an explicit yes (CLAUDE.md rule 19).
//
// One namespaced global — PortalAttach — per .claude/rules/multi-module-apps.md
// § frontend counterpart. Loads before portal.js. The pure helpers at the top
// are exported for tests/js/portal_attach.test.mjs.
var PortalAttach = (function () {
    'use strict';

    var UPLOAD_URL = '/assistant/api/upload';
    var IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp)$/i;
    var MAX_ITEMS = 8;
    var S = { on: false, openThread: '', slots: { hero: { items: [], vault: false }, chat: { items: [], vault: false } } };

    // ── Pure helpers (node-tested) ─────────────────────────────────────

    function kindOf(path) { return IMAGE_RE.test(String(path || '')) ? 'image' : 'file'; }

    // A slot's state → the extra fields of a WS `message`. Empty when nothing
    // is attached and the toggle is off, so a plain send is byte-for-byte the
    // message it always was.
    function payloadOf(slot) {
        var out = {};
        var paths = (slot.items || []).map(function (i) { return i.path; }).filter(Boolean);
        if (paths.length) out.attachments = paths;
        if (slot.vault) out.vault_context = true;
        return out;
    }

    // Add without duplicates, up to the cap. Returns false when refused.
    function addItem(slot, item) {
        if (!item || !item.path) return false;
        if (slot.items.some(function (i) { return i.path === item.path; })) return true;
        if (slot.items.length >= MAX_ITEMS) return false;
        slot.items.push({ path: item.path, name: item.name || item.path.split('/').pop(), kind: kindOf(item.path) });
        return true;
    }

    // ── DOM ─────────────────────────────────────────────────────────────

    var INPUT = { hero: 'hero-input', chat: 'chat-input' };

    function _esc(s) { return EOS_UI.esc(s == null ? '' : String(s)); }
    function _composer(name) {
        var input = document.getElementById(INPUT[name]);
        return input ? input.closest('.portal-composer') : null;
    }

    // Is this slot's composer sending a chat right now?
    function _active(name) {
        if (!S.on) return false;
        if (name === 'hero') return ACTIVE_VERB === 'think' && ACTIVE_BACKEND === 'chat' && !_pendingFolderId;
        return !!(currentAgent && currentAgent._backend === 'agent' && currentAgent._profile === 'chat');
    }

    function _mount(name) {
        var box = _composer(name);
        if (!box || box.querySelector('.portal-att-chips')) return;
        var chips = document.createElement('div');
        chips.className = 'portal-att-chips';
        chips.setAttribute('data-slot', name);
        box.insertBefore(chips, box.firstChild);
        chips.onclick = function (ev) {
            var x = ev.target.closest('[data-remove]');
            if (!x) return;
            var slot = S.slots[name];
            slot.items = slot.items.filter(function (i) { return i.path !== x.getAttribute('data-remove'); });
            render(name);
        };
        var plus = box.querySelector('.portal-attach');
        if (plus) {
            plus.setAttribute('data-default-title', plus.title);
            plus.onclick = function () { if (_active(name)) pick(name); };
        }
        var vault = document.createElement('button');
        vault.type = 'button';
        vault.className = 'portal-att-vault';
        vault.setAttribute('aria-pressed', 'false');
        vault.title = 'Ground the answer in your notes';
        vault.innerHTML = '&#x1F5C2; Vault';
        vault.onclick = function () { S.slots[name].vault = !S.slots[name].vault; render(name); };
        if (plus && plus.parentNode) plus.parentNode.insertBefore(vault, plus.nextSibling);
        var input = document.getElementById(INPUT[name]);
        if (input) input.addEventListener('paste', function (ev) { _onPaste(name, ev); });
    }

    function render(name) {
        var box = _composer(name);
        if (!box) return;
        var on = _active(name);
        var slot = S.slots[name];
        var plus = box.querySelector('.portal-attach');
        if (plus) {
            plus.disabled = !on;
            plus.classList.toggle('enabled', on);
            plus.title = on ? 'Attach a note, file or image' : (plus.getAttribute('data-default-title') || plus.title);
        }
        var vault = box.querySelector('.portal-att-vault');
        if (vault) {
            vault.style.display = on ? '' : 'none';
            vault.classList.toggle('active', slot.vault);
            vault.setAttribute('aria-pressed', slot.vault ? 'true' : 'false');
            vault.innerHTML = '&#x1F5C2; Vault ' + (slot.vault ? 'on' : 'off');
        }
        var chips = box.querySelector('.portal-att-chips');
        if (chips) {
            chips.style.display = on && slot.items.length ? '' : 'none';
            chips.innerHTML = slot.items.map(function (i) {
                return '<span class="portal-att-chip" title="' + EOS_UI.escAttr(i.path) + '">' +
                    (i.kind === 'image' ? '&#x1F5BC;' : '&#x1F4CE;') + ' ' + _esc(i.name) +
                    '<button type="button" data-remove="' + EOS_UI.escAttr(i.path) + '" aria-label="Remove ' + EOS_UI.escAttr(i.name) + '">&times;</button></span>';
            }).join('');
        }
    }

    function sync() {
        if (!S.on) return;
        // Files picked in one chat must not ride the next message in another:
        // the thread composer's slot empties when the open thread changes.
        var open = (typeof currentAgent !== 'undefined' && currentAgent) ? currentAgent.id : '';
        if (open !== S.openThread) {
            S.openThread = open;
            S.slots.chat.items = [];
        }
        render('hero');
        render('chat');
    }

    function pick(name) {
        EOS_UI.attachmentPicker({
            title: 'Attach to this chat',
            onPick: function (f) {
                if (!addItem(S.slots[name], f)) EOS.toast('Up to ' + MAX_ITEMS + ' attachments per message', false);
                render(name);
            }
        });
    }

    // A pasted screenshot uploads to the vault inbox, then attaches.
    function _onPaste(name, ev) {
        if (!_active(name)) return;
        var files = Array.from((ev.clipboardData && ev.clipboardData.files) || []).filter(function (f) {
            return /^image\//.test(f.type);
        });
        if (!files.length) return;
        ev.preventDefault();
        files.forEach(function (file) {
            var fd = new FormData();
            fd.append('file', file, file.name && file.name !== 'image.png' ? file.name : 'pasted-image.png');
            EOS.apiSafe(UPLOAD_URL, { method: 'POST', body: fd }).then(function (d) {
                if (!d || !d.path) { EOS.toast((d && d.error) || 'Upload failed', false); return; }
                if (!addItem(S.slots[name], d)) EOS.toast('Up to ' + MAX_ITEMS + ' attachments per message', false);
                render(name);
            });
        });
    }

    // The extra WS fields for this slot's next send, and the names to show in
    // the sent bubble. Clears the slot (the toggle stays: it is a preference).
    function take(name) {
        if (!S.on || !_active(name)) return { payload: {}, items: [] };
        var slot = S.slots[name];
        var out = { payload: payloadOf(slot), items: slot.items.slice() };
        slot.items = [];
        render(name);
        return out;
    }

    function init() {
        if (typeof PortalChat === 'undefined' || !PortalChat.isOn()) return;
        S.on = true;
        _mount('hero');
        _mount('chat');
        sync();
    }

    return {
        init: init,
        sync: sync,
        take: take,
        // pure — exported for tests/js/portal_attach.test.mjs
        kindOf: kindOf,
        payloadOf: payloadOf,
        addItem: addItem,
        slot: function (name) { return S.slots[name]; }   // the live slot for that composer
    };
})();
