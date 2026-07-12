/**
 * Home Portal — Slideshow Player Component
 * In-browser podcast player with synced scene images, live subtitles, and custom controls.
 * Load via: <script src="//HOST:8000/slideshow-player.js"></script>
 *
 * Usage:
 *   SlideshowPlayer.create(containerEl, {
 *     audioUrl: '/api/podcast/stream/xxx.mp3',
 *     slideshowUrl: '/api/podcast/slideshow/xxx.mp3',  // fetches all data
 *     compact: false,  // optional: compact card mode
 *   });
 *   // OR provide data directly:
 *   SlideshowPlayer.create(containerEl, { audioUrl, timings, scenes, topic });
 */
(function() {
  'use strict';

  var CSS = `
    :host { all: initial; display: block; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    .sp-root {
      position: relative;
      background: #0a0a1a;
      border-radius: 14px;
      overflow: hidden;
      border: 1px solid rgba(255,255,255,0.06);
    }

    /* === Expanded mode === */
    .sp-stage {
      position: relative;
      width: 100%;
      padding-top: 56.25%; /* 16:9 */
      background: #050510;
      overflow: hidden;
      cursor: pointer;
    }
    .sp-img {
      position: absolute;
      top: 0; left: 0; width: 100%; height: 100%;
      object-fit: cover;
      background: #0a0a1a;
      transition: opacity 0.8s ease;
    }
    .sp-img.behind { z-index: 1; }
    .sp-img.front { z-index: 2; }
    .sp-img.hidden { opacity: 0; }

    /* Gradient overlay for subtitle readability */
    .sp-gradient {
      position: absolute;
      bottom: 0; left: 0; right: 0;
      height: 50%;
      background: linear-gradient(transparent, rgba(0,0,0,0.85));
      z-index: 3;
      pointer-events: none;
    }

    /* Subtitle */
    .sp-subtitle {
      position: absolute;
      bottom: 52px; left: 16px; right: 16px;
      z-index: 4;
      font-size: 14px;
      line-height: 1.5;
      color: #fff;
      text-shadow: 0 1px 4px rgba(0,0,0,0.8);
      text-align: center;
      pointer-events: none;
      transition: opacity 0.3s;
      min-height: 22px;
    }
    .sp-subtitle .sp-speaker-a { color: #f97316; font-weight: 600; }
    .sp-subtitle .sp-speaker-b { color: #38bdf8; font-weight: 600; }

    /* Topic overlay */
    .sp-topic {
      position: absolute;
      top: 12px; left: 14px; right: 14px;
      z-index: 4;
      font-size: 13px;
      font-weight: 700;
      color: rgba(255,255,255,0.7);
      text-shadow: 0 1px 4px rgba(0,0,0,0.8);
      pointer-events: none;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    /* Controls bar */
    .sp-controls {
      position: absolute;
      bottom: 0; left: 0; right: 0;
      z-index: 5;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      background: rgba(0,0,0,0.5);
      backdrop-filter: blur(4px);
    }
    .sp-btn {
      width: 32px; height: 32px;
      border: none; border-radius: 8px;
      background: rgba(255,255,255,0.1);
      color: #fff;
      font-size: 14px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: background 0.15s;
    }
    .sp-btn:hover { background: rgba(255,255,255,0.2); }
    .sp-btn:active { transform: scale(0.93); }

    /* Speed button */
    .sp-speed-btn {
      width: auto;
      padding: 0 8px;
      height: 26px;
      border: none;
      border-radius: 13px;
      background: rgba(255,255,255,0.1);
      color: rgba(255,255,255,0.7);
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
      flex-shrink: 0;
      font-family: inherit;
      transition: background 0.15s;
    }
    .sp-speed-btn:hover { background: rgba(255,255,255,0.2); color: #fff; }
    .sp-speed-btn:active { transform: scale(0.93); }

    /* Compact progress bar */
    .sp-compact-progress {
      position: absolute;
      bottom: 0; left: 0; right: 0;
      height: 2px;
      background: rgba(255,255,255,0.06);
      z-index: 2;
    }
    .sp-compact-progress-fill {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #f97316, #fb923c);
      transition: width 0.3s linear;
    }

    /* Scrubber */
    .sp-scrub-wrap {
      flex: 1;
      position: relative;
      height: 20px;
      display: flex;
      align-items: center;
      cursor: pointer;
    }
    .sp-scrub-track {
      width: 100%;
      height: 4px;
      background: rgba(255,255,255,0.15);
      border-radius: 2px;
      position: relative;
      overflow: visible;
    }
    .sp-scrub-fill {
      height: 100%;
      background: linear-gradient(90deg, #f97316, #fb923c);
      border-radius: 2px;
      width: 0%;
      transition: width 0.1s linear;
    }
    .sp-scrub-thumb {
      position: absolute;
      top: 50%;
      transform: translate(-50%, -50%);
      width: 12px; height: 12px;
      border-radius: 50%;
      background: #fff;
      box-shadow: 0 1px 4px rgba(0,0,0,0.4);
      left: 0%;
      transition: left 0.1s linear;
    }
    .sp-scrub-wrap:hover .sp-scrub-thumb { width: 14px; height: 14px; }

    /* Scene markers */
    .sp-scene-marker {
      position: absolute;
      top: -2px;
      width: 2px; height: 8px;
      background: rgba(255,255,255,0.35);
      border-radius: 1px;
      z-index: 1;
      pointer-events: none;
    }

    /* Time display */
    .sp-time {
      font-size: 11px;
      color: rgba(255,255,255,0.6);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
      flex-shrink: 0;
      min-width: 72px;
      text-align: center;
    }

    /* === Compact mode === */
    .sp-root.compact .sp-stage { display: none; }
    .sp-compact-bar {
      display: none;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
    }
    .sp-root.compact .sp-compact-bar { display: flex; }
    .sp-compact-thumb {
      width: 48px; height: 48px;
      border-radius: 10px;
      object-fit: cover;
      flex-shrink: 0;
      background: linear-gradient(135deg, rgba(249,115,22,0.15), rgba(251,146,60,0.08));
    }
    .sp-compact-info {
      flex: 1;
      min-width: 0;
    }
    .sp-compact-title {
      font-size: 13px;
      font-weight: 600;
      color: #e0e0e0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-bottom: 2px;
    }
    .sp-compact-sub {
      font-size: 11px;
      color: #888;
      line-height: 1.4;
      display: -webkit-box;
      -webkit-line-clamp: 1;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .sp-compact-controls {
      display: none;
      padding: 0 14px 10px;
      align-items: center;
      gap: 8px;
    }
    .sp-root.compact .sp-compact-controls { display: flex; }
    .sp-root.compact .sp-controls { display: none; }
    .sp-root.compact .sp-subtitle { display: none; }
    .sp-root.compact .sp-gradient { display: none; }

    /* === Fullscreen === */
    .sp-root.fullscreen {
      position: fixed !important;
      top: 0; left: 0; right: 0; bottom: 0;
      z-index: 999999;
      border-radius: 0;
      border: none;
    }
    .sp-root.fullscreen .sp-stage {
      padding-top: 0;
      height: 100%;
    }
    .sp-root.fullscreen .sp-subtitle {
      font-size: 18px;
      bottom: 64px;
    }
    .sp-root.fullscreen .sp-topic {
      font-size: 16px;
    }

    /* Loading */
    .sp-loading {
      position: absolute;
      top: 50%; left: 50%;
      transform: translate(-50%, -50%);
      z-index: 10;
      color: rgba(255,255,255,0.4);
      font-size: 13px;
      font-weight: 500;
    }
  `;

  function formatTime(s) {
    if (isNaN(s) || s < 0) s = 0;
    var m = Math.floor(s / 60);
    var sec = Math.floor(s % 60);
    return m + ':' + (sec < 10 ? '0' : '') + sec;
  }

  /** Binary search for current segment index */
  function findSegment(timings, timeMs) {
    var lo = 0, hi = timings.length - 1, best = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (timings[mid].start_ms <= timeMs) {
        best = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    if (best >= 0 && timeMs <= timings[best].end_ms) return best;
    return -1;
  }

  /** Find current scene index */
  function findScene(scenes, timeMs) {
    for (var i = scenes.length - 1; i >= 0; i--) {
      if (timeMs >= scenes[i].start_ms) return i;
    }
    return 0;
  }

  function escHtml(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function createPlayer(container, opts) {
    opts = opts || {};
    var isCompact = !!opts.compact;

    // Create shadow DOM
    var host = document.createElement('div');
    host.className = 'slideshow-player-host';
    container.appendChild(host);
    var shadow = host.attachShadow({ mode: 'open' });
    host._spAudio = null; // exposed for external pause

    var style = document.createElement('style');
    style.textContent = CSS;
    shadow.appendChild(style);

    // Root element
    var root = document.createElement('div');
    root.className = 'sp-root' + (isCompact ? ' compact' : '');
    shadow.appendChild(root);

    // State
    var data = null;
    var audio = new Audio();
    audio.preload = 'auto';
    audio.crossOrigin = 'anonymous';
    var currentScene = -1;
    var imgA, imgB, frontIsA = true;
    var isPlaying = false;
    var isSeeking = false;
    var isFullscreen = false;

    // Build DOM
    root.innerHTML =
      '<div class="sp-loading">Loading slideshow...</div>' +

      // Compact bar
      '<div class="sp-compact-bar" style="position:relative">' +
        '<img class="sp-compact-thumb" src="" alt="">' +
        '<div class="sp-compact-info">' +
          '<div class="sp-compact-title"></div>' +
          '<div class="sp-compact-sub"></div>' +
        '</div>' +
        '<button class="sp-btn sp-play-btn">&#9654;</button>' +
        '<button class="sp-speed-btn sp-speed-c">1x</button>' +
        '<button class="sp-btn sp-fs-btn">&#x26F6;</button>' +
        '<div class="sp-compact-progress"><div class="sp-compact-progress-fill"></div></div>' +
      '</div>' +
      '<div class="sp-compact-controls">' +
        '<span class="sp-time sp-time-c">0:00 / 0:00</span>' +
        '<div class="sp-scrub-wrap">' +
          '<div class="sp-scrub-track">' +
            '<div class="sp-scrub-fill"></div>' +
            '<div class="sp-scrub-thumb"></div>' +
          '</div>' +
        '</div>' +
      '</div>' +

      // Expanded stage
      '<div class="sp-stage">' +
        '<img class="sp-img front" src="" alt="">' +
        '<img class="sp-img behind hidden" src="" alt="">' +
        '<div class="sp-gradient"></div>' +
        '<div class="sp-topic"></div>' +
        '<div class="sp-subtitle"></div>' +
        '<div class="sp-controls">' +
          '<button class="sp-btn sp-play-btn2">&#9654;</button>' +
          '<div class="sp-scrub-wrap sp-scrub-main">' +
            '<div class="sp-scrub-track">' +
              '<div class="sp-scrub-fill"></div>' +
              '<div class="sp-scrub-thumb"></div>' +
            '</div>' +
          '</div>' +
          '<span class="sp-time sp-time-e">0:00 / 0:00</span>' +
          '<button class="sp-speed-btn sp-speed-e">1x</button>' +
          '<button class="sp-btn sp-fs-btn2">&#x26F6;</button>' +
        '</div>' +
      '</div>';

    // Refs
    var loadingEl = root.querySelector('.sp-loading');
    var stageEl = root.querySelector('.sp-stage');
    imgA = root.querySelectorAll('.sp-img')[0];
    imgB = root.querySelectorAll('.sp-img')[1];
    var subtitleEl = root.querySelector('.sp-subtitle');
    var topicEl = root.querySelector('.sp-topic');

    // Compact refs
    var compactThumb = root.querySelector('.sp-compact-thumb');
    var compactTitle = root.querySelector('.sp-compact-title');
    var compactSub = root.querySelector('.sp-compact-sub');
    var playBtn = root.querySelector('.sp-play-btn');
    var fsBtn = root.querySelector('.sp-fs-btn');
    var timeC = root.querySelector('.sp-time-c');
    var scrubC = root.querySelector('.sp-compact-controls .sp-scrub-wrap');

    // Expanded refs
    var playBtn2 = root.querySelector('.sp-play-btn2');
    var fsBtn2 = root.querySelector('.sp-fs-btn2');
    var timeE = root.querySelector('.sp-time-e');
    var scrubE = root.querySelector('.sp-scrub-main');

    // Speed control refs
    var speedBtnC = root.querySelector('.sp-speed-c');
    var speedBtnE = root.querySelector('.sp-speed-e');
    var speeds = [0.75, 1, 1.25, 1.5, 2];
    var speedIdx = 1;

    // Compact progress refs
    var compactProgressFill = root.querySelector('.sp-compact-progress-fill');

    function cycleSpeed() {
      speedIdx = (speedIdx + 1) % speeds.length;
      audio.playbackRate = speeds[speedIdx];
      var label = speeds[speedIdx] + 'x';
      speedBtnC.textContent = label;
      speedBtnE.textContent = label;
    }
    speedBtnC.addEventListener('click', function(e) { e.stopPropagation(); cycleSpeed(); });
    speedBtnE.addEventListener('click', function(e) { e.stopPropagation(); cycleSpeed(); });

    // ── Load data ──
    async function init() {
      if (opts.slideshowUrl) {
        try {
          var resp = await fetch(opts.slideshowUrl);
          data = await resp.json();
        } catch (e) {
          loadingEl.textContent = 'Failed to load slideshow';
          return;
        }
      } else {
        data = {
          topic: opts.topic || '',
          audio_url: opts.audioUrl || '',
          timings: opts.timings || [],
          scenes: opts.scenes || [],
          duration_s: opts.duration_s || 0,
          voice_pair_label: opts.voice_pair_label || '',
          cover_url: opts.cover_url || '',
        };
      }

      loadingEl.style.display = 'none';
      audio.src = data.audio_url || opts.audioUrl;
      host._spAudio = audio;

      // Set topic
      topicEl.textContent = data.topic || '';
      compactTitle.textContent = data.topic || '';
      compactSub.textContent = data.voice_pair_label || '';

      // Preload first scene or cover
      var firstScene = (data.scenes && data.scenes.length > 0) ? data.scenes[0] : null;
      var firstImg = firstScene ? firstScene.image_url : (data.cover_url || '');
      if (firstImg) {
        imgA.src = firstImg;
        imgA.style.objectFit = (firstScene && firstScene.fit) ? firstScene.fit : 'cover';
        compactThumb.src = firstImg;
      }
      currentScene = 0;

      // Add scene markers to both scrubbers
      if (data.duration_s > 0 && data.scenes) {
        addSceneMarkers(scrubC);
        addSceneMarkers(scrubE);
      }
    }

    function addSceneMarkers(scrubWrap) {
      if (!scrubWrap) return;
      var track = scrubWrap.querySelector('.sp-scrub-track');
      if (!track) return;
      var dur = data.duration_s * 1000;
      for (var i = 1; i < data.scenes.length; i++) {
        var pct = (data.scenes[i].start_ms / dur) * 100;
        var marker = document.createElement('div');
        marker.className = 'sp-scene-marker';
        marker.style.left = pct + '%';
        track.appendChild(marker);
      }
    }

    // ── Playback ──
    function togglePlay() {
      if (audio.paused) {
        audio.play();
      } else {
        audio.pause();
      }
    }

    audio.addEventListener('play', function() {
      isPlaying = true;
      playBtn.innerHTML = '&#10074;&#10074;';
      playBtn2.innerHTML = '&#10074;&#10074;';
    });
    audio.addEventListener('pause', function() {
      isPlaying = false;
      playBtn.innerHTML = '&#9654;';
      playBtn2.innerHTML = '&#9654;';
    });

    // ── Time update → sync images + subtitles ──
    audio.addEventListener('timeupdate', function() {
      if (isSeeking || !data) return;
      var ct = audio.currentTime;
      var dur = audio.duration || data.duration_s || 1;
      var pct = (ct / dur) * 100;
      var timeMs = ct * 1000;

      // Update scrubbers
      updateScrubber(scrubC, pct);
      updateScrubber(scrubE, pct);

      // Update time displays
      var timeStr = formatTime(ct) + ' / ' + formatTime(dur);
      timeC.textContent = timeStr;
      timeE.textContent = timeStr;

      // Update compact progress bar
      if (compactProgressFill) {
        compactProgressFill.style.width = pct + '%';
      }

      // Sync scene image
      if (data.scenes && data.scenes.length > 0) {
        var scIdx = findScene(data.scenes, timeMs);
        if (scIdx !== currentScene && scIdx >= 0) {
          crossfadeTo(data.scenes[scIdx]);
          currentScene = scIdx;
        }
      }

      // Sync subtitle
      if (data.timings && data.timings.length > 0) {
        var segIdx = findSegment(data.timings, timeMs);
        if (segIdx >= 0) {
          var seg = data.timings[segIdx];
          var spkCls = seg.speaker === 'A' ? 'sp-speaker-a' : 'sp-speaker-b';
          var spkLabel = seg.speaker === 'A' ? 'A' : 'B';
          subtitleEl.innerHTML = '<span class="' + spkCls + '">' + spkLabel + ':</span> ' + escHtml(seg.text);
          // Compact sub: prepend time
          compactSub.textContent = formatTime(ct) + ' \u00b7 ' + (seg.speaker === 'A' ? 'A: ' : 'B: ') + (seg.text || '');
        } else {
          subtitleEl.innerHTML = '';
          compactSub.textContent = formatTime(ct) + ' / ' + formatTime(dur);
        }
      } else {
        // No timings — still show time in compact
        compactSub.textContent = formatTime(ct) + ' / ' + formatTime(dur);
      }
    });

    audio.addEventListener('ended', function() {
      isPlaying = false;
      playBtn.innerHTML = '&#9654;';
      playBtn2.innerHTML = '&#9654;';
    });

    function updateScrubber(wrap, pct) {
      if (!wrap) return;
      var fill = wrap.querySelector('.sp-scrub-fill');
      var thumb = wrap.querySelector('.sp-scrub-thumb');
      if (fill) fill.style.width = pct + '%';
      if (thumb) thumb.style.left = pct + '%';
    }

    function crossfadeTo(scene) {
      // Accepts a scene object {image_url, fit} (or a bare url for compat).
      var imgUrl = (scene && typeof scene === 'object') ? scene.image_url : scene;
      if (!imgUrl) return;
      var fit = (scene && typeof scene === 'object' && scene.fit) ? scene.fit : 'cover';
      // The "behind" image gets the new src, then we swap visibility
      var target = frontIsA ? imgB : imgA;
      var current = frontIsA ? imgA : imgB;
      target.src = imgUrl;
      target.style.objectFit = fit;  // "contain" = never crop the graph
      target.classList.remove('hidden');
      target.classList.add('front');
      target.classList.remove('behind');
      current.classList.add('hidden');
      current.classList.remove('front');
      current.classList.add('behind');
      frontIsA = !frontIsA;
    }

    // ── Seeking ──
    function seekFromEvent(e, wrap) {
      var rect = wrap.getBoundingClientRect();
      var x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
      var pct = Math.max(0, Math.min(1, x / rect.width));
      var dur = audio.duration || data.duration_s || 1;
      audio.currentTime = pct * dur;
      updateScrubber(wrap, pct * 100);
    }

    function bindScrub(wrap) {
      if (!wrap) return;
      var dragging = false;
      wrap.addEventListener('mousedown', function(e) {
        dragging = true; isSeeking = true;
        seekFromEvent(e, wrap);
      });
      wrap.addEventListener('touchstart', function(e) {
        dragging = true; isSeeking = true;
        seekFromEvent(e, wrap);
      }, { passive: true });
      document.addEventListener('mousemove', function(e) {
        if (dragging) seekFromEvent(e, wrap);
      });
      document.addEventListener('touchmove', function(e) {
        if (dragging) seekFromEvent(e, wrap);
      }, { passive: true });
      document.addEventListener('mouseup', function() {
        if (dragging) { dragging = false; isSeeking = false; }
      });
      document.addEventListener('touchend', function() {
        if (dragging) { dragging = false; isSeeking = false; }
      });
      // Click to seek
      wrap.addEventListener('click', function(e) {
        seekFromEvent(e, wrap);
      });
    }
    bindScrub(scrubC);
    bindScrub(scrubE);

    // ── Play button click ──
    playBtn.addEventListener('click', togglePlay);
    playBtn2.addEventListener('click', togglePlay);

    // Click stage to toggle play
    stageEl.addEventListener('click', function(e) {
      // Don't toggle if clicking controls
      if (e.target.closest('.sp-controls')) return;
      togglePlay();
    });

    // ── Fullscreen ──
    function toggleFullscreen() {
      if (isFullscreen) {
        root.classList.remove('fullscreen');
        if (document.exitFullscreen) document.exitFullscreen().catch(function(){});
        isFullscreen = false;
        fsBtn.innerHTML = '&#x26F6;';
        fsBtn2.innerHTML = '&#x26F6;';
      } else {
        root.classList.add('fullscreen');
        if (root.requestFullscreen) root.requestFullscreen().catch(function(){});
        isFullscreen = true;
        fsBtn.innerHTML = '&#10005;';
        fsBtn2.innerHTML = '&#10005;';
      }
    }
    fsBtn.addEventListener('click', function(e) { e.stopPropagation(); toggleFullscreen(); });
    fsBtn2.addEventListener('click', function(e) { e.stopPropagation(); toggleFullscreen(); });

    document.addEventListener('fullscreenchange', function() {
      if (!document.fullscreenElement && isFullscreen) {
        root.classList.remove('fullscreen');
        isFullscreen = false;
        fsBtn.innerHTML = '&#x26F6;';
        fsBtn2.innerHTML = '&#x26F6;';
      }
    });

    // ── Keyboard shortcuts ──
    host.addEventListener('keydown', function(e) {
      if (e.key === ' ' || e.key === 'k') { e.preventDefault(); togglePlay(); }
      if (e.key === 'f') toggleFullscreen();
      if (e.key === 's') cycleSpeed();
      if (e.key === 'ArrowLeft') { audio.currentTime = Math.max(0, audio.currentTime - 5); }
      if (e.key === 'ArrowRight') { audio.currentTime = Math.min(audio.duration, audio.currentTime + 5); }
      if (e.key === 'Escape' && isFullscreen) toggleFullscreen();
    });
    host.tabIndex = 0;

    // ── Public API ──
    var instance = {
      play: function() { audio.play(); },
      pause: function() { audio.pause(); },
      destroy: function() {
        audio.pause();
        audio.src = '';
        host.remove();
      },
      get audio() { return audio; },
    };

    init();
    return instance;
  }

  // Export global
  window.SlideshowPlayer = {
    create: createPlayer,
  };
})();
