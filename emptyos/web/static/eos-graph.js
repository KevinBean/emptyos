/* EOS_GRAPH — render a DecisionGraph (emptyos/sdk/decision_graph) into an <svg>,
 * with pan/zoom and run-state highlighting. Shared by any app that wants to draw
 * a workflow/decision graph the same way.
 *
 * Extracted from apps/public/labs/synth (the graph-pipeline runner UI) at its 2nd
 * consumer (apps/extension/dev/feature-pipeline's workflow view) per CLAUDE.md
 * rule 9. Pure DOM/SVG, no deps.
 *
 * Graph shape (from DecisionGraph.to_dict()):
 *   { start, nodes: [{ id, kind?, data:{label, pos:{x,y}, ...}, transitions:[{to,kind,label}] }] }
 *   - node.data.pos {x,y} are grid coords (multiplied by GX/GY); required for layout.
 *   - transition.kind "auto" → dashed edge; "choice" → solid.
 *   - node.kind "end" → coloured border (green success / red fail).
 *
 * Usage:
 *   EOS_GRAPH.render(svg, graph, {current:'x', visited:['a','b']});
 *   EOS_GRAPH.wireZoom(svg);                  // wheel-zoom + drag-pan (idempotent)
 *   EOS_GRAPH.zoomBy(svg, 1.2);  EOS_GRAPH.fit(svg);
 *
 * Network graph usage:
 *   EOS_GRAPH.renderNetwork('#mount', {
 *     center: 'subject',
 *     nodes: [{id:'subject', label:'Work conflict', kind:'subject'}],
 *     edges: [{from:'a', to:'subject', label:'conditions'}]
 *   });
 *
 * Per-<svg> state (graph, zoom, cfg, wired) is stashed on the element, so several
 * graphs can coexist on one page.
 */
(function () {
  var DEF = { NW: 150, NH: 42, GX: 200, GY: 120, OX: 30, OY: 30 };
  // End nodes whose id marks a failure terminal → red border (else green).
  var FAIL_ENDS = ['escalate', 'blocked', 'failed', 'error', 'abort'];

  function svgEl(tag, attrs) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function svgText(x, y, s, attrs) {
    var t = svgEl('text', Object.assign({ x: x, y: y, 'text-anchor': 'middle' }, attrs || {}));
    t.textContent = s;
    return t;
  }
  function ensureDefs(svg) {
    var defs = svg.querySelector('defs');
    if (!defs) { defs = svgEl('defs', {}); svg.insertBefore(defs, svg.firstChild); }
    if (!defs.querySelector('#eos-graph-arrow')) {
      var m = svgEl('marker', { id: 'eos-graph-arrow', markerWidth: 8, markerHeight: 8, refX: 7, refY: 3, orient: 'auto' });
      m.appendChild(svgEl('path', { d: 'M0,0 L7,3 L0,6 Z' }));
      defs.appendChild(m);
    }
  }
  function pos(n, cfg) {
    var p = (n.data && n.data.pos) || { x: 0, y: 0 };
    return { x: cfg.OX + p.x * cfg.GX, y: cfg.OY + p.y * cfg.GY };
  }

  function render(svg, graph, state, opts) {
    if (!svg || !graph) return;
    var cfg = Object.assign({}, DEF, opts || {});
    state = state || {};
    ensureDefs(svg);
    Array.prototype.slice.call(svg.children).forEach(function (c) { if (c.tagName !== 'defs') svg.removeChild(c); });
    svg._eosGraph = graph; svg._eosCfg = cfg;

    var byId = {}; (graph.nodes || []).forEach(function (n) { byId[n.id] = n; });
    var current = state.current;
    var hist = state.visited || state.history || [];
    // SVG presentation attributes do NOT resolve CSS var() — resolve theme
    // tokens to real values, else fill/text fall back to black-on-black.
    var rs = getComputedStyle(document.documentElement);
    function tok(name, fb) { var v = (rs.getPropertyValue(name) || '').trim(); return v || fb; }
    var C = {
      surface: tok('--surface', '#ffffff'), border: tok('--border', '#d0d0d0'),
      text: tok('--text', '#1a1a1a'), accent: tok('--accent', '#6c5ce7'),
      muted: tok('--text-muted', '#888888'),
      current: tok('--warning', '#f39c12'), ok: tok('--success', '#27ae60'), fail: tok('--danger', '#c0392b')
    };
    var arrow = svg.querySelector('#eos-graph-arrow path'); if (arrow) arrow.setAttribute('fill', C.muted);

    var P = svgEl('g', { class: 'eos-graph-view' });
    // edges
    (graph.nodes || []).forEach(function (n) {
      var p = pos(n, cfg);
      (n.transitions || []).forEach(function (t) {
        var tgt = byId[t.to]; if (!tgt) return;
        var q = pos(tgt, cfg);
        var x1 = p.x + cfg.NW, y1 = p.y + cfg.NH / 2, x2 = q.x, y2 = q.y + cfg.NH / 2;
        if (q.x <= p.x) { x1 = p.x + cfg.NW / 2; y1 = p.y + cfg.NH; x2 = q.x + cfg.NW / 2; y2 = q.y; } // back/loop edge
        P.appendChild(svgEl('path', {
          d: 'M' + x1 + ',' + y1 + ' L' + x2 + ',' + y2,
          stroke: C.muted, 'stroke-width': 1.4, fill: 'none',
          'stroke-dasharray': t.kind === 'auto' ? '4,3' : '', 'marker-end': 'url(#eos-graph-arrow)', opacity: .6
        }));
        if (t.label && t.label !== '→') {
          P.appendChild(svgText((x1 + x2) / 2, (y1 + y2) / 2 - 3, t.label, { fill: C.muted, 'font-size': 10 }));
        }
      });
    });
    // nodes
    (graph.nodes || []).forEach(function (n) {
      var p = pos(n, cfg);
      var fill = C.surface, stroke = C.border, tc = C.text;
      if (hist.indexOf(n.id) >= 0) { fill = C.accent; tc = '#fff'; stroke = C.accent; }
      if (n.id === current) { fill = C.current; tc = '#fff'; stroke = C.current; }
      if (n.kind === 'end') {
        var fail = (n.data && n.data.fail) || FAIL_ENDS.indexOf(n.id) >= 0;
        stroke = fail ? C.fail : C.ok;
      }
      var g = svgEl('g', {});
      g.appendChild(svgEl('rect', { x: p.x, y: p.y, width: cfg.NW, height: cfg.NH, rx: 8, fill: fill, stroke: stroke, 'stroke-width': (n.id === current ? 2.5 : 1.4) }));
      g.appendChild(svgText(p.x + cfg.NW / 2, p.y + cfg.NH / 2 + 4, (n.data && n.data.label) || n.id, { fill: tc, 'font-size': 11 }));
      P.appendChild(g);
    });
    svg.appendChild(P);
    applyZoom(svg);
  }

  // ── pan / zoom (state stashed on the <svg>) ──
  function zstate(svg) { if (!svg._eosZoom) svg._eosZoom = { k: 1, tx: 0, ty: 0 }; return svg._eosZoom; }
  function applyZoom(svg) {
    var g = svg.querySelector('.eos-graph-view'); var z = zstate(svg);
    if (g) g.setAttribute('transform', 'translate(' + z.tx + ',' + z.ty + ') scale(' + z.k + ')');
  }
  function pt(svg, evt) {
    var p = svg.createSVGPoint(); p.x = evt.clientX; p.y = evt.clientY;
    return p.matrixTransform(svg.getScreenCTM().inverse());
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function zoomBy(svg, factor, anchor) {
    var z = zstate(svg); var a = anchor || { x: 820, y: 170 };
    var cx = (a.x - z.tx) / z.k, cy = (a.y - z.ty) / z.k;
    z.k = clamp(z.k * factor, 0.5, 5);
    z.tx = a.x - z.k * cx; z.ty = a.y - z.k * cy;
    applyZoom(svg);
  }
  function fit(svg) { svg._eosZoom = { k: 1, tx: 0, ty: 0 }; applyZoom(svg); }
  function wireZoom(svg) {
    if (!svg || svg._eosWired) return; svg._eosWired = true;
    svg.style.cursor = 'grab';
    var drag = false, last = null;
    svg.addEventListener('wheel', function (e) { e.preventDefault(); zoomBy(svg, e.deltaY < 0 ? 1.12 : 0.89, pt(svg, e)); }, { passive: false });
    svg.addEventListener('pointerdown', function (e) { drag = true; last = pt(svg, e); svg.style.cursor = 'grabbing'; svg.setPointerCapture(e.pointerId); });
    svg.addEventListener('pointermove', function (e) { if (!drag) return; var z = zstate(svg), p = pt(svg, e); z.tx += p.x - last.x; z.ty += p.y - last.y; last = p; applyZoom(svg); });
    svg.addEventListener('pointerup', function (e) { drag = false; svg.style.cursor = 'grab'; try { svg.releasePointerCapture(e.pointerId); } catch (_) {} });
  }

  // -- generic network graph -------------------------------------------------
  // Small, dependency-free SVG renderer for arbitrary node/edge maps. This is
  // intentionally deterministic: app surfaces should not wobble when a user
  // opens a saved graph. Large vault-scale graphs can still use vis-network.
  var NET_DEF = { width: 760, height: 360, minHeight: 320, nodeR: 21, centerR: 31 };
  var NETWORK_STYLE_ID = 'eos-network-graph-style';
  var NETWORK_SEQ = 0;

  function ensureNetworkStyles() {
    if (document.getElementById(NETWORK_STYLE_ID)) return;
    var css = [
      '.eos-network-graph{display:grid;grid-template-columns:minmax(0,1fr) 220px;gap:12px;align-items:stretch;width:100%;min-height:320px}',
      '.eos-network-canvas{min-width:0;border:1px solid var(--border);border-radius:8px;background:var(--bg-surface);overflow:hidden}',
      '.eos-network-svg{display:block;width:100%;height:100%;min-height:320px}',
      '.eos-network-node{cursor:pointer;outline:none}',
      '.eos-network-node circle{transition:stroke-width .12s ease,filter .12s ease,fill .12s ease}',
      '.eos-network-node:hover circle,.eos-network-node:focus circle{stroke-width:3}',
      '.eos-network-node.is-selected circle{stroke-width:3.4;filter:drop-shadow(0 2px 8px rgba(0,0,0,.16))}',
      '.eos-network-label{font:600 11px var(--font-sans,var(--font));fill:var(--text);paint-order:stroke;stroke:var(--bg-surface);stroke-width:3px;stroke-linejoin:round}',
      '.eos-network-edge-label{font:500 10px var(--font-sans,var(--font));fill:var(--text-muted);paint-order:stroke;stroke:var(--bg-surface);stroke-width:3px;stroke-linejoin:round}',
      '.eos-network-inspector{border:1px solid var(--border);border-radius:8px;background:var(--bg-card);padding:12px;min-width:0;overflow:auto}',
      '.eos-network-inspector h4{margin:0 0 6px;font-size:14px;line-height:1.25;color:var(--text-heading,var(--text))}',
      '.eos-network-inspector p{margin:8px 0 0;color:var(--text-secondary,var(--text-muted));font-size:12px;line-height:1.45}',
      '.eos-network-meta{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}',
      '.eos-network-meta span{border:1px solid var(--border);border-radius:999px;padding:2px 7px;font-size:11px;color:var(--text-muted);background:var(--bg-surface)}',
      '.eos-network-graph.is-compact{grid-template-columns:1fr}',
      '.eos-network-graph.is-compact .eos-network-inspector{min-height:96px}',
      '@media(max-width:760px){.eos-network-graph{grid-template-columns:1fr}.eos-network-inspector{min-height:96px}}'
    ].join('');
    var style = document.createElement('style');
    style.id = NETWORK_STYLE_ID;
    style.textContent = css;
    document.head.appendChild(style);
  }

  function resolveTarget(target) {
    if (!target) return null;
    if (typeof target === 'string') return document.querySelector(target);
    return target;
  }

  function htmlEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function textOf(v) { return String(v == null ? '' : v).trim(); }

  function normaliseNetwork(raw) {
    raw = raw || {};
    var nodes = [];
    var byId = {};
    function addNode(n, i) {
      n = n || {};
      var id = textOf(n.id || n.key || n.path || n.label || ('node-' + i));
      if (!id) return null;
      if (byId[id]) return byId[id];
      var node = Object.assign({}, n, {
        id: id,
        label: textOf(n.label || n.title || id),
        kind: textOf(n.kind || n.type || n.group || 'node')
      });
      byId[id] = node;
      nodes.push(node);
      return node;
    }
    (raw.nodes || []).forEach(addNode);
    var edges = [];
    (raw.edges || raw.links || []).forEach(function (e, i) {
      e = e || {};
      var from = textOf(e.from || e.source);
      var to = textOf(e.to || e.target);
      if (!from || !to) return;
      if (!byId[from]) addNode({ id: from, label: from }, nodes.length);
      if (!byId[to]) addNode({ id: to, label: to }, nodes.length);
      edges.push(Object.assign({}, e, {
        id: textOf(e.id || ('edge-' + i)),
        from: from,
        to: to,
        label: textOf(e.label || e.relation || ''),
        kind: textOf(e.kind || e.type || 'edge')
      }));
    });
    var center = textOf(raw.center || raw.centerId || '');
    if (!center || !byId[center]) {
      var subject = nodes.find(function (n) { return n.kind === 'subject' || n.kind === 'center'; });
      center = subject ? subject.id : (nodes[0] ? nodes[0].id : '');
    }
    return { kind: raw.kind || 'network', center: center, nodes: nodes, edges: edges, stats: raw.stats || { nodes: nodes.length, edges: edges.length } };
  }

  function themeForNetwork() {
    var rs = getComputedStyle(document.documentElement);
    function tok(name, fb) { var v = (rs.getPropertyValue(name) || '').trim(); return v || fb; }
    return {
      surface: tok('--bg-surface', tok('--surface', '#ffffff')),
      card: tok('--bg-card', tok('--surface', '#ffffff')),
      border: tok('--border', '#d0d0d0'),
      text: tok('--text', '#1a1a1a'),
      muted: tok('--text-muted', '#777777'),
      accent: tok('--accent', '#6c5ce7'),
      success: tok('--success', '#27ae60'),
      warning: tok('--warning', '#f39c12'),
      danger: tok('--danger', '#c0392b')
    };
  }

  function kindColor(kind, C) {
    switch ((kind || '').toLowerCase()) {
      case 'subject': case 'center': return C.accent;
      case 'condition': return C.success;
      case 'lever': return C.warning;
      case 'story': return C.danger;
      case 'unknown': return C.muted;
      case 'relation': return C.accent;
      default: return C.muted;
    }
  }

  function edgeColor(kind, C) {
    switch ((kind || '').toLowerCase()) {
      case 'lever': return C.warning;
      case 'story': return C.danger;
      case 'unknown': return C.muted;
      case 'dependency': return C.accent;
      case 'condition': return C.success;
      default: return C.muted;
    }
  }

  function finiteNumber(v) {
    var n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function ringLayout(nodes, centerId, cfg) {
    var W = cfg.width, H = cfg.height;
    var cx = W / 2, cy = H / 2;
    var positions = {};
    var centerNode = nodes.find(function (n) { return n.id === centerId; }) || nodes[0];
    if (centerNode) positions[centerNode.id] = { x: cx, y: cy };

    var fixed = [];
    var free = [];
    nodes.forEach(function (n) {
      if (n.id === centerId) return;
      var x = finiteNumber(n.x != null ? n.x : (n.pos && n.pos.x));
      var y = finiteNumber(n.y != null ? n.y : (n.pos && n.pos.y));
      if (x != null && y != null) fixed.push({ node: n, x: x, y: y });
      else free.push(n);
    });
    fixed.forEach(function (p) { positions[p.node.id] = { x: p.x, y: p.y }; });

    var inner = free.filter(function (n) { return ['condition', 'relation', 'node'].indexOf((n.kind || '').toLowerCase()) >= 0; });
    var outer = free.filter(function (n) { return inner.indexOf(n) < 0; });
    if (!inner.length) { inner = outer; outer = []; }

    function place(items, rx, ry, offset) {
      var n = items.length;
      items.forEach(function (node, i) {
        var angle = offset + (Math.PI * 2 * i / Math.max(1, n));
        var x = Math.max(46, Math.min(W - 46, cx + Math.cos(angle) * rx));
        var y = Math.max(42, Math.min(H - 42, cy + Math.sin(angle) * ry));
        positions[node.id] = { x: x, y: y };
      });
    }
    place(inner, Math.max(150, W * 0.27), Math.max(86, H * 0.29), -Math.PI / 2);
    place(outer, Math.max(210, W * 0.38), Math.max(128, H * 0.39), -Math.PI / 2 + 0.38);
    return positions;
  }

  function layoutNetwork(raw, opts) {
    var graph = normaliseNetwork(raw);
    var cfg = Object.assign({}, NET_DEF, opts || {});
    var positions = ringLayout(graph.nodes, graph.center, cfg);
    return { graph: graph, positions: positions, cfg: cfg };
  }

  function splitLabel(label, maxChars, maxLines) {
    label = textOf(label);
    if (!label) return [''];
    maxChars = maxChars || 18;
    maxLines = maxLines || 2;
    var words = label.indexOf(' ') >= 0 ? label.split(/\s+/) : label.split('');
    var lines = [''];
    words.forEach(function (w) {
      var last = lines[lines.length - 1];
      var next = last ? (last + (label.indexOf(' ') >= 0 ? ' ' : '') + w) : w;
      if (next.length <= maxChars || !last) lines[lines.length - 1] = next;
      else if (lines.length < maxLines) lines.push(w);
      else lines[lines.length - 1] += ' ' + w;
    });
    if (lines.length > maxLines) lines = lines.slice(0, maxLines);
    var lastLine = lines[lines.length - 1];
    if (lastLine.length > maxChars) lines[lines.length - 1] = lastLine.slice(0, Math.max(1, maxChars - 1)) + '...';
    return lines;
  }

  function appendNetworkLabel(group, label, p, center, radius, isCenter) {
    var dx = p.x - center.x;
    var dy = p.y - center.y;
    var anchor = dx >= 0 ? 'start' : 'end';
    var x = p.x + (dx >= 0 ? radius + 8 : -radius - 8);
    var y = p.y - 2;
    if (isCenter || Math.abs(dx) < 44) {
      anchor = 'middle';
      x = p.x;
      y = p.y + (dy > 0 ? radius + 16 : -radius - 10);
    }
    var text = svgEl('text', { class: 'eos-network-label', x: x, y: y, 'text-anchor': anchor });
    splitLabel(label, isCenter ? 24 : 18, 2).forEach(function (line, i) {
      var tspan = svgEl('tspan', { x: x, dy: i ? 13 : 0 });
      tspan.textContent = line;
      text.appendChild(tspan);
    });
    group.appendChild(text);
  }

  function nodeMetaHtml(node) {
    var bits = [node.kind, node.type, node.role, node.changeable].filter(function (x) { return textOf(x); });
    if (!bits.length) return '';
    return '<div class="eos-network-meta">' + bits.map(function (x) { return '<span>' + htmlEsc(x) + '</span>'; }).join('') + '</div>';
  }

  function renderNetwork(target, raw, opts) {
    var container = resolveTarget(target);
    if (!container) return null;
    ensureNetworkStyles();
    opts = opts || {};
    var width = opts.width || Math.max(560, container.clientWidth || NET_DEF.width);
    var height = opts.height || Math.max(opts.minHeight || NET_DEF.minHeight, Math.min(520, Math.round(width * 0.48)));
    var L = layoutNetwork(raw, Object.assign({}, opts, { width: width, height: height }));
    var graph = L.graph, positions = L.positions, cfg = L.cfg;
    var C = themeForNetwork();
    var uid = 'eos-network-arrow-' + (++NETWORK_SEQ);
    var showEdgeLabels = opts.edgeLabels !== false && width >= 640 && graph.edges.length <= 18;
    container.classList.add('eos-network-graph');
    container.classList.toggle('is-compact', width < 640);
    container.innerHTML = '';
    if (!graph.nodes.length) {
      container.innerHTML = '<div class="eos-network-canvas"><div style="padding:24px;color:var(--text-muted)">No graph data.</div></div>';
      return { graph: graph, select: function () {} };
    }

    var canvas = document.createElement('div');
    canvas.className = 'eos-network-canvas';
    var svg = svgEl('svg', { class: 'eos-network-svg', viewBox: '0 0 ' + cfg.width + ' ' + cfg.height, role: 'img', 'aria-label': opts.title || 'Network graph' });
    var defs = svgEl('defs', {});
    var marker = svgEl('marker', { id: uid, markerWidth: 8, markerHeight: 8, refX: 7, refY: 3, orient: 'auto' });
    marker.appendChild(svgEl('path', { d: 'M0,0 L7,3 L0,6 Z', fill: C.muted }));
    defs.appendChild(marker);
    svg.appendChild(defs);

    var edgeLayer = svgEl('g', { class: 'eos-network-edges' });
    graph.edges.forEach(function (e) {
      var a = positions[e.from], b = positions[e.to];
      if (!a || !b) return;
      var color = edgeColor(e.kind, C);
      edgeLayer.appendChild(svgEl('line', {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        stroke: color, 'stroke-width': e.weight || 1.4, opacity: 0.58,
        'marker-end': 'url(#' + uid + ')'
      }));
      if (e.label && showEdgeLabels) {
        edgeLayer.appendChild(svgText((a.x + b.x) / 2, (a.y + b.y) / 2 - 4, e.label, {
          class: 'eos-network-edge-label', 'font-size': 10
        }));
      }
    });
    svg.appendChild(edgeLayer);

    var nodeLayer = svgEl('g', { class: 'eos-network-nodes' });
    var centerP = positions[graph.center] || { x: cfg.width / 2, y: cfg.height / 2 };
    graph.nodes.forEach(function (n) {
      var p = positions[n.id]; if (!p) return;
      var isCenter = n.id === graph.center;
      var r = isCenter ? cfg.centerR : cfg.nodeR;
      var color = n.color || kindColor(n.kind, C);
      var g = svgEl('g', { class: 'eos-network-node', tabindex: 0, role: 'button', 'data-node-id': n.id, 'aria-label': n.label });
      g.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: r, fill: C.card, stroke: color, 'stroke-width': isCenter ? 2.5 : 1.8 }));
      appendNetworkLabel(g, n.label, p, centerP, r, isCenter);
      nodeLayer.appendChild(g);
    });
    svg.appendChild(nodeLayer);
    canvas.appendChild(svg);
    container.appendChild(canvas);

    var inspector = null;
    if (opts.inspector !== false) {
      inspector = document.createElement('aside');
      inspector.className = 'eos-network-inspector';
      container.appendChild(inspector);
    }

    function byId(id) { return graph.nodes.find(function (n) { return n.id === id; }) || null; }
    function setInspector(node) {
      if (!inspector) return;
      if (!node) {
        inspector.innerHTML = '<h4>Network</h4><p>Select a node to inspect it.</p>';
        return;
      }
      inspector.innerHTML = '<h4>' + htmlEsc(node.label || node.id) + '</h4>' +
        nodeMetaHtml(node) +
        (node.description ? '<p>' + htmlEsc(node.description) + '</p>' : '');
    }
    function select(id) {
      var node = byId(id) || byId(graph.center) || graph.nodes[0];
      container._eosNetworkSelected = node ? node.id : '';
      Array.prototype.forEach.call(container.querySelectorAll('.eos-network-node'), function (el) {
        el.classList.toggle('is-selected', el.getAttribute('data-node-id') === container._eosNetworkSelected);
      });
      setInspector(node);
      if (node && typeof opts.onSelect === 'function') opts.onSelect(node, graph);
    }
    Array.prototype.forEach.call(container.querySelectorAll('.eos-network-node'), function (el) {
      el.addEventListener('click', function () { select(el.getAttribute('data-node-id')); });
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(el.getAttribute('data-node-id')); }
      });
    });
    select(opts.selectedId || container._eosNetworkSelected || graph.center);
    var api = { graph: graph, positions: positions, select: select, destroy: function () { container.innerHTML = ''; } };
    container._eosNetwork = api;
    return api;
  }

  window.EOS_GRAPH = {
    render: render,
    wireZoom: wireZoom,
    zoomBy: zoomBy,
    fit: fit,
    normaliseNetwork: normaliseNetwork,
    layoutNetwork: layoutNetwork,
    renderNetwork: renderNetwork
  };
})();
