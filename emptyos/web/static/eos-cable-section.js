// eos-cable-section.js — shared SVG renderers for cable engineering diagrams.
//
//   EOS_CABLE.crossSection(svgEl, spec) — concentric conductor/insulation
//     cross-section with a 1/r field-intensity gradient, leader-line
//     annotations, and a dimension strip.
//   EOS_CABLE.radialPlot(svgEl, spec)   — a radial line chart of one or more
//     1/r series across the wall (conductor surface → insulation surface).
//
// Both write SVG markup into svgEl.innerHTML and assume the consumer page
// provides the visual classes (cs-axis, cs-gridline, cs-tick, cs-axlabel,
// cs-curve, cs-dot, cs-guide, cs-leader, cs-anchor, cs-annot) and the
// `#cs-field` gradient target. First consumer: apps/cable-stress/. The class
// contract stays page-owned until a second consumer lands (CLAUDE.md rule 9),
// at which point the CSS graduates into a shared eos-cable-section.css.
//
// var()/color-mix() are emitted via style="" — they don't resolve in raw SVG
// presentation attributes.
(function (g) {
  g.EOS_CABLE = {
    // spec: {rc, R, probeR?, annotations:[{r, angleDeg, ly, head, sub}], dim?,
    //        conductorLabel?, cx?, cy?, maxR?, leaderX?}
    crossSection: function (svg, o) {
      var rc = o.rc, R = o.R;
      if (!(rc > 0) || !(R > rc)) { svg.innerHTML = ''; return; }
      var cx = o.cx || 175, cy = o.cy || 185, maxR = o.maxR || 145;
      var scale = maxR / R, rcPx = rc * scale, leaderX = o.leaderX || 330;
      var probeR = (o.probeR != null) ? o.probeR : null;
      var p = [];

      var stops = ['<stop offset="0%" style="stop-color:color-mix(in srgb, var(--accent) 86%, transparent)"/>'];
      for (var i = 0; i <= 7; i++) {
        var r = rc + (R - rc) * i / 7;
        var pct = Math.round(14 + (rc / r) * 72);
        stops.push('<stop offset="' + ((r / R) * 100).toFixed(1) + '%" style="stop-color:color-mix(in srgb, var(--accent) ' + pct + '%, transparent)"/>');
      }
      p.push('<defs><radialGradient id="cs-field" gradientUnits="userSpaceOnUse" cx="' + cx + '" cy="' + cy + '" r="' + maxR + '">' + stops.join('') + '</radialGradient></defs>');

      p.push('<circle cx="' + cx + '" cy="' + cy + '" r="' + maxR + '" style="fill:url(#cs-field);stroke:var(--border);stroke-width:1.5"/>');
      if (probeR != null && probeR >= rc && probeR <= R) {
        p.push('<circle cx="' + cx + '" cy="' + cy + '" r="' + (probeR * scale).toFixed(1) + '" style="fill:none;stroke:var(--text-dim);stroke-dasharray:4 3;stroke-width:1"/>');
      }
      p.push('<circle cx="' + cx + '" cy="' + cy + '" r="' + rcPx.toFixed(1) + '" style="fill:color-mix(in srgb, var(--text-dim) 55%, var(--bg-card));stroke:var(--text);stroke-width:1.5"/>');
      if (rcPx > 22) p.push('<text x="' + cx + '" y="' + (cy + 4) + '" class="cs-annot" text-anchor="middle">' + (o.conductorLabel || 'conductor') + '</text>');

      var d2r = Math.PI / 180;
      (o.annotations || []).forEach(function (a) {
        var rad = a.r * scale, t = a.angleDeg * d2r;
        var ax = cx + rad * Math.cos(t), ay = cy + rad * Math.sin(t);
        p.push('<polyline points="' + ax.toFixed(1) + ',' + ay.toFixed(1) + ' ' + leaderX + ',' + a.ly + ' ' + (leaderX + 14) + ',' + a.ly + '" class="cs-leader"/>');
        p.push('<circle cx="' + ax.toFixed(1) + '" cy="' + ay.toFixed(1) + '" r="2.5" class="cs-anchor"/>');
        p.push('<text x="' + (leaderX + 18) + '" y="' + (a.ly - 4) + '" class="cs-annot hd">' + a.head + '</text>');
        p.push('<text x="' + (leaderX + 18) + '" y="' + (a.ly + 10) + '" class="cs-annot">' + a.sub + '</text>');
      });

      if (o.dim) p.push('<text x="' + cx + '" y="' + (cy + maxR + 28) + '" class="cs-annot dim" text-anchor="middle">' + o.dim + '</text>');

      svg.innerHTML = p.join('');
    },

    // spec: {xmin, xmax, ln, series:[{V, peak, cls}], guideX?, xlabel?, ylabel?}
    // Each series value at radius r is V/(r·ln); peak (the value at xmin,
    // typically pre-rounded by the caller) sets the y-axis scale.
    radialPlot: function (svg, o) {
      var xmin = o.xmin, xmax = o.xmax, ln = o.ln, series = o.series || [];
      if (!(xmin > 0) || !(xmax > xmin)) { svg.innerHTML = ''; return; }
      var ymax = 0;
      series.forEach(function (s) { ymax = Math.max(ymax, s.peak); });
      if (!(ymax > 0)) { svg.innerHTML = ''; return; }
      ymax *= 1.08;
      var W = 600, H = 320, ml = 56, mr = 18, mt = 16, mb = 40;
      var plotW = W - ml - mr, plotH = H - mt - mb;
      function sx(r) { return ml + (r - xmin) / (xmax - xmin) * plotW; }
      function sy(e) { return mt + (1 - e / ymax) * plotH; }
      function val(s, r) { return s.V / (r * ln); }
      var p = [];

      var yt = 4;
      for (var i = 0; i <= yt; i++) {
        var ev = ymax * i / yt, py = sy(ev);
        p.push('<line x1="' + ml + '" y1="' + py.toFixed(1) + '" x2="' + (ml + plotW) + '" y2="' + py.toFixed(1) + '" class="cs-gridline"/>');
        p.push('<text x="' + (ml - 8) + '" y="' + (py + 3).toFixed(1) + '" class="cs-tick" text-anchor="end">' + ev.toFixed(1) + '</text>');
      }
      [xmin, (xmin + xmax) / 2, xmax].forEach(function (r) {
        p.push('<text x="' + sx(r).toFixed(1) + '" y="' + (mt + plotH + 16) + '" class="cs-tick" text-anchor="middle">' + r.toFixed(1) + '</text>');
      });
      p.push('<line x1="' + ml + '" y1="' + mt + '" x2="' + ml + '" y2="' + (mt + plotH) + '" class="cs-axis"/>');
      p.push('<line x1="' + ml + '" y1="' + (mt + plotH) + '" x2="' + (ml + plotW) + '" y2="' + (mt + plotH) + '" class="cs-axis"/>');

      series.forEach(function (s) {
        var N = 48, seg = [];
        for (var i = 0; i <= N; i++) {
          var r = xmin + (xmax - xmin) * i / N;
          seg.push((i ? 'L' : 'M') + sx(r).toFixed(1) + ' ' + sy(val(s, r)).toFixed(1));
        }
        p.push('<path d="' + seg.join(' ') + '" class="cs-curve ' + s.cls + '"/>');
        p.push('<circle cx="' + sx(xmin).toFixed(1) + '" cy="' + sy(val(s, xmin)).toFixed(1) + '" r="3.5" class="cs-dot ' + s.cls + '"/>');
        p.push('<circle cx="' + sx(xmax).toFixed(1) + '" cy="' + sy(val(s, xmax)).toFixed(1) + '" r="3.5" class="cs-dot ' + s.cls + '"/>');
      });

      if (o.guideX != null && o.guideX >= xmin && o.guideX <= xmax) {
        var gx = sx(o.guideX).toFixed(1);
        p.push('<line x1="' + gx + '" y1="' + mt + '" x2="' + gx + '" y2="' + (mt + plotH) + '" class="cs-guide"/>');
        series.forEach(function (s) {
          p.push('<circle cx="' + gx + '" cy="' + sy(val(s, o.guideX)).toFixed(1) + '" r="3.5" class="cs-dot ' + s.cls + '"/>');
        });
      }

      p.push('<text x="' + (ml + plotW / 2) + '" y="' + (H - 4) + '" class="cs-axlabel" text-anchor="middle">' + (o.xlabel || '') + '</text>');
      var ymid = mt + plotH / 2;
      p.push('<text x="14" y="' + ymid + '" class="cs-axlabel" text-anchor="middle" transform="rotate(-90 14 ' + ymid + ')">' + (o.ylabel || '') + '</text>');

      svg.innerHTML = p.join('');
    }
  };
})(typeof window !== 'undefined' ? window : globalThis);
