// EOS_UI.singleLineDiagram — auto-laid-out IEC single-line-diagram
// renderer. Reads nodes + edges from any topology shape and produces an
// SVG with proper symbols (busbar, breaker, generator, load, transformer).
//
// Substrate goal: reusable from apps/cables, apps/lines, apps/grid,
// apps/earthing — anywhere a network needs an SLD view of the same
// nodes/edges that already feed load-flow or short-circuit calcs.
//
// Usage:
//   EOS_UI.singleLineDiagram(container, {
//     nodes: [{id, label, kind, voltage_kv, p_load_kw, p_gen_kw, is_slack}],
//     edges: [{id, from_node, to_node, cable_id, length_m, lf_voltage_drop_pct}],
//     options: { width: 800, height: 600, showVoltages: true },
//   })  →  { exportSVG(), exportPNG(), root: <svg> }

(function (root) {
  if (!root.EOS_UI) root.EOS_UI = {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  // Vault frontmatter returns booleans as strings, so a literal "false"
  // is truthy in JS and quietly poisons every is_slack check downstream
  // (every node becomes a slack root → BFS layout collapses to one row).
  // Coerce here, once, so callers can pass raw vault rows.
  function _bool(v) {
    if (v === true) return true;
    if (v === false || v == null) return false;
    var s = String(v).trim().toLowerCase();
    return s === 'true' || s === '1' || s === 'yes';
  }
  function _num(v) {
    if (v == null || v === '') return null;
    var n = +v;
    return isFinite(n) ? n : null;
  }
  function _normalizeNodes(nodes) {
    return (nodes || []).map(function (n) {
      var c = Object.assign({}, n);
      c.is_slack = _bool(n.is_slack);
      if (n.voltage_kv != null) c.voltage_kv = _num(n.voltage_kv);
      if (n.lf_voltage_kv != null) c.lf_voltage_kv = _num(n.lf_voltage_kv);
      if (n.p_load_kw != null) c.p_load_kw = _num(n.p_load_kw);
      if (n.p_gen_kw != null) c.p_gen_kw = _num(n.p_gen_kw);
      return c;
    });
  }
  function _normalizeEdges(edges) {
    return (edges || []).map(function (e) {
      var c = Object.assign({}, e);
      if (e.length_m != null) c.length_m = _num(e.length_m);
      if (e.lf_voltage_drop_pct != null) c.lf_voltage_drop_pct = _num(e.lf_voltage_drop_pct);
      return c;
    });
  }

  // BFS layers from any slack/substation/source node. Returns
  // [[nodeId, ...], ...] top-down so we can render row by row.
  function layerise(nodes, edges) {
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    var roots = nodes.filter(function (n) {
      return n.is_slack || n.kind === 'substation' || n.kind === 'source';
    }).map(function (n) { return n.id; });
    if (!roots.length && nodes.length) roots = [nodes[0].id];

    var visited = {}, layers = [];
    var frontier = roots.slice();
    while (frontier.length) {
      layers.push(frontier);
      frontier.forEach(function (id) { visited[id] = true; });
      var next = [];
      frontier.forEach(function (id) {
        edges.forEach(function (e) {
          var other = e.from_node === id ? e.to_node : (e.to_node === id ? e.from_node : null);
          if (other && !visited[other] && next.indexOf(other) === -1) next.push(other);
        });
      });
      frontier = next;
    }
    // Anything disconnected ends up in a final layer so it's still drawn.
    var orphans = nodes.filter(function (n) { return !visited[n.id]; }).map(function (n) { return n.id; });
    if (orphans.length) layers.push(orphans);
    return layers;
  }

  // Symbol-by-kind. Each returns SVG string anchored at (x, y).
  function symbol(kind, x, y, n) {
    if (kind === 'substation' || n.is_slack) {
      // Bus-bar with infeed: thick horizontal line + 3 feeders down
      return '<g class="sld-sym-substation">' +
        '<line x1="' + (x - 36) + '" y1="' + (y - 14) + '" x2="' + (x + 36) + '" y2="' + (y - 14) + '" stroke="currentColor" stroke-width="3"/>' +
        '<line x1="' + (x - 18) + '" y1="' + (y - 14) + '" x2="' + (x - 18) + '" y2="' + (y - 24) + '" stroke="currentColor" stroke-width="1.5"/>' +
        '<line x1="' + x + '" y1="' + (y - 14) + '" x2="' + x + '" y2="' + (y - 24) + '" stroke="currentColor" stroke-width="1.5"/>' +
        '<line x1="' + (x + 18) + '" y1="' + (y - 14) + '" x2="' + (x + 18) + '" y2="' + (y - 24) + '" stroke="currentColor" stroke-width="1.5"/>' +
      '</g>';
    }
    if (kind === 'turbine') {
      // Circle with G inside (generator)
      return '<g class="sld-sym-gen">' +
        '<circle cx="' + x + '" cy="' + y + '" r="14" fill="white" stroke="currentColor" stroke-width="1.5"/>' +
        '<text x="' + x + '" y="' + (y + 4) + '" text-anchor="middle" font-size="11" font-weight="600" fill="currentColor">G</text>' +
      '</g>';
    }
    if (kind === 'load') {
      // Triangle pointing down (load symbol)
      return '<g class="sld-sym-load">' +
        '<polygon points="' + (x - 12) + ',' + (y - 12) + ' ' + (x + 12) + ',' + (y - 12) + ' ' + x + ',' + (y + 8) +
          '" fill="white" stroke="currentColor" stroke-width="1.5"/>' +
      '</g>';
    }
    if (kind === 'bus' || kind === 'rmu') {
      // Short bus-bar with switch
      return '<g class="sld-sym-bus">' +
        '<line x1="' + (x - 24) + '" y1="' + y + '" x2="' + (x + 24) + '" y2="' + y + '" stroke="currentColor" stroke-width="2.5"/>' +
        '<rect x="' + (x - 6) + '" y="' + (y - 4) + '" width="12" height="8" fill="white" stroke="currentColor" stroke-width="1.2"/>' +
      '</g>';
    }
    if (kind === 'junction') {
      // Small filled dot
      return '<circle cx="' + x + '" cy="' + y + '" r="4" fill="currentColor"/>';
    }
    if (kind === 'source' || kind === 'generator') {
      // Circle with sine-wave inside
      return '<g class="sld-sym-src">' +
        '<circle cx="' + x + '" cy="' + y + '" r="14" fill="white" stroke="currentColor" stroke-width="1.5"/>' +
        '<path d="M' + (x - 8) + ',' + y + ' Q' + (x - 4) + ',' + (y - 6) + ' ' + x + ',' + y +
          ' T' + (x + 8) + ',' + y + '" fill="none" stroke="currentColor" stroke-width="1.2"/>' +
      '</g>';
    }
    // Default: rounded rect with kind initial
    return '<g class="sld-sym-default">' +
      '<rect x="' + (x - 16) + '" y="' + (y - 10) + '" width="32" height="20" rx="3" fill="white" stroke="currentColor" stroke-width="1.5"/>' +
      '<text x="' + x + '" y="' + (y + 4) + '" text-anchor="middle" font-size="10" fill="currentColor">' + esc((kind || '?').slice(0, 3)) + '</text>' +
    '</g>';
  }

  function _build(nodes, edges, opts) {
    var W = (opts && opts.width)  || 900;
    var H = (opts && opts.height) || 700;
    var showV = !(opts && opts.showVoltages === false);

    var layers = layerise(nodes, edges);
    var posById = {};
    var marginY = 70;
    var rowH = layers.length > 1 ? (H - 2 * marginY) / Math.max(layers.length - 1, 1) : 0;
    layers.forEach(function (row, li) {
      var n = row.length;
      var dx = W / (n + 1);
      row.forEach(function (id, i) {
        posById[id] = { x: Math.round(dx * (i + 1)), y: Math.round(marginY + li * rowH) };
      });
    });

    // Edges first (so symbols overlay)
    var edgeSvg = edges.map(function (e) {
      var a = posById[e.from_node], b = posById[e.to_node];
      if (!a || !b) return '';
      // Right-angle elbow: drop from a, run horizontally to under b, drop to b
      var midY = (a.y + b.y) / 2;
      var d = 'M' + a.x + ',' + a.y + ' L' + a.x + ',' + midY + ' L' + b.x + ',' + midY + ' L' + b.x + ',' + b.y;
      var stroke = '#444';
      if (e.lf_voltage_drop_pct != null) {
        var vd = +e.lf_voltage_drop_pct;
        stroke = vd > 8 ? '#ef4444' : (vd > 5 ? '#f59e0b' : (vd > 2 ? '#fbbf24' : '#10b981'));
      }
      var label = '';
      if (e.cable_id) label = e.cable_id;
      else if (e.length_m) label = (+e.length_m).toFixed(0) + ' m';
      var labelSvg = label
        ? '<text x="' + ((a.x + b.x) / 2) + '" y="' + (midY - 4) + '" text-anchor="middle" font-size="9" fill="#666">' + esc(label) + '</text>'
        : '';
      return '<path d="' + d + '" fill="none" stroke="' + stroke + '" stroke-width="1.5"/>' + labelSvg;
    }).join('');

    // Nodes
    var nodeSvg = nodes.map(function (n) {
      var p = posById[n.id];
      if (!p) return '';
      var sym = symbol(n.kind || 'bus', p.x, p.y, n);
      var lbl = n.label || n.id;
      var vlabel = '';
      if (showV && n.voltage_kv != null) vlabel = n.voltage_kv + ' kV';
      else if (showV && n.lf_voltage_kv != null) vlabel = (+n.lf_voltage_kv).toFixed(2) + ' kV';
      var p_kw = '';
      if (n.p_gen_kw != null && +n.p_gen_kw > 0) p_kw = (+n.p_gen_kw / 1000).toFixed(1) + ' MW';
      else if (n.p_load_kw != null && +n.p_load_kw > 0) p_kw = (+n.p_load_kw / 1000).toFixed(1) + ' MW';
      return '<g class="sld-node">' +
        sym +
        '<text x="' + p.x + '" y="' + (p.y - 30) + '" text-anchor="middle" font-size="11" font-weight="600" fill="currentColor">' + esc(lbl) + '</text>' +
        (vlabel ? '<text x="' + p.x + '" y="' + (p.y + 32) + '" text-anchor="middle" font-size="9" fill="#666">' + esc(vlabel) + '</text>' : '') +
        (p_kw ?  '<text x="' + p.x + '" y="' + (p.y + 44) + '" text-anchor="middle" font-size="9" fill="#666">' + esc(p_kw) + '</text>' : '') +
      '</g>';
    }).join('');

    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" style="background:white;color:#222;font-family:system-ui,sans-serif">' +
      edgeSvg + nodeSvg + '</svg>';
  }

  // Mermaid-graph emitter. Same nodes + edges, different render path —
  // produces text that can be copied into a markdown doc, version-
  // controlled, or rendered by mermaid.js. Shape per kind tries to
  // mirror the SVG symbols (trapezoid for substation, circle for
  // generators, rect for bus, ((·)) for junctions).
  function _safeId(id) {
    // Mermaid node ids must be alphanumeric/underscore — strip everything
    // else and prefix with `n` so numeric-leading ids stay valid.
    return 'n_' + String(id).replace(/[^A-Za-z0-9]+/g, '_');
  }
  function _mermaidShape(n) {
    var kw = n.kind || 'bus';
    var label = n.label || n.id;
    var v = n.voltage_kv != null ? ' (' + n.voltage_kv + 'kV)' : '';
    var p = '';
    if (n.p_gen_kw != null && +n.p_gen_kw > 0) p = '<br/>' + (+n.p_gen_kw / 1000).toFixed(1) + ' MW gen';
    else if (n.p_load_kw != null && +n.p_load_kw > 0) p = '<br/>' + (+n.p_load_kw / 1000).toFixed(1) + ' MW load';
    var text = '"' + (label + v + p).replace(/"/g, '\\"') + '"';
    if (kw === 'substation' || n.is_slack) return _safeId(n.id) + '[/' + text + '\\]';
    if (kw === 'turbine' || kw === 'source' || kw === 'generator') return _safeId(n.id) + '((' + text + '))';
    if (kw === 'junction') return _safeId(n.id) + '((' + (label.length > 6 ? label.slice(0, 6) + '…' : label) + '))';
    if (kw === 'load') return _safeId(n.id) + '[/' + text + '/]';
    return _safeId(n.id) + '[' + text + ']';
  }
  function toMermaid(nodes, edges, opts) {
    var dir = (opts && opts.direction) || 'TD';
    var lines = ['graph ' + dir];
    nodes.forEach(function (n) { lines.push('  ' + _mermaidShape(n)); });
    edges.forEach(function (e) {
      var label = '';
      if (e.cable_id) label = e.cable_id;
      else if (e.length_m) label = (+e.length_m).toFixed(0) + ' m';
      var arrow = label
        ? '-- "' + label.replace(/"/g, '\\"') + '" -->'
        : '-->';
      lines.push('  ' + _safeId(e.from_node) + ' ' + arrow + ' ' + _safeId(e.to_node));
    });
    // Class colouring by V-drop for converged load-flow runs.
    var colored = edges.filter(function (e) { return e.lf_voltage_drop_pct != null; });
    if (colored.length) {
      lines.push('  classDef green stroke:#10b981,stroke-width:2px;');
      lines.push('  classDef amber stroke:#f59e0b,stroke-width:2px;');
      lines.push('  classDef red   stroke:#ef4444,stroke-width:3px;');
    }
    return lines.join('\n');
  }

  EOS_UI.singleLineDiagram = function (container, args) {
    var nodes = _normalizeNodes((args && args.nodes) || []);
    var edges = _normalizeEdges((args && args.edges) || []);
    var opts = (args && args.options) || {};
    var html = _build(nodes, edges, opts);
    container.innerHTML = html;
    var svg = container.querySelector('svg');
    return {
      root: svg,
      toMermaid: function (extra) { return toMermaid(nodes, edges, Object.assign({}, opts, extra || {})); },
      exportSVG: function () {
        var s = new XMLSerializer().serializeToString(svg);
        var blob = new Blob([s], { type: 'image/svg+xml;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url; a.download = (opts.filename || 'sld') + '.svg';
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      },
      exportPNG: async function () {
        var s = new XMLSerializer().serializeToString(svg);
        var blob = new Blob([s], { type: 'image/svg+xml;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var img = new Image();
        await new Promise(function (resolve, reject) {
          img.onload = resolve; img.onerror = reject; img.src = url;
        });
        var W = (opts.width || 900) * 2, H = (opts.height || 700) * 2;
        var canvas = document.createElement('canvas');
        canvas.width = W; canvas.height = H;
        var ctx = canvas.getContext('2d');
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, H);
        ctx.drawImage(img, 0, 0, W, H);
        URL.revokeObjectURL(url);
        canvas.toBlob(function (b) {
          var u = URL.createObjectURL(b);
          var a = document.createElement('a');
          a.href = u; a.download = (opts.filename || 'sld') + '.png';
          document.body.appendChild(a); a.click(); document.body.removeChild(a);
          setTimeout(function () { URL.revokeObjectURL(u); }, 1000);
        }, 'image/png');
      },
    };
  };
})(window);
