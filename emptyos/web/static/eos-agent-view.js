/* EOS_AGENT_VIEW — shared, DOM-free helpers for pages that render agent sessions.
 *
 * Two pages draw the same apps/agent session: /agent/ (the power surface) and
 * /portal/ (the chat home). Each kept its own copy of these helpers — portal's
 * header said "copied verbatim from agent.js; if both prove identical, that is
 * the rule-9 moment". They were identical, except in one place that mattered:
 * the history walk. Every message the agent persists is stored as a
 * "full-message dict" ({content, tool_calls?, tool_call_id?}, see
 * agent/sessions.py::_persist_message), and agent.js only understood plain
 * strings and block lists — so reopening a session in /agent/ drew an empty
 * transcript. One walk, one place to be right.
 *
 * Everything here returns strings or plain data; the pages own their markup and
 * CSS classes (portal draws `.portal-tc`, /agent/ draws `.tool-call`). Needs
 * `EOS_UI.esc` (eos-components.js) loaded first.
 */
var EOS_AGENT_VIEW = (function () {
    'use strict';

    function esc(s) { return EOS_UI.esc(s == null ? '' : String(s)); }

    /* Colourise a unified diff. Line classes (d-hdr/d-hunk/d-add/d-del/d-ctx)
     * let each theme own the palette. Not EOS_UI.diffLinesHtml / renderDiffLines:
     * those take pre-split [{kind, text}] rows (a SandboxedWrite payload) and
     * draw .eos-diff <div>s; tools hand back a raw unified-diff STRING with
     * ---/+++ header lines, and both agent pages style <pre class="diff"> spans. */
    function diffHtml(diff) {
        if (!diff) return '';
        var out = String(diff).split('\n').map(function (line) {
            var cls = 'd-ctx';
            if (line.startsWith('+++') || line.startsWith('---')) cls = 'd-hdr';
            else if (line.startsWith('@@')) cls = 'd-hunk';
            else if (line.startsWith('+')) cls = 'd-add';
            else if (line.startsWith('-')) cls = 'd-del';
            return '<span class="' + cls + '">' + esc(line) + '</span>';
        }).join('\n');
        return '<pre class="diff">' + out + '</pre>';
    }

    /* Rich views a tool attached to its result via `display` (diff, preview,
     * write/edit metadata, bash exit code). Live WS results only — history
     * carries no `display`. */
    function toolExtrasHtml(display) {
        if (!display || typeof display !== 'object') return '';
        var parts = [];
        if (display.diff) {
            parts.push('<div class="tc-section"><div class="tc-section-label">diff</div>' +
                diffHtml(display.diff) + '</div>');
        }
        if (display.preview) {
            parts.push('<div class="tc-section"><div class="tc-section-label">preview</div>' +
                '<pre class="diff">' + esc(display.preview) + '</pre></div>');
        }
        if (display.path && (display.bytes_delta !== undefined || display.action)) {
            var meta = esc(display.path);
            if (display.bytes_delta !== undefined) {
                var d = display.bytes_delta;
                meta += ' · ' + (d >= 0 ? '+' : '') + esc(d) + ' bytes';
            }
            if (display.action) meta += ' · ' + esc(display.action);
            if (display.replacements !== undefined) meta += ' · ' + esc(display.replacements) + ' replacement(s)';
            parts.push('<div class="tc-meta">' + meta + '</div>');
        }
        if (display.exit_code !== undefined) {
            var ok = display.exit_code === 0;
            parts.push('<div class="tc-meta">' +
                '<span class="tc-exit ' + (ok ? 'ok' : 'bad') + '">exit ' + esc(display.exit_code) + '</span>' +
                (display.command ? ' <code>' + esc(display.command) + '</code>' : '') +
                '</div>');
        }
        return parts.join('');
    }

    /* The server prices every call (usage.cost); a missing cost shows as no
     * number, never a guessed one. */
    function fmtCost(c) {
        if (!c || c <= 0) return '$0';
        if (c < 0.0001) return '<$0.0001';
        return '$' + c.toFixed(4);
    }

    function _int(v) { return parseInt(v || 0, 10) || 0; }

    /* The per-turn footer as data. `turn` = {elapsedS, tools, planMode}.
     * Returns the display parts plus the parsed numbers, so a page that keeps
     * session totals (/agent/'s cost badge) adds exactly what it showed. */
    function footerParts(usage, turn) {
        usage = usage || {};
        turn = turn || {};
        var prompt = _int(usage.prompt_tokens || usage.input_tokens);
        var completion = _int(usage.completion_tokens || usage.output_tokens);
        var cached = _int(usage.cached_tokens || usage.cache_read_input_tokens);
        var cost = parseFloat(usage.cost);
        if (!isFinite(cost) || cost < 0) cost = 0;
        var tools = _int(turn.tools);
        var parts = [(Number(turn.elapsedS) || 0).toFixed(1) + 's'];
        if (prompt + completion > 0) parts.push((prompt + completion).toLocaleString() + ' tokens');
        if (cached > 0 && prompt > 0) parts.push(Math.round(100 * cached / prompt) + '% cache');
        if (tools > 0) parts.push(tools + ' tool' + (tools === 1 ? '' : 's'));
        if (cost > 0) parts.push(fmtCost(cost));
        if (turn.planMode) parts.push('plan mode');
        return { parts: parts, prompt: prompt, completion: completion, cost: cost };
    }

    /* A tool result's text, whatever it arrived as: a string, a block list
     * ([{type:'text', text}]), or a dict with an inner `content`. */
    function resultText(content) {
        if (content == null) return '';
        if (typeof content === 'string') return content;
        if (Array.isArray(content)) {
            return content.map(function (b) { return (b && b.text) || ''; }).join('');
        }
        if (typeof content === 'object' && content.content != null) return resultText(content.content);
        try { return JSON.stringify(content); } catch (e) { return String(content); }
    }

    function _parseArgs(raw) {
        if (raw && typeof raw === 'object') return raw;
        if (typeof raw !== 'string' || !raw) return {};
        try { return JSON.parse(raw); } catch (e) { return { arguments: raw }; }
    }

    /* Assistant content → ordered segments. Adjacent text blocks merge so a
     * markdown construct split across blocks (a code fence, a list) renders
     * whole; OpenAI `tool_calls` follow the text, as the model emitted them. */
    function _assistantSegments(content, toolCalls) {
        var segs = [];
        var buf = '';
        function flush() { if (buf) { segs.push({ type: 'text', text: buf }); buf = ''; } }
        if (typeof content === 'string') buf = content;
        else if (Array.isArray(content)) {
            content.forEach(function (b) {
                if (!b) return;
                if (b.type === 'text') buf += (b.text || '');
                else if (b.type === 'tool_use') {
                    flush();
                    segs.push({ type: 'tool_use', id: b.id || '', name: b.name || 'tool', input: b.input || {} });
                }
            });
        }
        flush();
        (toolCalls || []).forEach(function (tc) {
            if (!tc) return;
            var fn = tc.function || {};
            segs.push({
                type: 'tool_use',
                id: tc.id || '',
                name: fn.name || tc.name || 'tool',
                input: _parseArgs(tc.function ? fn.arguments : tc.input),
            });
        });
        return segs;
    }

    function _user(content, h, m) {
        if (typeof content === 'string') {
            if (content) h.user(content, m);
            return;
        }
        if (!Array.isArray(content)) return;
        content.forEach(function (b) {
            if (!b) return;
            if (b.type === 'tool_result') h.toolResult(b.tool_use_id || '', resultText(b.content), !!b.is_error);
            else if (b.type === 'text' && b.text) h.user(b.text, m);
        });
    }

    /* Replay stored messages through callbacks:
     *   h.user(text, message)               a user turn (message: the stored
     *                                       row, e.g. its eos_attached names)
     *   h.assistant(segments)               [{type:'text',text} | {type:'tool_use',id,name,input}]
     *   h.toolResult(id, text, isError)     fill in the matching tool card
     *   h.notice(text)   (optional)         a message the SYSTEM put in the user role
     * Three stored encodings, all live in the same table:
     *   1. plain string content;
     *   2. an Anthropic block list;
     *   3. the full-message dict — {content, tool_calls} / {content, tool_call_id}.
     * Display metadata stored beside the content (agent/sessions.py
     * turn_display_marks) wins over it: `display_text` is what the user typed
     * when the server prepended context the model needed, and `origin:
     * 'system'` marks the loop's own nudges — shown as a notice, never as the
     * user's words, and dropped when the page has no notice callback.
     * An assistant message with nothing to show (no text, no calls) is
     * skipped, and so is any role other than user / assistant / tool. */
    function walkHistory(messages, h) {
        (messages || []).forEach(function (m) {
            if (!m) return;
            var role = m.role;
            var content = m.content;
            var toolCalls = m.tool_calls;
            var toolCallId = m.tool_call_id;
            if (content && typeof content === 'object' && !Array.isArray(content)) {
                toolCalls = content.tool_calls || toolCalls;
                toolCallId = content.tool_call_id || toolCallId;
                content = content.content;
            }
            if (role === 'tool') {
                h.toolResult(toolCallId || '', resultText(content), false);
            } else if (role === 'assistant') {
                var segs = _assistantSegments(content, toolCalls);
                if (segs.length) h.assistant(segs);
            } else if (role === 'user') {
                if (m.origin === 'system') {
                    if (h.notice && typeof content === 'string' && content) h.notice(content);
                } else if (typeof m.display_text === 'string' && m.display_text) {
                    h.user(m.display_text, m);
                } else {
                    _user(content, h, m);
                }
            }
        });
    }

    return {
        diffHtml: diffHtml,
        toolExtrasHtml: toolExtrasHtml,
        fmtCost: fmtCost,
        footerParts: footerParts,
        resultText: resultText,
        walkHistory: walkHistory,
    };
})();
