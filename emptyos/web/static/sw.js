/**
 * EmptyOS Service Worker — cache static assets, network-first for API, offline fallback for HTML.
 */

var CACHE_NAME = 'eos-v11';
var OFFLINE_URL = '/offline.html';
var WRITE_DB = 'emptyos-offline-writes';
var WRITE_STORE = 'writes';
var WRITE_SYNC_TAG = 'eos-offline-writes';
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

function openWriteDB() {
  return new Promise(function(resolve, reject) {
    var req = indexedDB.open(WRITE_DB, 1);
    req.onupgradeneeded = function() {
      var db = req.result;
      if (!db.objectStoreNames.contains(WRITE_STORE)) {
        db.createObjectStore(WRITE_STORE, { keyPath: 'id' });
      }
    };
    req.onsuccess = function() { resolve(req.result); };
    req.onerror = function() { reject(req.error); };
  });
}

function writeTransaction(mode, action) {
  return openWriteDB().then(function(db) {
    return new Promise(function(resolve, reject) {
      var tx = db.transaction(WRITE_STORE, mode);
      var store = tx.objectStore(WRITE_STORE);
      var result;
      try { result = action(store); } catch (err) { reject(err); return; }
      tx.oncomplete = function() { resolve(result); };
      tx.onerror = function() { reject(tx.error); };
      tx.onabort = function() { reject(tx.error); };
    }).finally(function() { db.close(); });
  });
}

function queueOfflineWrite(request) {
  return request.clone().text().then(function(body) {
    var headers = [];
    request.headers.forEach(function(value, key) { headers.push([key, value]); });
    var id = request.headers.get('X-EOS-Offline-Write-ID') ||
      ('offline-' + Date.now() + '-' + Math.random().toString(16).slice(2));
    var row = {
      id: id,
      url: request.url,
      method: request.method,
      headers: headers,
      body: body,
      created_at: new Date().toISOString()
    };
    return writeTransaction('readwrite', function(store) { store.put(row); }).then(function() {
      if (self.registration.sync) {
        self.registration.sync.register(WRITE_SYNC_TAG).catch(function() {});
      }
      return new Response(JSON.stringify({ok: true, queued: true, offline: true, id: id}), {
        status: 202,
        headers: {
          'Content-Type': 'application/json',
          'X-EOS-Offline-Queued': '1'
        }
      });
    });
  });
}

function listOfflineWrites() {
  return openWriteDB().then(function(db) {
    return new Promise(function(resolve, reject) {
      var tx = db.transaction(WRITE_STORE, 'readonly');
      var req = tx.objectStore(WRITE_STORE).getAll();
      req.onsuccess = function() { resolve(req.result || []); };
      req.onerror = function() { reject(req.error); };
      tx.oncomplete = function() { db.close(); };
    });
  });
}

function deleteOfflineWrite(id) {
  return writeTransaction('readwrite', function(store) { store.delete(id); });
}

function notifyClients(message) {
  return self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(function(clients) {
    clients.forEach(function(client) { client.postMessage(message); });
  });
}

function flushOfflineWrites() {
  return listOfflineWrites().then(function(rows) {
    rows.sort(function(a, b) { return (a.created_at || '').localeCompare(b.created_at || ''); });
    return rows.reduce(function(chain, row) {
      return chain.then(function() {
        return fetch(row.url, {
          method: row.method,
          headers: row.headers,
          body: row.body,
          credentials: 'same-origin'
        }).then(function(resp) {
          // Success and permanent client errors both leave the queue. Server
          // failures stay for the next online/sync attempt.
          if (resp.ok || (resp.status >= 400 && resp.status < 500)) {
            return deleteOfflineWrite(row.id).then(function() {
              return notifyClients({
                type: 'eos:offline-write-replayed', id: row.id,
                ok: resp.ok, status: resp.status
              });
            });
          }
        }).catch(function() {});
      });
    }, Promise.resolve());
  });
}

self.addEventListener('sync', function(e) {
  if (e.tag === WRITE_SYNC_TAG) e.waitUntil(flushOfflineWrites());
});

self.addEventListener('message', function(e) {
  if (e.data && e.data.type === 'eos:flush-offline-writes') {
    e.waitUntil(flushOfflineWrites());
  }
});

self.addEventListener('fetch', function(e) {
  var url = new URL(e.request.url);

  // Dark-flagged offline mutation path. eos.js adds the opt-in header only
  // for bounded JSON writes in task/journal/projects/people.
  if (e.request.method === 'POST' &&
      url.origin === self.location.origin &&
      e.request.headers.get('X-EOS-Offline-Queue') === '1') {
    e.respondWith(fetch(e.request.clone()).catch(function() {
      return queueOfflineWrite(e.request);
    }));
    return;
  }

  // Skip other non-GET, WebSocket, and cross-origin requests.
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
