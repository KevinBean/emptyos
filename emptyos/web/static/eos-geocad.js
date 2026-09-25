/* EOS_GEOCAD — Leaflet-Geoman drawing layer for georeferenced editing.
 *
 * Two ways to use it.
 *
 *   1) Standalone editor (creates its own map):
 *
 *        <div id="editor" style="height:600px"></div>
 *        <script src="/static/eos-map.js"></script>
 *        <script src="/static/eos-geocad.js"></script>
 *        <link rel="stylesheet" href="/static/eos-geocad.css">
 *
 *        var ed = EOS_GEOCAD.mount('#editor', {
 *          layerId: 'cables-22kv',
 *          basemap: 'both',         // osm | aerial | both
 *          readonly: false,
 *          onFeatureChange: function(fc){ ... },  // full FeatureCollection
 *        });
 *        ed.ready.then(function(){ ed.fitBounds(); });
 *
 *   2) Attach to an existing EOS_MAP (e.g. cables in geo mode):
 *
 *        var map = EOS_MAP.create('#map', {center:[lat,lng], zoom:15});
 *        var draw = EOS_GEOCAD.attach(map, {
 *          layerId: 'cables-22kv',
 *          onFeatureChange: function(fc){ ... },
 *        });
 *
 * Persistence is the caller's responsibility — wire `onFeatureChange` to
 * POST /geo-cad/api/layers/{id}/geojson. The shim never auto-saves.
 *
 * Coordinate convention: GeoJSON [lon, lat] (RFC 7946). Leaflet uses
 * [lat, lng]; Geoman handles the conversion via toGeoJSON().
 *
 * ed.addPoint(lat, lon, props) adds one Point feature programmatically —
 * e.g. from EOS.geocode()'s address lookup — without a Geoman draw
 * interaction. Fires onFeatureChange like a hand-drawn feature would.
 */
(function() {
  // Leaflet-Geoman free, MIT-licensed. SRI deferred — pin on first
  // production deploy; OK to ship without for now since CDN is unpkg
  // and the version is exact.
  var GEOMAN_VERSION = '2.19.2';
  var GEOMAN_CSS = 'https://unpkg.com/@geoman-io/leaflet-geoman-free@' + GEOMAN_VERSION + '/dist/leaflet-geoman.css';
  var GEOMAN_JS  = 'https://unpkg.com/@geoman-io/leaflet-geoman-free@' + GEOMAN_VERSION + '/dist/leaflet-geoman.min.js';

  var _loading = null;
  function ensureGeoman() {
    if (window.L && window.L.PM) return Promise.resolve(window.L);
    if (_loading) return _loading;

    if (!window.EOS_MAP || !window.EOS_MAP.ensureLeaflet) {
      return Promise.reject(new Error('EOS_GEOCAD: EOS_MAP not loaded; include /static/eos-map.js first.'));
    }

    _loading = window.EOS_MAP.ensureLeaflet().then(function(L) {
      return new Promise(function(resolve, reject) {
        if (!document.querySelector('link[data-eos-geoman]')) {
          var link = document.createElement('link');
          link.rel = 'stylesheet';
          link.href = GEOMAN_CSS;
          link.crossOrigin = '';
          link.setAttribute('data-eos-geoman', '');
          document.head.appendChild(link);
        }
        if (window.L && window.L.PM) { resolve(L); return; }
        var s = document.createElement('script');
        s.src = GEOMAN_JS;
        s.crossOrigin = '';
        s.onload = function() { resolve(L); };
        s.onerror = function() { reject(new Error('Failed to load Leaflet-Geoman')); };
        document.head.appendChild(s);
      });
    });
    return _loading;
  }

  function _featureCollection(geoLayer) {
    if (!geoLayer || !geoLayer.toGeoJSON) {
      return {type: 'FeatureCollection', features: []};
    }
    var data = geoLayer.toGeoJSON();
    if (!data) return {type: 'FeatureCollection', features: []};
    if (data.type === 'FeatureCollection') return data;
    if (data.type === 'Feature') return {type: 'FeatureCollection', features: [data]};
    return {type: 'FeatureCollection', features: []};
  }

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // A property value is "link-shaped" if it's a same-origin in-app URL —
  // an absolute path ("/inspection-queue/packet/w-123") or http(s) URL.
  // Cross-app back-links (work_item_url, *_url, href, link, url) ride this.
  function _isLinkUrl(v) {
    if (typeof v !== 'string') return false;
    // Same-origin in-app links only. A leading single slash is an absolute
    // path; reject protocol-relative "//host" (it would navigate the top frame
    // off-origin — open-redirect / phishing). An http(s) URL is allowed only
    // when it resolves to this exact origin.
    if (/^\/(?!\/)/.test(v)) return true;
    if (/^https?:\/\//i.test(v)) {
      try {
        return new URL(v).origin === location.origin;
      } catch (e) {
        return false;
      }
    }
    return false;
  }

  function _looksLikeLinkKey(k) {
    return /(^|_)(url|href|link)$/i.test(k) || k === 'work_item_url';
  }

  // Render a single property value. Link-shaped values under link-shaped
  // keys become inline anchors; everything else is escaped plain text
  // (previously injected raw — now escaped, a strict safety improvement).
  function _propValueHtml(k, v) {
    if (_looksLikeLinkKey(k) && _isLinkUrl(v)) {
      return '<a href="' + _esc(v) + '" target="_top" rel="noopener">' + _esc(v) + '</a>';
    }
    return _esc(v);
  }

  // Pull the best back-link out of a feature's properties and render a
  // prominent "Open" affordance. Priority: work_item_url (the inspection
  // queue cross-link), then any *_url / href / link / url key.
  function _deepLinkHtml(p) {
    var url = null, label = 'Open';
    if (_isLinkUrl(p.work_item_url)) { url = p.work_item_url; label = 'Open work item'; }
    if (!url) {
      var keys = Object.keys(p).filter(function(k) {
        return k.indexOf('_') !== 0 && _looksLikeLinkKey(k) && _isLinkUrl(p[k]);
      });
      if (keys.length) { url = p[keys[0]]; }
    }
    if (!url) return '';
    return '<a class="eos-geocad-popup-link" href="' + _esc(url) +
           '" target="_top" rel="noopener">' + _esc(label) + ' &rarr;</a>';
  }

  function _attach(L, map, opts) {
    opts = opts || {};
    var self = {
      _L: L, _map: map, _layer: null,
      _readonly: !!opts.readonly,
      _onChange: opts.onFeatureChange || function() {},
      _layerId: opts.layerId || null,
      ready: null,
    };

    self._layer = L.geoJSON(null, {
      style: function(feature) {
        var s = (feature && feature.properties && feature.properties._style) || opts.style || {};
        return Object.assign({color: '#4cc7f5', weight: 3, opacity: 0.9, fillOpacity: 0.25}, s);
      },
      pointToLayer: function(feature, latlng) {
        return L.circleMarker(latlng, {radius: 6, color: '#f0c040', weight: 2, fillOpacity: 0.6});
      },
      onEachFeature: function(feature, layer) {
        var p = (feature && feature.properties) || {};
        var entries = Object.keys(p)
          .filter(function(k){ return k.indexOf('_') !== 0; })
          .map(function(k){ return '<b>' + _esc(k) + ':</b> ' + _propValueHtml(k, p[k]); });
        // Deep-link: any property carrying an in-app URL (e.g. a cross-app
        // back-link like work_item_url -> /inspection-queue/packet/<id>) gets
        // a dedicated "Open" affordance at the bottom of the popup so the
        // feature is a one-click jump back to its owning record.
        var linkHtml = _deepLinkHtml(p);
        if (linkHtml) entries.push(linkHtml);
        if (entries.length) layer.bindPopup(entries.join('<br>'));
      },
    }).addTo(map);

    function fireChange() {
      try { self._onChange(_featureCollection(self._layer)); }
      catch (e) { /* caller bug, swallow */ }
    }

    function _wireGeomanIfDrawingMode() {
      if (self._readonly) return;
      // Geoman global controls
      map.pm.addControls({
        position: 'topright',
        drawCircle: false,
        drawCircleMarker: false,
        drawText: false,
        editControls: true,
        cutPolygon: true,
        rotateMode: false,
      });
      map.pm.setGlobalOptions({
        snappable: true,
        snapDistance: opts.snapTolerancePx || 20,
      });

      map.on('pm:create', function(ev) {
        var lyr = ev.layer;
        // Move newly-drawn layer into our managed geoLayer
        self._layer.addLayer(lyr);
        try { map.removeLayer(lyr); } catch (_) {}
        // Geoman editing on the canonical layer instance
        if (lyr.pm) lyr.pm.enable({allowSelfIntersection: false});
        fireChange();
      });
      map.on('pm:remove', function(/*ev*/) { fireChange(); });
      // Edits on existing layers
      self._layer.on('pm:edit', fireChange);
      self._layer.on('pm:dragend', fireChange);
      self._layer.on('pm:cut', fireChange);
    }

    self.loadGeoJSON = function(fc) {
      self._layer.clearLayers();
      if (fc && fc.type === 'FeatureCollection' && fc.features) {
        self._layer.addData(fc);
      }
      return self;
    };

    self.exportGeoJSON = function() {
      return _featureCollection(self._layer);
    };

    // geo-cad-no-geocode-integration: add one point feature programmatically
    // (e.g. from EOS.geocode's address lookup) without going through
    // Geoman's draw-tool interaction. lat/lon are plain WGS84 decimal
    // degrees; internally GeoJSON wants [lon, lat] (RFC 7946) — callers
    // never have to think about the axis order.
    self.addPoint = function(lat, lon, props) {
      var feature = {
        type: 'Feature',
        properties: Object.assign({}, props || {}),
        geometry: {type: 'Point', coordinates: [lon, lat]},
      };
      self._layer.addData(feature);
      fireChange();
      return self;
    };

    self.fitBounds = function(opt) {
      try {
        var b = self._layer.getBounds();
        if (b && b.isValid()) map.fitBounds(b, opt || {padding: [30, 30]});
      } catch (_) {}
      return self;
    };

    self.setReadonly = function(ro) {
      self._readonly = !!ro;
      if (self._readonly) {
        try { map.pm.removeControls(); } catch (_) {}
      } else {
        _wireGeomanIfDrawingMode();
      }
    };

    self.destroy = function() {
      try { map.pm && map.pm.removeControls(); } catch (_) {}
      try { map.removeLayer(self._layer); } catch (_) {}
      self._layer = null;
    };

    self.loadFromServer = function() {
      if (!self._layerId) return Promise.resolve(null);
      return fetch('/geo-cad/api/layers/' + encodeURIComponent(self._layerId) + '/geojson')
        .then(function(r){ return r.ok ? r.json() : null; })
        .then(function(fc){ if (fc) self.loadGeoJSON(fc); return fc; });
    };

    self.saveToServer = function() {
      if (!self._layerId) return Promise.resolve({error: 'no layerId bound'});
      var fc = self.exportGeoJSON();
      return fetch('/geo-cad/api/layers/' + encodeURIComponent(self._layerId) + '/geojson', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(fc),
      }).then(function(r){ return r.json(); });
    };

    _wireGeomanIfDrawingMode();
    return self;
  }

  function attach(eosMap, opts) {
    return ensureGeoman().then(function(L) {
      return eosMap.ready.then(function() {
        return _attach(L, eosMap.map(), opts || {});
      });
    });
  }

  function mount(container, opts) {
    opts = opts || {};
    var basemap = opts.basemap || 'both';
    // Defer EOS_MAP creation until Geoman is ready so the user sees a
    // single coherent paint instead of a basemap jumping when controls appear.
    var eosMapReady = ensureGeoman().then(function() {
      return window.EOS_MAP.create(container, {
        center: opts.center || [0, 0],
        zoom: opts.zoom != null ? opts.zoom : 2,
        tiles: basemap,
      });
    });

    var instance = {
      ready: null,
      _eosMap: null,
      _attached: null,
    };

    instance.ready = eosMapReady.then(function(map) {
      instance._eosMap = map;
      return map.ready.then(function() {
        return ensureGeoman().then(function(L) {
          var attached = _attach(L, map.map(), opts);
          instance._attached = attached;
          if (opts.layerId) {
            return attached.loadFromServer().then(function() {
              attached.fitBounds();
              return instance;
            });
          }
          return instance;
        });
      });
    });

    instance.exportGeoJSON = function() {
      return instance._attached ? instance._attached.exportGeoJSON()
                                : {type: 'FeatureCollection', features: []};
    };
    instance.loadGeoJSON = function(fc) {
      if (instance._attached) instance._attached.loadGeoJSON(fc);
      return instance;
    };
    instance.fitBounds = function(opt) {
      if (instance._attached) instance._attached.fitBounds(opt);
      return instance;
    };
    instance.addPoint = function(lat, lon, props) {
      if (instance._attached) instance._attached.addPoint(lat, lon, props);
      return instance;
    };
    instance.saveToServer = function() {
      return instance._attached ? instance._attached.saveToServer()
                                : Promise.resolve({error: 'not ready'});
    };
    instance.setReadonly = function(ro) {
      if (instance._attached) instance._attached.setReadonly(ro);
      return instance;
    };
    instance.map = function() { return instance._eosMap; };

    return instance;
  }

  window.EOS_GEOCAD = {
    mount: mount,
    attach: attach,
    ensureGeoman: ensureGeoman,
  };
})();
