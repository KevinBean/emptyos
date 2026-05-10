// EOS_UI.libraryPicker — generic typeahead picker over an app's library
// JSON endpoint. Used by apps/cables to pick a Nexans entry; reusable
// from apps/lines, apps/grid, apps/earthing, etc.
//
// Usage:
//   EOS_UI.libraryPicker({
//     endpoint: '/cables/api/library',         // returns {library: [{id, ...}]}
//     filterFields: [                          // optional chip filters
//       { key: 'rated_voltage_kv', label: 'V', values: [11, 22, 33] },
//       { key: 'conductor_material', label: 'Mat', values: ['Cu', 'Al'] },
//     ],
//     summary: function(entry) {                // text shown for each row
//       return entry.conductor_csa_mm2 + 'mm² · ' + entry.conductor_material +
//              ' · ' + entry.rated_voltage_kv + 'kV';
//     },
//     ampacity: function(entry) {               // optional: shown as a green chip
//       return entry.base_rating_a;
//     },
//     onPick: function(entry) { ... },          // user selected this entry
//     title: 'Pick a cable',                    // modal title
//   });

(function (root) {
  if (!root.EOS_UI) root.EOS_UI = {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  EOS_UI.libraryPicker = async function (opts) {
    if (!opts || !opts.endpoint || !opts.onPick) {
      throw new Error('libraryPicker requires endpoint + onPick');
    }
    var summary = opts.summary || function (e) { return e.id; };
    var ampacity = opts.ampacity || function () { return null; };
    var filterFields = opts.filterFields || [];
    var title = opts.title || 'Pick from library';

    // Fetch once, filter client-side. Library JSON is typically <500 entries
    // — server-side filtering would be premature.
    var data = await fetch(opts.endpoint).then(function (r) { return r.json(); });
    var entries = (data && (data.library || data.entries || data)) || [];

    var state = { q: '', filters: {} };

    function buildBody() {
      var search =
        '<div class="lp-search">' +
          '<input id="lp-input" type="text" placeholder="Search id / size / material…" autocomplete="off">' +
        '</div>';
      var chips = filterFields.map(function (f) {
        return '<div class="lp-chips">' +
          '<span style="font-size:10px;color:var(--text-muted);align-self:center;margin-right:4px">' + esc(f.label) + ':</span>' +
          (f.values || []).map(function (v) {
            var active = state.filters[f.key] === v;
            return '<button class="lp-chip' + (active ? ' active' : '') + '" data-key="' + esc(f.key) + '" data-val="' + esc(v) + '">' + esc(v) + '</button>';
          }).join('') +
        '</div>';
      }).join('');
      return search + chips + '<div class="lp-list" id="lp-list"></div>';
    }

    function applyFilters() {
      var q = state.q.toLowerCase().trim();
      return entries.filter(function (e) {
        for (var k in state.filters) {
          if (state.filters[k] != null && String(e[k]) !== String(state.filters[k])) return false;
        }
        if (!q) return true;
        var hay = [e.id, e.label, e.conductor_material, e.insulation_material,
                   e.conductor_csa_mm2, e.rated_voltage_kv].join(' ').toLowerCase();
        return hay.indexOf(q) !== -1;
      });
    }

    function renderList() {
      var rows = applyFilters().slice(0, 100).map(function (e) {
        var amp = ampacity(e);
        return '<div class="lp-row" data-id="' + esc(e.id) + '">' +
          '<div><div>' + esc(summary(e)) + '</div>' +
            '<div class="lp-id">' + esc(e.id) + '</div></div>' +
          (amp != null ? '<div class="lp-amp">' + esc(amp) + ' A</div>' : '<div></div>') +
          '<div class="muted" style="font-size:10px">→</div>' +
        '</div>';
      });
      var listEl = document.getElementById('lp-list');
      if (!listEl) return;
      listEl.innerHTML = rows.length ? rows.join('') :
        '<div class="muted" style="padding:1rem;text-align:center">No matches</div>';
      // Click handlers
      Array.prototype.forEach.call(listEl.querySelectorAll('.lp-row'), function (row) {
        row.onclick = function () {
          var id = row.getAttribute('data-id');
          var entry = entries.find(function (e) { return e.id === id; });
          if (entry) { EOS_UI.closeModal(); opts.onPick(entry); }
        };
      });
    }

    EOS_UI.modal({ title: title, body: buildBody(), width: '560px' });

    // Wire up after the modal renders.
    setTimeout(function () {
      var input = document.getElementById('lp-input');
      if (input) {
        input.focus();
        input.addEventListener('input', function () {
          state.q = this.value;
          renderList();
        });
      }
      Array.prototype.forEach.call(document.querySelectorAll('.lp-chip'), function (chip) {
        chip.onclick = function () {
          var k = chip.getAttribute('data-key');
          var v = chip.getAttribute('data-val');
          // Toggle
          if (String(state.filters[k]) === String(v)) state.filters[k] = null;
          else state.filters[k] = v;
          // Refresh chips' active state
          Array.prototype.forEach.call(document.querySelectorAll('.lp-chip'), function (c) {
            c.classList.toggle('active',
              c.getAttribute('data-key') === k &&
              String(c.getAttribute('data-val')) === String(state.filters[k]));
          });
          renderList();
        };
      });
      renderList();
    }, 0);
  };
})(window);
