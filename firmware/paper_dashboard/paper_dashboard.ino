/*
 * paper_dashboard.ino — EmptyOS e-ink dashboard satellite (skeleton)
 * Target: M5Stack M5Paper Color (ESP32-S3R8 · 16 MB flash · 8 MB PSRAM ·
 *         4" E Ink Spectra 6, 400×600 · RX8130CE RTC · 1250 mAh battery)
 *
 * A `display`-category device in the EmptyOS /devices registry. It is a PULL-mode
 * ambient panel, NOT an interactive screen: the daemon RENDERS the whole dashboard
 * as a PNG (`GET /devices/api/dashboard/<id>.png`) and the board just blits it, then
 * deep-sleeps until the next refresh. So there is no on-device layout engine, no
 * fonts on the board, and **no SD card needed** — the panel content lives in the
 * daemon. (The microSD slot is optional: only useful for an offline cache of the
 * last image or large local CJK fonts, neither of which this path requires.)
 *
 * Why this shape: Spectra 6 colour e-ink has a multi-second full refresh, so an
 * "orb that animates" (the voice satellite) makes no sense here. The right model is
 * wake → fetch → draw → deep-sleep. e-ink holds the image with zero power while
 * asleep, so 1250 mAh lasts weeks. Refresh is BUTTON-ONLY by default (BUTTON_ONLY
 * below): no timer wake at all — the panel redraws only when the top key is
 * pressed. Flip BUTTON_ONLY to false for the old timed cadence.
 *
 * Provisioning mirrors the voice satellite: a WiFiManager captive portal
 * ("EOS-Paper") collects Wi-Fi + daemon URL + token + device id into NVS. Hold a
 * button at boot to re-open it. secrets.h is optional (pre-fills portal defaults).
 *
 * Libraries: M5Unified (M5GFX has drawPng built in), WiFiManager (tzapu).
 * Board: Tools → Board → M5Stack → "M5PaperColor" (install the M5Stack boards
 * package: boards-manager URL
 * https://static-cdn.m5stack.com/resource/arduino/package_m5stack_index.json).
 * Buttons (docs.m5stack.com/en/core/PaperColor pinmap): A=GPIO10, B=GPIO9,
 * C=GPIO1 — all RTC-capable on the S3, so ext1 deep-sleep wake works.
 *
 * STATUS: HARDWARE-VERIFIED 2026-07-03 — full cycle live on a real M5PaperColor
 * (join → register 200 → dashboard 200 → e-ink draw → deep sleep). Rotation 0
 * confirmed correct. Button-only refresh + top-key ext1 press-to-wake verified on
 * hardware 2026-07-12 (press → wake → redraw). Bring-up gotchas from real hardware:
 *   - Download mode latches until a REAL reset; esptool's RTS reset can leave
 *     the chip parked in the ROM bootloader → `esptool --after watchdog-reset`
 *     is the reliable software kick into the app.
 *   - Serial monitoring must NOT assert DTR/RTS (holds the S3 in reset) —
 *     pyserial with dtr=False rts=False, or the IDE monitor.
 *   - DHCP on a mesh AP proved flaky after soft resets; attempt 2+ pins a
 *     static IP that MUST be the router's own lease for this MAC (this mesh
 *     proxy-ARPs unknown static IPs into a black hole).
 *   - NVS values from a half-completed portal save silently override secrets.h
 *     → compiled-in secrets are now authoritative (loadConfig).
 * The AUDIO superset of this board (ES8311 codec + mic + speaker) is
 * intentionally NOT used here — display-only. A later phase could fold the
 * voice path in (see README "Phase 2").
 */

#include <M5Unified.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <WiFiManager.h>      // tzapu/WiFiManager — captive-portal provisioning
#include <Preferences.h>      // NVS-persisted config
#include <esp_sleep.h>
#include <driver/rtc_io.h>    // RTC-domain pullups survive deep sleep (ext1 wake)

#if __has_include("secrets.h")
  #include "secrets.h"
#endif
#ifndef SECRET_DAEMON_BASE
  #define SECRET_DAEMON_BASE ""
#endif
#ifndef SECRET_AUTH_TOKEN
  #define SECRET_AUTH_TOKEN ""
#endif
#ifndef SECRET_WIFI_SSID
  #define SECRET_WIFI_SSID ""
#endif
#ifndef SECRET_WIFI_PASS
  #define SECRET_WIFI_PASS ""
#endif

// ── Config (NVS-loaded; portal-set) ──────────────────────────────────────────
static String      DAEMON_BASE;
static String      AUTH_TOKEN;
static String      DEVICE_ID;
static Preferences g_prefs;

// ── Panel / refresh ──────────────────────────────────────────────────────────
static const char* BOARD_ID       = "m5paper-color";
static const int   PANEL_W        = 400;     // Spectra 6 native portrait; swap for landscape
static const int   PANEL_H        = 600;
static const int   BTN_WAKE_GPIO  = 1;       // TOP side key — hardware-probed 2026-07-03:
                                             // top=GPIO1, middle=GPIO10, bottom=GPIO9,
                                             // all active-low. Wake source; tap = next
                                             // view, hold = voice turn.
// Button-only refresh: the panel never wakes on a timer — it sleeps until the
// top key is pressed. Set to false to restore the timed cadence below (and with
// it the daemon's X-EOS-Refresh-Min pacing).
static const bool     BUTTON_ONLY   = true;
static const uint64_t REFRESH_MIN = 15;      // FALLBACK deep-sleep minutes when BUTTON_ONLY is
                                             // false — the daemon's X-EOS-Refresh-Min response
                                             // header then overrides it per poll (quiet hours,
                                             // dirty-flag fast follow-up).
static const uint32_t HOLD_PORTAL_MS = 1200; // hold a button this long at boot → reopen setup portal

// Minutes until the next wake — seeded from REFRESH_MIN, overridden by the
// X-EOS-Refresh-Min header on each successful dashboard fetch. Unused when
// BUTTON_ONLY.
static uint64_t g_sleepMin = REFRESH_MIN;

static WiFiClient       g_tcp;
static WiFiClientSecure g_tls;

// ── HTTP helpers (mirror the voice satellite) ────────────────────────────────
static bool httpBegin(HTTPClient& http, const String& url) {
  if (url.startsWith("https://")) { g_tls.setInsecure(); return http.begin(g_tls, url); }
  return http.begin(g_tcp, url);
}

// ── NVS config ───────────────────────────────────────────────────────────────
static void loadConfig() {
  g_prefs.begin("eos", true);
  DAEMON_BASE = g_prefs.getString("daemon", SECRET_DAEMON_BASE);
  AUTH_TOKEN  = g_prefs.getString("token",  SECRET_AUTH_TOKEN);
  DEVICE_ID   = g_prefs.getString("devid",  "paper-01");
  g_prefs.end();
  // Compiled-in secrets are authoritative when present — a half-saved portal
  // visit can leave empty/truncated NVS values that silently 401 every call.
  if (strlen(SECRET_DAEMON_BASE)) DAEMON_BASE = SECRET_DAEMON_BASE;
  if (strlen(SECRET_AUTH_TOKEN))  AUTH_TOKEN  = SECRET_AUTH_TOKEN;
}
static void saveConfig(const String& d, const String& t, const String& id) {
  g_prefs.begin("eos", false);
  g_prefs.putString("daemon", d); g_prefs.putString("token", t); g_prefs.putString("devid", id);
  g_prefs.end();
  DAEMON_BASE = d; AUTH_TOKEN = t; DEVICE_ID = id;
}

static bool g_paramsSaved = false;
static void onSaveParams() { g_paramsSaved = true; }

// ── A tiny status line (drawn fast; the real content is the fetched PNG) ──────
static void status(const char* msg) {
  M5.Display.fillScreen(TFT_WHITE);
  M5.Display.setTextColor(TFT_BLACK, TFT_WHITE);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(16, 16);
  M5.Display.print(msg);
  M5.Display.display();   // e-ink: push the framebuffer
}

// ── Zero-touch connect: raw WiFi.begin with compiled-in creds (secrets.h) ────
// WiFiManager's single connect attempt proved flaky where a plain WiFi.begin
// join succeeds in ~2s (verified on real hardware) — so try this first; the
// portal below is the fallback / reconfiguration path.
static bool rawConnect() {
  if (!strlen(SECRET_WIFI_SSID)) return false;
  // Surface the radio's own failure reasons — join debugging is blind without them.
  WiFi.onEvent([](WiFiEvent_t e, WiFiEventInfo_t info) {
    if (e == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
      Serial.printf("[wifi] DISCONNECTED reason=%d\n", (int)info.wifi_sta_disconnected.reason);
    else if (e == ARDUINO_EVENT_WIFI_STA_CONNECTED)
      Serial.println("[wifi] ASSOC OK (waiting for IP)");
    else if (e == ARDUINO_EVENT_WIFI_STA_GOT_IP)
      Serial.printf("[wifi] GOT IP %s\n", WiFi.localIP().toString().c_str());
  });
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  // The SSID is a mesh (2 BSSIDs, same name). Scan, then PIN the join to the
  // strongest node's BSSID + channel — mesh client-steering can bounce a
  // joining STA between nodes and stall the handshake otherwise.
  uint8_t bssid[6] = {0};
  int channel = 0, best = -999;
  int n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    if (WiFi.SSID(i) == SECRET_WIFI_SSID && WiFi.RSSI(i) > best) {
      best = WiFi.RSSI(i);
      channel = WiFi.channel(i);
      memcpy(bssid, WiFi.BSSID(i), 6);
    }
  }
  WiFi.scanDelete();
  Serial.printf("[boot] scan: target '%s' best rssi=%d ch=%d bssid=%02x:%02x:%02x:%02x:%02x:%02x\n",
                SECRET_WIFI_SSID, best, channel,
                bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5]);

  for (int attempt = 1; attempt <= 3; attempt++) {
    Serial.printf("[boot] raw connect (attempt %d)…\n", attempt);
    WiFi.disconnect(true, true);
    delay(400);
    if (attempt >= 2) {
      // DHCP proved unreliable after soft resets on this board — pin a static
      // IP on retries. MUST be the address the router's own DHCP already
      // leased to this MAC (.19): this mesh proxy-ARPs unknown static IPs
      // into a black hole (verified — .212 answered from the router's MAC).
      Serial.println("[boot]   using static IP fallback (leased addr)");
      WiFi.config(IPAddress(192, 168, 20, 19), IPAddress(192, 168, 20, 1),
                  IPAddress(255, 255, 255, 0), IPAddress(192, 168, 20, 1));
    }
    if (channel > 0)
      WiFi.begin(SECRET_WIFI_SSID, SECRET_WIFI_PASS, channel, bssid);   // pinned join
    else
      WiFi.begin(SECRET_WIFI_SSID, SECRET_WIFI_PASS);
    // Cap TX power: full 19.5 dBm can brown the rail during association on
    // battery-fed S3 boards — a classic intermittent-join cause.
    WiFi.setTxPower(WIFI_POWER_11dBm);
    uint32_t t0 = millis();
    wl_status_t st = WiFi.status(), prev = (wl_status_t)255;
    while (st != WL_CONNECTED && millis() - t0 < 15000) {
      delay(250);
      st = WiFi.status();
      if (st != prev) { Serial.printf("[boot]   status=%d @%lums\n", (int)st, millis() - t0); prev = st; }
    }
    if (st == WL_CONNECTED) return true;
  }
  return false;
}

// ── On-device provisioning: WiFiManager captive portal ("EOS-Paper") ─────────
static bool provision(bool forcePortal) {
  WiFiManager wm;
  wm.setConnectTimeout(20);
  wm.setConfigPortalTimeout(300);
  wm.setSaveParamsCallback(onSaveParams);
  WiFiManagerParameter pD("daemon", "Daemon URL (http://ip:9000)", DAEMON_BASE.c_str(), 96);
  WiFiManagerParameter pT("token",  "Auth token",                  AUTH_TOKEN.c_str(),  96);
  WiFiManagerParameter pI("devid",  "Device id",                   DEVICE_ID.c_str(),   40);
  wm.addParameter(&pD); wm.addParameter(&pT); wm.addParameter(&pI);
  // Zero-touch provisioning: with secrets.h Wi-Fi creds compiled in, autoConnect
  // uses them when no creds are stored — no portal interaction needed at all.
  if (strlen(SECRET_WIFI_SSID)) wm.preloadWiFi(SECRET_WIFI_SSID, SECRET_WIFI_PASS);
  status("Setup: join EOS-Paper");
  bool ok = forcePortal ? wm.startConfigPortal("EOS-Paper") : wm.autoConnect("EOS-Paper");
  if (g_paramsSaved) saveConfig(pD.getValue(), pT.getValue(), pI.getValue());
  return ok;
}

// ── /devices registry: announce as a `display` device + heartbeat ────────────
static void postJson(const String& path, const String& json) {
  HTTPClient http;
  if (!httpBegin(http, String(DAEMON_BASE) + path)) {
    Serial.printf("[http] begin FAILED for %s\n", path.c_str());
    return;
  }
  http.setTimeout(8000);
  http.addHeader("Authorization", String("Bearer ") + AUTH_TOKEN);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(json);   // best-effort; 404 is fine if the devices app isn't installed
  Serial.printf("[http] POST %s -> %d\n", path.c_str(), code);
  http.end();
}
static void registerDevice() {
  // Telemetry rides the register body (setup() reruns every wake, so this is
  // effectively a per-poll report). battery: -1 when the driver can't read it.
  int  batt = M5.Power.getBatteryLevel();   // 0-100, or -1 if unsupported
  long rssi = (WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : 0;
  postJson("/devices/api/register",
           String("{\"id\":\"") + DEVICE_ID + "\",\"category\":\"display\",\"board\":\"" +
           BOARD_ID + "\",\"name\":\"Paper dashboard\",\"capabilities\":[\"display\"]" +
           ",\"battery\":" + batt + ",\"rssi\":" + rssi + "}");
}

// ── Fetch the server-rendered dashboard PNG and blit it ──────────────────────
static bool fetchAndDraw(bool cycleView) {
  HTTPClient http;
  String url = String(DAEMON_BASE) + "/devices/api/dashboard/" + DEVICE_ID +
               "?w=" + String(PANEL_W) + "&h=" + String(PANEL_H);
  if (cycleView) url += "&view=next";   // button wake → next configured view
  if (!httpBegin(http, url)) return false;
  // 40s: a cache-miss compose can invoke a cold LLM for the companion section
  // (~20s) + a weather refetch. -11 (read timeout) at 15s was a real failure.
  http.setTimeout(40000);
  http.addHeader("Authorization", String("Bearer ") + AUTH_TOKEN);
  // The daemon owns pacing: X-EOS-Refresh-Min carries the sleep hint
  // (base cadence / quiet hours / dirty-flag fast follow-up).
  const char* keys[] = {"X-EOS-Refresh-Min"};
  http.collectHeaders(keys, 1);
  int code = http.GET();
  if (code != 200) {
    http.end();
    status((String("HTTP ") + code + " — check daemon/token").c_str());
    return false;
  }
  Serial.printf("[http] GET dashboard -> %d\n", code);
  long hint = http.header("X-EOS-Refresh-Min").toInt();
  if (!BUTTON_ONLY && hint >= 1 && hint <= 240) g_sleepMin = (uint64_t)hint;
  // ~10–40 KB PNG → read into a String buffer (plenty of PSRAM headroom). For a
  // larger panel, switch to a streamed PSRAM buffer; for now this is simplest.
  String body = http.getString();
  http.end();
  if (body.length() < 8) { status("empty image"); return false; }

  // M5GFX decodes PNG natively — no separate PNG library needed. Rotation is
  // set once in setup() (native portrait); adjust there if the first flash
  // draws sideways.
  M5.Display.fillScreen(TFT_WHITE);
  M5.Display.drawPng((const uint8_t*)body.c_str(), body.length(), 0, 0);
  M5.Display.display();   // commit the framebuffer to the e-ink panel
  return true;
}

// ── Deep sleep until the next refresh (e-ink holds the image at zero power) ───
// With BUTTON_ONLY there is no timer wake at all: the panel holds its image
// indefinitely and only redraws when the top key is pressed.
static void sleepUntilRefresh() {
  if (!BUTTON_ONLY) esp_sleep_enable_timer_wakeup(g_sleepMin * 60ULL * 1000000ULL);
  // BtnA also wakes it (deep sleep → setup() reruns): tap = next view, hold =
  // voice turn. GPIO10 per the PaperColor pinmap (active-low).
  // ALL_LOW not ANY_LOW: this core's IDF predates the ANY_LOW alias — with a
  // single pin in the mask the two are identical.
  // The DIGITAL pullup from pinMode() powers down in deep sleep — the pin
  // must be held high by the RTC-domain pullup or the ALL_LOW wake never
  // fires (observed: button holds did nothing before this).
  rtc_gpio_init((gpio_num_t)BTN_WAKE_GPIO);
  rtc_gpio_set_direction((gpio_num_t)BTN_WAKE_GPIO, RTC_GPIO_MODE_INPUT_ONLY);
  rtc_gpio_pullup_en((gpio_num_t)BTN_WAKE_GPIO);
  rtc_gpio_pulldown_dis((gpio_num_t)BTN_WAKE_GPIO);
  // Keep the RTC peripheral domain powered in deep sleep — it feeds the
  // pullup above; without this the pin floats and ALL_LOW never fires.
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);
  esp_sleep_enable_ext1_wakeup(1ULL << BTN_WAKE_GPIO, ESP_EXT1_WAKEUP_ALL_LOW);
  Serial.printf("[sleep] btn level now=%d (1=idle-high ok) — sleeping\n",
                (int)rtc_gpio_get_level((gpio_num_t)BTN_WAKE_GPIO));
  esp_deep_sleep_start();
}

// Deep sleep resets the chip, so the whole cycle lives in setup(); loop() is unused.
void setup() {
  Serial.begin(115200);

  // Button gestures FIRST — the user's finger is on the key RIGHT NOW; the
  // CDC settle wait comes after (it used to run first, which meant a "hold"
  // had to last 3+ seconds to be seen — the invisible-wake bug).
  //   deep-sleep top-key TAP  → cycle to the next view
  //   deep-sleep top-key HOLD → voice turn (record while held — Phase 2)
  //   cold-boot  top-key HOLD → reopen the setup portal
  esp_sleep_wakeup_cause_t wake = esp_sleep_get_wakeup_cause();
  bool ext1Wake = (wake == ESP_SLEEP_WAKEUP_EXT1);
  // Release the RTC hold from the last sleep so pinMode works normally.
  rtc_gpio_deinit((gpio_num_t)BTN_WAKE_GPIO);
  pinMode(BTN_WAKE_GPIO, INPUT_PULLUP);
  delay(30);
  bool held = (digitalRead(BTN_WAKE_GPIO) == LOW);
  bool cycleView = false, voiceMode = false, forcePortal = false;
  if (ext1Wake) {
    if (held) {
      uint32_t t0 = millis();
      while (digitalRead(BTN_WAKE_GPIO) == LOW && millis() - t0 < 450) delay(20);
      voiceMode = (millis() - t0 >= 450);   // still held ~½s after wake = talk
    }
    cycleView = !voiceMode;
  } else if (held) {
    uint32_t t0 = millis();
    while (digitalRead(BTN_WAKE_GPIO) == LOW && millis() - t0 < HOLD_PORTAL_MS) delay(20);
    forcePortal = (millis() - t0 >= HOLD_PORTAL_MS);
  }

  // Voice mode races to the mic — skip the CDC wait (early prints may be lost).
  delay(voiceMode ? 200 : 2500);
  Serial.println("[boot] paper_dashboard start");
  Serial.printf("[boot] wake cause=%d (2=ext1 button, 4=timer) voice=%d cycle=%d\n",
                (int)wake, (int)voiceMode, (int)cycleView);
  loadConfig();
  Serial.printf("[boot] config: daemon=%s devid=%s token=%s\n",
                DAEMON_BASE.c_str(), DEVICE_ID.c_str(), AUTH_TOKEN.length() ? "set" : "EMPTY");

  bool connected = false;
  if (voiceMode) {
    // The user is holding the button and about to speak: kick the Wi-Fi join
    // off NON-BLOCKING first (initiated before display init, per the verified
    // ordering), then bring up the board + mic and record while it associates.
    Serial.println("[boot] voice mode — hold to talk");
    voiceKickWifi();
    auto cfg = M5.config();
    cfg.internal_spk = true;            // explicit: audio callbacks attach at begin
    cfg.internal_mic = true;
    M5.begin(cfg);
    // M5.begin() resets the button GPIOs to plain input (codex-diagnosed) —
    // re-apply the pullup or voiceBtnHeld() reads garbage and capture = 0 bytes.
    pinMode(BTN_WAKE_GPIO, INPUT_PULLUP);
    Serial.printf("[boot] M5.begin ok (voice), board=%d heldNow=%d\n",
                  (int)M5.getBoard(), (int)(digitalRead(BTN_WAKE_GPIO) == LOW));
    M5.Display.setRotation(0);
    runVoiceMode();                     // record → await Wi-Fi → turn → spoken reply
    connected = (WiFi.status() == WL_CONNECTED) || rawConnect();
  } else {
    // Wi-Fi JOIN BEFORE display init — hardware-verified ordering: with the
    // panel initialized first, the radio join fails/hangs (a bare sketch joins
    // in ~2s; after M5.begin the same join times out). Join first, then bring
    // the display up; the established connection survives display init.
    connected = !forcePortal && rawConnect();
    Serial.printf("[boot] raw connect: %s\n", connected ? "OK" : "no");

    auto cfg = M5.config();
    M5.begin(cfg);   // Board: "M5PaperColor" (M5Stack boards package) — see header.
    Serial.printf("[boot] M5.begin ok, board=%d\n", (int)M5.getBoard());
    M5.Display.setRotation(0);   // native portrait 400×600; VERIFY[board] on first flash
    Serial.printf("[boot] display %dx%d\n", M5.Display.width(), M5.Display.height());
  }

  if (!connected) {
    Serial.printf("[boot] provisioning (forcePortal=%d)…\n", (int)forcePortal);
    connected = provision(forcePortal) && WiFi.status() == WL_CONNECTED;
  }
  if (!connected) {
    Serial.println("[boot] Wi-Fi FAILED — sleeping");
    status("Wi-Fi setup timeout — hold a button at boot to retry");
    sleepUntilRefresh();   // try again next wake
    return;
  }
  Serial.printf("[boot] Wi-Fi ok: %s rssi=%d ip=%s\n",
                WiFi.SSID().c_str(), (int)WiFi.RSSI(), WiFi.localIP().toString().c_str());

  Serial.println("[boot] registering…");
  registerDevice();          // also serves as the first heartbeat + telemetry report
  Serial.println("[boot] fetching dashboard…");
  bool ok = fetchAndDraw(cycleView);   // draw the dashboard (status shown on failure)
  if (BUTTON_ONLY)
    Serial.printf("[boot] draw %s — sleeping until button\n", ok ? "OK" : "FAILED");
  else
    Serial.printf("[boot] draw %s — sleeping %llu min\n", ok ? "OK" : "FAILED",
                  (unsigned long long)g_sleepMin);
  sleepUntilRefresh();
}

void loop() { /* unused — deep sleep resets into setup() each cycle */ }
