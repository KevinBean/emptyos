/* EmptyOS Shared UI Components — JS
   Include via: <script src="/static/eos-components.js"></script>
   Requires: eos.js loaded first
*/

var EOS_UI = {
    // Toast notification
    toast: function(msg, ok) {
        var el = document.getElementById('eos-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'eos-toast';
            el.className = 'eos-toast eos-toast-ok';
            document.body.appendChild(el);
        }
        el.textContent = msg;
        el.className = 'eos-toast ' + (ok !== false ? 'eos-toast-ok' : 'eos-toast-err') + ' show';
        clearTimeout(el._timer);
        el._timer = setTimeout(function() { el.classList.remove('show'); }, 2500);
    },

    // Escape HTML
    esc: function(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; },

    // Strip markdown noise so TTS reads prose, not punctuation. Collapses fenced
    // code blocks to a short marker (too long to speak verbatim), flattens inline
    // code to plain text, drops heading/bold/italic chars, normalises newlines.
    // Used by apps/assistant speakText and the hands-free overlay — extracted here
    // because both were verbatim copies of the same six regex replacements.
    stripMarkdownForTts: function(text) {
        return String(text || '')
            .replace(/```[\s\S]*?```/g, '(code block)')
            .replace(/`[^`]+`/g, function(m) { return m.slice(1, -1); })
            .replace(/[#*_~\[\]]/g, '')
            .replace(/\n{2,}/g, '. ')
            .replace(/\n/g, ' ')
            .trim();
    },

    // Tab switching — works with .eos-tab buttons and .eos-tab-content panels.
    // Usage 1 (array): EOS_UI.switchTab(['log','history','calendar'], 'history')
    // Usage 2 (auto):  EOS_UI.switchTab(name) — finds tabs by data-tab attribute
    // HTML: <div class="eos-tabs"><div class="eos-tab" data-tab="log" onclick="EOS_UI.switchTab('log')">Log</div>...</div>
    //       <div id="tab-log" class="eos-tab-content active">...</div>
    switchTab: function(tabsOrName, name) {
        if (typeof tabsOrName === 'string') {
            // Auto mode: find all tabs by data-tab attribute
            name = tabsOrName;
            document.querySelectorAll('.eos-tab[data-tab]').forEach(function(el) {
                el.classList.toggle('active', el.getAttribute('data-tab') === name);
            });
        } else {
            // Legacy array mode
            tabsOrName.forEach(function(t, i) {
                var tabEl = document.querySelectorAll('.eos-tab')[i];
                if (tabEl) tabEl.classList.toggle('active', tabsOrName[i] === name);
            });
        }
        document.querySelectorAll('.eos-tab-content').forEach(function(el) {
            var id = el.id.replace('tab-', '');
            el.classList.toggle('active', id === name);
        });
    },

    // SVG Sparkline — inline trend chart
    // Usage: EOS_UI.sparkline(targetId, [3,5,2,8,6,9,4], {color:'#8b5cf6', height:40, width:120})
    sparkline: function(targetId, values, opts) {
        var el = document.getElementById(targetId);
        if (!el || !values || !values.length) return;
        opts = opts || {};
        var w = opts.width || el.offsetWidth || 120;
        var h = opts.height || 40;
        var color = opts.color || 'var(--accent)';
        var fill = opts.fill || false;

        var min = Math.min.apply(null, values);
        var max = Math.max.apply(null, values);
        var range = max - min || 1;
        var pad = 2;
        var step = (w - pad * 2) / (values.length - 1);

        var points = values.map(function(v, i) {
            var x = pad + i * step;
            var y = h - pad - ((v - min) / range) * (h - pad * 2);
            return x.toFixed(1) + ',' + y.toFixed(1);
        });

        var svg = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">';
        if (fill) {
            svg += '<polygon points="' + pad + ',' + (h - pad) + ' ' + points.join(' ') + ' ' + (w - pad) + ',' + (h - pad) +
                '" fill="' + color + '" opacity="0.1"/>';
        }
        svg += '<polyline points="' + points.join(' ') + '" fill="none" stroke="' + color +
            '" stroke-width="' + (opts.strokeWidth || 2) + '" stroke-linecap="round" stroke-linejoin="round"/>';
        // Last point dot
        var last = points[points.length - 1].split(',');
        svg += '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="3" fill="' + color + '"/>';
        svg += '</svg>';
        el.innerHTML = svg;
    },


    // SVG Donut chart from {category: amount} data
    donut: function(targetId, data, colors) {
        colors = colors || ['#8b5cf6','#3b82f6','#10b981','#f59e0b','#ef4444','#ec4899','#06b6d4','#84cc16','#f97316','#6366f1','#14b8a6','#e11d48'];
        var entries = Object.entries(data).sort(function(a,b) { return b[1]-a[1]; });
        var total = entries.reduce(function(s,e) { return s+e[1]; }, 0);
        if (total === 0) { document.getElementById(targetId).innerHTML = ''; return; }

        var r=50, cx=60, cy=60, C=2*Math.PI*r, offset=0;
        var circles = entries.map(function(e,i) {
            var pct=e[1]/total, dash=pct*C;
            var c='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="'+colors[i%colors.length]+'" stroke-width="14" stroke-dasharray="'+dash.toFixed(1)+' '+(C-dash).toFixed(1)+'" stroke-dashoffset="-'+offset.toFixed(1)+'" style="transition:stroke-dasharray 0.5s"/>';
            offset+=dash; return c;
        }).join('');
        var svg = '<div><svg width="130" height="130" viewBox="0 0 120 120" style="transform:rotate(-90deg)">'+circles+'</svg></div>';
        var legend = '<div class="eos-legend">' + entries.slice(0,8).map(function(e,i) {
            var pct = (e[1]/total*100).toFixed(0);
            var val = typeof e[1]==='number' && e[1]%1!==0 ? e[1].toFixed(1) : e[1];
            return '<div class="eos-legend-row"><span class="eos-legend-dot" style="background:'+colors[i%colors.length]+'"></span><span class="eos-legend-name">'+e[0]+'</span><span class="eos-legend-val">'+val+'</span><span class="eos-legend-pct">'+pct+'%</span></div>';
        }).join('') + '</div>';

        document.getElementById(targetId).innerHTML = svg + legend;
    },

    // SVG Ring (health score, level progress, etc.)
    ring: function(targetId, score, max, color) {
        max = max || 100;
        var ringEl = document.getElementById(targetId);
        if (!ringEl) return;
        var circumference = parseFloat(ringEl.getAttribute('stroke-dasharray') || '377');
        var offset = circumference - (circumference * score / max);
        ringEl.style.strokeDashoffset = offset;
        ringEl.style.stroke = color || (score >= 80 ? '#10b981' : score >= 60 ? '#f59e0b' : '#ef4444');
    },

    // Heatmap from {date: count} data
    heatmap: function(targetId, data, days) {
        days = days || 90;
        var cells = [];
        var today = new Date();
        for (var i = days-1; i >= 0; i--) {
            var d = new Date(today); d.setDate(d.getDate() - i);
            var key = d.toISOString().slice(0, 10);
            var count = data[key] || 0;
            var cls = count === 0 ? '' : count <= 1 ? 'eos-hm-1' : count <= 3 ? 'eos-hm-2' : count <= 5 ? 'eos-hm-3' : 'eos-hm-4';
            cells.push('<div class="eos-hm ' + cls + '" title="' + key + ': ' + count + '"></div>');
        }
        var el = document.getElementById(targetId);
        el.classList.add('eos-heatmap');
        el.innerHTML = cells.join('');
    },

    // Render entry list
    entries: function(targetId, items, config) {
        config = config || {};
        var el = document.getElementById(targetId);
        if (!items.length) { el.innerHTML = '<div class="eos-empty">No items</div>'; return; }
        var textField = config.text || 'description';
        var valField = config.value || 'amount';
        var catField = config.category || 'category';
        var dateField = config.date || 'date';
        var prefix = config.prefix || '';
        var colors = config.colors || ['#8b5cf6','#3b82f6','#10b981','#f59e0b','#ef4444','#ec4899','#06b6d4','#84cc16'];

        el.innerHTML = items.map(function(e) {
            var text = e[textField] || e.text || e.name || e.note || '';
            var val = e[valField];
            var valStr = val != null ? prefix + (typeof val==='number' ? val.toFixed(2) : val) : '';
            var cat = e[catField] || '';
            var date = (e[dateField] || e.timestamp || '').slice(0,10);
            var ci = Math.abs(cat.split('').reduce(function(h,c){return((h<<5)-h)+c.charCodeAt(0)|0},0)) % colors.length;
            return '<div class="eos-entry card-in">' +
                '<span class="eos-entry-date">' + date.slice(5) + '</span>' +
                '<span class="eos-entry-text">' + EOS_UI.esc(text) + '</span>' +
                (cat ? '<span class="eos-entry-badge" style="background:'+colors[ci]+'18;color:'+colors[ci]+'">' + EOS_UI.esc(cat) + '</span>' : '') +
                (valStr ? '<span class="eos-entry-val">' + valStr + '</span>' : '') +
                '</div>';
        }).join('');
    },

    // Open modal
    openModal: function(modalId) {
        document.getElementById(modalId).classList.add('show');
    },
    closeModal: function(modalId) {
        if (modalId) { var el = document.getElementById(modalId); if (el) el.classList.remove('show'); return; }
        var overlay = document.getElementById('eos-modal-overlay');
        if (overlay) overlay.remove();
        if (EOS_UI._modalOnClose) EOS_UI._modalOnClose();
        EOS_UI._modalOnClose = null;
        if (EOS_UI._modalEscHandler) {
            document.removeEventListener('keydown', EOS_UI._modalEscHandler);
            EOS_UI._modalEscHandler = null;
        }
    },

    // --- Markdown renderer with Obsidian-flavored note links ---
    renderMarkdown: function(text) {
        // Step 1: Extract wikilinks + vault paths BEFORE esc (they contain [] which survive esc, but do it cleanly)
        var _links = [];
        var _ph = function(html) { var id = '\x00LINK' + _links.length + '\x00'; _links.push(html); return id; };

        // Wikilinks: [[Note Name]] or [[Note Name|Display]]
        text = text.replace(/\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g, function(_, target, display) {
            var label = display || target;
            var path = target.replace(/\s/g, '-');
            if (!path.endsWith('.md')) path += '.md';
            return _ph('<a href="#" onclick="EOS.viewNote(\'' + EOS.escPath(path) + '\');return false" class="obs-link note-ref" title="' + EOS_UI.esc(target) + '">📎 ' + EOS_UI.esc(label) + '</a>');
        });

        // Full vault paths: /path/to/vault/.../Note.md
        var vp = (EOS.vaultPath || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        text = text.replace(new RegExp('(' + vp + '/[^\\s,)\\]]+\\.md)', 'g'), function(m) {
            var name = EOS.fileName(m).replace(/-/g, ' ');
            return _ph('<a href="#" onclick="EOS.viewNote(\'' + EOS.escPath(m) + '\');return false" class="obs-link note-ref" title="' + EOS_UI.esc(m) + '">📄 ' + EOS_UI.esc(name) + '</a>');
        });

        // Bare filenames: Something-Name.md
        text = text.replace(/(?<![\/\w])(\b[\w][\w-]+\.md)\b/g, function(m) {
            var name = m.replace('.md', '').replace(/-/g, ' ');
            return _ph('<a href="#" onclick="EOS.viewNote(\'' + EOS.escPath(m) + '\');return false" class="obs-link note-ref">' + EOS_UI.esc(name) + '</a>');
        });

        // Step 2: Separate frontmatter from body
        var frontmatter = '';
        var body = text;
        var fmMatch = text.match(/^---\n([\s\S]*?)\n---\n?/);
        if (fmMatch) {
            frontmatter = fmMatch[1];
            body = text.slice(fmMatch[0].length);
        }

        // Step 3: Extract code blocks before escaping (preserve content)
        var _codeBlocks = [];
        body = body.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
            var id = '\x00CODE' + _codeBlocks.length + '\x00';
            _codeBlocks.push('<pre class="obs-code-block"><code class="lang-' + (lang || 'text') + '">' + EOS_UI.esc(code.trimEnd()) + '</code></pre>');
            return id;
        });

        // Step 4: Escape HTML + markdown formatting
        var html = EOS_UI.esc(body)
            // Obsidian tags: #tag
            .replace(/(^|\s)#([\w\u4e00-\u9fff][\w\u4e00-\u9fff-]*)/g, '$1<span class="obs-tag">#$2</span>')
            // Headers (proper sizes)
            .replace(/^#{4} (.+)$/gm, '<h5>$1</h5>')
            .replace(/^#{3} (.+)$/gm, '<h4>$1</h4>')
            .replace(/^#{2} (.+)$/gm, '<h3>$1</h3>')
            .replace(/^#{1} (.+)$/gm, '<h2>$1</h2>')
            // Checkboxes
            .replace(/^- \[x\] (.+)$/gm, '<li class="obs-done">✅ $1</li>')
            .replace(/^- \[ \] (.+)$/gm, '<li class="obs-todo">⬜ $1</li>')
            // Bold + italic
            .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
            // Strikethrough
            .replace(/~~(.+?)~~/g, '<del>$1</del>')
            // Inline code
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            // Blockquote (including callouts)
            .replace(/^&gt; \[!(\w+)\](.*)$/gm, '<blockquote class="obs-callout obs-callout-$1"><strong>$1</strong>$2</blockquote>')
            .replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>')
            // Tables
            .replace(/((?:^\|.+\|$\n?)+)/gm, function(block) {
                var rows = block.trim().split('\n').filter(function(r) { return r.trim(); });
                if (rows.length < 2) return block;
                var isAlignRow = /^\|[\s:-]+\|$/.test(rows[1]);
                var startIdx = isAlignRow ? 2 : 0;
                var headerRow = isAlignRow ? rows[0] : null;
                var thead = '';
                if (headerRow) {
                    var hCells = headerRow.split('|').slice(1, -1);
                    thead = '<thead><tr>' + hCells.map(function(c) { return '<th>' + c.trim() + '</th>'; }).join('') + '</tr></thead>';
                }
                var tbody = rows.slice(startIdx).map(function(r) {
                    var cells = r.split('|').slice(1, -1);
                    return '<tr>' + cells.map(function(c) { return '<td>' + c.trim() + '</td>'; }).join('') + '</tr>';
                }).join('');
                return '<table class="obs-table">' + thead + '<tbody>' + tbody + '</tbody></table>';
            })
            // Unordered lists
            .replace(/^[-*] (.+)$/gm, '<li>$1</li>')
            // Numbered lists
            .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
            // Horizontal rule
            .replace(/^---$/gm, '<hr>')
            // Wrap consecutive <li> in <ul>
            .replace(/((?:<li[^>]*>.*<\/li>\n?)+)/g, '<ul>$1</ul>')
            // Merge consecutive blockquotes
            .replace(/<\/blockquote>\n?<blockquote>/g, '<br>')
            // Paragraphs
            .replace(/\n{2,}/g, '</p><p>')
            .replace(/([^>])\n([^<])/g, '$1<br>$2')
            .replace(/^(.+)/, '<p>$1</p>')
            .replace(/<p>\s*<\/p>/g, '');

        // Step 5: Restore code blocks
        _codeBlocks.forEach(function(block, i) {
            html = html.replace('\x00CODE' + i + '\x00', block);
        });

        // Step 6: Add frontmatter display
        if (frontmatter) {
            var fmHtml = '<div class="obs-frontmatter"><div class="obs-fm-label">Properties</div>';
            frontmatter.split('\n').forEach(function(line) {
                var m = line.match(/^(\w[\w-]*)\s*:\s*(.+)/);
                if (m) {
                    var val = m[2].trim();
                    // Render tags as pills
                    if (m[1] === 'tags') {
                        var tags = val.replace(/[\[\]]/g, '').split(',').map(function(t) { return t.trim(); }).filter(Boolean);
                        val = tags.map(function(t) { return '<span class="obs-tag">#' + EOS_UI.esc(t) + '</span>'; }).join(' ');
                    } else {
                        val = EOS_UI.esc(val);
                    }
                    fmHtml += '<div class="obs-fm-row"><span class="obs-fm-key">' + EOS_UI.esc(m[1]) + '</span><span class="obs-fm-val">' + val + '</span></div>';
                }
            });
            fmHtml += '</div>';
            html = fmHtml + html;
        }

        // Step 3: Restore link placeholders
        _links.forEach(function(link, i) {
            html = html.replace('\x00LINK' + i + '\x00', link);
        });

        return html;
    },

    // --- Note Viewer / Editor ---
    // Universal vault note viewer+editor that can be called from any app page.
    // Usage: EOS.viewNote('30_Resources/Books/My-Book.md')
    //        EOS.editNote('20_Areas/Health/mood-log.md')

    _noteOverlay: null,

    _ensureNoteUI: function() {
        if (EOS_UI._noteOverlay) return;
        var overlay = document.createElement('div');
        overlay.id = 'eos-note-overlay';
        overlay.className = 'eos-note-overlay';
        overlay.innerHTML =
            '<div class="eos-note-panel">' +
                '<div class="eos-note-header">' +
                    '<div class="eos-note-title" id="eos-note-title"></div>' +
                    '<div class="eos-note-actions">' +
                        '<button class="eos-note-btn" id="eos-note-viewer" title="Open in viewer" onclick="EOS_UI._openNoteInViewer()">Open ↗</button>' +
                        '<button class="eos-note-btn eos-note-btn-edit" id="eos-note-edit-btn" onclick="EOS_UI._toggleEdit()">Edit</button>' +
                        '<button class="eos-note-btn eos-note-btn-close" onclick="EOS_UI.closeNote()">×</button>' +
                    '</div>' +
                '</div>' +
                '<div class="eos-note-body" id="eos-note-body">' +
                    '<pre class="eos-note-content" id="eos-note-view"></pre>' +
                    '<textarea class="eos-note-editor" id="eos-note-editor" style="display:none"></textarea>' +
                '</div>' +
                '<div class="eos-note-footer" id="eos-note-footer" style="display:none">' +
                    '<button class="eos-btn eos-btn-sm" onclick="EOS_UI._saveNote()">Save</button>' +
                    '<button class="eos-btn eos-btn-sm eos-btn-ghost" onclick="EOS_UI._cancelEdit()">Cancel</button>' +
                    '<span class="eos-note-status" id="eos-note-status"></span>' +
                '</div>' +
            '</div>';
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) EOS_UI.closeNote();
        });
        document.body.appendChild(overlay);
        EOS_UI._noteOverlay = overlay;
    },

    _notePath: '',
    _noteEditing: false,
    _noteOriginal: '',

    viewNote: function(path) {
        EOS_UI._ensureNoteUI();
        path = EOS.normPath(path);
        EOS_UI._notePath = path;
        EOS_UI._noteEditing = false;
        var overlay = document.getElementById('eos-note-overlay');
        var titleEl = document.getElementById('eos-note-title');
        var viewEl = document.getElementById('eos-note-view');
        var editorEl = document.getElementById('eos-note-editor');
        var footerEl = document.getElementById('eos-note-footer');
        var editBtn = document.getElementById('eos-note-edit-btn');

        // Show loading
        titleEl.textContent = EOS.fileName(path).replace(/-/g, ' ');
        viewEl.textContent = 'Loading...';
        editorEl.style.display = 'none';
        viewEl.style.display = 'block';
        footerEl.style.display = 'none';
        editBtn.textContent = 'Edit';
        overlay.classList.add('open');
        document.body.style.overflow = 'hidden';

        // Fetch content
        fetch(EOS.base + '/api/vault/read?path=' + encodeURIComponent(path))
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.error) {
                    viewEl.textContent = 'Error: ' + d.error;
                    return;
                }
                EOS_UI._noteOriginal = d.content;
                viewEl.innerHTML = EOS_UI.renderMarkdown(d.content);
                viewEl.style.whiteSpace = 'normal';
            })
            .catch(function(e) {
                viewEl.textContent = 'Failed to load: ' + e.message;
            });
    },

    editNote: function(path) {
        EOS_UI.viewNote(path);
        setTimeout(function() { EOS_UI._toggleEdit(); }, 300);
    },

    _editorKeydown: function(e) {
        var ta = e.target;
        // Ctrl+S / Cmd+S — save
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault();
            EOS_UI._saveNote();
            return;
        }
        // Tab — indent
        if (e.key === 'Tab') {
            e.preventDefault();
            var start = ta.selectionStart, end = ta.selectionEnd;
            if (e.shiftKey) {
                // Unindent: remove leading tab/spaces from selected lines
                var before = ta.value.substring(0, start);
                var sel = ta.value.substring(start, end);
                var after = ta.value.substring(end);
                var lineStart = before.lastIndexOf('\n') + 1;
                var block = ta.value.substring(lineStart, end);
                var unindented = block.replace(/^(\t|    )/gm, '');
                var diff = block.length - unindented.length;
                ta.value = ta.value.substring(0, lineStart) + unindented + after;
                ta.selectionStart = Math.max(lineStart, start - (diff > 0 ? Math.min(diff, 4) : 0));
                ta.selectionEnd = end - diff;
            } else if (start === end) {
                ta.value = ta.value.substring(0, start) + '    ' + ta.value.substring(end);
                ta.selectionStart = ta.selectionEnd = start + 4;
            } else {
                // Indent selected lines
                var before2 = ta.value.substring(0, start);
                var lineStart2 = before2.lastIndexOf('\n') + 1;
                var block2 = ta.value.substring(lineStart2, end);
                var indented = block2.replace(/^/gm, '    ');
                var diff2 = indented.length - block2.length;
                ta.value = ta.value.substring(0, lineStart2) + indented + ta.value.substring(end);
                ta.selectionStart = start + 4;
                ta.selectionEnd = end + diff2;
            }
            return;
        }
        // Ctrl+B — bold
        if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
            e.preventDefault();
            EOS_UI._wrapSelection(ta, '**', '**');
            return;
        }
        // Ctrl+I — italic
        if ((e.ctrlKey || e.metaKey) && e.key === 'i') {
            e.preventDefault();
            EOS_UI._wrapSelection(ta, '*', '*');
            return;
        }
        // Ctrl+K — link
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            EOS_UI._wrapSelection(ta, '[[', ']]');
            return;
        }
    },

    _wrapSelection: function(ta, before, after) {
        var start = ta.selectionStart, end = ta.selectionEnd;
        var sel = ta.value.substring(start, end);
        ta.value = ta.value.substring(0, start) + before + sel + after + ta.value.substring(end);
        ta.selectionStart = start + before.length;
        ta.selectionEnd = end + before.length;
        ta.focus();
    },

    closeNote: function() {
        var overlay = document.getElementById('eos-note-overlay');
        if (overlay) {
            overlay.classList.remove('open');
            document.body.style.overflow = '';
        }
    },

    _toggleEdit: function() {
        var viewEl = document.getElementById('eos-note-view');
        var editorEl = document.getElementById('eos-note-editor');
        var footerEl = document.getElementById('eos-note-footer');
        var editBtn = document.getElementById('eos-note-edit-btn');

        EOS_UI._noteEditing = !EOS_UI._noteEditing;
        if (EOS_UI._noteEditing) {
            editorEl.value = EOS_UI._noteOriginal;
            viewEl.style.display = 'none';
            editorEl.style.display = 'block';
            footerEl.style.display = 'flex';
            editBtn.textContent = 'View';
            editorEl.onkeydown = EOS_UI._editorKeydown;
            editorEl.focus();
        } else {
            EOS_UI._noteOriginal = editorEl.value;
            viewEl.innerHTML = EOS_UI.renderMarkdown(EOS_UI._noteOriginal);
            viewEl.style.whiteSpace = 'normal';
            viewEl.style.display = 'block';
            editorEl.style.display = 'none';
            footerEl.style.display = 'none';
            editorEl.onkeydown = null;
            editBtn.textContent = 'Edit';
        }
    },

    _cancelEdit: function() {
        var editorEl = document.getElementById('eos-note-editor');
        editorEl.value = EOS_UI._noteOriginal;
        EOS_UI._noteEditing = true;
        EOS_UI._toggleEdit();
    },

    _saveNote: function() {
        var editorEl = document.getElementById('eos-note-editor');
        var statusEl = document.getElementById('eos-note-status');
        var content = editorEl.value;
        statusEl.textContent = 'Saving...';

        fetch(EOS.base + '/api/vault/write', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: EOS_UI._notePath, content: content}),
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.ok) {
                EOS_UI._noteOriginal = content;
                statusEl.textContent = 'Saved!';
                EOS_UI.toast('Note saved', true);
                setTimeout(function() { statusEl.textContent = ''; }, 2000);
            } else {
                statusEl.textContent = 'Error: ' + (d.error || 'unknown');
                EOS_UI.toast('Save failed', false);
            }
        })
        .catch(function(e) {
            statusEl.textContent = 'Failed: ' + e.message;
            EOS_UI.toast('Save failed', false);
        });
    },

    _openNoteInViewer: function() {
        if (EOS_UI._notePath) {
            EOS.openInViewer(EOS_UI._notePath);
        }
    },

    // --- Stat Cards ---
    // Render a row of stat cards into a target element.
    // items: [{value, label, color?}] or {label: value, ...}
    statCards: function(targetId, items) {
        var el = document.getElementById(targetId);
        if (!el) return;
        // Normalize: accept either array or object
        if (!Array.isArray(items)) {
            items = Object.entries(items).map(function(e) { return {label: e[0], value: e[1]}; });
        }
        el.innerHTML = items.map(function(s, i) {
            // Prefer semantic variant; fall back to legacy inline color.
            var variantCls = s.variant ? ' eos-stat-card--' + s.variant : '';
            var colorAttr = (!s.variant && s.color) ? ' style="color:' + s.color + '"' : '';
            return '<div class="eos-stat-card' + variantCls + '" style="animation-delay:' + (i * 0.05) + 's">' +
                '<div class="eos-stat-val"' + colorAttr + '>' + EOS_UI.esc(String(s.value)) + '</div>' +
                '<div class="eos-stat-lbl">' + EOS_UI.esc(s.label) + '</div>' +
                '</div>';
        }).join('');
    },

    // --- Entity Card ---
    // Build a list-item card for entities (projects, posts, contacts, etc.).
    // Returns an HTML string. Compose in lists: items.map(EOS_UI.entityCard).join('')
    // opts: {
    //   title: string,
    //   subtitle?: string,
    //   badges?: [{label, variant}],        // variant = "status-active", "priority-high", "age-fresh", ...
    //   meta?: string | HTML,                // bottom row (date, counts, small notes)
    //   body?: string,                       // optional body text (small paragraph between head and meta)
    //   actions?: string,                    // HTML for action buttons (rendered in eec-actions)
    //   onClick?: string,                    // onclick JS expression; wraps card as clickable
    //   id?: string,                         // optional DOM id
    //   className?: string,                  // extra classes on the card root
    // }
    entityCard: function(opts) {
        var esc = EOS_UI.esc;
        var parts = [];
        var headRight = '';
        if (opts.badges && opts.badges.length) {
            headRight = '<div class="eec-badges right">' + opts.badges.map(function(b) {
                return '<span class="eos-badge eos-badge-' + esc(b.variant || 'neutral') + '">' + esc(b.label) + '</span>';
            }).join('') + '</div>';
        }
        parts.push('<div class="eec-head">');
        parts.push('<div style="flex:1;min-width:0">');
        parts.push('<div class="eec-title">' + esc(opts.title || '') + '</div>');
        if (opts.subtitle) parts.push('<div class="eec-sub">' + esc(opts.subtitle) + '</div>');
        parts.push('</div>');
        if (headRight) parts.push(headRight);
        parts.push('</div>');
        if (opts.body) parts.push('<div class="eec-body">' + opts.body + '</div>');
        if (opts.meta) parts.push('<div class="eec-meta">' + opts.meta + '</div>');
        if (opts.actions) parts.push('<div class="eec-actions">' + opts.actions + '</div>');

        var cls = 'eos-entity-card' + (opts.className ? ' ' + opts.className : '');
        if (!opts.onClick) cls += ' no-hover';
        var attrs = '';
        if (opts.id) attrs += ' id="' + esc(opts.id) + '"';
        if (opts.onClick) attrs += ' onclick="' + opts.onClick.replace(/"/g, '&quot;') + '"';
        return '<div class="' + cls + '"' + attrs + '>' + parts.join('') + '</div>';
    },

    // --- Provenance chip — required on AI-authored content.
    // opts: {mode: 'local'|'cloud'|'user', model?: string, provider?: string,
    //        cost?: number, title?: string (tooltip)}
    // Returns an HTML string; caller inserts it into an AI card's top-right.
    provenance: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var mode = opts.mode || 'user';
        var icon = mode === 'local' ? '🔒' : mode === 'cloud' ? '☁' : '👤';
        var parts = ['<span class="eos-badge-provenance-icon">' + icon + '</span>'];
        parts.push('<span>' + esc(mode) + '</span>');
        if (opts.provider) parts.push('<span>·</span><span>' + esc(opts.provider) + '</span>');
        if (opts.model) parts.push('<span>·</span><span>' + esc(opts.model) + '</span>');
        if (opts.cost != null && mode === 'cloud') {
            var c = typeof opts.cost === 'number' ? '~$' + opts.cost.toFixed(Math.max(3, Math.ceil(-Math.log10(opts.cost || 0.001)))) : opts.cost;
            parts.push('<span class="eos-badge-provenance-cost">· ' + esc(c) + '</span>');
        }
        var title = opts.title ? ' title="' + esc(opts.title) + '"' : '';
        return '<span class="eos-badge-provenance eos-badge-provenance-' + esc(mode) + '"' + title + '>' + parts.join('') + '</span>';
    },

    // --- Empty state ---
    // opts: {message, icon?, actionLabel?, onAction? (JS expression string)}
    emptyState: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var parts = [];
        if (opts.icon) parts.push('<span class="eos-empty-state-icon">' + opts.icon + '</span>');
        parts.push('<p class="eos-empty-state-message">' + esc(opts.message || 'Nothing here yet.') + '</p>');
        if (opts.actionLabel && opts.onAction) {
            parts.push('<div class="eos-empty-state-action"><button class="eos-btn eos-btn-sm" onclick="' + opts.onAction.replace(/"/g, '&quot;') + '">' + esc(opts.actionLabel) + '</button></div>');
        }
        return '<div class="eos-empty-state">' + parts.join('') + '</div>';
    },

    // --- Error state ---
    // opts: {message, onRetry? (JS expression string)}
    errorState: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var parts = ['<span class="eos-error-state-icon">⚠</span>'];
        parts.push('<p class="eos-error-state-message">' + esc(opts.message || 'Something went wrong.') + '</p>');
        if (opts.onRetry) {
            parts.push('<div class="eos-error-state-action"><button class="eos-btn eos-btn-sm eos-btn-ghost" onclick="' + opts.onRetry.replace(/"/g, '&quot;') + '">Retry</button></div>');
        }
        return '<div class="eos-error-state">' + parts.join('') + '</div>';
    },

    // --- Warning banner ---
    // Inline warning strip — feature-disabled notices, public-mode gates, etc.
    // Returns HTML string; caller assigns to a container's innerHTML.
    // opts: {message, icon? (default "⚠"), tone? "warning"|"info" (default warning)}
    warningBanner: function(opts) {
        opts = opts || {};
        var esc = EOS_UI.esc;
        var tone = opts.tone === 'info' ? 'info' : 'warning';
        var icon = opts.icon || (tone === 'info' ? 'ⓘ' : '⚠');
        return '<div class="eos-warning-banner eos-warning-banner--' + tone + '">' +
            '<span class="eos-warning-banner-icon">' + esc(icon) + '</span>' +
            '<span class="eos-warning-banner-message">' + esc(opts.message || '') + '</span>' +
            '</div>';
    },

    // --- Modal ---
    // Show a modal with custom content. Returns the modal element.
    // options: {title, body (HTML string), onClose?, width?}
    modal: function(options) {
        var existing = document.getElementById('eos-modal-overlay');
        if (existing) existing.remove();

        var overlay = document.createElement('div');
        overlay.id = 'eos-modal-overlay';
        overlay.className = 'eos-modal-overlay show';
        overlay.onclick = function(e) { if (e.target === overlay) EOS_UI.closeModal(); };

        var modal = document.createElement('div');
        modal.className = 'eos-modal';
        if (options.width) modal.style.maxWidth = options.width;

        modal.innerHTML =
            '<div class="eos-modal-header">' +
                '<h2>' + EOS_UI.esc(options.title || '') + '</h2>' +
                '<button class="eos-modal-close" onclick="EOS_UI.closeModal()">&times;</button>' +
            '</div>' +
            '<div class="eos-modal-body">' + (options.body || '') + '</div>';

        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        EOS_UI._modalOnClose = options.onClose || null;

        // Escape key closes modal
        if (EOS_UI._modalEscHandler) {
            document.removeEventListener('keydown', EOS_UI._modalEscHandler);
        }
        EOS_UI._modalEscHandler = function(e) {
            if (e.key === 'Escape' && document.getElementById('eos-modal-overlay')) {
                EOS_UI.closeModal();
            }
        };
        document.addEventListener('keydown', EOS_UI._modalEscHandler);
        return modal;
    },
    _modalOnClose: null,

    // --- Form Builder ---
    // Build a simple form inside a modal. Returns form HTML string.
    // fields: [{key, label, type?, placeholder?, value?, options?}]
    formHtml: function(fields, submitLabel) {
        return fields.map(function(f) {
            var id = 'eos-form-' + f.key;
            var val = f.value || '';
            var input;
            if (f.type === 'textarea') {
                input = '<textarea id="' + id + '" class="eos-form-input" placeholder="' + EOS_UI.esc(f.placeholder || '') + '" rows="3">' + EOS_UI.esc(val) + '</textarea>';
            } else if (f.type === 'select' && f.options) {
                var opts = f.options.map(function(o) {
                    return '<option value="' + EOS_UI.esc(o) + '"' + (o === val ? ' selected' : '') + '>' + EOS_UI.esc(o) + '</option>';
                }).join('');
                input = '<select id="' + id + '" class="eos-form-input">' + opts + '</select>';
            } else if (f.type === 'number') {
                input = '<input id="' + id + '" class="eos-form-input" type="number" value="' + EOS_UI.esc(val) + '" placeholder="' + EOS_UI.esc(f.placeholder || '') + '" step="any">';
            } else {
                input = '<input id="' + id + '" class="eos-form-input" type="text" value="' + EOS_UI.esc(val) + '" placeholder="' + EOS_UI.esc(f.placeholder || '') + '">';
            }
            return '<div class="eos-form-group"><label class="eos-form-label">' + EOS_UI.esc(f.label) + '</label>' + input + '</div>';
        }).join('') +
        '<div class="eos-form-actions"><button class="eos-btn eos-btn-primary" onclick="EOS_UI._submitForm()">' + EOS_UI.esc(submitLabel || 'Save') + '</button></div>';
    },

    // Collect form values by field keys
    formValues: function(fields) {
        var vals = {};
        fields.forEach(function(f) {
            var el = document.getElementById('eos-form-' + f.key);
            if (el) vals[f.key] = f.type === 'number' ? parseFloat(el.value) || 0 : el.value;
        });
        return vals;
    },

    _formCallback: null,
    _formFields: null,

    // Show a form modal. onSubmit(values) called with form data.
    formModal: function(title, fields, onSubmit) {
        EOS_UI._formCallback = onSubmit;
        EOS_UI._formFields = fields;
        EOS_UI.modal({
            title: title,
            body: EOS_UI.formHtml(fields, 'Save'),
        });
    },

    _submitForm: function() {
        if (EOS_UI._formCallback && EOS_UI._formFields) {
            var vals = EOS_UI.formValues(EOS_UI._formFields);
            EOS_UI._formCallback(vals);
        }
        EOS_UI.closeModal();
    },

    // --- Attachment Picker ---
    // Modal with two tabs: Vault (search vault notes) / Upload (drag-drop OS files).
    // On pick, fires onPick({type, path, name}) and closes the modal.
    // opts: {
    //   onPick: function({type:'vault'|'upload', path, name}) — required
    //   uploadUrl: string — POST endpoint for multipart upload (default '/assistant/api/upload')
    //   searchUrl: string — GET endpoint returning {files:[{path,name,folder,tags}]} (default '/assistant/api/vault-files')
    //   title: string — modal title (default 'Attach')
    // }
    attachmentPicker: function(opts) {
        opts = opts || {};
        var onPick = opts.onPick || function() {};
        var uploadUrl = opts.uploadUrl || '/assistant/api/upload';
        var searchUrl = opts.searchUrl || '/assistant/api/vault-files';

        var body =
            '<div class="eos-tabs" id="eos-att-tabs">' +
                '<div class="eos-tab active" data-tab="vault" onclick="EOS_UI._attTab(\'vault\')">Vault</div>' +
                '<div class="eos-tab" data-tab="upload" onclick="EOS_UI._attTab(\'upload\')">Upload</div>' +
            '</div>' +
            '<div id="eos-att-vault">' +
                '<input id="eos-att-search" class="eos-form-input" type="text" placeholder="Search vault notes…" autofocus>' +
                '<div id="eos-att-list" class="eos-att-list" style="margin-top:10px;max-height:50vh;overflow-y:auto"></div>' +
            '</div>' +
            '<div id="eos-att-upload" style="display:none">' +
                '<div id="eos-att-drop" class="eos-att-drop">' +
                    '<div class="eos-att-drop-icon">⬆</div>' +
                    '<div>Drop files here, or <label class="eos-att-browse">browse<input id="eos-att-file" type="file" multiple style="display:none"></label></div>' +
                    '<div class="eos-att-hint">Files land in the vault inbox and stay searchable.</div>' +
                '</div>' +
                '<div id="eos-att-progress" style="margin-top:10px"></div>' +
            '</div>';

        EOS_UI.modal({title: opts.title || 'Attach', body: body, width: '560px'});
        EOS_UI._attOnPick = onPick;
        EOS_UI._attUploadUrl = uploadUrl;
        EOS_UI._attSearchUrl = searchUrl;

        if (opts.initialTab === 'upload') EOS_UI._attTab('upload');
        EOS_UI._attLoadVault('');
        if (opts.initialFiles && opts.initialFiles.length) {
            EOS_UI._attTab('upload');
            EOS_UI._attUpload(opts.initialFiles);
        }

        var search = document.getElementById('eos-att-search');
        var debounce = null;
        search.addEventListener('input', function() {
            clearTimeout(debounce);
            debounce = setTimeout(function() { EOS_UI._attLoadVault(search.value); }, 180);
        });
        search.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                var first = document.querySelector('#eos-att-list .eos-att-row');
                if (first) first.click();
            }
        });

        var drop = document.getElementById('eos-att-drop');
        var fileInput = document.getElementById('eos-att-file');
        fileInput.addEventListener('change', function() {
            EOS_UI._attUpload(Array.from(fileInput.files || []));
            fileInput.value = '';
        });
        ['dragenter','dragover'].forEach(function(ev) {
            drop.addEventListener(ev, function(e) { e.preventDefault(); drop.classList.add('drag'); });
        });
        ['dragleave','drop'].forEach(function(ev) {
            drop.addEventListener(ev, function(e) { e.preventDefault(); drop.classList.remove('drag'); });
        });
        drop.addEventListener('drop', function(e) {
            var files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
            if (files.length) EOS_UI._attUpload(files);
        });
    },

    _attTab: function(name) {
        var tabs = document.querySelectorAll('#eos-att-tabs .eos-tab');
        tabs.forEach(function(t) { t.classList.toggle('active', t.dataset.tab === name); });
        document.getElementById('eos-att-vault').style.display = (name === 'vault') ? '' : 'none';
        document.getElementById('eos-att-upload').style.display = (name === 'upload') ? '' : 'none';
        if (name === 'vault') {
            var s = document.getElementById('eos-att-search');
            if (s) s.focus();
        }
    },

    _attLoadVault: function(q) {
        var list = document.getElementById('eos-att-list');
        if (!list) return;
        list.innerHTML = '<div class="eos-att-empty">Searching…</div>';
        var url = EOS_UI._attSearchUrl + '?limit=50&q=' + encodeURIComponent(q || '');
        fetch(url).then(function(r) { return r.json(); }).then(function(d) {
            var files = (d && d.files) || [];
            if (!files.length) {
                list.innerHTML = '<div class="eos-att-empty">No notes match.</div>';
                return;
            }
            list.innerHTML = files.map(function(f) {
                var folder = f.folder ? '<span class="eos-att-folder">' + EOS_UI.esc(f.folder) + '/</span>' : '';
                var tags = (f.tags || []).slice(0, 3).map(function(t) {
                    return '<span class="obs-tag">#' + EOS_UI.esc(t) + '</span>';
                }).join(' ');
                return '<div class="eos-att-row" data-path="' + EOS_UI.esc(f.path) + '" data-name="' + EOS_UI.esc(f.name) + '">' +
                            '<div class="eos-att-row-main">' + folder + '<span class="eos-att-name">' + EOS_UI.esc(f.name) + '</span></div>' +
                            (tags ? '<div class="eos-att-row-tags">' + tags + '</div>' : '') +
                        '</div>';
            }).join('');
            Array.from(list.querySelectorAll('.eos-att-row')).forEach(function(row) {
                row.onclick = function() {
                    var cb = EOS_UI._attOnPick;
                    EOS_UI.closeModal();
                    if (cb) cb({type: 'vault', path: row.dataset.path, name: row.dataset.name});
                };
            });
        }).catch(function() {
            list.innerHTML = '<div class="eos-att-empty">Search failed.</div>';
        });
    },

    _attUpload: function(files) {
        if (!files || !files.length) return;
        var prog = document.getElementById('eos-att-progress');
        var url = EOS_UI._attUploadUrl;
        var cb = EOS_UI._attOnPick;
        var remaining = files.length;
        files.forEach(function(file) {
            var line = document.createElement('div');
            line.className = 'eos-att-uprow';
            line.textContent = '⏳ ' + file.name;
            if (prog) prog.appendChild(line);

            var fd = new FormData();
            fd.append('file', file);
            fetch(url, {method: 'POST', body: fd}).then(function(r) { return r.json(); }).then(function(d) {
                if (d && d.path) {
                    line.textContent = '✓ ' + (d.name || file.name);
                    if (cb) cb({type: 'upload', path: d.path, name: d.name || file.name});
                } else {
                    line.textContent = '✗ ' + file.name + ' — ' + ((d && d.error) || 'upload failed');
                }
            }).catch(function(e) {
                line.textContent = '✗ ' + file.name + ' — ' + (e && e.message ? e.message : 'network error');
            }).finally(function() {
                remaining -= 1;
                if (remaining <= 0 && cb) {
                    setTimeout(function() {
                        if (document.getElementById('eos-att-progress')) EOS_UI.closeModal();
                    }, 600);
                }
            });
        });
    },

    // --- Model tier picker (shared across chat apps) ---
    // Tiers are user-facing aliases that map to concrete provider names.
    // fast → openai-nano, standard (default) → openai-mini (gpt-5.4-mini), pro → openai (gpt-5.4).
    // "auto" leaves the global chain in charge (claude-cli → openai-mini → ollama).
    TIERS: {
        auto:     { provider: 'auto',         label: 'Auto',     hint: 'System chooses (Claude free tier → OpenAI mini → local)' },
        fast:     { provider: 'openai-nano',  label: 'Fast',     hint: 'OpenAI nano — cheapest, quickest' },
        standard: { provider: 'openai-mini',  label: 'Standard', hint: 'OpenAI mini — best $/quality (default)' },
        pro:      { provider: 'openai',       label: 'Pro',      hint: 'OpenAI full (gpt-5.4) — strongest reasoning' },
    },

    // Map a provider name back to a tier key (for highlighting current selection).
    tierFromProvider: function(providerName) {
        if (!providerName) return 'auto';
        for (var k in EOS_UI.TIERS) {
            if (EOS_UI.TIERS[k].provider === providerName) return k;
        }
        return null; // unknown provider (e.g. claude-cli, ollama) — don't highlight a tier
    },

    // Show a modal with tier buttons. onSelect receives {tier, provider}.
    // opts: { current: 'standard'|<provider-name>, onSelect: function, title?: string }
    tierPicker: function(opts) {
        opts = opts || {};
        var current = opts.current || 'standard';
        // Accept either a tier key or a provider name.
        if (!EOS_UI.TIERS[current]) {
            var fromProv = EOS_UI.tierFromProvider(current);
            current = fromProv || 'auto';
        }
        var order = ['auto', 'fast', 'standard', 'pro'];
        var html = '<div class="eos-tier-picker">';
        order.forEach(function(k) {
            var t = EOS_UI.TIERS[k];
            var active = (k === current) ? ' eos-tier-active' : '';
            html += '<button type="button" class="eos-tier-btn' + active + '" data-tier="' + k + '">'
                 +   '<span class="eos-tier-label">' + EOS_UI.esc(t.label) + '</span>'
                 +   '<span class="eos-tier-hint">' + EOS_UI.esc(t.hint) + '</span>'
                 + '</button>';
        });
        html += '</div>';
        EOS_UI.modal({ title: opts.title || 'Model tier', body: html });
        // Wire buttons after modal is in DOM.
        setTimeout(function() {
            var btns = document.querySelectorAll('.eos-tier-btn');
            btns.forEach(function(b) {
                b.onclick = function() {
                    var tier = b.getAttribute('data-tier');
                    var prov = (EOS_UI.TIERS[tier] || {}).provider;
                    EOS_UI.closeModal();
                    if (typeof opts.onSelect === 'function') opts.onSelect({tier: tier, provider: prov});
                };
            });
        }, 0);
    },

    // --- Chat transcript (shared by chat-shaped apps) ---
    //
    // Owns rendering of user/assistant/system messages and streaming turns
    // inside an `.eos-chat-transcript` container. Apps keep control of
    // sessions, WebSockets, and tool-call specifics; the transcript just
    // renders message bubbles consistently.
    //
    // Usage:
    //   var tr = EOS_UI.chatTranscript(document.getElementById('transcript'));
    //   tr.clear();
    //   tr.appendUser('hello');
    //   var turn = tr.startAssistant();
    //   tr.appendChunk(turn, 'streaming text…');
    //   tr.finalize(turn);                 // re-renders as markdown
    //   tr.showEmpty('<p>Start typing…</p>');
    chatTranscript: function(mountEl) {
        if (!mountEl) return null;
        function scroll() { mountEl.scrollTop = mountEl.scrollHeight; }
        return {
            el: mountEl,
            clear: function() { mountEl.innerHTML = ''; },
            showEmpty: function(html) {
                mountEl.innerHTML = '<div class="eos-chat-empty">' + (html || '') + '</div>';
            },
            appendUser: function(text) {
                var t = document.createElement('div');
                t.className = 'eos-chat-turn user';
                t.innerHTML = '<div class="eos-chat-role-label user">You</div>' +
                              '<div class="eos-chat-turn-content"></div>';
                t.querySelector('.eos-chat-turn-content').textContent = text || '';
                mountEl.appendChild(t); scroll(); return t;
            },
            appendAssistant: function(text, opts) {
                opts = opts || {};
                var t = document.createElement('div');
                t.className = 'eos-chat-turn assistant';
                var label = opts.label || 'Assistant';
                t.innerHTML = '<div class="eos-chat-role-label assistant">' + EOS_UI.esc(label) + '</div>' +
                              '<div class="eos-chat-turn-content markdown"></div>';
                var body = t.querySelector('.eos-chat-turn-content');
                body.innerHTML = EOS_UI.renderMarkdown ? EOS_UI.renderMarkdown(text || '') : EOS_UI.esc(text || '');
                mountEl.appendChild(t); scroll(); return t;
            },
            appendSystem: function(text) {
                var t = document.createElement('div');
                t.className = 'eos-chat-turn system';
                t.innerHTML = '<div class="eos-chat-role-label">System</div>' +
                              '<div class="eos-chat-turn-content dim"></div>';
                t.querySelector('.eos-chat-turn-content').textContent = text || '';
                mountEl.appendChild(t); scroll(); return t;
            },
            startAssistant: function(opts) {
                opts = opts || {};
                var t = document.createElement('div');
                t.className = 'eos-chat-turn assistant';
                var label = opts.label || 'Assistant';
                t.innerHTML = '<div class="eos-chat-role-label assistant">' + EOS_UI.esc(label) + '</div>' +
                              '<div class="eos-chat-turn-content streaming"></div>';
                mountEl.appendChild(t); scroll(); return t;
            },
            appendChunk: function(turn, text) {
                if (!turn) return;
                var body = turn.querySelector('.eos-chat-turn-content');
                if (body) { body.textContent = (body.textContent || '') + (text || ''); scroll(); }
            },
            setStreamText: function(turn, fullText) {
                // For protocols that send cumulative text, not deltas.
                if (!turn) return;
                var body = turn.querySelector('.eos-chat-turn-content');
                if (body) { body.textContent = fullText || ''; scroll(); }
            },
            finalize: function(turn, opts) {
                opts = opts || {};
                if (!turn) return;
                var body = turn.querySelector('.eos-chat-turn-content');
                if (!body) return;
                var text = opts.text != null ? opts.text : body.textContent;
                body.classList.remove('streaming');
                body.classList.add('markdown');
                body.innerHTML = EOS_UI.renderMarkdown ? EOS_UI.renderMarkdown(text) : EOS_UI.esc(text);
                scroll();
            },
            scrollToBottom: scroll,
        };
    },

    // --- Loading State ---
    loading: function(targetId, show) {
        var el = document.getElementById(targetId);
        if (!el) return;
        if (show !== false) {
            el.innerHTML = '<div class="eos-loading"><div class="eos-spinner"></div></div>';
        }
    },

    // --- Confirm Dialog ---
    // Callback form: EOS_UI.confirm('Delete this?', function() { /* yes */ })
    // Async form:    if (!await EOS_UI.confirm('Delete this?')) return;
    // Options:       EOS_UI.confirm({message: '...', action: 'Remove', danger: true})
    confirm: function(messageOrOpts, onYes) {
        var message, action, danger;
        if (typeof messageOrOpts === 'object') {
            message = messageOrOpts.message || 'Are you sure?';
            action = messageOrOpts.action || 'Delete';
            danger = messageOrOpts.danger !== false;
            onYes = onYes || messageOrOpts.onYes;
        } else {
            message = messageOrOpts || 'Are you sure?';
            action = 'Delete';
            danger = true;
        }
        var btnClass = danger ? 'eos-btn eos-btn-danger' : 'eos-btn eos-btn-primary';
        var resolve;
        var promise = new Promise(function(res) { resolve = res; });
        EOS_UI.modal({
            title: 'Confirm',
            body: '<p style="margin:0 0 16px;font-size:15px;color:var(--text-secondary)">' + EOS_UI.esc(message) + '</p>' +
                '<div class="eos-form-actions">' +
                    '<button class="eos-btn" id="eos-confirm-no">Cancel</button>' +
                    '<button class="' + btnClass + '" id="eos-confirm-yes">' + EOS_UI.esc(action) + '</button>' +
                '</div>',
        });
        // Resolve BEFORE closeModal so the overlay-close _modalOnClose (which
        // also calls resolve) doesn't win the race — Promises are first-write.
        document.getElementById('eos-confirm-no').onclick = function() { resolve(false); EOS_UI.closeModal(); };
        document.getElementById('eos-confirm-yes').onclick = function() { resolve(true); if(onYes) onYes(); EOS_UI.closeModal(); };
        // Overlay click or Escape → cancel
        var origClose = EOS_UI._modalOnClose;
        EOS_UI._modalOnClose = function() { resolve(false); if(origClose) origClose(); };
        return promise;
    },

    // --- Dictionary Popup (double-click word lookup) ---

    _dictInit: false,

    initDict: function() {
        if (EOS_UI._dictInit) return;
        if (document.body.hasAttribute('data-no-dict')) return;
        EOS_UI._dictInit = true;

        // Create popup element
        var popup = document.createElement('div');
        popup.id = 'eos-dict-popup';
        document.body.appendChild(popup);

        // Global dblclick handler
        document.addEventListener('dblclick', function(e) {
            if (e.target.closest('#eos-dict-popup')) return;
            if (e.target.closest('input, textarea, [contenteditable]')) return;
            var sel = window.getSelection().toString().trim();
            if (!sel || sel.length > 40 || sel.indexOf(' ') > 15) return;
            EOS_UI.showDict(sel, e.clientX, e.clientY);
        });

        // Click outside to close
        document.addEventListener('click', function(e) {
            if (!e.target.closest('#eos-dict-popup')) {
                document.getElementById('eos-dict-popup').classList.remove('active');
            }
        });
    },

    showDict: function(word, x, y) {
        var popup = document.getElementById('eos-dict-popup');
        popup.innerHTML = '<div class="eos-dict-loading">Looking up...</div>';
        popup.classList.add('active');
        popup.style.left = Math.min(x, window.innerWidth - 320) + 'px';
        popup.style.top = Math.min(y + 10, window.innerHeight - 360) + 'px';

        // Try free dictionary API first, then EmptyOS dictionary app
        fetch('https://api.dictionaryapi.dev/api/v2/entries/en/' + encodeURIComponent(word))
            .then(function(r) { return r.ok ? r.json() : Promise.reject('not found'); })
            .then(function(json) {
                if (!json[0]) throw 'empty';
                var entry = json[0];
                // Find audio URL from phonetics
                var audioUrl = '';
                if (entry.phonetics) {
                    for (var pi = 0; pi < entry.phonetics.length; pi++) {
                        if (entry.phonetics[pi].audio) { audioUrl = entry.phonetics[pi].audio; break; }
                    }
                }
                EOS_UI._renderDict(popup, {
                    word: entry.word,
                    ipa: entry.phonetic || (entry.phonetics && entry.phonetics[0] && entry.phonetics[0].text) || '',
                    audio: audioUrl,
                    meanings: entry.meanings.slice(0, 3).map(function(m) {
                        return {
                            pos: m.partOfSpeech,
                            defs: m.definitions.slice(0, 2).map(function(d) {
                                return { def: d.definition, ex: d.example || '' };
                            })
                        };
                    })
                });
            })
            .catch(function() {
                // Fallback to EmptyOS dictionary app
                fetch(EOS.base + '/dictionary/api/lookup?word=' + encodeURIComponent(word))
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data.error) {
                            popup.innerHTML = '<div class="eos-dict-word">' + EOS_UI.esc(word) + '</div>' +
                                '<div style="color:var(--text-muted);margin-top:8px">No definition found</div>';
                            return;
                        }
                        EOS_UI._renderDict(popup, {
                            word: data.word || word,
                            ipa: data.phonetic || '',
                            meanings: [{pos: data.part_of_speech || '', defs: [{def: data.definition || '', ex: ''}]}],
                            chinese: data.chinese || ''
                        });
                    })
                    .catch(function() {
                        popup.innerHTML = '<div class="eos-dict-word">' + EOS_UI.esc(word) + '</div>' +
                            '<div style="color:var(--text-muted);margin-top:8px">Lookup failed</div>';
                    });
            });
    },

    _renderDict: function(popup, data) {
        var html = '<div class="eos-dict-word">' + EOS_UI.esc(data.word);
        if (data.audio) html += ' <span class="eos-dict-play" onclick="EOS_UI._dictPlay(\'' + data.audio.replace(/'/g, "\\'") + '\')">&#128264;</span>';
        html += '</div>';
        if (data.ipa) html += '<span class="eos-dict-ipa">' + EOS_UI.esc(data.ipa) + '</span>';
        if (data.meanings) {
            data.meanings.forEach(function(m) {
                if (m.pos) html += '<div class="eos-dict-pos">' + EOS_UI.esc(m.pos) + '</div>';
                m.defs.forEach(function(d) {
                    html += '<div class="eos-dict-def">' + EOS_UI.esc(d.def) + '</div>';
                    if (d.ex) html += '<div class="eos-dict-ex">"' + EOS_UI.esc(d.ex) + '"</div>';
                });
            });
        }
        if (data.chinese) {
            html += '<div class="eos-dict-zh">' + EOS_UI.esc(data.chinese) + '</div>';
        }
        html += '<button class="eos-dict-save" onclick="EOS_UI._dictSave(\'' + EOS_UI.esc(data.word).replace(/'/g, "\\'") + '\')">Save to Dictionary</button>';
        popup.innerHTML = html;
    },

    _dictPlay: function(url) {
        try { new Audio(url).play(); } catch(e) {}
    },

    _dictSave: function(word) {
        fetch(EOS.base + '/dictionary/api/lookup?word=' + encodeURIComponent(word), {method: 'GET'})
            .then(function() {
                EOS_UI.toast('Saved: ' + word);
                document.getElementById('eos-dict-popup').classList.remove('active');
            })
            .catch(function() { EOS_UI.toast('Save failed', false); });
    },

    // ── Compare Table (reusable across apps) ──────────────
    // Usage: EOS_UI.compareTable({
    //   container: '#my-div' or element,
    //   columns: [{key:'balance', label:'Final Balance', format:'$'}, ...],
    //   rows: [{name:'Floor', values:{balance:1366994, income:54680}}, ...],
    //   highlight: 'max'  // highlight best per column: 'max', 'min', or null
    // })
    compareTable: function(opts) {
        var el = typeof opts.container === 'string' ? document.querySelector(opts.container) : opts.container;
        if (!el) return;
        var cols = opts.columns || [];
        var rows = opts.rows || [];
        var hi = opts.highlight || null;

        // Find best values per column
        var best = {}, worst = {};
        if (hi) {
            cols.forEach(function(c) {
                var vals = rows.map(function(r) { return r.values[c.key]; }).filter(function(v) { return typeof v === 'number'; });
                if (vals.length) {
                    best[c.key] = hi === 'max' ? Math.max.apply(null, vals) : Math.min.apply(null, vals);
                    worst[c.key] = hi === 'max' ? Math.min.apply(null, vals) : Math.max.apply(null, vals);
                }
            });
        }

        var html = '<table class="eos-compare-table"><thead><tr><th>Scenario</th>';
        cols.forEach(function(c) { html += '<th>' + EOS_UI.esc(c.label) + '</th>'; });
        html += '</tr></thead><tbody>';
        rows.forEach(function(r) {
            html += '<tr><td>' + EOS_UI.esc(r.name) + '</td>';
            cols.forEach(function(c) {
                var v = r.values[c.key];
                var cls = '';
                if (hi && typeof v === 'number') {
                    if (v === best[c.key]) cls = ' class="eos-cmp-best"';
                    else if (v === worst[c.key]) cls = ' class="eos-cmp-worst"';
                }
                var display = v;
                if (typeof v === 'number') {
                    if (c.format === '$') display = '$' + Math.round(v).toLocaleString();
                    else if (c.format === '%') display = v.toFixed(1) + '%';
                    else display = v.toLocaleString();
                } else if (typeof v === 'boolean') {
                    display = v ? 'Yes' : 'No';
                }
                html += '<td' + cls + '>' + display + '</td>';
            });
            html += '</tr>';
        });
        html += '</tbody></table>';
        el.innerHTML = html;
    },

    // Compare bar chart (requires Plotly)
    // Usage: EOS_UI.compareChart({
    //   container: '#chart-div',
    //   labels: ['Floor','Target','Strong'],
    //   values: [1366994, 1826840, 2402035],
    //   colors: ['#f59e0b','#3b82f6','#10b981'],  // optional
    //   title: 'Final Balance',
    //   yformat: '$,.0f',
    // })
    compareChart: function(opts) {
        var el = typeof opts.container === 'string' ? document.querySelector(opts.container) : opts.container;
        if (!el || typeof Plotly === 'undefined') return;
        el.style.display = 'block';
        var colors = opts.colors || ['#f59e0b','#3b82f6','#10b981','#8b5cf6','#ef4444','#06b6d4','#ec4899','#84cc16'];
        var hasLongLabels = opts.labels.some(function(l) { return l.length > 12; });
        Plotly.newPlot(el, [{
            x: opts.labels, y: opts.values, type: 'bar',
            marker: { color: colors.slice(0, opts.labels.length) },
            text: opts.values.map(function(v) { return opts.yformat === '$,.0f' ? '$' + Math.round(v).toLocaleString() : v; }),
            textposition: 'outside',
        }], {
            title: opts.title || '',
            xaxis: { tickangle: hasLongLabels ? -30 : 0 },
            yaxis: { title: opts.ytitle || '', tickformat: opts.yformat || '' },
            margin: {t:40, b: hasLongLabels ? 100 : 50, l:80, r:20},
            height: opts.height || 380,
            paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color: getComputedStyle(document.body).getPropertyValue('--text'), size: 12 },
        }, {responsive: true});
    },

    // --- Settings slide-out (shared) ---
    // Usage:
    //   EOS_UI.settingsPanel({
    //     id: 'app-settings',                // panel DOM id
    //     title: 'App Settings',
    //     fields: [
    //       {key: 'projects.stale_days', label: 'Stale After (days)', type: 'number', default: 90, hint: 'Flagged stale after N days.'},
    //       {key: 'projects.show_foo', label: 'Show foo', type: 'boolean', default: false},
    //     ],
    //     onSave: function(values) { /* optional — after save */ },
    //   });
    // Fields read from/written to /settings/api/config + /settings/api/set-bulk.
    // Types: 'number' | 'text' | 'boolean' | 'select' (with options:[]) | 'textarea'
    settingsPanel: function(opts) {
        var id = opts.id || 'eos-app-settings';
        var existing = document.getElementById(id);
        if (existing) existing.remove();

        var panel = document.createElement('div');
        panel.id = id;
        panel.className = 'eos-settings-panel';

        var body = opts.fields.map(function(f, i) {
            var inputId = id + '-f-' + i;
            var input;
            if (f.type === 'boolean') {
                input = '<label class="sf-checkbox"><input type="checkbox" id="' + inputId + '"> <span>' + EOS_UI.esc(f.label) + '</span></label>';
                return '<div class="sf-group">' + input + (f.hint ? '<div class="sf-hint">' + EOS_UI.esc(f.hint) + '</div>' : '') + '</div>';
            }
            if (f.type === 'select') {
                var opts2 = (f.options || []).map(function(o) {
                    var val = (typeof o === 'string') ? o : o.value;
                    var lab = (typeof o === 'string') ? o : (o.label || o.value);
                    return '<option value="' + EOS_UI.esc(val) + '">' + EOS_UI.esc(lab) + '</option>';
                }).join('');
                input = '<select class="sf-select" id="' + inputId + '">' + opts2 + '</select>';
            } else if (f.type === 'textarea') {
                input = '<textarea class="sf-textarea" id="' + inputId + '" rows="3"' + (f.placeholder ? ' placeholder="' + EOS_UI.esc(f.placeholder) + '"' : '') + '></textarea>';
            } else {
                var t = (f.type === 'number') ? 'number' : (f.type === 'password' ? 'password' : 'text');
                input = '<input type="' + t + '" class="sf-input" id="' + inputId + '"' + (f.placeholder ? ' placeholder="' + EOS_UI.esc(f.placeholder) + '"' : '') + (f.min != null ? ' min="' + f.min + '"' : '') + (f.max != null ? ' max="' + f.max + '"' : '') + '>';
            }
            return '<div class="sf-group">' +
                '<label class="sf-label" for="' + inputId + '">' + EOS_UI.esc(f.label) + '</label>' +
                input +
                (f.hint ? '<div class="sf-hint">' + EOS_UI.esc(f.hint) + '</div>' : '') +
            '</div>';
        }).join('');

        panel.innerHTML =
            '<div class="sp-head">' +
                '<h3>' + EOS_UI.esc(opts.title || 'Settings') + '</h3>' +
                '<button class="eos-btn-sm eos-btn-ghost" data-act="close">Close</button>' +
            '</div>' +
            '<div class="sp-body">' + body + '</div>' +
            '<div class="sp-foot">' +
                '<button class="eos-btn-sm eos-btn-ghost" data-act="close">Cancel</button>' +
                '<button class="eos-btn-sm eos-btn-primary" data-act="save">Save</button>' +
            '</div>';

        document.body.appendChild(panel);

        var close = function() { panel.classList.remove('open'); };
        panel.querySelectorAll('[data-act="close"]').forEach(function(b) { b.onclick = close; });

        var fieldEls = opts.fields.map(function(_, i) { return document.getElementById(id + '-f-' + i); });

        var load = async function() {
            try {
                var r = await EOS.api('/settings/api/config');
                var s = (r && r.settings) || {};
                opts.fields.forEach(function(f, i) {
                    var el = fieldEls[i];
                    var v = (s[f.key] != null) ? s[f.key] : f.default;
                    if (f.type === 'boolean') el.checked = !!v;
                    else el.value = (v == null ? '' : v);
                });
            } catch(e) {
                opts.fields.forEach(function(f, i) {
                    var el = fieldEls[i];
                    if (f.type === 'boolean') el.checked = !!f.default;
                    else el.value = (f.default == null ? '' : f.default);
                });
            }
        };

        panel.querySelector('[data-act="save"]').onclick = async function() {
            var payload = {};
            for (var i = 0; i < opts.fields.length; i++) {
                var f = opts.fields[i], el = fieldEls[i];
                if (f.type === 'boolean') {
                    payload[f.key] = el.checked;
                } else if (f.type === 'number') {
                    var n = parseFloat(el.value);
                    if (el.value !== '' && isNaN(n)) { EOS_UI.toast(f.label + ' must be a number', false); return; }
                    payload[f.key] = (el.value === '' ? null : n);
                } else {
                    payload[f.key] = el.value;
                }
            }
            try {
                var r = await EOS.post('/settings/api/set-bulk', payload);
                if (r && r.error) { EOS_UI.toast(r.error, false); return; }
                EOS_UI.toast('Settings saved');
                close();
                if (opts.onSave) opts.onSave(payload);
            } catch(e) {
                EOS_UI.toast('Failed to save settings', false);
            }
        };

        return {
            open: async function() { await load(); panel.classList.add('open'); },
            close: close,
            panel: panel,
        };
    },

    // --- Hash-based deep-link routing (shared) ---
    // Usage:
    //   var route = EOS_UI.hashRoute({
    //     onShow: function(id) { showDetail(id); },    // called when hash set
    //     onHide: function() { hideDetailDom(); },     // called when hash empty
    //   });
    //   // In your showDetail(id): route.set(id);
    //   // In your hideDetail():   route.clear();
    //   // On page init:           route.init();       // reads current hash
    hashRoute: function(opts) {
        var onShow = opts.onShow || function(){};
        var onHide = opts.onHide || function(){};

        var read = function() {
            return location.hash ? decodeURIComponent(location.hash.slice(1)) : '';
        };
        var apply = function() {
            var id = read();
            if (id) onShow(id); else onHide();
        };

        if (EOS_UI._hashRouteListener) {
            window.removeEventListener('popstate', EOS_UI._hashRouteListener);
        }
        EOS_UI._hashRouteListener = apply;
        window.addEventListener('popstate', apply);

        return {
            set: function(id) {
                if (!id || read() === id) return;
                history.pushState({eosId: id}, '', '#' + encodeURIComponent(id));
            },
            clear: function() {
                if (location.hash) history.pushState({}, '', location.pathname + location.search);
            },
            init: function() { apply(); },
            current: read,
        };
    },

    // ── Test Panel ──────────────────────────────────────────
    //   EOS_UI.testPanel({id, app_id, title?, onComplete?})
    //   Returns {open(), close(), panel}
    //   Slide-out panel showing per-test pass/fail results for an app.
    testPanel: function(opts) {
        var id = opts.id || 'eos-test-panel';
        var appId = opts.app_id || '';
        var title = opts.title || 'Tests';
        var onComplete = opts.onComplete || null;

        // Remove existing panel with same id
        var old = document.getElementById(id);
        if (old) old.remove();

        var panel = document.createElement('div');
        panel.id = id;
        panel.className = 'eos-settings-panel eos-test-panel';
        panel.innerHTML =
            '<div class="sp-head"><span>' + EOS_UI.esc(title) + '</span><span class="sp-close" style="cursor:pointer">&times;</span></div>' +
            '<div class="sp-body">' +
              '<div class="tp-summary"></div>' +
              '<div class="tp-actions" style="display:flex;gap:8px;align-items:center;margin:10px 0">' +
                '<button class="tp-run-btn" style="padding:6px 16px;border-radius:4px;background:var(--accent);color:#000;border:none;cursor:pointer;font-weight:600">Run Tests</button>' +
                '<input class="tp-filter" placeholder="-k filter (optional)" style="flex:1;padding:5px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg-2);color:var(--text)">' +
                '<span class="tp-status" style="font-size:12px;color:var(--text-dim)"></span>' +
              '</div>' +
              '<div class="tp-test-list"></div>' +
              '<details class="tp-output-wrap" style="margin-top:10px"><summary style="cursor:pointer;font-size:12px;color:var(--text-dim)">Raw output</summary><pre class="tp-output" style="max-height:300px;overflow:auto;font-size:11px;background:#111;padding:8px;border-radius:4px;white-space:pre-wrap"></pre></details>' +
            '</div>' +
            '<div class="sp-foot"><button class="tp-close-btn" style="padding:6px 16px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer">Close</button></div>';
        document.body.appendChild(panel);

        var summaryEl = panel.querySelector('.tp-summary');
        var listEl = panel.querySelector('.tp-test-list');
        var outputEl = panel.querySelector('.tp-output');
        var statusEl = panel.querySelector('.tp-status');
        var runBtn = panel.querySelector('.tp-run-btn');
        var filterInput = panel.querySelector('.tp-filter');

        panel.querySelector('.sp-close').onclick = function() { close(); };
        panel.querySelector('.tp-close-btn').onclick = function() { close(); };
        runBtn.onclick = function() { runTests(); };

        function close() { panel.classList.remove('open'); }

        function renderSummary(summary) {
            if (!summary) { summaryEl.innerHTML = '<div style="color:var(--text-dim);padding:8px 0">Never run</div>'; return; }
            var p = summary.passed || 0, f = summary.failed || 0, e = summary.errors || 0, s = summary.skipped || 0;
            var total = p + f + e + s;
            var allPass = f === 0 && e === 0;
            summaryEl.innerHTML =
                '<div style="display:flex;gap:12px;align-items:center;padding:8px 0">' +
                  '<span style="font-size:24px;font-weight:700;color:' + (allPass ? 'var(--accent)' : '#f44') + '">' + (allPass ? p + ' PASS' : f + ' FAIL') + '</span>' +
                  '<span style="color:var(--text-dim);font-size:13px">' + total + ' tests' + (s ? ', ' + s + ' skipped' : '') + '</span>' +
                  (summary.wall_time ? '<span style="color:var(--text-dim);font-size:12px">' + summary.wall_time + 's</span>' : '') +
                  (summary.timestamp ? '<span style="color:var(--text-dim);font-size:11px">' + summary.timestamp + '</span>' : '') +
                '</div>';
        }

        function renderTests(tests) {
            if (!tests || !tests.length) { listEl.innerHTML = ''; return; }
            var html = '';
            var currentClass = '';
            tests.forEach(function(t) {
                // Group by class: TestFoo::test_bar → header "TestFoo"
                var parts = t.name.split('::');
                var cls = parts.length > 1 ? parts[0] : '';
                var testName = parts.length > 1 ? parts.slice(1).join('::') : t.name;
                if (cls && cls !== currentClass) {
                    currentClass = cls;
                    html += '<div style="font-size:11px;font-weight:600;color:var(--text-dim);margin:8px 0 2px;border-bottom:1px solid var(--border);padding-bottom:2px">' + EOS_UI.esc(cls) + '</div>';
                }
                var color = t.status === 'PASSED' ? '#0f8' : t.status === 'FAILED' ? '#f44' : '#888';
                var dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + color + ';margin-right:6px"></span>';
                html += '<div style="font-size:12px;padding:2px 0;display:flex;align-items:center">' + dot + '<span>' + EOS_UI.esc(testName) + '</span></div>';
            });
            listEl.innerHTML = html;
        }

        function runTests() {
            runBtn.disabled = true;
            statusEl.textContent = 'Running...';
            listEl.innerHTML = '';
            outputEl.textContent = '';
            var body = {app_id: appId, timeout: 300};
            var f = filterInput.value.trim();
            if (f) body.filter = f;
            EOS.post('/tests/api/run-app', body).then(function(res) {
                runBtn.disabled = false;
                if (res.error) { statusEl.textContent = 'Error: ' + res.error; return; }
                statusEl.textContent = '';
                renderSummary(res.summary);
                renderTests(res.tests || []);
                outputEl.textContent = res.output || '';
                if (onComplete) onComplete(res);
            }).catch(function(e) {
                runBtn.disabled = false;
                statusEl.textContent = 'Failed: ' + e.message;
            });
        }

        async function open() {
            panel.classList.add('open');
            // Load last-run from history
            try {
                var history = await EOS.api('/tests/api/history');
                // Find this app's test file in history
                var keys = Object.keys(history);
                var appKey = keys.find(function(k) { return k.indexOf('test_' + appId.replace(/-/g, '_')) >= 0; });
                if (appKey) {
                    renderSummary(history[appKey]);
                } else {
                    renderSummary(null);
                }
            } catch(e) {
                renderSummary(null);
            }
        }

        return { open: open, close: close, panel: panel };
    },

    // === Interactive Components ===

    /** Render a filter bar with pill buttons. Returns {setActive, getActive}. */
    // categories: [{key, label, count?}]. Pass includeAll:false in opts to
    // suppress the leading "All" pill; pass initial to override default active.
    filterBar: function(targetId, categories, onChange, opts) {
        var el = document.getElementById(targetId);
        if (!el) return {};
        opts = opts || {};
        var includeAll = opts.includeAll !== false;
        var active = opts.initial || (includeAll ? 'all' : (categories[0] && categories[0].key));
        function render() {
            var pills = (includeAll ? [{key:'all', label: opts.allLabel || 'All'}] : []).concat(categories);
            el.innerHTML = pills.map(function(c) {
                var count = (c.count != null)
                    ? '<span class="eos-filter-pill-count">' + EOS_UI.esc(String(c.count)) + '</span>'
                    : '';
                return '<button class="eos-filter-pill' + (c.key === active ? ' active' : '') +
                    '" data-key="' + EOS_UI.esc(c.key) + '">' + EOS_UI.esc(c.label) + count + '</button>';
            }).join('');
            el.querySelectorAll('.eos-filter-pill').forEach(function(btn) {
                btn.onclick = function() {
                    active = btn.dataset.key;
                    render();
                    if (onChange) onChange(active);
                };
            });
        }
        render();
        return {
            setActive: function(key) { active = key; render(); },
            getActive: function() { return active; },
            update: function(newCategories) { categories = newCategories; render(); },
        };
    },

    /** Render a filterable card grid. Returns {update, filter}. */
    cardGrid: function(targetId, items, opts) {
        var el = document.getElementById(targetId);
        if (!el) return {};
        opts = opts || {};
        var renderCard = opts.render; // function(item, index) -> HTML string
        var onClick = opts.onClick;   // function(item)
        var currentFilter = null;
        var currentTagFilter = null;

        function render() {
            el.classList.add('filtering');
            setTimeout(function() {
                var filtered = items;
                if (currentFilter && currentFilter !== 'all') {
                    filtered = filtered.filter(function(item) {
                        return item.category === currentFilter || item.categoryGroup === currentFilter;
                    });
                }
                if (currentTagFilter) {
                    filtered = filtered.filter(function(item) {
                        return item.tags && item.tags.indexOf(currentTagFilter) !== -1;
                    });
                }
                el.innerHTML = filtered.map(function(item, i) {
                    return renderCard ? renderCard(item, i) : '<div class="eos-icard">' + EOS_UI.esc(item.title || '') + '</div>';
                }).join('');
                el.classList.remove('filtering');
                if (onClick) {
                    el.querySelectorAll('.eos-icard').forEach(function(card, i) {
                        card.onclick = function() { onClick(filtered[i]); };
                    });
                }
            }, 150);
        }
        render();
        return {
            update: function(newItems) { items = newItems; render(); },
            filter: function(category) { currentFilter = category; render(); },
            filterTag: function(tag) { currentTagFilter = (currentTagFilter === tag) ? null : tag; render(); },
            getTagFilter: function() { return currentTagFilter; }
        };
    },

    /** Create a slide-up detail panel. Returns {open, close, setContent}. */
    slidePanel: function(opts) {
        opts = opts || {};
        var panel = document.createElement('div');
        panel.className = 'eos-slide-panel';
        panel.innerHTML = '<div class="eos-slide-panel-backdrop"></div>' +
            '<button class="eos-slide-panel-close">ESC</button>' +
            '<div class="eos-slide-panel-sheet"><div class="eos-slide-panel-body"></div></div>';
        document.body.appendChild(panel);
        var body = panel.querySelector('.eos-slide-panel-body');
        var closeBtn = panel.querySelector('.eos-slide-panel-close');
        var backdrop = panel.querySelector('.eos-slide-panel-backdrop');

        function close() {
            panel.classList.remove('open');
            document.body.style.overflow = '';
            if (opts.onClose) opts.onClose();
        }
        closeBtn.onclick = close;
        backdrop.onclick = close;
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && panel.classList.contains('open')) close();
        });

        return {
            open: function(html) {
                body.innerHTML = html || '';
                panel.classList.add('open');
                document.body.style.overflow = 'hidden';
                panel.querySelector('.eos-slide-panel-sheet').scrollTop = 0;
                if (opts.onOpen) opts.onOpen();
            },
            close: close,
            setContent: function(html) { body.innerHTML = html; }
        };
    },

    /** Render a tag cloud with frequency-based sizing. Returns {setActive}. */
    tagCloud: function(targetId, tags, onClick) {
        // tags: [{name, count}] or {name: count}
        var el = document.getElementById(targetId);
        if (!el) return {};
        var tagList = Array.isArray(tags) ? tags :
            Object.keys(tags).map(function(k) { return {name: k, count: tags[k]}; });
        tagList.sort(function(a, b) { return a.name.localeCompare(b.name); });
        var maxCount = Math.max.apply(null, tagList.map(function(t) { return t.count; })) || 1;
        var active = null;

        function render() {
            el.innerHTML = tagList.map(function(t) {
                var size = 11 + (t.count / maxCount) * 7;
                return '<span class="eos-tag-cloud-item' + (active === t.name ? ' active' : '') +
                    '" data-tag="' + EOS_UI.esc(t.name) + '" style="font-size:' + size + 'px">' +
                    EOS_UI.esc(t.name) + '</span>';
            }).join('');
            el.querySelectorAll('.eos-tag-cloud-item').forEach(function(item) {
                item.onclick = function() {
                    active = (active === item.dataset.tag) ? null : item.dataset.tag;
                    render();
                    if (onClick) onClick(active);
                };
            });
        }
        render();
        return { setActive: function(tag) { active = tag; render(); } };
    },

    /** Set up scroll-tracking nav that highlights active section. */
    scrollNav: function(navId, sectionIds) {
        var nav = document.getElementById(navId);
        if (!nav) return;
        var links = nav.querySelectorAll('a[href^="#"]');
        var sections = sectionIds.map(function(id) { return document.getElementById(id); }).filter(Boolean);
        function update() {
            var scrollY = window.scrollY + 100;
            var current = '';
            sections.forEach(function(sec) {
                if (sec.offsetTop <= scrollY) current = sec.id;
            });
            links.forEach(function(a) {
                a.classList.toggle('active', a.getAttribute('href') === '#' + current);
            });
        }
        var ticking = false;
        window.addEventListener('scroll', function() {
            if (!ticking) { requestAnimationFrame(function() { update(); ticking = false; }); ticking = true; }
        });
        // Smooth scroll
        links.forEach(function(a) {
            a.addEventListener('click', function(e) {
                e.preventDefault();
                var target = document.querySelector(a.getAttribute('href'));
                if (target) target.scrollIntoView({ behavior: 'smooth' });
            });
        });
        update();
    },

    /** Set up IntersectionObserver reveal animations for .eos-reveal elements. */
    reveal: function(selector) {
        var els = document.querySelectorAll(selector || '.eos-reveal');
        if (!els.length) return;
        var observer = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('revealed');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });
        els.forEach(function(el) { observer.observe(el); });
    },

    // --- Cloud consent UX --------------------------------------------------
    // Show the consent modal for a pending cloud-provider request.
    // opts: {id, provider, capability, data_summary, findings}
    // Calls POST /api/cloud/consent with {id, approved, remember}.
    cloudConsent: function(opts) {
        var title = 'Cloud provider: ' + EOS_UI.esc(opts.provider || '?');
        var cap = EOS_UI.esc(opts.capability || '');
        var summary = opts.data_summary ? EOS_UI.esc(opts.data_summary) : '';
        var findings = Array.isArray(opts.findings) ? opts.findings : [];
        var findingsHtml = '';
        if (findings.length) {
            var items = findings.map(function(f) {
                return '<li><b>' + EOS_UI.esc(f.pattern || '?') + '</b>' +
                       (f.preview ? ' — <code>' + EOS_UI.esc(f.preview) + '</code>' : '') +
                       '</li>';
            }).join('');
            findingsHtml =
                '<div style="margin:0 0 12px;padding:10px 12px;border-radius:6px;' +
                'background:rgba(210,153,34,0.10);border:1px solid rgba(210,153,34,0.45);font-size:12px">' +
                    '<div style="font-weight:600;color:#d29922;margin-bottom:4px">' +
                        '&#9888; Local scan detected ' + findings.length + ' potential ' +
                        (findings.length === 1 ? 'match' : 'matches') +
                    '</div>' +
                    '<ul style="margin:4px 0 0;padding-left:18px">' + items + '</ul>' +
                '</div>';
        }
        var body =
            '<p style="margin:0 0 12px;font-size:13px;color:var(--text-secondary)">' +
                'This call will leave your machine and be sent to <b>' + EOS_UI.esc(opts.provider || '') + '</b>' +
                (cap ? ' (capability: <code>' + cap + '</code>)' : '') + '.' +
            '</p>' +
            findingsHtml +
            (summary ?
                '<details' + (findings.length ? ' open' : '') + ' style="margin:0 0 16px">' +
                '<summary style="cursor:pointer;font-size:12px;color:var(--text-secondary)">Preview data</summary>' +
                '<pre style="margin:8px 0 0;padding:8px;background:var(--bg-secondary,#0d1117);border:1px solid var(--border,#30363d);border-radius:6px;font-size:12px;white-space:pre-wrap;max-height:320px;overflow:auto">' +
                summary + '</pre></details>'
                : '') +
            '<label style="display:flex;align-items:center;gap:6px;margin:0 0 16px;font-size:13px;cursor:pointer">' +
                '<input type="checkbox" id="eos-consent-remember" checked> ' +
                'Remember for this session' +
            '</label>' +
            '<div class="eos-form-actions">' +
                '<button class="eos-btn" id="eos-consent-deny">Deny</button>' +
                '<button class="eos-btn eos-btn-primary" id="eos-consent-approve">Approve</button>' +
            '</div>';
        EOS_UI.modal({title: title, body: body, width: '520px'});
        var send = function(approved) {
            var remember = document.getElementById('eos-consent-remember');
            var payload = {
                id: opts.id,
                approved: approved,
                remember: remember ? !!remember.checked : true,
            };
            EOS_UI.closeModal();
            fetch('/api/cloud/consent', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            }).catch(function() {});
        };
        var approveBtn = document.getElementById('eos-consent-approve');
        var denyBtn = document.getElementById('eos-consent-deny');
        if (approveBtn) approveBtn.onclick = function() { send(true); };
        if (denyBtn) denyBtn.onclick = function() { send(false); };
    },

    // Small badge indicating which provider handled a request.
    // opts: {provider, is_cloud}
    // Returns HTML string (use as innerHTML or template fragment).
    providerBadge: function(opts) {
        if (!opts || !opts.provider) return '';
        var cls = opts.is_cloud ? 'eos-badge eos-provider-cloud' : 'eos-badge eos-provider-local';
        var icon = opts.is_cloud ? '&#9729;' : '&#8962;';  // cloud / home
        return '<span class="' + cls + '" title="' +
            (opts.is_cloud ? 'Cloud provider — data left your machine' : 'Local provider — data stayed on your machine') +
            '">' + icon + ' ' + EOS_UI.esc(opts.provider) + '</span>';
    },

    // --- Agent tool-permission UX ------------------------------------------
    // Show a permission modal for a pending agent tool call.
    // opts: {id, session_id, tool, input, summary}
    // Calls POST /agent/api/permission/{id}/{approve|deny} with {scope}.
    agentPermission: function(opts) {
        var title = 'Tool permission: ' + EOS_UI.esc(opts.tool || '?');
        var summary = opts.summary || '';
        var inputJson = '';
        try {
            inputJson = JSON.stringify(opts.input || {}, null, 2);
        } catch (e) { inputJson = ''; }
        var body =
            '<p style="margin:0 0 12px;font-size:13px;color:var(--text-secondary)">' +
                'The agent is requesting to run <b>' + EOS_UI.esc(opts.tool || '') + '</b>.' +
            '</p>' +
            (summary ?
                '<div style="margin:0 0 12px;padding:8px 10px;border-radius:6px;' +
                'background:rgba(56,139,253,0.08);border:1px solid rgba(56,139,253,0.35);' +
                'font-family:var(--font-mono,monospace);font-size:12px;white-space:pre-wrap">' +
                EOS_UI.esc(summary) + '</div>' : '') +
            (inputJson ?
                '<details style="margin:0 0 16px">' +
                '<summary style="cursor:pointer;font-size:12px;color:var(--text-secondary)">Full input</summary>' +
                '<pre style="margin:8px 0 0;padding:8px;background:var(--bg-secondary,#0d1117);border:1px solid var(--border,#30363d);border-radius:6px;font-size:12px;white-space:pre-wrap;max-height:240px;overflow:auto">' +
                EOS_UI.esc(inputJson) + '</pre></details>' : '') +
            '<label style="display:flex;align-items:center;gap:6px;margin:0 0 16px;font-size:13px;cursor:pointer">' +
                '<input type="checkbox" id="eos-agent-perm-session"> ' +
                'Approve for the rest of this session' +
            '</label>' +
            '<div class="eos-form-actions">' +
                '<button class="eos-btn" id="eos-agent-perm-deny">Deny</button>' +
                '<button class="eos-btn eos-btn-primary" id="eos-agent-perm-approve">Approve</button>' +
            '</div>';
        EOS_UI.modal({title: title, body: body, width: '520px'});
        var send = function(approved) {
            var remember = document.getElementById('eos-agent-perm-session');
            var scope = (remember && remember.checked) ? 'session' : 'once';
            EOS_UI.closeModal();
            var action = approved ? 'approve' : 'deny';
            fetch('/agent/api/permission/' + encodeURIComponent(opts.id) + '/' + action, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({scope: scope}),
            }).catch(function() {});
        };
        var approveBtn = document.getElementById('eos-agent-perm-approve');
        var denyBtn = document.getElementById('eos-agent-perm-deny');
        if (approveBtn) approveBtn.onclick = function() { send(true); };
        if (denyBtn) denyBtn.onclick = function() { send(false); };
    },

    // Install a global WebSocket listener that shows the consent modal
    // whenever a `cloud:consent_requested` event arrives. Call once per page.
    initCloudConsent: function() {
        if (EOS_UI._cloudConsentInit) return;
        EOS_UI._cloudConsentInit = true;
        if (typeof EmptyOSRealtime === 'undefined') return;
        if (!window.eosRealtime) {
            try {
                window.eosRealtime = new EmptyOSRealtime();
                window.eosRealtime.connect();
            } catch (e) { return; }
        }
        window.eosRealtime.on('cloud:consent_requested', function(data) {
            EOS_UI.cloudConsent(data || {});
        });
    },

    // --- Demo banner ------------------------------------------------------
    // Fetch demo status and, if enabled, prepend a dismissible banner to the
    // top of the page. Runs once per page load on any app.
    initDemoBanner: function() {
        if (EOS_UI._demoBannerInit) return;
        EOS_UI._demoBannerInit = true;
        fetch('/api/demo/status').then(function(r) { return r.ok ? r.json() : null; })
            .then(function(j) {
                if (!j || !j.enabled || !j.banner) return;
                if (document.getElementById('eos-demo-banner')) return;
                var bar = document.createElement('div');
                bar.id = 'eos-demo-banner';
                bar.className = 'eos-demo-banner';
                var links = '';
                if (j.about_url) {
                    links += ' <a class="eos-demo-banner-link" href="' + EOS_UI.esc(j.about_url) +
                             '" target="_blank" rel="noopener">About &rarr;</a>';
                }
                if (j.install_url) {
                    links += ' <a class="eos-demo-banner-link" href="' + EOS_UI.esc(j.install_url) +
                             '" target="_blank" rel="noopener">Source &rarr;</a>';
                }
                bar.innerHTML =
                    '<span class="eos-demo-banner-text">' + EOS_UI.esc(j.banner) + '</span>' + links;
                document.body.insertBefore(bar, document.body.firstChild);
                document.body.classList.add('eos-demo-active');
            })
            .catch(function() {});
    },

    // --- AI (think) offline banner ----------------------------------------
    // Fetch think-capability status. If no provider is available (or offline
    // is simulated), render a single system-level banner so users know
    // enhancement features will be unavailable. CRUD paths keep working.
    // Pages can listen for the `eos:think-status` event if they want to
    // disable/enable their own AI buttons.
    initThinkStatus: function() {
        if (EOS_UI._thinkStatusInit) return;
        EOS_UI._thinkStatusInit = true;

        var render = function(j) {
            var existing = document.getElementById('eos-think-banner');
            if (!j || j.available) {
                if (existing) existing.remove();
                document.body.classList.remove('eos-think-offline');
                return;
            }
            var reason = j.reason || 'AI providers are offline';
            var text = j.simulated
                ? 'AI is simulated offline. Enhancement features will show this banner instead of results.'
                : 'AI is offline — ' + reason + '. CRUD features still work; AI-powered buttons are disabled.';
            if (existing) {
                var span = existing.querySelector('.eos-think-banner-text');
                if (span) span.textContent = text;
                return;
            }
            var bar = document.createElement('div');
            bar.id = 'eos-think-banner';
            bar.className = 'eos-think-banner';
            bar.innerHTML =
                '<span class="eos-think-banner-icon" aria-hidden="true">&#9888;</span>' +
                '<span class="eos-think-banner-text">' + EOS_UI.esc(text) + '</span>';
            document.body.insertBefore(bar, document.body.firstChild);
            document.body.classList.add('eos-think-offline');
        };

        var check = function() {
            fetch('/api/think-status').then(function(r) { return r.ok ? r.json() : null; })
                .then(function(j) {
                    render(j);
                    try {
                        window.dispatchEvent(new CustomEvent('eos:think-status', { detail: j || { available: false } }));
                    } catch (e) {}
                })
                .catch(function() {});
        };

        check();
        // Re-check periodically — settings change, providers come back, etc.
        setInterval(check, 30000);
        // Re-check when the tab regains focus so banner reacts fast after toggling the setting.
        window.addEventListener('focus', check);
    },

    // PWA install banner — dismissible. Pages that want it call EOS_UI.pwaInstall.mount().
    // Non-destructive: already-installed or permanently-dismissed users see nothing.
    pwaInstall: {
        _DISMISS_KEY: 'eos:pwa-install-dismissed',
        _isStandalone: function() {
            return window.matchMedia && window.matchMedia('(display-mode: standalone)').matches
                || window.navigator.standalone === true;
        },
        _isIOS: function() {
            var ua = navigator.userAgent || '';
            return /iPad|iPhone|iPod/.test(ua) && !window.MSStream;
        },
        _dismissed: function() {
            try { return localStorage.getItem(this._DISMISS_KEY) === '1'; } catch(e) { return false; }
        },
        _dismiss: function() {
            try { localStorage.setItem(this._DISMISS_KEY, '1'); } catch(e) {}
        },
        mount: function() {
            var self = this;
            if (self._isStandalone() || self._dismissed()) return;

            var render = function(mode) {
                if (document.getElementById('eos-pwa-banner')) return;
                var bar = document.createElement('div');
                bar.id = 'eos-pwa-banner';
                bar.className = 'eos-pwa-banner';
                var msg = mode === 'ios'
                    ? 'Install EmptyOS: tap Share, then "Add to Home Screen".'
                    : 'Install EmptyOS as an app for faster access.';
                var actionBtn = mode === 'ios'
                    ? ''
                    : '<button class="eos-pwa-install-btn" data-action="install">Install</button>';
                bar.innerHTML =
                    '<span class="eos-pwa-banner-msg">' + msg + '</span>' +
                    actionBtn +
                    '<button class="eos-pwa-dismiss-btn" data-action="dismiss" aria-label="Dismiss">&times;</button>';
                bar.addEventListener('click', function(e) {
                    var action = e.target.getAttribute('data-action');
                    if (action === 'install' && window._eosInstallPromptEvent) {
                        window._eosInstallPromptEvent.prompt();
                        window._eosInstallPromptEvent.userChoice.finally(function() {
                            window._eosInstallPromptEvent = null;
                            bar.remove();
                        });
                    } else if (action === 'dismiss') {
                        self._dismiss();
                        bar.remove();
                    }
                });
                document.body.insertBefore(bar, document.body.firstChild);
            };

            // iOS: no beforeinstallprompt — render hint immediately for non-standalone Safari.
            if (self._isIOS()) {
                render('ios');
                return;
            }
            // Other browsers: only render once the install prompt is ready.
            if (window._eosInstallPromptEvent) {
                render('prompt');
            } else {
                window.addEventListener('eos:pwa-installable', function() { render('prompt'); }, { once: true });
            }
        },
    },
};

// Global fetch wrapper — turns 503 `{error:"ai_offline"}` responses into
// a toast + banner refresh so clicking an AI button while AI is offline
// gives honest feedback instead of a silent failure. The response object
// itself is returned unchanged so callers can still handle .ok / .status.
(function _eosWrapFetch() {
    if (typeof window === 'undefined' || window._eosFetchWrapped) return;
    window._eosFetchWrapped = true;
    var _origFetch = window.fetch.bind(window);
    var _lastToast = 0;
    window.fetch = function(input, init) {
        return _origFetch(input, init).then(function(res) {
            if (res && res.status === 503) {
                var probe = res.clone();
                probe.json().then(function(j) {
                    if (!j) return;
                    var err = j.error || '';
                    if (err === 'ai_offline' || err === 'capability_offline') {
                        var now = Date.now();
                        if (now - _lastToast > 2500) {
                            _lastToast = now;
                            if (window.EOS_UI && EOS_UI.toast) {
                                EOS_UI.toast(j.message || 'AI is offline — try again when a provider is available.');
                            }
                        }
                        if (window.EOS_UI && EOS_UI.initThinkStatus) {
                            EOS_UI._thinkStatusInit = false;
                            EOS_UI.initThinkStatus();
                        }
                    }
                }).catch(function() {});
            }
            return res;
        });
    };
})();

// ── View helpers (viewSwitcher / kanbanLayout / inlineCellEdit / pillBadge) ──
// Extracted from apps/boards in 2026-04 so tasks/projects/future apps can grow
// table/kanban/calendar views with the same chrome. Card content + group
// resolution are pluggable; the helpers own only the layout + drag-drop.

EOS_UI.pillBadge = function(value, colorMap) {
    var palette = ['blue','amber','green','emerald','red','purple','orange','gray'];
    var color = (colorMap && colorMap[value]) || 'gray';
    if (palette.indexOf(color) === -1) color = 'gray';
    return '<span class="eos-pill eos-pill-' + color + '">' + EOS_UI.esc(String(value)) + '</span>';
};

// EOS_UI.viewSwitcher({mountId, views, active, onChange})
//   views   = ['table','kanban',...] OR [{type, label?, icon?}, ...]
//   onChange(viewType) fires on click; helper toggles .active for you.
EOS_UI.viewSwitcher = function(opts) {
    var ICONS = {table:'☰', kanban:'▥', calendar:'📅', timeline:'⏳', chart:'📈', gallery:'▦', list:'≡'};
    var mount = document.getElementById(opts.mountId);
    if (!mount) return null;
    var views = (opts.views || []).map(function(v) {
        if (typeof v === 'string') return {type: v};
        return v;
    });
    if (!views.length) views = [{type: 'table'}];
    var active = opts.active || views[0].type;
    mount.classList.add('eos-view-tabs');
    mount.innerHTML = views.map(function(v) {
        var label = v.label || (v.type.charAt(0).toUpperCase() + v.type.slice(1));
        var icon = v.icon != null ? v.icon : (ICONS[v.type] || '');
        return '<button type="button" class="eos-view-tab' + (v.type === active ? ' active' : '') +
               '" data-view="' + EOS_UI.esc(v.type) + '">' +
               (icon ? '<span class="eos-view-tab-icon">' + EOS_UI.esc(icon) + '</span>' : '') +
               EOS_UI.esc(label) + '</button>';
    }).join('');
    mount.querySelectorAll('.eos-view-tab').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var v = btn.getAttribute('data-view');
            mount.querySelectorAll('.eos-view-tab').forEach(function(b) { b.classList.remove('active'); });
            btn.classList.add('active');
            if (typeof opts.onChange === 'function') opts.onChange(v);
        });
    });
    return {
        setActive: function(v) {
            mount.querySelectorAll('.eos-view-tab').forEach(function(b) {
                b.classList.toggle('active', b.getAttribute('data-view') === v);
            });
        }
    };
};

// EOS_UI.kanbanLayout({mountId, items, groups, getGroup|inGroup, renderCard, onMove, getItemId, colorMap, wrapCards})
//   groups     = [{key, label, color?}]
//   getGroup   = function(item) -> string  (1:1 grouping — most common)
//   inGroup    = function(item, groupKey) -> bool  (multi-membership — overrides getGroup)
//   renderCard = function(item) -> HTML string. If wrapCards=true (default), this is the
//                card body and the helper wraps in <div class="eos-kanban-card"...>.
//                If wrapCards=false, you return a complete element (e.g. EOS_UI.entityCard);
//                the helper just sets data-id + draggable on the root via post-render hook.
//   onMove     = function(item, newGroupKey) — called on successful drop
//   getItemId  = function(item) -> stable string id (default: item.id || item.file)
//   colorMap   = {value: paletteName} — overrides per-group `color` if group.color absent
//   wrapCards  = boolean (default true) — see renderCard above
EOS_UI.kanbanLayout = function(opts) {
    var mount = document.getElementById(opts.mountId);
    if (!mount) return;
    var items = opts.items || [];
    var groups = opts.groups || [];
    var inGroup = opts.inGroup;
    var getGroup = opts.getGroup || function(it) { return ''; };
    if (!inGroup) inGroup = function(it, key) { return getGroup(it) === key; };
    var getId = opts.getItemId || function(it) { return it.id || it.file || ''; };
    var renderCard = opts.renderCard || function(it) { return EOS_UI.esc(String(it.title || it.name || it.text || getId(it))); };
    var colorMap = opts.colorMap || {};
    var wrap = opts.wrapCards !== false;

    mount.classList.add('eos-kanban');
    mount.innerHTML = groups.map(function(grp) {
        var inG = items.filter(function(it) { return inGroup(it, grp.key); });
        var color = grp.color || colorMap[grp.key] || 'gray';
        var headerPill = '<span class="eos-pill eos-pill-' + EOS_UI.esc(color) + '" style="margin-right:0.4rem">' +
                         EOS_UI.esc(grp.label || grp.key || '—') + '</span>';
        return '<div class="eos-kanban-col">' +
            '<div class="eos-kanban-col-header"><span class="eos-kanban-col-title">' + headerPill + '</span>' +
            '<span class="eos-kanban-col-count">' + inG.length + '</span></div>' +
            '<div class="eos-kanban-items" data-group-key="' + EOS_UI.esc(grp.key) + '">' +
            inG.map(function(it) {
                if (wrap) {
                    return '<div class="eos-kanban-card" draggable="true" data-id="' +
                           EOS_UI.esc(getId(it)) + '">' + renderCard(it) + '</div>';
                }
                // Caller-rendered card; we mark the first child element as the
                // drag handle by tagging the wrapper itself.
                return '<div class="eos-kanban-card-host" draggable="true" data-id="' +
                       EOS_UI.esc(getId(it)) + '">' + renderCard(it) + '</div>';
            }).join('') +
            '</div></div>';
    }).join('');

    // Drag-drop wiring. Track the drag id in module-local scope to avoid
    // depending on dataTransfer (Safari quirks).
    var dragId = '';
    var dragSelector = wrap ? '.eos-kanban-card' : '.eos-kanban-card-host';
    mount.querySelectorAll(dragSelector).forEach(function(card) {
        card.addEventListener('dragstart', function(e) {
            dragId = card.getAttribute('data-id') || '';
            card.classList.add('dragging');
            if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
        });
        card.addEventListener('dragend', function() {
            card.classList.remove('dragging');
        });
    });
    mount.querySelectorAll('.eos-kanban-items').forEach(function(zone) {
        zone.addEventListener('dragover', function(e) { e.preventDefault(); zone.classList.add('drag-over'); });
        zone.addEventListener('dragleave', function() { zone.classList.remove('drag-over'); });
        zone.addEventListener('drop', function(e) {
            e.preventDefault();
            zone.classList.remove('drag-over');
            mount.querySelectorAll('.dragging').forEach(function(el) { el.classList.remove('dragging'); });
            if (!dragId) return;
            var targetKey = zone.getAttribute('data-group-key') || '';
            var item = items.find(function(it) { return getId(it) === dragId; });
            dragId = '';
            if (!item) return;
            if (inGroup(item, targetKey)) return;  // already there → no-op
            if (typeof opts.onMove === 'function') opts.onMove(item, targetKey);
        });
    });
};

// EOS_UI.inlineCellEdit({el, value, type, options, onSave, onCancel})
//   type = 'text' | 'select' | 'date' | 'number'
//   options = list of strings (for select)
//   onSave(newValue) on blur or Enter; Esc reverts.
EOS_UI.inlineCellEdit = function(opts) {
    var el = opts.el;
    if (!el || el.querySelector('input,select,textarea')) return;
    var old = opts.value != null ? String(opts.value) : el.textContent;
    var type = opts.type || 'text';
    var input;
    if (type === 'select') {
        input = document.createElement('select');
        (opts.options || []).forEach(function(o) {
            var op = document.createElement('option');
            op.value = o; op.textContent = o;
            if (String(o) === old) op.selected = true;
            input.appendChild(op);
        });
    } else {
        input = document.createElement('input');
        input.type = (type === 'date' ? 'date' : (type === 'number' ? 'number' : 'text'));
        input.value = old;
    }
    input.className = 'eos-cell-edit-input';
    el.innerHTML = '';
    el.appendChild(input);
    input.focus();
    if (input.select) try { input.select(); } catch (e) {}
    var done = false;
    function commit() {
        if (done) return;
        done = true;
        var v = input.value;
        if (typeof opts.onSave === 'function') opts.onSave(v);
    }
    function revert() {
        if (done) return;
        done = true;
        el.textContent = old;
        if (typeof opts.onCancel === 'function') opts.onCancel();
    }
    input.addEventListener('blur', commit);
    input.addEventListener('change', function() { if (type === 'select') commit(); });
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') input.blur();
        else if (e.key === 'Escape') { revert(); input.blur(); }
    });
};

// Sticky job-progress card — bottom-right, survives scrolling. Use for any
// long-running async job (MV render, podcast gen, ComfyUI batch, etc.).
//   var job = EOS_UI.jobProgress({id, onCancel});
//   job.update({stage:'Generating images', detail:'scene 9 running', pct:42});
//   job.done({stage:'Ready', detail:'output.mp4'});  // auto-hides after 4s
//   job.hide();
// Multiple instances coexist when given distinct ids; same id reuses the card.
EOS_UI.jobProgress = function(opts) {
    opts = opts || {};
    var id = opts.id || 'eos-job-default';
    var elId = 'eos-job-' + id.replace(/[^a-z0-9_-]/gi, '_');
    function ensure() {
        var box = document.getElementById(elId);
        if (box) return box;
        // Stack multiple cards by counting existing ones
        var existing = document.querySelectorAll('.eos-job-progress').length;
        box = document.createElement('div');
        box.id = elId;
        box.className = 'eos-job-progress';
        box.style.bottom = 'calc(env(safe-area-inset-bottom) + ' + (88 + existing * 86) + 'px)';
        box.innerHTML =
            '<div class="eos-job-row">' +
                '<span class="eos-spinner" style="width:14px;height:14px;border-width:2px"></span>' +
                '<b class="eos-job-stage">Working…</b>' +
                '<span class="eos-job-detail"></span>' +
                '<button class="eos-btn eos-btn-sm eos-btn-ghost eos-job-cancel" style="margin-left:auto;display:none">Cancel</button>' +
            '</div>' +
            '<div class="eos-bar"><div class="eos-bar-fill eos-job-bar" style="width:0%"></div></div>';
        document.body.appendChild(box);
        if (opts.onCancel) {
            box.querySelector('.eos-job-cancel').addEventListener('click', opts.onCancel);
        }
        return box;
    }
    function update(state) {
        state = state || {};
        var box = ensure();
        box.style.display = '';
        if (state.stage != null) box.querySelector('.eos-job-stage').textContent = state.stage;
        box.querySelector('.eos-job-detail').textContent = state.detail || '';
        if (state.pct != null) {
            box.querySelector('.eos-job-bar').style.width = Math.max(0, Math.min(100, state.pct)) + '%';
        }
        var btn = box.querySelector('.eos-job-cancel');
        if (btn) btn.style.display = (opts.onCancel && state.cancellable !== false) ? '' : 'none';
    }
    function hide() {
        var box = document.getElementById(elId);
        if (!box) return;
        box.style.display = 'none';
        var bar = box.querySelector('.eos-job-bar'); if (bar) bar.style.width = '0%';
    }
    function done(state) {
        update(Object.assign({pct:100}, state||{}));
        var box = document.getElementById(elId);
        if (box) {
            var btn = box.querySelector('.eos-job-cancel');
            if (btn) btn.style.display = 'none';
        }
        setTimeout(hide, (state && state.lingerMs) || 4000);
    }
    function destroy() {
        var box = document.getElementById(elId);
        if (box) box.remove();
    }
    return {update: update, hide: hide, done: done, destroy: destroy, id: id};
};

// Auto-init dictionary popup + cloud-consent listener + demo banner on all pages
// PWA install banner auto-mounts only on home-like pages to avoid noise on every app.
function _eosAutoInit() {
    EOS_UI.initDict();
    EOS_UI.initCloudConsent();
    EOS_UI.initDemoBanner();
    EOS_UI.initThinkStatus();
    var p = location.pathname;
    var isHomeLike = p === '/' || p === '/hub/' || p === '/hub' ||
                     document.body && document.body.getAttribute('data-pwa-install') === 'true';
    if (isHomeLike) EOS_UI.pwaInstall.mount();
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _eosAutoInit);
} else {
    _eosAutoInit();
}

// --- Spotlight primitive — used by the product tour to highlight a real
// element on a real page and attach a tooltip. Pure DOM, no deps.
//
// EOS_UI.spotlight(selector, {
//   title?, body, stepLabel? ("2 / 6"),
//   nextLabel? ("Next →"), prevLabel? ("← Back"), skipLabel? ("Skip"),
//   onNext?, onPrev?, onSkip?,            // called when buttons clicked
//   placement? ("bottom"|"top"|"auto"),   // tooltip side; default auto
//   padding? (default 8),                 // px around target
//   waitFor? ms (default 6000)            // poll for selector before giving up
// }) -> {update(), close()}
//
// If selector is missing, a center-screen tooltip with no cutout is shown so
// the tour still progresses (e.g. "Welcome" steps without a target element).
EOS_UI.spotlight = function(selector, opts) {
    opts = opts || {};
    var pad = opts.padding == null ? 8 : opts.padding;
    var waitFor = opts.waitFor == null ? 6000 : opts.waitFor;
    var existing = document.getElementById('eos-spotlight-root');
    if (existing) existing.remove();

    var root = document.createElement('div');
    root.id = 'eos-spotlight-root';
    root.className = 'eos-spotlight';
    root.innerHTML =
        '<div class="eos-spotlight-mask" id="eos-spotlight-mask"></div>' +
        '<div class="eos-spotlight-tooltip" id="eos-spotlight-tip" role="dialog" aria-modal="true">' +
            '<div class="eos-spotlight-step" id="eos-spotlight-step"></div>' +
            '<div class="eos-spotlight-title" id="eos-spotlight-title"></div>' +
            '<div class="eos-spotlight-body" id="eos-spotlight-body"></div>' +
            '<div class="eos-spotlight-actions">' +
                '<button class="eos-btn eos-btn-sm eos-btn-ghost" id="eos-spotlight-skip"></button>' +
                '<div style="flex:1"></div>' +
                '<button class="eos-btn eos-btn-sm eos-btn-ghost" id="eos-spotlight-prev"></button>' +
                '<button class="eos-btn eos-btn-sm" id="eos-spotlight-next"></button>' +
            '</div>' +
        '</div>';
    document.body.appendChild(root);

    var mask = document.getElementById('eos-spotlight-mask');
    var tip = document.getElementById('eos-spotlight-tip');
    var stepEl = document.getElementById('eos-spotlight-step');
    var titleEl = document.getElementById('eos-spotlight-title');
    var bodyEl = document.getElementById('eos-spotlight-body');
    var skipBtn = document.getElementById('eos-spotlight-skip');
    var prevBtn = document.getElementById('eos-spotlight-prev');
    var nextBtn = document.getElementById('eos-spotlight-next');

    stepEl.textContent = opts.stepLabel || '';
    titleEl.textContent = opts.title || '';
    bodyEl.innerHTML = opts.body || '';
    skipBtn.textContent = opts.skipLabel || 'Skip tour';
    prevBtn.textContent = opts.prevLabel || '← Back';
    nextBtn.textContent = opts.nextLabel || 'Next →';
    skipBtn.style.display = opts.onSkip ? '' : 'none';
    prevBtn.style.display = opts.onPrev ? '' : 'none';
    nextBtn.style.display = opts.onNext ? '' : 'none';
    if (opts.onSkip) skipBtn.onclick = opts.onSkip;
    if (opts.onPrev) prevBtn.onclick = opts.onPrev;
    if (opts.onNext) nextBtn.onclick = opts.onNext;

    function position() {
        var target = selector ? document.querySelector(selector) : null;
        if (!target) {
            // Center-screen mode — no cutout
            mask.style.background = 'rgba(0,0,0,0.55)';
            mask.style.clipPath = '';
            tip.style.left = '50%';
            tip.style.top = '50%';
            tip.style.transform = 'translate(-50%, -50%)';
            return;
        }
        var r = target.getBoundingClientRect();
        var W = window.innerWidth, H = window.innerHeight;
        var x = Math.max(0, r.left - pad), y = Math.max(0, r.top - pad);
        var w = Math.min(W - x, r.width + pad * 2), h = Math.min(H - y, r.height + pad * 2);
        // Cutout via clip-path (evenodd)
        mask.style.background = 'rgba(0,0,0,0.55)';
        mask.style.clipPath =
            'polygon(0 0, 0 100%, 100% 100%, 100% 0, 0 0,' +
            x + 'px ' + y + 'px,' +
            x + 'px ' + (y + h) + 'px,' +
            (x + w) + 'px ' + (y + h) + 'px,' +
            (x + w) + 'px ' + y + 'px,' +
            x + 'px ' + y + 'px)';
        // Place tooltip
        var tipW = Math.min(360, W - 24);
        tip.style.width = tipW + 'px';
        // Prefer below; if no room, put above
        var place = opts.placement || 'auto';
        var below = (y + h + 12 + 200 < H);
        if (place === 'top') below = false;
        if (place === 'bottom') below = true;
        var tipX = Math.max(12, Math.min(W - tipW - 12, r.left));
        var tipY = below ? (y + h + 12) : Math.max(12, y - 12 - tip.offsetHeight);
        tip.style.transform = '';
        tip.style.left = tipX + 'px';
        tip.style.top = tipY + 'px';
        try { target.scrollIntoView({block: 'center', behavior: 'smooth'}); } catch (e) {}
    }

    var pollStart = Date.now();
    function tryPosition() {
        if (selector && !document.querySelector(selector)) {
            if (Date.now() - pollStart < waitFor) {
                return setTimeout(tryPosition, 120);
            }
            // give up — center-screen fallback
        }
        position();
    }
    tryPosition();

    var onResize = function() { position(); };
    window.addEventListener('resize', onResize);
    window.addEventListener('scroll', onResize, true);

    return {
        update: function(newOpts) {
            newOpts = newOpts || {};
            if (newOpts.title != null) titleEl.textContent = newOpts.title;
            if (newOpts.body != null) bodyEl.innerHTML = newOpts.body;
            if (newOpts.stepLabel != null) stepEl.textContent = newOpts.stepLabel;
            position();
        },
        close: function() {
            window.removeEventListener('resize', onResize);
            window.removeEventListener('scroll', onResize, true);
            if (root.parentNode) root.parentNode.removeChild(root);
        },
        reposition: position,
    };
};

/**
 * EOS_UI.searchBar(opts) — global app + vault search input.
 * Mounts into opts.mount (DOM element or selector). Returns {focus, destroy}.
 *
 * opts:
 *   mount         — required. Container element or selector string.
 *   placeholder   — input placeholder text. Default "Search apps, vault, commands…".
 *   showKbdHint   — show "/" hint chip on the right. Default true.
 *   focusKey      — global key that focuses the bar from anywhere. Default "/". Pass null to disable.
 *   onSelectApp   — fn(app) called when a result is chosen. Default: location.href = app.web_prefix + '/'.
 *   onFallback    — fn(query) called when Enter pressed with no app match. Default: location.href = '/search/?q=' + query.
 *   maxResults    — cap on app matches. Default 8.
 *   filter        — fn(app, query) → bool. Override the default name/id/desc match.
 */
EOS_UI.searchBar = function(opts) {
    opts = opts || {};
    var mount = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
    if (!mount) { console.warn('EOS_UI.searchBar: no mount element'); return null; }
    var placeholder = opts.placeholder || 'Search apps, vault, commands…';
    var showKbd = opts.showKbdHint !== false;
    var focusKey = opts.focusKey === undefined ? '/' : opts.focusKey;
    var maxResults = opts.maxResults || 8;
    var onSelect = opts.onSelectApp || function(a) { location.href = (a.web_prefix || ('/' + a.id)) + '/'; };
    var onFallback = opts.onFallback || function(q) { location.href = '/search/?q=' + encodeURIComponent(q); };
    var filter = opts.filter || function(a, q) {
        return (a.name || '').toLowerCase().indexOf(q) >= 0
            || (a.id || '').toLowerCase().indexOf(q) >= 0
            || (a.description || '').toLowerCase().indexOf(q) >= 0;
    };

    function escHtml(s) {
        if (s == null) return '';
        return String(s).replace(/[&<>"']/g, function(c){
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
        });
    }

    var wrap = document.createElement('div');
    wrap.className = 'eos-search';
    wrap.innerHTML =
        '<span class="eos-search-icon">&#128269;</span>' +
        '<input class="eos-search-input" type="text" placeholder="' + escHtml(placeholder) + '" autocomplete="off" spellcheck="false">' +
        (showKbd && focusKey ? '<span class="eos-search-kbd">' + escHtml(focusKey) + '</span>' : '') +
        '<div class="eos-search-results"></div>';
    mount.appendChild(wrap);

    var input = wrap.querySelector('.eos-search-input');
    var results = wrap.querySelector('.eos-search-results');
    var apps = [];
    var idx = -1;

    fetch('/api/apps').then(function(r){return r.json();}).then(function(list){
        apps = (list || []).filter(function(a){ return a && (a.web_prefix || a.id); });
    }).catch(function(){});

    // currentItems is the flat list shown in render order; each item is
    // {kind: 'app'|'note'|'fallback', payload: ...}. Keyboard arrows + clicks
    // index into this array. Section headers don't go into it.
    var currentItems = [];
    var noteFetchSeq = 0;
    var noteDebounce = null;
    var lastNotes = [];
    var lastNotesQuery = '';

    function renderSections(qLower, qRaw, notesLoading) {
        var html = '';
        var flat = [];
        var matchedApps = apps.filter(function(a){ return filter(a, qLower); }).slice(0, maxResults);

        if (matchedApps.length) {
            html += '<div class="eos-search-section">Apps</div>';
            matchedApps.forEach(function(a){
                flat.push({kind: 'app', payload: a});
                var i = flat.length - 1;
                html += '<div class="eos-search-item' + (i === idx ? ' active' : '') + '" data-i="' + i + '">' +
                    '<span class="eos-search-item-icon">' + escHtml(a.icon || '\u25A2') + '</span>' +
                    '<span class="eos-search-item-name">' + escHtml(a.name || a.id) + '</span>' +
                    '<span class="eos-search-item-desc">' + escHtml((a.description || '').slice(0, 60)) + '</span>' +
                    '</div>';
            });
        }

        if (lastNotes.length || notesLoading) {
            html += '<div class="eos-search-section">Notes' +
                    (notesLoading ? ' <span class="eos-search-loading">searching...</span>' : '') +
                    '</div>';
            lastNotes.slice(0, maxResults).forEach(function(n){
                flat.push({kind: 'note', payload: n});
                var i = flat.length - 1;
                var title = n.title || (n.path || '').split('/').pop() || 'untitled';
                var snippet = (n.snippet || n.preview || n.excerpt || '').replace(/\s+/g, ' ').slice(0, 80);
                html += '<div class="eos-search-item' + (i === idx ? ' active' : '') + '" data-i="' + i + '">' +
                    '<span class="eos-search-item-icon">\u{1F4DD}</span>' +
                    '<span class="eos-search-item-name">' + escHtml(title) + '</span>' +
                    '<span class="eos-search-item-desc">' + escHtml(snippet) + '</span>' +
                    '</div>';
            });
        }

        // Always-on fallback link to the full search page
        html += '<div class="eos-search-section">Other</div>';
        flat.push({kind: 'fallback', payload: {q: qRaw}});
        var fi = flat.length - 1;
        html += '<div class="eos-search-item' + (fi === idx ? ' active' : '') + '" data-i="' + fi + '">' +
            '<span class="eos-search-item-icon">\u{1F50D}</span>' +
            '<span class="eos-search-item-name">Open full search</span>' +
            '<span class="eos-search-item-desc">All vault matches for &ldquo;' + escHtml(qRaw) + '&rdquo;</span>' +
            '</div>';

        results.innerHTML = html;
        results.classList.add('open');
        currentItems = flat;
    }

    function fetchNotes(qRaw) {
        var seq = ++noteFetchSeq;
        if (qRaw !== lastNotesQuery) { lastNotes = []; lastNotesQuery = qRaw; }
        renderSections(qRaw.toLowerCase(), qRaw, true);
        fetch('/search/api/search?q=' + encodeURIComponent(qRaw) + '&top=8')
            .then(function(r){ return r.json(); })
            .then(function(data){
                if (seq !== noteFetchSeq) return;
                lastNotes = (data && data.results) || [];
                renderSections(qRaw.toLowerCase(), qRaw, false);
            })
            .catch(function(){
                if (seq !== noteFetchSeq) return;
                renderSections(qRaw.toLowerCase(), qRaw, false);
            });
    }

    input.addEventListener('input', function(){
        var qRaw = this.value.trim();
        if (!qRaw) {
            results.classList.remove('open');
            idx = -1; currentItems = []; lastNotes = []; lastNotesQuery = '';
            clearTimeout(noteDebounce);
            return;
        }
        idx = 0;
        renderSections(qRaw.toLowerCase(), qRaw, false);
        clearTimeout(noteDebounce);
        noteDebounce = setTimeout(function(){ fetchNotes(qRaw); }, 200);
    });

    input.addEventListener('keydown', function(e){
        var items = results.querySelectorAll('.eos-search-item');
        if (e.key === 'ArrowDown') {
            e.preventDefault(); idx = Math.min(idx + 1, items.length - 1); paintActive(items);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault(); idx = Math.max(idx - 1, 0); paintActive(items);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            var picked = currentItems[idx];
            if (picked) pickItem(picked);
            else if (this.value.trim()) onFallback(this.value.trim());
        } else if (e.key === 'Escape') {
            results.classList.remove('open'); this.blur();
        }
    });

    function pickItem(item) {
        if (item.kind === 'app') return onSelect(item.payload);
        if (item.kind === 'note') {
            var path = item.payload.path || '';
            if (path) location.href = '/search/?q=' + encodeURIComponent(input.value.trim()) + '&open=' + encodeURIComponent(path);
            else onFallback(input.value.trim());
            return;
        }
        if (item.kind === 'fallback') return onFallback(item.payload.q || input.value.trim());
    }

    results.addEventListener('click', function(e){
        var item = e.target.closest('.eos-search-item');
        if (!item) return;
        var picked = currentItems[+item.dataset.i];
        if (picked) pickItem(picked);
    });

    function paintActive(items) {
        items.forEach(function(el, i){ el.classList.toggle('active', i === idx); });
    }

    function onDocClick(e){
        if (!wrap.contains(e.target)) results.classList.remove('open');
    }
    document.addEventListener('click', onDocClick);

    var onGlobalKey = null;
    if (focusKey) {
        onGlobalKey = function(e){
            if (e.key === focusKey && document.activeElement !== input
                && !e.target.matches('input, textarea, [contenteditable]')) {
                e.preventDefault();
                input.focus();
            }
        };
        document.addEventListener('keydown', onGlobalKey);
    }

    return {
        focus: function(){ input.focus(); },
        destroy: function(){
            document.removeEventListener('click', onDocClick);
            if (onGlobalKey) document.removeEventListener('keydown', onGlobalKey);
            wrap.remove();
        },
    };
};
