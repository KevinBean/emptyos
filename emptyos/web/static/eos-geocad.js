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
          .map(function(k){ return '<b>' + k + ':</b> ' + p[k]; });
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
