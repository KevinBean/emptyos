/*
 * voice_satellite.ino — EmptyOS voice satellite (v2)
 * Target: M5Stack AtomS3R (ESP32-S3 + PSRAM) + Atomic Echo Base (ES8311 mic + speaker)
 *
 * A home voice assistant that reuses the EmptyOS daemon's brain. Two modes off the
 * one screen-button (the whole front face is the button; the tiny side button is RESET):
 *   - TAP         -> single turn: VAD captures one utterance, sends, replies, idle.
 *   - DOUBLE-TAP  -> new conversation: clears the threaded context.
 *   - LONG-PRESS  -> FREEHAND: talk<->reply with NO taps between turns; ~3 min silence closes.
 *   - SHAKE (IMU) -> hands-free single turn without finding the button.
 *
 * The daemon owns STT, the LLM, intent dispatch, the action gate, and all vault writes.
 * Audio: M5EchoBase (ES8311). Do NOT use M5.Mic/M5.Speaker (they fight it for I2S).
 * Config is provisioned ON-DEVICE: a WiFiManager captive portal ("EOS-Satellite")
 * collects Wi-Fi + daemon URL + token + device id, persisted to NVS. Hold the front
 * button at boot to re-open it. secrets.h is optional (pre-fills portal defaults only).
 *
 * Libraries: M5Unified, M5EchoBase, ArduinoJson (v7), WiFiManager (tzapu).
 * Board: "M5AtomS3" · Tools -> PSRAM: OPI PSRAM (required for the audio buffer).
 */

#include <M5Unified.h>
#include <M5EchoBase.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <WiFiManager.h>      // tzapu/WiFiManager — captive-portal provisioning
#include <Preferences.h>      // NVS-persisted config (daemon URL, token, device id)

// secrets.h is now OPTIONAL — it only pre-fills defaults for the setup portal.
// Provisioning happens on-device via the captive portal; values persist in NVS.
#if __has_include("secrets.h")
  #include "secrets.h"
#endif
#ifndef SECRET_DAEMON_BASE
  #define SECRET_DAEMON_BASE ""
#endif
#ifndef SECRET_AUTH_TOKEN
  #define SECRET_AUTH_TOKEN ""
#endif

// Runtime config — loaded from NVS (portal-set); defaults from secrets.h if present.
static String      DAEMON_BASE;
static String      AUTH_TOKEN;
static Preferences g_prefs;

// ── TEST MODE ───────────────────────────────────────────────────────────────
// 1 = local loopback: TAP records, then plays it straight back out the speaker
//     (no Wi-Fi, no daemon) to verify mic-in + speaker-out. Set 0 for normal use.
#define LOOPBACK_TEST 0

// ── Audio / capture ─────────────────────────────────────────────────────────
static const uint32_t MIC_RATE   = 16000;
static const size_t   MAX_SEC    = 10;
static const size_t   MAX_BYTES  = MIC_RATE * 2 * MAX_SEC;
static const size_t   MIN_BYTES  = MIC_RATE;            // ~0.5 s minimum utterance
static const size_t   FRAME      = 320;                 // 20 ms @ 16 kHz (samples)
static const size_t   FRAME_B    = FRAME * 2;

// ── VAD (hysteresis — mirrors the simulator's onset/silence two-threshold model) ──
// Two thresholds, not one: speech is CONFIRMED above `onset`, the pause only counts
// below `silence`, and the band between keeps soft speech alive (no mid-word cut-off).
static const int      MIN_THRESHOLD   = 80;            // floor for the onset threshold
static const int      MIN_SILENCE     = 55;            // floor for the silence threshold
static const float    ONSET_MULT      = 2.0f;          // onset margin above the floor (codex: 2.0–2.5)
static const float    SILENCE_MULT    = 1.35f;         // pause counts below this margin (hysteresis)
static const uint32_t HANGOVER_MS     = 1000;          // trailing silence that ends an utterance (was 1500)
static const uint32_t MAX_UTTER_MS    = 10000;
static const uint32_t SINGLE_ONSET_MS = 6000;          // tap: wait this long for speech (sim noSpeechMs)
static const uint32_t FREEHAND_IDLE_MS = 180000;       // 3 min silence closes freehand
static const int      METER_REF       = 6000;          // VU full-scale reference (mean-abs)

// ── Device / framework ──────────────────────────────────────────────────────
static String         DEVICE_ID;                       // NVS-set (portal); registers in /devices
static const bool     FAST_MODE       = true;          // send fast=1 (fast think model); set false for quality
static const uint32_t SLEEP_MS        = 30000;         // blank the screen (backlight off) after this idle
static const uint8_t  DEFAULT_BRIGHT  = 100;

// ── Echo Base pins (AtomS3R) ────────────────────────────────────────────────
// One sketch, selectable board. AtomS3R + Atomic Echo Base is implemented; CoreS3 /
// Cardputer have built-in codecs (a different driver, not M5EchoBase) — their audio
// path is a follow-up. The struct captures board identity + IMU + Echo Base pins.
struct BoardProfile {
  const char* id;
  bool        hasImu;                            // 6-axis IMU → shake-to-talk
  int ebSDA, ebSCL, ebDIN, ebWS, ebDOUT, ebBCK;  // Atomic Echo Base I2C/I2S pins
};
static const BoardProfile BOARD_ATOMS3R = { "atoms3r", true, 38, 39, 7, 6, 5, 8 };
static const BoardProfile& BOARD = BOARD_ATOMS3R;   // ← select your board here

M5EchoBase echobase;
static uint32_t g_audioRate = 0;

// ── Session / runtime state ─────────────────────────────────────────────────
static String   g_sessionMessages = "[]";
static String   g_companion       = "";
static uint8_t* g_buf             = nullptr;
static int      g_floorEst        = MIN_THRESHOLD;   // VAD: adaptive ambient-noise estimate
static int      g_onsetThr        = MIN_THRESHOLD;   // VAD: speech-confirmed level (from floor)
static int      g_silenceThr      = MIN_SILENCE;     // VAD: pause-counts-below level (hysteresis)
static bool     g_freehand        = false;
static uint32_t g_lastAct         = 0;       // last activity ms (screen sleep)
static uint32_t g_lastHb          = 0;       // last heartbeat ms
static bool     g_asleep          = false;
static bool     g_exit            = false;   // set when a button press should abort a wait

static WiFiClientSecure g_tls;
static WiFiClient       g_tcp;
static WiFiClientSecure g_streamTls;          // dedicated to the streaming turn, so
static WiFiClient       g_streamTcp;          // playUrl's g_tcp stays free during it

enum State { ST_IDLE, ST_LISTEN, ST_THINK, ST_SPEAK, ST_ERROR, ST_ARMED };

// ── Display: orb renderer (ports the browser simulator's visual language) ────
// A center orb whose radius/colour track the state, on a flicker-free 128x128
// sprite: idle breathing, listening grows with the mic + ripple rings, thinking
// spins an arc, speaking pulses, plus the state label and wrapped transcript/reply.
static M5Canvas g_cv(&M5.Display);              // offscreen sprite (push once per frame)
static float    g_phase        = 0.0f;          // animation clock
static String   g_uiLabel      = "READY";
static String   g_uiTranscript = "";
static String   g_uiReply      = "Tap=talk  Hold=free";

struct OrbRGB { uint8_t r, g, b; };
static OrbRGB hueRGB(State s) {                  // mirrors the sim's state->hue map
  switch (s) {
    case ST_LISTEN: return {  51, 221, 102 };    // green
    case ST_ARMED:  return {  34, 187, 221 };    // cyan (freehand)
    case ST_THINK:  return { 221, 170,  51 };    // amber
    case ST_SPEAK:  return {  51, 153, 221 };    // blue
    case ST_ERROR:  return { 238,  68,  68 };    // red
    default:        return {  51, 170, 136 };    // idle teal
  }
}
static uint16_t shade(OrbRGB c, float f) {
  int r = (int)(c.r * f), g = (int)(c.g * f), b = (int)(c.b * f);
  return M5.Display.color565(r > 255 ? 255 : r, g > 255 ? 255 : g, b > 255 ? 255 : b);
}

// Centered word-wrap into the sprite; returns the y after the last line drawn.
static int cvWrap(const String& txt, int y, int maxLines, uint16_t color) {
  if (!txt.length()) return y;
  g_cv.setTextColor(color);
  g_cv.setTextSize(1);
  g_cv.setTextDatum(top_center);
  String line = "", word = "";
  int lines = 0, n = (int)txt.length();
  for (int i = 0; i <= n; i++) {
    char c = (i < n) ? txt[i] : ' ';
    if (c == ' ' || c == '\n') {
      String test = line.length() ? line + " " + word : word;
      if (g_cv.textWidth(test) > 122 && line.length()) {
        g_cv.drawString(line, 64, y); y += 11;
        if (++lines >= maxLines) return y;
        line = word;
      } else line = test;
      word = "";
    } else if (c != '\r') word += c;
  }
  if (line.length() && lines < maxLines) { g_cv.drawString(line, 64, y); y += 11; }
  return y;
}

// One frame of the orb UI. `level` (0..1) drives the listening/speaking radius.
static void renderUI(State s, float level) {
  g_phase += 0.05f;
  const float t = g_phase;
  const int cx = 64, cy = 50;
  OrbRGB c = hueRGB(s);

  float r;
  switch (s) {
    case ST_LISTEN: case ST_ARMED: r = 15 + level * 26; break;   // grows with the mic
    case ST_SPEAK:  r = 18 + sinf(t * 3) * 3;            break;   // gentle pulse
    case ST_THINK:  r = 18;                              break;
    case ST_ERROR:  r = 18 + sinf(t * 2) * 4;            break;
    default:        r = 17 + sinf(t) * 3;                break;   // idle breathing
  }
  if (r > 40) r = 40;
  const int ir = (int)r;

  g_cv.fillScreen(TFT_BLACK);

  // ripple rings while a loud voice is heard
  if ((s == ST_LISTEN || s == ST_ARMED) && level > 0.10f) {
    g_cv.drawCircle(cx, cy, ir + 6,  shade(c, 0.6f));
    g_cv.drawCircle(cx, cy, ir + 12, shade(c, 0.3f));
  }
  // thinking — a short arc of dots sweeping the rim
  if (s == ST_THINK) {
    float a0 = fmodf(t, 6.2832f);
    for (float a = a0; a < a0 + 1.8f; a += 0.25f)
      g_cv.fillCircle(cx + (int)(cosf(a) * 26), cy + (int)(sinf(a) * 26), 2, shade(c, 1.0f));
  }
  // orb body — concentric circles approximate the sim's radial gradient
  g_cv.fillCircle(cx, cy, ir,                shade(c, 0.35f));
  g_cv.fillCircle(cx, cy, (int)(r * 0.62f),  shade(c, 0.78f));
  g_cv.fillCircle(cx, cy, (int)(r * 0.30f),  shade(c, 1.20f));

  // freehand — pulsing cyan edge + a top-right badge dot (non-blocking, like the sim)
  if (g_freehand) {
    uint16_t fc = shade(hueRGB(ST_ARMED), 0.5f + 0.5f * fabsf(sinf(t * 2)));
    g_cv.drawRect(0, 0, 128, 128, fc);
    g_cv.fillCircle(119, 9, 5, shade(hueRGB(ST_ARMED), 1.0f));
  }

  // state label (top) + transcript (blue) + reply (white), below the orb
  g_cv.setTextColor(TFT_WHITE); g_cv.setTextSize(1); g_cv.setTextDatum(top_center);
  g_cv.drawString(g_uiLabel, 64, 2);
  int y = 90;
  y = cvWrap(g_uiTranscript, y, 2, M5.Display.color565(120, 175, 255));
  cvWrap(g_uiReply, y, 3, TFT_WHITE);

  g_cv.pushSprite(0, 0);
}

// Thin wrappers so every existing call site keeps working, now orb-rendered.
static void screen(State s, const String& a, const String& b) {
  g_uiLabel = a; g_uiTranscript = ""; g_uiReply = b;
  renderUI(s, 0.0f);
  Serial.printf("[ui] %s | %s\n", a.c_str(), b.c_str());
}

// Called each capture frame — animates the listening orb from the mic energy.
static void drawMeter(int energy) {
  float level = energy / (float)METER_REF;
  if (level > 1.0f) level = 1.0f;
  renderUI(g_freehand ? ST_ARMED : ST_LISTEN, level);
}

static void idleScreen() {
  g_uiLabel = "READY";                  // keep the last transcript/reply visible, like the sim
  renderUI(ST_IDLE, 0.0f);
}

// ── Audio init ──────────────────────────────────────────────────────────────
// This board's ES8311/Echo Base clocks the ADC *and* DAC at ~2x the requested rate.
// Proven both ways: reply playback came out 2x fast (chipmunk), AND mic audio labelled
// 16k was really ~32k, so the daemon's whisper couldn't read it ("Didn't catch that").
// Compensate ONCE here — request HALF the logical rate so the real capture/playback rate
// equals what we tell the daemon. Callers everywhere pass LOGICAL rates (MIC_RATE, the
// reply's header rate); `g_audioRate` tracks the logical rate for the redundant-init guard.
static void audioInit(uint32_t logicalRate) {
  if (logicalRate == g_audioRate) return;
  echobase.init(logicalRate / 2, BOARD.ebSDA, BOARD.ebSCL, BOARD.ebDIN, BOARD.ebWS, BOARD.ebDOUT, BOARD.ebBCK, Wire);
  echobase.setSpeakerVolume(75);
  echobase.setMicGain(ES8311_MIC_GAIN_36DB);   // 30->36 dB: speech was only ~1025 mean-abs, low for onset/ASR
  echobase.setMicAdcVolume(70);
  g_audioRate = logicalRate;
  Serial.printf("[audio] init @ %u Hz logical (codec %u)\n", logicalRate, logicalRate / 2);
}

// DC-removed mean-absolute amplitude. Removing the per-frame DC offset matters:
// the ES8311 ADC settles with a slow DC drift after init, and raw mean-abs counts
// that drift as "noise", which is one source of the unstable noise floor.
static int frameEnergy(const int16_t* f, size_t n) {
  long mean = 0;
  for (size_t i = 0; i < n; i++) mean += f[i];
  mean /= (long)n;
  uint32_t sum = 0;
  for (size_t i = 0; i < n; i++) { int v = (int)f[i] - (int)mean; sum += (v < 0 ? -v : v); }
  return (int)(sum / n);
}

// Thresholds from a floor estimate: a multiplicative margin AND an absolute margin,
// so a near-zero floor still needs a real signal and a high floor isn't unreachable.
static inline int onsetThrFor(int floor)   { int t = max((int)(floor * ONSET_MULT),   floor + 180); return max(t, MIN_THRESHOLD); }
static inline int silenceThrFor(int floor) { int t = max((int)(floor * SILENCE_MULT), floor + 70);  return max(t, MIN_SILENCE); }

// Seed the ambient-noise estimate robustly. Paints the listening orb instantly, then
// discards ~120 ms of ADC/I2S settling and takes a LOW-ENVELOPE estimate (mean of the
// quietest few frames) rather than the plain mean — so an EMI/Wi-Fi burst in one frame
// can't inflate the floor (the bug behind onset thresholds landing above the user's
// voice). The onset-wait loop then adapts this downward further, so a high seed can't
// lock the mic out.
static void measureNoiseFloor() {
  audioInit(MIC_RATE);
  g_uiLabel = g_freehand ? "FREEHAND" : "LISTENING"; g_uiTranscript = ""; g_uiReply = "";
  renderUI(g_freehand ? ST_ARMED : ST_LISTEN, 0.0f);          // instant feedback on tap
  static int16_t f[FRAME];
  for (int i = 0; i < 6; i++) echobase.record((uint8_t*)f, FRAME_B);   // discard ~120 ms of settling
  int lo[3] = { 999999, 999999, 999999 };                              // 3 quietest frames seen
  for (int i = 0; i < 14; i++) {                                       // ~280 ms sample window
    echobase.record((uint8_t*)f, FRAME_B);
    int e = frameEnergy(f, FRAME);
    if      (e < lo[0]) { lo[2] = lo[1]; lo[1] = lo[0]; lo[0] = e; }
    else if (e < lo[1]) { lo[2] = lo[1]; lo[1] = e; }
    else if (e < lo[2]) { lo[2] = e; }
  }
  g_floorEst   = (lo[0] + lo[1] + lo[2]) / 3;
  g_onsetThr   = onsetThrFor(g_floorEst);
  g_silenceThr = silenceThrFor(g_floorEst);
  Serial.printf("[vad] floor %d -> onset %d / silence %d\n", g_floorEst, g_onsetThr, g_silenceThr);
}

static bool buttonInterrupt() {
  M5.update();
  if (M5.BtnA.wasClicked() || M5.BtnA.wasHold()) { g_exit = true; return true; }
  return false;
}

// Capture one utterance: wait up to onsetMs for speech, then record to silence-end.
// Returns bytes captured (0 = no speech / aborted). Sets g_exit on a button press.
static size_t captureUtterance(uint32_t onsetMs, State armState) {
  static int16_t f[FRAME];
  g_exit = false;
  screen(armState, g_freehand ? "Freehand" : "Listening", "speak...");

  // 1. wait for onset — the floor adapts DOWN here so a high seed self-corrects, and
  //    onset fires on 2-of-last-3 loud frames (bursty speech) or one clear spike.
  uint32_t t0 = millis();
  int maxE = 0, hist[3] = { 0, 0, 0 }, hi = 0;
  while (true) {
    if (buttonInterrupt()) return 0;
    echobase.record((uint8_t*)f, FRAME_B);
    int e = frameEnergy(f, FRAME);
    if (e > maxE) maxE = e;
    drawMeter(e);
    // adaptive floor: fast down when quiet, very slow up in the soft band, frozen on loud
    if      (e < g_floorEst)               g_floorEst = (g_floorEst * 4 + e) / 5;
    else if (e * 100 < g_floorEst * 135)   g_floorEst = (g_floorEst * 200 + e) / 201;
    int onset = onsetThrFor(g_floorEst);
    hist[hi % 3] = e; hi++;
    int above = (hist[0] > onset) + (hist[1] > onset) + (hist[2] > onset);
    bool spike = e > max(g_floorEst * 3, g_floorEst + 450);
    if ((hi >= 3 && above >= 2) || spike) break;
    if (millis() - t0 > onsetMs) {             // no speech onset in the window
      Serial.printf("[vad] NO onset; loudest frame %d (onset %d, floor %d) — did the mic hear you?\n",
                    maxE, onset, g_floorEst);
      return 0;
    }
  }
  g_silenceThr = silenceThrFor(g_floorEst);    // lock the pause threshold from the adapted floor

  // 2. capture until trailing silence or max length (keep the onset frame)
  screen(ST_LISTEN, g_freehand ? "Freehand" : "Listening", "");
  size_t total = 0;
  memcpy(g_buf + total, f, FRAME_B); total += FRAME_B;
  uint32_t capStart = millis(); int sil = 0;
  while (total + FRAME_B <= MAX_BYTES) {
    echobase.record((uint8_t*)f, FRAME_B);
    int e = frameEnergy(f, FRAME);
    drawMeter(e);
    memcpy(g_buf + total, f, FRAME_B); total += FRAME_B;
    if (e < g_silenceThr) { sil += 20; if ((uint32_t)sil >= HANGOVER_MS) break; }   // truly quiet → count pause
    else sil = 0;                                                                    // speaking / soft band → keep alive
    if (millis() - capStart > MAX_UTTER_MS) break;
  }
  int16_t* sp = (int16_t*)g_buf; size_t ns = total / 2; long acc = 0; int pk = 0;
  for (size_t i = 0; i < ns; i++) { int v = sp[i]; int a = v < 0 ? -v : v; acc += a; if (a > pk) pk = a; }
  Serial.printf("[mic] captured %u bytes  peak %d  mean %ld  (onset %d / silence %d)\n",
                (unsigned)total, pk, ns ? acc / (long)ns : 0, g_onsetThr, g_silenceThr);
  return total;
}

// ── HTTP ────────────────────────────────────────────────────────────────────
static bool httpBeginOn(HTTPClient& http, const String& url, WiFiClient& tcp, WiFiClientSecure& tls) {
  if (url.startsWith("https://")) { tls.setInsecure(); return http.begin(tls, url); }
  return http.begin(tcp, url);
}
static bool httpBegin(HTTPClient& http, const String& url) {
  return httpBeginOn(http, url, g_tcp, g_tls);
}

static void writeWavHeader(uint8_t* h, uint32_t nSamples, uint32_t rate) {
  uint32_t dataBytes = nSamples * 2, chunk = 36 + dataBytes, byteRate = rate * 2;
  uint16_t fmt = 1, ch = 1, ba = 2, bps = 16; uint32_t sub1 = 16;
  memcpy(h, "RIFF", 4);          memcpy(h + 4, &chunk, 4);
  memcpy(h + 8, "WAVE", 4);      memcpy(h + 12, "fmt ", 4);
  memcpy(h + 16, &sub1, 4);      memcpy(h + 20, &fmt, 2);
  memcpy(h + 22, &ch, 2);        memcpy(h + 24, &rate, 4);
  memcpy(h + 28, &byteRate, 4);  memcpy(h + 32, &ba, 2);
  memcpy(h + 34, &bps, 2);       memcpy(h + 36, "data", 4);   // data tag at 36 (was 38 — 2-byte bug)
  memcpy(h + 40, &dataBytes, 4);                              // data size at 40 (was 42)
}

static void playWavBuffer(uint8_t* buf, size_t len) {
  if (len < 44 || memcmp(buf, "RIFF", 4) != 0) {
    Serial.printf("[play] reply not WAV (0x%02x) — pin daemon speak=kokoro\n", buf[0]);
    return;
  }
  uint16_t ch; memcpy(&ch, buf + 22, 2);
  uint32_t rate; memcpy(&rate, buf + 24, 4);
  uint32_t byteRate; memcpy(&byteRate, buf + 28, 4);
  uint16_t bps; memcpy(&bps, buf + 34, 2);
  Serial.printf("[play] WAV ch=%u rate=%u byteRate=%u bps=%u len=%u\n",
                (unsigned)ch, (unsigned)rate, (unsigned)byteRate, (unsigned)bps, (unsigned)len);
  size_t off = 12, dataOff = 0; uint32_t dataLen = 0;
  while (off + 8 <= len) {
    uint32_t sz; memcpy(&sz, buf + off + 4, 4);
    if (memcmp(buf + off, "data", 4) == 0) { dataOff = off + 8; dataLen = sz; break; }
    off += 8 + sz + (sz & 1);
  }
  if (!dataOff) { dataOff = 44; dataLen = len - 44; }
  if (dataOff + dataLen > len) dataLen = len - dataOff;
  // I2S DMA needs a 4-byte-aligned start; ffmpeg's LIST chunk leaves an unaligned
  // data offset (e.g. 78) that garbles playback — move the PCM to the buffer start.
  if (dataOff) memmove(buf, buf + dataOff, dataLen);
  // Pass the reply's TRUE rate — audioInit compensates the codec's 2x internally now
  // (requests half the logical rate), so playback lands at the right pitch and the mic
  // path stays honest 16k for the daemon. (Replies are transcoded to MIC_RATE, so this
  // is usually a no-op re-init; a different reply rate re-inits correctly.)
  audioInit(rate);
  echobase.setMute(false);
  echobase.play(buf, dataLen);
}

static void playUrl(const String& path) {
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
        playWavBuffer(b, got);
        heap_caps_free(b);
      }
    }
  }
  http.end();
}

static String friendlyError(const String& err) {
  if (err == "too quiet") return "Speak closer";
  if (err == "Could not understand audio") return "Didn't catch that";
  if (err == "no audio") return "No audio";
  return err;
}

// Read the NDJSON stream from device_turn_stream and play each sentence as it lands,
// so sentence 1 starts speaking while the rest still generates (the fast-mode win).
// Accumulates bytes across TCP packets (a line may be split); while playUrl() blocks
// playing one sentence, the daemon keeps streaming into the socket buffer.
static void streamTurn(HTTPClient& http) {
  WiFiClient* s = http.getStreamPtr();
  String reply = "", buf = "";
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
              g_uiLabel = "SPEAKING"; g_uiTranscript = (const char*)(ev["text"] | ""); g_uiReply = "";
              renderUI(ST_SPEAK, 0.5f);
              Serial.printf("[ui] heard: %s\n", g_uiTranscript.c_str());
            } else if (!strcmp(t, "text")) {
              reply += (const char*)(ev["delta"] | "");
              g_uiReply = reply; renderUI(ST_SPEAK, 0.5f);
            } else if (!strcmp(t, "audio")) {
              const char* u = ev["url"] | "";
              if (u[0]) playUrl(String(u));            // blocking; socket buffers meanwhile
            } else if (!strcmp(t, "error")) {
              screen(ST_ERROR, "Aura", friendlyError(String((const char*)(ev["error"] | ""))));
              delay(1500); return;
            } else if (!strcmp(t, "done")) {
              if (!ev["session"]["messages"].isNull()) serializeJson(ev["session"]["messages"], g_sessionMessages);
              g_companion = ev["session"]["companion"] | "";
              Serial.printf("[turn] stt=%d gen=%d ms (fast=%d) | %s\n",
                            (int)(ev["timing"]["stt_ms"] | 0), (int)(ev["timing"]["gen_ms"] | 0),
                            (int)(ev["timing"]["fast"] | 0), reply.c_str());
              return;
            }
          }
        }
        buf = "";
        deadline = millis() + 60000;                   // reset idle deadline on activity
      } else if (c != '\r') {
        buf += c;
      }
    }
    if (!http.connected() && !s->available()) break;
    delay(5);
  }
}

// Send one captured utterance through device_turn, render + play the reply.
static void handleTurn(size_t bytes) {
  screen(ST_THINK, "Thinking", "");
  uint32_t nSamples = bytes / 2;
  const String B = "----eosSat";
  String pre =
    "--" + B + "\r\nContent-Disposition: form-data; name=\"messages\"\r\n\r\n" + g_sessionMessages + "\r\n"
    "--" + B + "\r\nContent-Disposition: form-data; name=\"companion\"\r\n\r\n" + g_companion + "\r\n"
    "--" + B + "\r\nContent-Disposition: form-data; name=\"device_id\"\r\n\r\n" + DEVICE_ID + "\r\n"
    "--" + B + "\r\nContent-Disposition: form-data; name=\"device_board\"\r\n\r\n" + BOARD.id + "\r\n"
    "--" + B + "\r\nContent-Disposition: form-data; name=\"fast\"\r\n\r\n" + (FAST_MODE ? "1" : "0") + "\r\n"
    "--" + B + "\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\n";
  String post = "\r\n--" + B + "--\r\n";

  size_t bodyLen = pre.length() + 44 + bytes + post.length();
  uint8_t* body = (uint8_t*) heap_caps_malloc(bodyLen, MALLOC_CAP_SPIRAM);
  if (!body) { screen(ST_ERROR, "ERR", "no memory"); delay(1200); return; }
  size_t o = 0;
  memcpy(body + o, pre.c_str(), pre.length());   o += pre.length();
  writeWavHeader(body + o, nSamples, MIC_RATE);  o += 44;
  memcpy(body + o, g_buf, bytes);                o += bytes;
  memcpy(body + o, post.c_str(), post.length()); o += post.length();

  // Fast mode streams (play sentence 1 while the rest generates); quality mode
  // returns one collapsed JSON reply with all audio.
  const char* path = FAST_MODE ? "/voice-assistant/api/device_turn_stream"
                               : "/voice-assistant/api/device_turn";
  HTTPClient http;
  // Fast path uses dedicated stream clients so playUrl() (g_tcp) doesn't collide with
  // the still-open streaming socket while it fetches each sentence's audio.
  bool ok = FAST_MODE ? httpBeginOn(http, String(DAEMON_BASE) + path, g_streamTcp, g_streamTls)
                      : httpBegin(http, String(DAEMON_BASE) + path);
  if (!ok) { heap_caps_free(body); return; }
  http.setConnectTimeout(10000);
  http.setTimeout(45000);
  http.addHeader("Authorization", String("Bearer ") + AUTH_TOKEN);
  http.addHeader("Content-Type", "multipart/form-data; boundary=" + B);
  uint32_t _t0 = millis();
  int code = http.POST(body, bodyLen);
  heap_caps_free(body);
  Serial.printf("[http] %s -> %d in %lu ms (fast=%d)\n",
                FAST_MODE ? "stream" : "turn", code, (unsigned long)(millis() - _t0), FAST_MODE ? 1 : 0);

  if (code != 200) { screen(ST_ERROR, "Network", "HTTP " + String(code)); delay(1500); http.end(); return; }

  if (FAST_MODE) { streamTurn(http); http.end(); return; }   // streaming fast path

  // ── Collapsed path (quality mode) ──
  String payload = http.getString();
  http.end();
  JsonDocument doc;
  if (deserializeJson(doc, payload)) { screen(ST_ERROR, "Error", "bad reply"); delay(1200); return; }
  if (doc["error"].is<const char*>()) {
    screen(ST_ERROR, "Aura", friendlyError(String((const char*)doc["error"])));
    delay(1500);
    return;
  }

  String transcript = doc["transcript"] | "";
  String reply      = doc["reply_text"] | "";
  g_uiLabel = "SPEAKING"; g_uiTranscript = transcript; g_uiReply = reply;
  renderUI(ST_SPEAK, 0.5f);
  Serial.printf("[ui] %s | %s\n", transcript.c_str(), reply.c_str());

  if (!doc["session"]["messages"].isNull()) serializeJson(doc["session"]["messages"], g_sessionMessages);
  g_companion = doc["session"]["companion"] | "";

  if (!doc["timing"].isNull())              // where did the time go: STT vs LLM+TTS vs transcode
    Serial.printf("[turn] stt=%d gen=%d xcode=%d ms (fast=%d)\n",
                  (int)(doc["timing"]["stt_ms"] | 0), (int)(doc["timing"]["gen_ms"] | 0),
                  (int)(doc["timing"]["xcode_ms"] | 0), (int)(doc["timing"]["fast"] | 0));

  for (JsonVariant v : doc["audio_urls"].as<JsonArray>())
    playUrl(String((const char*)v));
}

// ── Modes ───────────────────────────────────────────────────────────────────
static void doSingleTurn() {
  measureNoiseFloor();
  size_t n = captureUtterance(SINGLE_ONSET_MS, ST_LISTEN);
  if (n >= MIN_BYTES) handleTurn(n);
  idleScreen();
}

// Local mic->speaker loopback: record an utterance, play it straight back.
// No network — proves the device's own audio in + out path end to end.
static void loopbackTest() {
  measureNoiseFloor();
  size_t n = captureUtterance(SINGLE_ONSET_MS, ST_LISTEN);
  if (n >= MIN_BYTES) {
    int16_t* sp = (int16_t*)g_buf; size_t ns = n / 2; long acc = 0; int pk = 0;
    for (size_t i = 0; i < ns; i++) { int v = sp[i]; int a = v < 0 ? -v : v; acc += a; if (a > pk) pk = a; }
    Serial.printf("[loopback] %u bytes  peak %d  mean %ld  -> playing back\n", (unsigned)n, pk, ns ? acc / (long)ns : 0);
    screen(ST_SPEAK, "Playback", String((unsigned)(n / 32)) + " ms");
    audioInit(MIC_RATE);
    echobase.setMute(false);
    echobase.play(g_buf, n);            // raw PCM @ MIC_RATE
  } else {
    Serial.println("[loopback] nothing captured");
  }
  idleScreen();
}

static void runFreehand() {
  g_freehand = true;
  measureNoiseFloor();
  while (g_freehand) {
    size_t n = captureUtterance(FREEHAND_IDLE_MS, ST_ARMED);
    if (g_exit) break;            // a tap/hold exited freehand
    if (n == 0) break;            // 3 min silence -> close
    if (n >= MIN_BYTES) handleTurn(n);
  }
  g_freehand = false;
  idleScreen();
}

// ── Lifecycle ───────────────────────────────────────────────────────────────
static bool g_paramsSaved = false;
static void onSaveParams() { g_paramsSaved = true; }

static void loadConfig() {
  g_prefs.begin("eos", true);
  DAEMON_BASE = g_prefs.getString("daemon", SECRET_DAEMON_BASE);
  AUTH_TOKEN  = g_prefs.getString("token",  SECRET_AUTH_TOKEN);
  DEVICE_ID   = g_prefs.getString("devid",  "atoms3r-01");
  g_prefs.end();
}
static void saveConfig(const String& d, const String& t, const String& id) {
  g_prefs.begin("eos", false);
  g_prefs.putString("daemon", d); g_prefs.putString("token", t); g_prefs.putString("devid", id);
  g_prefs.end();
  DAEMON_BASE = d; AUTH_TOKEN = t; DEVICE_ID = id;
}

// On-device provisioning: WiFiManager runs a captive portal ("EOS-Satellite") for
// Wi-Fi + daemon URL + token + device id, persisted to NVS. forcePortal=true (hold
// the front button at boot) re-opens it to reconfigure without reflashing.
static void provision(bool forcePortal) {
  WiFiManager wm;
  wm.setConnectTimeout(20);          // fail a stale/out-of-range saved AP fast → fall to the portal
  wm.setConfigPortalTimeout(300);    // 5 min to set up from your phone
  wm.setSaveParamsCallback(onSaveParams);
  WiFiManagerParameter pD("daemon", "Daemon URL (http://ip:9000)", DAEMON_BASE.c_str(), 96);
  WiFiManagerParameter pT("token",  "Auth token",                  AUTH_TOKEN.c_str(),  96);
  WiFiManagerParameter pI("devid",  "Device id",                   DEVICE_ID.c_str(),   40);
  wm.addParameter(&pD); wm.addParameter(&pT); wm.addParameter(&pI);
  screen(ST_THINK, "Setup", "Join EOS-Satellite");
  bool ok = forcePortal ? wm.startConfigPortal("EOS-Satellite") : wm.autoConnect("EOS-Satellite");
  if (g_paramsSaved) saveConfig(pD.getValue(), pT.getValue(), pI.getValue());
  screen(ok ? ST_IDLE : ST_ERROR, ok ? "Wi-Fi OK" : "Setup timeout",
         ok ? WiFi.localIP().toString() : "hold btn = setup");
  delay(800);
}

// ── Device registry (announce to the daemon's /devices framework) ────────────
static void postJson(const String& path, const String& json) {
  HTTPClient http;
  if (!httpBegin(http, String(DAEMON_BASE) + path)) return;
  http.setTimeout(8000);
  http.addHeader("Authorization", String("Bearer ") + AUTH_TOKEN);
  http.addHeader("Content-Type", "application/json");
  http.POST(json);            // fire-and-forget; 404 is fine if the devices app isn't installed
  http.end();
}
static void registerDevice() {
  postJson("/devices/api/register",
           String("{\"id\":\"") + DEVICE_ID + "\",\"category\":\"controller\",\"board\":\"" +
           BOARD.id + "\",\"name\":\"Voice satellite\",\"units\":[\"echo-base\"]}");
}
static void heartbeatDevice() {
  postJson(String("/devices/api/devices/") + DEVICE_ID + "/heartbeat", "{}");
}
static void wake() {
  g_lastAct = millis();
  if (g_asleep) { M5.Display.setBrightness(DEFAULT_BRIGHT); g_asleep = false; idleScreen(); }
}
static void newConversation() {           // double-tap: drop the threaded context
  g_sessionMessages = "[]";
  g_companion = "";
  screen(ST_IDLE, "New chat", "context cleared");
  delay(700);
  idleScreen();
}
static bool checkShake() {                 // a sharp accel spike = shake (board with an IMU)
  if (!BOARD.hasImu || !M5.Imu.isEnabled()) return false;
  static uint32_t lastShake = 0;
  float ax = 0, ay = 0, az = 0;
  if (!M5.Imu.getAccel(&ax, &ay, &az)) return false;
  float mag = sqrtf(ax * ax + ay * ay + az * az);   // ~1.0 g at rest
  if (fabsf(mag - 1.0f) > 0.8f && millis() - lastShake > 1500) { lastShake = millis(); return true; }
  return false;
}

void setup() {
  Serial.begin(115200);
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setTextWrap(true);
  g_cv.setColorDepth(16);
  g_cv.setPsram(true);                  // 128x128x2 = 32 KB sprite in PSRAM
  g_cv.createSprite(128, 128);
  audioInit(MIC_RATE);
  g_buf = (uint8_t*) heap_caps_malloc(MAX_BYTES, MALLOC_CAP_SPIRAM);
  if (!g_buf) { screen(ST_ERROR, "PSRAM FAIL", "enable OPI PSRAM"); while (true) delay(1000); }
#if LOOPBACK_TEST
  screen(ST_IDLE, "Loopback", "Tap=rec+play");   // no Wi-Fi needed for the audio test
#else
  loadConfig();
  M5.update();
  bool forceSetup = false;
  if (M5.BtnA.isPressed()) {                  // a deliberate ~1.5s hold forces the setup portal;
    uint32_t t0 = millis();                   // a brief/accidental face touch does NOT
    while (M5.BtnA.isPressed() && millis() - t0 < 1500) { M5.update(); delay(20); }
    forceSetup = (millis() - t0 >= 1500);
  }
  provision(forceSetup);
  registerDevice();
  g_audioRate = 0; audioInit(MIC_RATE);   // re-init the codec AFTER Wi-Fi (the heavy radio/portal
                                          // stack can leave the mic path degraded)
#endif
  idleScreen();
  g_lastAct = millis();
}

void loop() {
  M5.update();
#if LOOPBACK_TEST
  if (M5.BtnA.wasClicked() || M5.BtnA.wasHold()) loopbackTest();   // tap = record + play back
#else
  if (millis() - g_lastHb > 60000) { g_lastHb = millis(); heartbeatDevice(); }  // stay 'online' in the registry
  bool shake = checkShake();
  if (g_asleep) {                                  // asleep: any gesture only wakes (consumed)
    if (shake || M5.BtnA.wasClicked() || M5.BtnA.wasHold() || M5.BtnA.wasDoubleClicked()) wake();
    delay(20); return;
  }
  if (shake)                           { doSingleTurn();    g_lastAct = millis(); }  // shake: hands-free talk
  else if (M5.BtnA.wasDoubleClicked()) { newConversation(); g_lastAct = millis(); }  // double-tap: fresh context
  else if (M5.BtnA.wasHold())          { runFreehand();     g_lastAct = millis(); }  // long-press: freehand
  else if (M5.BtnA.wasClicked())       { doSingleTurn();    g_lastAct = millis(); }  // tap: one turn
  if (millis() - g_lastAct > SLEEP_MS) { M5.Display.setBrightness(0); g_asleep = true; }  // idle → screen off
  else {                                                          // animate the idle orb ~30 fps
    static uint32_t lastDraw = 0;
    if (millis() - lastDraw > 33) { lastDraw = millis(); renderUI(ST_IDLE, 0.0f); }
  }
#endif
  delay(5);
}
