/* EOS_MAP — thin Leaflet wrapper for shared map rendering across apps.
 *
 *   <script src="/static/eos-map.js"></script>
 *   <link rel="stylesheet" href="/static/eos-map.css">
 *
 *   var map = EOS_MAP.create('#map', {center: [-33.86, 151.21], zoom: 12, tiles: 'both'});
 *   map.setMarkers(items, {
 *     latFor: function(it){ return it.lat; },
 *     lngFor: function(it){ return it.lng; },   // also accepts it.lon
 *     popupFor: function(it){ return '<b>'+it.name+'</b>'; },
 *     iconFor:  function(it){ return {className:'eos-map-dot', size:10}; },  // optional
 *     onClick:  function(it){ ... },
 *   }).then(function(){ map.fitBounds(); });
 *
 * Drop to raw Leaflet via map.L() / map.map() when the 80% API isn't enough.
 */
(function() {
  var LEAFLET_CSS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
  var LEAFLET_JS  = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
  var CSS_SRI = 'sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';
  var JS_SRI  = 'sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=';

  var _loading = null;
  function ensureLeaflet() {
    if (window.L) return Promise.resolve(window.L);
    if (_loading) return _loading;
    _loading = new Promise(function(resolve, reject) {
      if (!document.querySelector('link[data-eos-leaflet]')) {
        var link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = LEAFLET_CSS;
        link.integrity = CSS_SRI;
        link.crossOrigin = '';
        link.setAttribute('data-eos-leaflet', '');
        document.head.appendChild(link);
      }
      var s = document.createElement('script');
      s.src = LEAFLET_JS;
      s.integrity = JS_SRI;
      s.crossOrigin = '';
      s.onload = function() { resolve(window.L); };
      s.onerror = function() { reject(new Error('Failed to load Leaflet')); };
      document.head.appendChild(s);
    });
    return _loading;
  }

  function _tileLayers(L) {
    return {
      osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors', maxZoom: 19,
      }),
      aerial: L.tileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        {attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics', maxZoom: 19},
      ),
    };
  }

  function create(container, opts) {
    opts = opts || {};
    var el = typeof container === 'string' ? document.querySelector(container) : container;
    if (!el) throw new Error('EOS_MAP: container not found');

    var self = {_L: null, _map: null, _markers: null, _lines: null};

    self.ready = ensureLeaflet().then(function(L) {
      var map = L.map(el).setView(opts.center || [0, 0], opts.zoom != null ? opts.zoom : 10);
      var tiles = _tileLayers(L);
      var which = opts.tiles || 'osm';
      var switcher = {'OpenStreetMap': tiles.osm, 'Aerial': tiles.aerial};
      if (which === 'aerial') {
        tiles.aerial.addTo(map);
      } else if (which === 'both') {
        tiles.osm.addTo(map);
        L.control.layers(switcher, null, {position: 'topleft', collapsed: true}).addTo(map);
      } else if (which === 'both-aerial') {
        tiles.aerial.addTo(map);
        L.control.layers(switcher, null, {position: 'topleft', collapsed: true}).addTo(map);
      } else {
        tiles.osm.addTo(map);
      }
      self._L = L;
      self._map = map;
      self._markers = L.layerGroup().addTo(map);
      self._lines = L.layerGroup().addTo(map);
      return self;
    });

    self.setMarkers = function(items, opt) {
      return self.ready.then(function() {
        var L = self._L;
        self._markers.clearLayers();
        opt = opt || {};
        var latFor = opt.latFor || function(it) { return it.lat; };
        var lngFor = opt.lngFor || function(it) { return it.lng != null ? it.lng : it.lon; };
        (items || []).forEach(function(it) {
          var lat = latFor(it), lng = lngFor(it);
          if (lat == null || lng == null || isNaN(+lat) || isNaN(+lng)) return;
          var ic = opt.iconFor ? (opt.iconFor(it) || {}) : {};
          var size = ic.size || 10;
          var divOpts = {
            className: ic.className || 'eos-map-dot',
            iconSize: [size, size],
          };
          if (ic.html != null) divOpts.html = ic.html;
          var marker = L.marker([+lat, +lng], {icon: L.divIcon(divOpts)});
          if (opt.popupFor) {
            var html = opt.popupFor(it);
            if (html) marker.bindPopup(html);
          }
          if (opt.onClick) marker.on('click', function() { opt.onClick(it); });
          marker.addTo(self._markers);
        });
        return self;
      });
    };

    self.setPolylines = function(lines, opt) {
      return self.ready.then(function() {
        var L = self._L;
        self._lines.clearLayers();
        opt = opt || {};
        (lines || []).forEach(function(line) {
          var points = line.points || line;
          if (!points || points.length < 2) return;
          var style = opt.styleFor ? opt.styleFor(line) : null;
          L.polyline(points, style || {color: '#4b7', weight: 2, opacity: 0.7}).addTo(self._lines);
        });
        return self;
      });
    };

    self.fitBounds = function(opt) {
      return self.ready.then(function() {
        var L = self._L;
        var all = [];
        self._markers.eachLayer(function(m) { all.push(m.getLatLng()); });
        self._lines.eachLayer(function(l) {
          (l.getLatLngs() || []).forEach(function(p) { all.push(p); });
        });
        if (all.length) self._map.fitBounds(L.latLngBounds(all), opt || {padding: [30, 30]});
        return self;
      });
    };

    self.clear = function() {
      return self.ready.then(function() {
        self._markers.clearLayers();
        self._lines.clearLayers();
        return self;
      });
    };

    self.invalidateSize = function() {
      return self.ready.then(function() { self._map.invalidateSize(); return self; });
    };

    self.map = function() { return self._map; };
    self.L = function() { return self._L; };

    return self;
  }

  window.EOS_MAP = {create: create, ensureLeaflet: ensureLeaflet};
})();
