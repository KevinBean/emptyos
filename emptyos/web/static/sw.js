/**
 * EmptyOS Service Worker — cache static assets, network-first for API, offline fallback for HTML.
 */

var CACHE_NAME = 'eos-v8';
var OFFLINE_URL = '/offline.html';
var STATIC_ASSETS = [
  '/static/theme.css',
  '/static/eos.js',
  '/static/eos-components.css',
  '/static/eos-components.js',
  '/static/eos-keys.js',
  '/static/realtime.js',
  '/static/page-assistant.js',
  '/static/favicon.svg',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/manifest.webmanifest',
  OFFLINE_URL,
  // iOS PWA splash screens — precache so first cold-launch after install
  // doesn't flash white. ~500KB total, downloaded during SW install.
  '/static/splash/splash-750x1334.png',
  '/static/splash/splash-828x1792.png',
  '/static/splash/splash-1125x2436.png',
  '/static/splash/splash-1170x2532.png',
  '/static/splash/splash-1179x2556.png',
  '/static/splash/splash-1242x2688.png',
  '/static/splash/splash-1284x2778.png',
  '/static/splash/splash-1290x2796.png',
  '/static/splash/splash-1536x2048.png',
  '/static/splash/splash-1620x2160.png',
  '/static/splash/splash-1668x2224.png',
  '/static/splash/splash-1668x2388.png',
  '/static/splash/splash-2048x2732.png',
];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(STATIC_ASSETS);
    }).then(function() {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(
        names.filter(function(n) { return n !== CACHE_NAME; })
          .map(function(n) { return caches.delete(n); })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', function(e) {
  var url = new URL(e.request.url);

  // Skip non-GET, WebSocket, and cross-origin
  if (e.request.method !== 'GET') return;
  if (url.protocol === 'ws:' || url.protocol === 'wss:') return;
  if (url.origin !== self.location.origin) return;

  // API calls: network only (don't cache dynamic data)
  if (url.pathname.indexOf('/api/') !== -1) return;

  // Static assets: network-first (dev-friendly — always picks up changes)
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      fetch(e.request).then(function(resp) {
        if (resp.ok && resp.status === 200) {
          var clone = resp.clone();
          caches.open(CACHE_NAME).then(function(c) { c.put(e.request, clone); });
        }
        return resp;
      }).catch(function() {
        return caches.match(e.request);
      })
    );
    return;
  }

  // HTML / app pages: network-first, fallback to cache, then to offline page
  var isHTMLRequest = e.request.mode === 'navigate' ||
                      (e.request.headers.get('accept') || '').indexOf('text/html') !== -1;

  e.respondWith(
    fetch(e.request).then(function(resp) {
      if (resp.ok && resp.status === 200) {
        var clone = resp.clone();
        caches.open(CACHE_NAME).then(function(c) { c.put(e.request, clone); });
      }
      return resp;
    }).catch(function() {
      return caches.match(e.request).then(function(cached) {
        if (cached) return cached;
        if (isHTMLRequest) return caches.match(OFFLINE_URL);
        return new Response('', { status: 504, statusText: 'Gateway Timeout' });
      });
    })
  );
});
