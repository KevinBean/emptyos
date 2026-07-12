/*
 * voice.ino — Phase 2: push-to-talk companion turns on the M5PaperColor.
 *
 * Gesture: HOLD BtnA while the board sleeps → it wakes into voice mode, beeps,
 * records while the button stays held (release = done), sends the utterance to
 * the daemon's puck endpoint (/voice-assistant/api/device_turn_stream), and the
 * companion speaks the reply through the board's own speaker (sentence-by-
 * sentence, fast mode). The normal dashboard cycle then runs, so the panel
 * redraws with whatever the turn changed — voice REFLECTs onto the paper.
 * A quick TAP of BtnA still just cycles views (paper_dashboard.ino).
 *
 * Audio: M5Unified's native M5.Mic / M5.Speaker (the PaperColor is a known
 * board — no hand-rolled ES8311 driver like the puck's Echo Base needed).
 * VERIFY[board]: whether this codec shows the puck's ~2× clock quirk — if
 * replies play at chipmunk/half speed, halve/double the rate in paperPlayWav.
 *
 * Arduino concatenates all .ino files in the sketch folder into one
 * translation unit, so this file shares paper_dashboard.ino's statics
 * (DAEMON_BASE, AUTH_TOKEN, DEVICE_ID, BTN_WAKE_GPIO, httpBegin, …).
 */

#include <ArduinoJson.h>

static const uint32_t VOICE_RATE      = 16000;                    // whisper-ideal
static const size_t   VOICE_CHUNK     = 320;                      // 20 ms frames
static const size_t   VOICE_MAX_BYTES = VOICE_RATE * 2 * 15;      // 15 s ceiling
static const size_t   VOICE_MIN_BYTES = VOICE_RATE;               // 0.5 s floor

// Dedicated clients for the NDJSON stream so per-sentence audio fetches
// (paperPlayUrl → g_tcp) don't collide with the still-open streaming socket.
static WiFiClient       g_vStreamTcp;
static WiFiClientSecure g_vStreamTls;

static bool voiceBtnHeld() { return digitalRead(BTN_WAKE_GPIO) == LOW; }

// Kick the Wi-Fi join off non-blocking (called BEFORE M5.begin, per the
// verified join-before-display ordering) so association overlaps recording.
// Uses the leased static IP immediately — DHCP is the proven-flaky part.
static void voiceKickWifi() {
  if (!strlen(SECRET_WIFI_SSID)) return;
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.config(IPAddress(192, 168, 20, 19), IPAddress(192, 168, 20, 1),
              IPAddress(255, 255, 255, 0), IPAddress(192, 168, 20, 1));
  WiFi.begin(SECRET_WIFI_SSID, SECRET_WIFI_PASS);
  WiFi.setTxPower(WIFI_POWER_11dBm);
}

static bool voiceAwaitWifi(uint32_t ms) {
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < ms) delay(100);
  return WiFi.status() == WL_CONNECTED;
}

// 44-byte PCM WAV header (16-bit mono). Ported from the puck (incl. its fixed
// data-tag offsets — 36/40, not 38/42).
static void writeWavHeader(uint8_t* h, uint32_t nSamples, uint32_t rate) {
  uint32_t dataBytes = nSamples * 2, chunk = 36 + dataBytes, byteRate = rate * 2;
  uint16_t fmt = 1, ch = 1, ba = 2, bps = 16; uint32_t sub1 = 16;
  memcpy(h, "RIFF", 4);          memcpy(h + 4, &chunk, 4);
  memcpy(h + 8, "WAVE", 4);      memcpy(h + 12, "fmt ", 4);
  memcpy(h + 16, &sub1, 4);      memcpy(h + 20, &fmt, 2);
  memcpy(h + 22, &ch, 2);        memcpy(h + 24, &rate, 4);
  memcpy(h + 28, &byteRate, 4);  memcpy(h + 32, &ba, 2);
  memcpy(h + 34, &bps, 2);       memcpy(h + 36, "data", 4);
  memcpy(h + 40, &dataBytes, 4);
}

// ── capture: record while the button stays held ─────────────────────────────
// DC-removed mean-absolute frame energy (ported from the puck) — the ES-family
// ADCs settle with a DC drift that plain mean-abs would count as noise.
static int voiceFrameEnergy(const int16_t* f, size_t n) {
  long mean = 0;
  for (size_t i = 0; i < n; i++) mean += f[i];
  mean /= (long)n;
  uint32_t sum = 0;
  for (size_t i = 0; i < n; i++) { int v = (int)f[i] - (int)mean; sum += (v < 0 ? -v : v); }
  return (int)(sum / n);
}

// Record one utterance, VAD-stopped: the button only WAKES the board — the
// user releases whenever, speaks after the beep, and recording ends after
// ~1.2s of trailing silence (or 4s with no speech, or the 15s ceiling).
// This decouples capture from button timing entirely — the e-ink M5.begin
// takes ~3s, longer than a natural button hold (hardware-observed).
static size_t recordUtterance(uint8_t* buf) {
  M5.Speaker.end();
  delay(20);
  {
    auto mc = M5.Mic.config();
    mc.sample_rate = VOICE_RATE;
    mc.over_sampling = 1;
    M5.Mic.config(mc);
  }
  size_t total = 0;
  int16_t frame[VOICE_CHUNK];
  // Settle + noise floor (~0.3s, pre-speech: the user was just beeped at).
  for (int i = 0; i < 5; i++) {
    bool r = M5.Mic.record(frame, VOICE_CHUNK, VOICE_RATE);
    if (i == 0) Serial.printf("[voice] first record()=%d micEnabled=%d\n",
                              (int)r, (int)M5.Mic.isEnabled());
    while (M5.Mic.isRecording()) delay(1);
  }
  int floorEst = 0;
  for (int i = 0; i < 10; i++) {
    M5.Mic.record(frame, VOICE_CHUNK, VOICE_RATE);
    while (M5.Mic.isRecording()) delay(1);
    floorEst += voiceFrameEnergy(frame, VOICE_CHUNK);
  }
  floorEst /= 10;
  int onsetThr   = max(floorEst * 2, floorEst + 200);
  int silenceThr = max((int)(floorEst * 1.35f), floorEst + 80);
  Serial.printf("[voice] floor=%d onset=%d silence=%d\n", floorEst, onsetThr, silenceThr);

  bool sawSpeech = false;
  uint32_t start = millis(), lastLoud = millis();
  while (total + VOICE_CHUNK * 2 <= VOICE_MAX_BYTES) {
    int16_t* dst = (int16_t*)(buf + total);
    if (!M5.Mic.record(dst, VOICE_CHUNK, VOICE_RATE)) { Serial.println("[voice] record() false — stop"); break; }
    while (M5.Mic.isRecording()) delay(1);
    total += VOICE_CHUNK * 2;
    int e = voiceFrameEnergy(dst, VOICE_CHUNK);
    if (e >= onsetThr) sawSpeech = true;
    if (e >= silenceThr) lastLoud = millis();
    if (sawSpeech && millis() - lastLoud > 1200) break;          // utterance ended
    if (!sawSpeech && millis() - start > 4000) { total = 0; break; }   // never spoke
  }
  M5.Mic.end();
  Serial.printf("[voice] captured %u bytes (%.1fs) sawSpeech=%d\n",
                (unsigned)total, total / 2.0f / VOICE_RATE, (int)sawSpeech);
  return total;
}

// ── playback: WAV buffer / URL through the board speaker ─────────────────────
static void paperBeep(int freq, int ms) {
  // Warm the audio power rail (G45) BEFORE the enable callback's ES8311 I2C
  // writes — the callback raises it with no settle delay, and a codec that
  // missed its init plays silence (codex-diagnosed).
  pinMode(45, OUTPUT);
  digitalWrite(45, HIGH);
  delay(60);
  bool ok = M5.Speaker.begin();
  delay(20);
  Serial.printf("[voice] spk begin=%d enabled=%d\n", (int)ok, (int)M5.Speaker.isEnabled());
  M5.Speaker.setVolume(255);
  M5.Speaker.tone(freq, ms);
  uint32_t t0 = millis();
  while (M5.Speaker.isPlaying() && millis() - t0 < (uint32_t)ms + 200) delay(5);
}

static void paperPlayWav(uint8_t* buf, size_t len) {
  if (len < 44 || memcmp(buf, "RIFF", 4) != 0) {
    Serial.printf("[voice] reply not WAV (0x%02x) — pin daemon speak=kokoro\n", buf[0]);
    return;
  }
  uint32_t rate; memcpy(&rate, buf + 24, 4);
  uint16_t ch;   memcpy(&ch, buf + 22, 2);
  // Find the data chunk (ffmpeg often inserts a LIST chunk before it).
  size_t off = 12, dataOff = 0; uint32_t dataLen = 0;
  while (off + 8 <= len) {
    uint32_t sz; memcpy(&sz, buf + off + 4, 4);
    if (memcmp(buf + off, "data", 4) == 0) { dataOff = off + 8; dataLen = sz; break; }
    off += 8 + sz + (sz & 1);
  }
  if (!dataOff) { dataOff = 44; dataLen = len - 44; }
  if (dataOff + dataLen > len) dataLen = len - dataOff;
  Serial.printf("[voice] play WAV rate=%u ch=%u data=%u\n",
                (unsigned)rate, (unsigned)ch, (unsigned)dataLen);
  M5.Speaker.begin();
  M5.Speaker.setVolume(200);
  // Raw PCM path: rate comes from the header. VERIFY[board]: if pitch is off
  // (codec 2x quirk), change `rate` to `rate/2` or `rate*2` here.
  M5.Speaker.playRaw((const int16_t*)(buf + dataOff), dataLen / 2, rate, ch == 2);
  uint32_t guard = millis() + 30000;
  while (M5.Speaker.isPlaying() && millis() < guard) delay(10);
}

static void paperPlayUrl(const String& path) {
  HTTPClient http;
  if (!httpBegin(http, String(DAEMON_BASE) + path)) return;
  http.setTimeout(20000);
  http.addHeader("Authorization", String("Bearer ") + AUTH_TOKEN);
  if (http.GET() == 200) {
    int len = http.getSize();
    if (len > 0) {
      uint8_t* b = (uint8_t*) heap_caps_malloc(len, MALLOC_CAP_SPIRAM);
      if (b) {
        WiFiClient* s = http.getStreamPtr();
        int got = 0;
        while (http.connected() && got < len) {
          int a = s->available();
          if (a > 0) got += s->readBytes(b + got, min(a, len - got)); else delay(1);
        }
        paperPlayWav(b, (size_t)got);
        heap_caps_free(b);
      }
    }
  }
  http.end();
}

// ── the turn: multipart upload → NDJSON stream → speak each sentence ────────
static void voiceStreamTurn(HTTPClient& http) {
  WiFiClient* s = http.getStreamPtr();
  String buf = "", reply = "";
  uint32_t deadline = millis() + 60000;
  while (millis() < deadline) {
    while (s->available()) {
      char c = (char) s->read();
      if (c == '\n') {
        buf.trim();
        if (buf.length()) {
          JsonDocument ev;
          if (!deserializeJson(ev, buf)) {
            const char* t = ev["type"] | "";
            if (!strcmp(t, "transcript")) {
              Serial.printf("[voice] heard: %s\n", (const char*)(ev["text"] | ""));
            } else if (!strcmp(t, "text")) {
              reply += (const char*)(ev["delta"] | "");
            } else if (!strcmp(t, "audio")) {
              const char* u = ev["url"] | "";
              if (u[0]) paperPlayUrl(String(u));       // blocking; socket buffers meanwhile
            } else if (!strcmp(t, "error")) {
              Serial.printf("[voice] error: %s\n", (const char*)(ev["error"] | ""));
              paperBeep(300, 350);                     // low buzz = something went wrong
              return;
            } else if (!strcmp(t, "done")) {
              Serial.printf("[voice] turn done | stt=%d gen=%d ms | %s\n",
                            (int)(ev["timing"]["stt_ms"] | 0),
                            (int)(ev["timing"]["gen_ms"] | 0), reply.c_str());
              return;
            }
          }
        }
        buf = "";
        deadline = millis() + 60000;
      } else if (c != '\r') {
        buf += c;
      }
    }
    if (!http.connected() && !s->available()) break;
    delay(5);
  }
}

static void voiceTurn(uint8_t* pcm, size_t bytes) {
  uint32_t nSamples = bytes / 2;
  const String B = "----eosPaper";
  String pre =
    "--" + B + "\r\nContent-Disposition: form-data; name=\"device_id\"\r\n\r\n" + DEVICE_ID + "\r\n"
    "--" + B + "\r\nContent-Disposition: form-data; name=\"device_board\"\r\n\r\n" + BOARD_ID + "\r\n"
    "--" + B + "\r\nContent-Disposition: form-data; name=\"fast\"\r\n\r\n1\r\n"
    "--" + B + "\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\n";
  String post = "\r\n--" + B + "--\r\n";

  size_t bodyLen = pre.length() + 44 + bytes + post.length();
  uint8_t* body = (uint8_t*) heap_caps_malloc(bodyLen, MALLOC_CAP_SPIRAM);
  if (!body) { Serial.println("[voice] no memory for body"); return; }
  size_t o = 0;
  memcpy(body + o, pre.c_str(), pre.length());          o += pre.length();
  writeWavHeader(body + o, nSamples, VOICE_RATE);       o += 44;
  memcpy(body + o, pcm, bytes);                         o += bytes;
  memcpy(body + o, post.c_str(), post.length());        o += post.length();

  HTTPClient http;
  String url = String(DAEMON_BASE) + "/voice-assistant/api/device_turn_stream";
  bool ok = url.startsWith("https://")
              ? (g_vStreamTls.setInsecure(), http.begin(g_vStreamTls, url))
              : http.begin(g_vStreamTcp, url);
  if (!ok) { heap_caps_free(body); return; }
  http.setConnectTimeout(10000);
  http.setTimeout(45000);
  http.addHeader("Authorization", String("Bearer ") + AUTH_TOKEN);
  http.addHeader("Content-Type", "multipart/form-data; boundary=" + B);
  uint32_t t0 = millis();
  int code = http.POST(body, bodyLen);
  heap_caps_free(body);
  Serial.printf("[voice] stream -> %d in %lu ms\n", code, (unsigned long)(millis() - t0));
  if (code == 200) voiceStreamTurn(http);
  else paperBeep(300, 350);
  http.end();
}

// ── entry point, called from setup() when the wake was a BtnA HOLD ───────────
// Records first (mic needs no network), then joins Wi-Fi, then runs the turn.
// Returns after the spoken reply; the caller falls through to the normal
// register→fetch→draw→sleep cycle, so the panel shows what the turn changed.
static void runVoiceMode() {
  uint8_t* pcm = (uint8_t*) heap_caps_malloc(VOICE_MAX_BYTES, MALLOC_CAP_SPIRAM);
  if (!pcm) { Serial.println("[voice] no PSRAM for capture"); return; }
  paperBeep(880, 120);                      // "listening" cue — speak after this
  size_t n = recordUtterance(pcm);          // VAD-stopped; button already released
  paperBeep(660, 80);                       // "got it" cue
  if (n < VOICE_MIN_BYTES) {
    Serial.println("[voice] too short — ignoring");
    heap_caps_free(pcm);
    return;
  }
  // The join was kicked off before recording; usually it's done by now.
  if (!voiceAwaitWifi(12000) && !rawConnect()) {
    Serial.println("[voice] no Wi-Fi — dropping turn");
    paperBeep(300, 350);
    heap_caps_free(pcm);
    return;
  }
  Serial.printf("[voice] Wi-Fi ok ip=%s\n", WiFi.localIP().toString().c_str());
  voiceTurn(pcm, n);
  heap_caps_free(pcm);
}
