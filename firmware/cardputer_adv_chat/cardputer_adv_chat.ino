/*
 * cardputer_adv_chat.ino — EmptyOS Cardputer Adv chat terminal
 * Target: M5Stack Cardputer Adv (ESP32-S3, 56-key keyboard, 1.14" 240x135 LCD)
 *
 * Type a message, hit Enter, and it goes straight to one fixed EmptyOS rooms
 * agent (POST /rooms/api/chat) — a pocket chat terminal, not a capture/note
 * inbox. Mirrors plugins/telegram's two-way bridge: any [DO:] action the
 * agent proposes is gated server-side (the room's gate_mode="gate" + holds
 * on the allowlisted verbs) and lands as a pending Apply/Reject card.
 *
 * Config is provisioned ON-DEVICE: a WiFiManager captive portal
 * ("EOS-Cardputer") collects Wi-Fi + daemon URL + auth token + room/agent id
 * + device id, persisted to NVS. Hold the backtick (`) key at boot ~1.5s to
 * re-open it. secrets.h is optional (pre-fills portal defaults only).
 *
 * Conversation history is NOT tracked on-device — the daemon's rooms app
 * persists it per room id server-side, so this firmware is a dumb
 * send/receive terminal (unlike the voice-satellite firmware, which has to
 * echo `session.messages` back on every turn).
 *
 * Hardware-capability features (round 2):
 *   - Tab           -> show the oldest pending [DO:] action (if any); Y/N
 *                      applies/rejects it via the same rooms review gate
 *                      every other surface (web UI, Telegram) already uses.
 *   - IMU sleep/wake -> screen off after ~30s idle; wakes on any keypress or
 *                      a shake (BMI270), mirrors voice_satellite.ino.
 *   - "/ir <name>"   -> fires a saved NEC IR code via the Adv's IR LED.
 *                      Entirely on-device, never touches the network — add
 *                      real codes to IR_CODES below.
 *   - battery/rssi   -> reported on every periodic re-register, visible in
 *                      the daemon's /devices/ dashboard.
 *
 * No audio — Cardputer's ES8311 codec path is an unverified/deferred driver
 * (different from M5EchoBase), same note as CoreS3/Cardputer in
 * firmware/voice_satellite/voice_satellite.ino. Text only, on purpose.
 *
 * Libraries: M5Unified, M5Cardputer, IRremote, ArduinoJson (v7), WiFiManager (tzapu).
 * Board: Tools -> Board -> M5Stack -> "M5Cardputer".
 * VERIFY[hardware]: confirm the Adv variant is recognized under this same
 * board entry (M5Cardputer library was updated for CardputerADV June 2026).
 */

// IR pin + library behaviour flags must be defined BEFORE the M5Cardputer/
// IRremote includes (mirrors the M5Cardputer library's own bundled example,
// examples/Basic/ir_nec/ir_nec.ino, read directly to ground this).
#define DISABLE_CODE_FOR_RECEIVER   // the Adv has no IR receiver — TX only
#define SEND_PWM_BY_TIMER
#define IR_TX_PIN 44

#include <M5Cardputer.h>
#include <IRremote.hpp>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <WiFiManager.h>      // tzapu/WiFiManager — captive-portal provisioning
#include <Preferences.h>      // NVS-persisted config

// secrets.h is OPTIONAL — it only pre-fills defaults for the setup portal.
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
#ifndef SECRET_ROOM_ID
  #define SECRET_ROOM_ID "cardputer"     // matches the daemon-side room/agent id
#endif

static const char*    AP_NAME                = "EOS-Cardputer";
static const uint8_t  DISPLAY_BRIGHTNESS     = 100;
static const uint32_t REQUEST_TIMEOUT_MS     = 45000;   // an LLM turn is slower than a plain capture POST
static const uint32_t REREGISTER_INTERVAL_MS = 60000;   // also refreshes battery/rssi telemetry (register is idempotent)
static const uint32_t SLEEP_MS               = 30000;   // screen off after this much idle
static const size_t   MAX_INPUT_CHARS        = 220;     // keep the POST body + on-screen buffer sane
static const int      BODY_SIZE              = 2;        // textSize for typed input + reply body (was 1 — too small to read)
static const int      LINE_H                 = 18;        // px between body lines at BODY_SIZE
static const int      CONTENT_Y0             = 18;        // first body line's y, just below the header bar
static const int      VISIBLE_LINES          = 5;        // reply lines shown at once at BODY_SIZE (240x135)
static const int      WRAP_WIDTH_PX          = 228;       // leaves a small margin at 240px wide

// ── Local IR remote — send-only, never touches the network ──────────────────
// The Adv has no IR receiver, so codes can't be learned on-device. Add real
// NEC {address, command} pairs for your own devices, e.g.:
//   {"tv-power", 0x1111, 0x34},
struct IrCode { const char* name; uint16_t address; uint8_t command; };
static const IrCode IR_CODES[] = {
  // (empty until real codes are added — "/ir <name>" will report "Unknown code")
};
static const size_t IR_CODES_LEN = sizeof(IR_CODES) / sizeof(IR_CODES[0]);

// ── Runtime config — loaded from NVS (portal-set); defaults from secrets.h if present ──
static String      daemonBase;
static String      authToken;
static String      roomId;      // rooms `agent_id` this device talks to
static String      deviceId;
static Preferences prefs;
static WiFiClient       tcp;
static WiFiClientSecure tls;
static bool paramsSaved = false;
static bool busy        = false;
static uint32_t lastReregister = 0;

// ── Chat / UI state ──────────────────────────────────────────────────────────
enum UiState { UI_TYPING, UI_SENDING, UI_REPLY, UI_ERROR, UI_SETUP };
enum Screen  { SCR_TYPING, SCR_REPLY, SCR_PENDING };   // which content is on screen (survives sleep/wake)

static String              g_input        = "";   // what the user is currently typing
static String              g_reply        = "";   // last full reply text
static std::vector<String> g_replyLines;           // word-wrapped reply
static int                 g_scrollTop    = 0;      // first visible reply line
static int                 g_pendingCount = 0;      // server_results length on the last reply
static Screen               g_screen      = SCR_TYPING;

static String  g_pendId      = "";   // oldest open pending action's id (empty = none)
static String  g_pendSummary = "";   // "app.method {args}" shown on SCR_PENDING

static bool     g_asleep  = false;
static uint32_t g_lastAct = 0;       // last user-activity ms (keypress or shake)

static M5Canvas g_cv(&M5Cardputer.Display);   // offscreen sprite — flicker-free redraws on every keystroke

// ── Drawing ───────────────────────────────────────────────────────────────────
static uint16_t stateColor(UiState state) {
  switch (state) {
    case UI_SENDING: return g_cv.color565(246, 183, 56);
    case UI_REPLY:    return g_cv.color565(45, 190, 108);
    case UI_ERROR:    return g_cv.color565(224, 74, 74);
    case UI_SETUP:    return g_cv.color565(75, 150, 230);
    default:          return g_cv.color565(50, 178, 155);   // UI_TYPING
  }
}

// Word-wraps `text` into lines that fit `maxWidth` px at the given text size.
static std::vector<String> wrapText(const String& text, int maxWidth, int textSize = BODY_SIZE) {
  g_cv.setTextSize(textSize);   // textWidth() below depends on this
  std::vector<String> lines;
  String line = "", word = "";
  int n = text.length();
  for (int i = 0; i <= n; i++) {
    char c = (i < n) ? text[i] : ' ';
    if (c == ' ' || c == '\n') {
      String test = line.length() ? line + " " + word : word;
      if (g_cv.textWidth(test) > maxWidth && line.length()) {
        lines.push_back(line);
        line = word;
      } else {
        line = test;
      }
      word = "";
      if (c == '\n') { lines.push_back(line); line = ""; }
    } else if (c != '\r') {
      word += c;
    }
  }
  if (line.length() || lines.empty()) lines.push_back(line);
  return lines;
}

static void drawHeaderFooter(UiState state, const String& footer) {
  const int w = g_cv.width(), h = g_cv.height();
  const uint16_t accent = stateColor(state);
  g_cv.fillRoundRect(4, 3, w - 8, 4, 2, accent);
  g_cv.setTextDatum(top_left);
  g_cv.setTextColor(accent, TFT_BLACK);
  g_cv.setTextSize(1);
  g_cv.drawString("EmptyOS \xC2\xB7 " + roomId, 6, 10);   // "·" = UTF-8 middle dot
  if (footer.length()) {
    g_cv.setTextDatum(bottom_left);
    g_cv.setTextColor(g_cv.color565(150, 160, 170), TFT_BLACK);
    g_cv.drawString(footer, 6, h - 3);
  }
}

static void renderTyping() {
  g_cv.fillScreen(TFT_BLACK);
  drawHeaderFooter(UI_TYPING, WiFi.status() == WL_CONNECTED ? "Enter=send  Tab=pending" : "No Wi-Fi");
  g_cv.setTextDatum(top_left);
  g_cv.setTextColor(TFT_WHITE, TFT_BLACK);
  // Word-wrap the input across multiple BODY_SIZE lines (not a single truncated
  // line) so it's actually readable while typing; show the tail once it's long.
  std::vector<String> lines = wrapText("> " + g_input, WRAP_WIDTH_PX);
  int start = max(0, (int)lines.size() - VISIBLE_LINES);
  int y = CONTENT_Y0;
  for (int i = start; i < (int)lines.size(); i++) {
    String line = lines[i];
    if (i == (int)lines.size() - 1) line += "_";   // cursor on the last visible line
    g_cv.drawString(line, 6, y);
    y += LINE_H;
  }
  g_cv.pushSprite(0, 0);
}

static void renderSending() {
  g_cv.fillScreen(TFT_BLACK);
  drawHeaderFooter(UI_SENDING, "");
  g_cv.setTextDatum(top_left);
  g_cv.setTextColor(stateColor(UI_SENDING), TFT_BLACK);
  g_cv.setTextSize(BODY_SIZE);
  g_cv.drawString("Thinking...", 6, CONTENT_Y0);
  g_cv.pushSprite(0, 0);
}

static void renderReply() {
  g_cv.fillScreen(TFT_BLACK);
  String footer = "; up  . down  any key: new";
  if (g_pendingCount > 0) {
    footer = String("\xE2\x8F\xB3 ") + g_pendingCount + " pending review  |  " + footer;   // hourglass emoji
  }
  drawHeaderFooter(UI_REPLY, footer);
  g_cv.setTextDatum(top_left);
  g_cv.setTextColor(TFT_WHITE, TFT_BLACK);
  g_cv.setTextSize(BODY_SIZE);
  int y = CONTENT_Y0;
  int shown = 0;
  for (int i = g_scrollTop; i < (int)g_replyLines.size() && shown < VISIBLE_LINES; i++, shown++) {
    g_cv.drawString(g_replyLines[i], 6, y);
    y += LINE_H;
  }
  g_cv.pushSprite(0, 0);
}

static void renderError(const String& title, const String& detail) {
  g_cv.fillScreen(TFT_BLACK);
  drawHeaderFooter(UI_ERROR, "");
  g_cv.setTextDatum(top_left);
  g_cv.setTextColor(stateColor(UI_ERROR), TFT_BLACK);
  g_cv.setTextSize(BODY_SIZE);
  g_cv.drawString(title, 6, CONTENT_Y0);
  g_cv.setTextColor(g_cv.color565(190, 200, 210), TFT_BLACK);
  std::vector<String> lines = wrapText(detail, WRAP_WIDTH_PX);   // detail can be longer than one line at BODY_SIZE
  int y = CONTENT_Y0 + LINE_H;
  for (size_t i = 0; i < lines.size() && i < 3; i++) { g_cv.drawString(lines[i], 6, y); y += LINE_H; }
  g_cv.pushSprite(0, 0);
  Serial.printf("[ui] %s | %s\n", title.c_str(), detail.c_str());
}

static void renderSetup(const String& title, const String& detail) {
  g_cv.fillScreen(TFT_BLACK);
  drawHeaderFooter(UI_SETUP, "");
  g_cv.setTextDatum(top_left);
  g_cv.setTextColor(stateColor(UI_SETUP), TFT_BLACK);
  g_cv.setTextSize(BODY_SIZE);
  g_cv.drawString(title, 6, CONTENT_Y0);
  g_cv.setTextColor(g_cv.color565(190, 200, 210), TFT_BLACK);
  std::vector<String> lines = wrapText(detail, WRAP_WIDTH_PX);
  int y = CONTENT_Y0 + LINE_H;
  for (size_t i = 0; i < lines.size() && i < 3; i++) { g_cv.drawString(lines[i], 6, y); y += LINE_H; }
  g_cv.pushSprite(0, 0);
  Serial.printf("[ui] %s | %s\n", title.c_str(), detail.c_str());
}

// A brief inline message (IR send result, Apply/Reject outcome) — not a full
// UiState of its own, just a short-lived flash before returning to typing.
static void flashMessage(const String& msg, uint16_t color) {
  g_cv.fillScreen(TFT_BLACK);
  drawHeaderFooter(UI_TYPING, "");
  g_cv.setTextDatum(top_left);
  g_cv.setTextColor(color, TFT_BLACK);
  g_cv.setTextSize(BODY_SIZE);
  g_cv.drawString(msg, 6, CONTENT_Y0);
  g_cv.pushSprite(0, 0);
}

static void renderPending() {
  g_cv.fillScreen(TFT_BLACK);
  drawHeaderFooter(UI_REPLY, g_pendId.length() ? "Y=apply  N=reject  Tab=back" : "Tab: back to typing");
  g_cv.setTextDatum(top_left);
  g_cv.setTextColor(TFT_WHITE, TFT_BLACK);
  if (!g_pendId.length()) {
    g_cv.setTextSize(BODY_SIZE);
    g_cv.drawString("No pending actions", 6, CONTENT_Y0);
  } else {
    std::vector<String> lines = wrapText(g_pendSummary, WRAP_WIDTH_PX);
    int y = CONTENT_Y0;
    for (size_t i = 0; i < lines.size() && i < (size_t)VISIBLE_LINES; i++) { g_cv.drawString(lines[i], 6, y); y += LINE_H; }
  }
  g_cv.pushSprite(0, 0);
}

// Redraws whichever screen is currently active — used after waking from sleep.
static void redrawCurrent() {
  switch (g_screen) {
    case SCR_REPLY:   renderReply();   break;
    case SCR_PENDING: renderPending(); break;
    default:          renderTyping();  break;
  }
}

// ── HTTP ──────────────────────────────────────────────────────────────────────
static bool beginHttp(HTTPClient& http, const String& url) {
  if (url.startsWith("https://")) {
    tls.setInsecure();
    return http.begin(tls, url);
  }
  return http.begin(tcp, url);
}

static int postJson(const String& path, const String& body, String* response = nullptr) {
  if (!daemonBase.length() || WiFi.status() != WL_CONNECTED) return -1;
  HTTPClient http;
  if (!beginHttp(http, daemonBase + path)) return -2;
  http.setConnectTimeout(7000);
  http.setTimeout(REQUEST_TIMEOUT_MS);
  if (authToken.length()) http.addHeader("Authorization", "Bearer " + authToken);
  http.addHeader("Content-Type", "application/json");
  const int code = http.POST(body);
  if (response != nullptr && code > 0) *response = http.getString();
  http.end();
  return code;
}

static int getJson(const String& path, String* response = nullptr) {
  if (!daemonBase.length() || WiFi.status() != WL_CONNECTED) return -1;
  HTTPClient http;
  if (!beginHttp(http, daemonBase + path)) return -2;
  http.setConnectTimeout(7000);
  http.setTimeout(REQUEST_TIMEOUT_MS);
  if (authToken.length()) http.addHeader("Authorization", "Bearer " + authToken);
  const int code = http.GET();
  if (response != nullptr && code > 0) *response = http.getString();
  http.end();
  return code;
}

static String jsonEscape(const String& input) {
  String out;
  out.reserve(input.length() + 8);
  for (size_t i = 0; i < input.length(); ++i) {
    const char c = input[i];
    if (c == '\\' || c == '"') out += '\\';
    if (c >= 0x20) out += c;
  }
  return out;
}

// Battery level (0-100), or -1 if the driver can't read it — the devices app
// already knows to drop a -1 rather than store it (apps/extension/others/devices/app.py
// api_register: "battery -1 = driver can't read it -> drop rather than store").
// VERIFY[hardware]: M5Cardputer.Power is the guessed sub-object name, mirroring
// the already-confirmed .Keyboard/.Display/.Imu pattern.
static int batteryLevel() {
  return M5Cardputer.Power.getBatteryLevel();
}

// Register (or re-register — idempotent, refreshes last_seen + merges fields
// per devices/app.py's own docstring) into the daemon's /devices registry.
// Called once at boot and periodically thereafter so battery/rssi telemetry
// stays fresh (there is no separate telemetry-only endpoint to call instead).
static void registerDevice() {
  const int battery = batteryLevel();
  const int rssi = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0;
  const String body = String("{\"id\":\"") + jsonEscape(deviceId) +
    "\",\"category\":\"input\",\"board\":\"cardputer_adv\"," +
    "\"name\":\"Cardputer chat\"," +
    "\"units\":[\"keyboard\",\"display\",\"imu\",\"ir\"]," +
    "\"battery\":" + String(battery) + "," +
    "\"rssi\":" + String(rssi) + "}";
  const int code = postJson("/devices/api/register", body);
  Serial.printf("[device] register -> %d (battery=%d rssi=%d)\n", code, battery, rssi);
}

// ── Local IR remote ──────────────────────────────────────────────────────────
static bool sendIrCode(const String& name) {
  for (size_t i = 0; i < IR_CODES_LEN; i++) {
    if (name.equalsIgnoreCase(IR_CODES[i].name)) {
      IrSender.sendNEC(IR_CODES[i].address, IR_CODES[i].command, 0);
      Serial.printf("[ir] sent %s (addr=0x%04X cmd=0x%02X)\n", IR_CODES[i].name, IR_CODES[i].address, IR_CODES[i].command);
      return true;
    }
  }
  return false;
}

// Sends one chat turn to the configured room and renders the reply. Uses
// ArduinoJson to build the request body (not manual string concat like the
// register call above) because this is the one call that carries arbitrary
// user-typed text and needs real JSON escaping.
static void sendChat(const String& text) {
  busy = true;
  g_input = "";
  renderSending();

  JsonDocument req;
  req["agent_id"] = roomId;
  req["text"] = text;
  String body;
  serializeJson(req, body);

  String response;
  const int code = postJson("/rooms/api/chat", body, &response);
  Serial.printf("[chat] HTTP %d | %s\n", code, response.c_str());

  if (code == 200) {
    JsonDocument doc;
    const DeserializationError err = deserializeJson(doc, response);
    if (!err) {
      g_reply = doc["response"] | "(no reply)";
      g_pendingCount = 0;
      if (doc["server_results"].is<JsonArray>()) {
        g_pendingCount = (int)doc["server_results"].as<JsonArray>().size();
      }
      g_replyLines = wrapText(g_reply, WRAP_WIDTH_PX);
      g_scrollTop = 0;
      g_screen = SCR_REPLY;
      renderReply();
    } else {
      renderError("BAD REPLY", "EmptyOS returned invalid data");
      delay(1800);
      g_screen = SCR_TYPING;
      renderTyping();
    }
  } else if (code == 401 || code == 403) {
    renderError("AUTH FAILED", "Check the token in setup");
    delay(1800);
    g_screen = SCR_TYPING;
    renderTyping();
  } else if (code == 404) {
    renderError("ROOM MISSING", "Check the room/agent id");
    delay(1800);
    g_screen = SCR_TYPING;
    renderTyping();
  } else if (WiFi.status() != WL_CONNECTED) {
    renderError("NO WI-FI", "Hold ` at boot for setup");
    delay(1800);
    g_screen = SCR_TYPING;
    renderTyping();
  } else {
    renderError("DAEMON OFFLINE", "Check daemon URL and port");
    delay(1800);
    g_screen = SCR_TYPING;
    renderTyping();
  }
  busy = false;
}

// ── Physical Approve/Reject (Tab) — the same rooms review gate every other
// surface (web UI, global pending dashboard, Telegram cards) already uses.
// GET /rooms/api/pending?status=open returns a bare JSON array, oldest first
// (apps/public/standard/rooms/pending.py:967, list_pending) — show the first.
static void showPending() {
  busy = true;
  String response;
  const int code = getJson("/rooms/api/pending?status=open", &response);
  busy = false;

  g_pendId = "";
  g_pendSummary = "";
  if (code == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, response) && doc.is<JsonArray>() && doc.size() > 0) {
      JsonObject first = doc[0];
      g_pendId = String((const char*)(first["id"] | ""));
      String appId  = String((const char*)(first["app"] | ""));
      String method = String((const char*)(first["method"] | ""));
      String argsStr;
      if (first["args"].is<JsonObject>()) {
        serializeJson(first["args"], argsStr);
        if (argsStr.length() > 60) argsStr = argsStr.substring(0, 57) + "...";   // keep it on-screen
      }
      g_pendSummary = appId + "." + method + "  " + argsStr;
    }
  }
  g_screen = SCR_PENDING;
  renderPending();
}

static void resolvePending(bool apply) {
  if (!g_pendId.length()) return;
  busy = true;
  String resp;
  const int code = postJson("/rooms/api/pending/" + g_pendId + (apply ? "/apply" : "/reject"), "{}", &resp);
  busy = false;
  if (code == 200) {
    flashMessage(apply ? "Applied" : "Rejected", g_cv.color565(45, 190, 108));
  } else {
    flashMessage("Failed: HTTP " + String(code), g_cv.color565(224, 74, 74));
  }
  delay(1200);
  g_pendId = "";
  g_screen = SCR_TYPING;
  g_input = "";
  renderTyping();
}

// ── Config / provisioning ──────────────────────────────────────────────────────
static void loadConfig() {
  prefs.begin("eos", true);
  daemonBase = prefs.getString("daemon", SECRET_DAEMON_BASE);
  authToken  = prefs.getString("token",  SECRET_AUTH_TOKEN);
  roomId     = prefs.getString("room",   SECRET_ROOM_ID);
  deviceId   = prefs.getString("devid",  "cardputer-01");
  prefs.end();
  while (daemonBase.endsWith("/")) daemonBase.remove(daemonBase.length() - 1);
}

static void saveConfig(const String& daemon, const String& token, const String& room, const String& id) {
  prefs.begin("eos", false);
  prefs.putString("daemon", daemon);
  prefs.putString("token", token);
  prefs.putString("room", room);
  prefs.putString("devid", id);
  prefs.end();
  daemonBase = daemon; authToken = token; roomId = room; deviceId = id;
  while (daemonBase.endsWith("/")) daemonBase.remove(daemonBase.length() - 1);
}

static void onSaveParams() { paramsSaved = true; }

// On-device provisioning: WiFiManager runs a captive portal for Wi-Fi + daemon
// URL + token + room id + device id, persisted to NVS. forcePortal=true (hold
// backtick at boot) re-opens it to reconfigure without reflashing. Reuses the
// "eos" NVS namespace / "daemon"/"token"/"devid" keys the other three boards
// already use, so a device already set up for voice/worklog keeps its Wi-Fi,
// daemon URL, token, and device id here too — only "room" is new.
static void provision(bool forcePortal) {
  WiFiManager wm;
  wm.setConnectTimeout(20);
  wm.setConfigPortalTimeout(300);
  wm.setSaveParamsCallback(onSaveParams);
  WiFiManagerParameter pD("daemon", "Daemon URL (http://ip:9000)", daemonBase.c_str(), 96);
  WiFiManagerParameter pT("token",  "Auth token",                  authToken.c_str(),  96);
  WiFiManagerParameter pR("room",   "Room / agent id",             roomId.c_str(),     40);
  WiFiManagerParameter pI("devid",  "Device id",                   deviceId.c_str(),   40);
  wm.addParameter(&pD); wm.addParameter(&pT); wm.addParameter(&pR); wm.addParameter(&pI);

  renderSetup("SETUP", String("Join ") + AP_NAME);
  const bool connected = forcePortal ? wm.startConfigPortal(AP_NAME) : wm.autoConnect(AP_NAME);
  if (paramsSaved) saveConfig(pD.getValue(), pT.getValue(), pR.getValue(), pI.getValue());
  renderSetup(connected ? "WI-FI OK" : "SETUP ENDED",
              connected ? WiFi.localIP().toString() : "Hold ` to retry");
  delay(1000);
}

// True if `key` is among the characters pressed in the current key-state frame.
static bool keyHeld(char key) {
  if (!M5Cardputer.Keyboard.isPressed()) return false;
  Keyboard_Class::KeysState st = M5Cardputer.Keyboard.keysState();
  for (auto c : st.word) if (c == key) return true;
  return false;
}

// ── IMU sleep/wake ───────────────────────────────────────────────────────────
// A sharp accel-magnitude spike = shake. Same threshold as
// firmware/voice_satellite/voice_satellite.ino's checkShake(). CORRECTED
// during compile: M5Cardputer.h (read directly) shows M5Cardputer's other
// sub-objects (.Display/.Power/.Speaker/.Mic) are just references to the
// shared M5Unified M5.* singleton, and IMU isn't re-exposed on M5Cardputer
// at all — reached via the same M5.Imu voice_satellite.ino already uses.
static bool checkShake() {
  if (!M5.Imu.isEnabled()) return false;
  static uint32_t lastShake = 0;
  float ax = 0, ay = 0, az = 0;
  if (!M5.Imu.getAccel(&ax, &ay, &az)) return false;
  float mag = sqrtf(ax * ax + ay * ay + az * az);   // ~1.0 g at rest
  if (fabsf(mag - 1.0f) > 0.8f && millis() - lastShake > 1500) { lastShake = millis(); return true; }
  return false;
}

static void wake() {
  g_lastAct = millis();
  if (g_asleep) {
    M5Cardputer.Display.setBrightness(DISPLAY_BRIGHTNESS);
    g_asleep = false;
    redrawCurrent();
  }
}

// ── Lifecycle ────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  auto cfg = M5.config();
  // VERIFY[hardware]: some M5Cardputer library releases use
  // M5Cardputer.begin(cfg, true) — check against the installed version if this
  // doesn't compile or the keyboard/display don't init.
  M5Cardputer.begin(cfg);
  M5Cardputer.Display.setBrightness(DISPLAY_BRIGHTNESS);
  M5Cardputer.Display.setTextWrap(false);   // we wrap manually (word-wrap, not char-wrap)
  g_cv.setColorDepth(16);
  g_cv.createSprite(M5Cardputer.Display.width(), M5Cardputer.Display.height());   // ~63 KB, fits internal RAM — no PSRAM needed

  IrSender.begin(DISABLE_LED_FEEDBACK);
  IrSender.setSendPin(IR_TX_PIN);

  loadConfig();
  M5Cardputer.update();
  bool forceSetup = false;
  if (keyHeld('`')) {
    const uint32_t started = millis();
    while (keyHeld('`') && millis() - started < 1500) { M5Cardputer.update(); delay(20); }
    forceSetup = (millis() - started >= 1500);
  }

  provision(forceSetup);
  registerDevice();
  renderTyping();
  g_lastAct = millis();
}

void loop() {
  M5Cardputer.update();

  if (millis() - lastReregister >= REREGISTER_INTERVAL_MS) {
    lastReregister = millis();
    registerDevice();   // idempotent — also refreshes battery/rssi + last_seen
  }

  if (busy) { delay(10); return; }   // ignore input while a request is in flight

  const bool keyEvent = M5Cardputer.Keyboard.isChange() && M5Cardputer.Keyboard.isPressed();
  const bool shakeEvent = checkShake();

  if (g_asleep) {
    if (keyEvent || shakeEvent) wake();   // consumed — nothing else processes this loop
    delay(20);
    return;
  }

  if (keyEvent || shakeEvent) g_lastAct = millis();   // shake while awake just extends awake time

  if (millis() - g_lastAct > SLEEP_MS) {
    M5Cardputer.Display.setBrightness(0);
    g_asleep = true;
    delay(20);
    return;
  }

  if (!keyEvent) { delay(5); return; }

  Keyboard_Class::KeysState st = M5Cardputer.Keyboard.keysState();

  if (g_screen == SCR_REPLY) {
    // Scroll with `;` (up) / `.` (down) — Cardputer has no dedicated arrow
    // keys; this mirrors the common community convention for this keyboard.
    bool scrolled = false;
    for (auto c : st.word) {
      if (c == ';')      { if (g_scrollTop > 0) { g_scrollTop--; scrolled = true; } }
      else if (c == '.') { if (g_scrollTop < (int)g_replyLines.size() - 1) { g_scrollTop++; scrolled = true; } }
    }
    if (scrolled) {
      renderReply();
    } else if (st.enter || st.del || st.tab || !st.word.empty()) {
      // Any other key dismisses the reply and starts a new message.
      g_screen = SCR_TYPING;
      g_input = "";
      renderTyping();
    }
    delay(5);
    return;
  }

  if (g_screen == SCR_PENDING) {
    if (g_pendId.length()) {
      bool resolved = false;
      for (auto c : st.word) {
        if (c == 'y' || c == 'Y') { resolvePending(true);  resolved = true; break; }
        if (c == 'n' || c == 'N') { resolvePending(false); resolved = true; break; }
      }
      if (resolved) { delay(5); return; }
    }
    if (st.tab || st.enter || st.del || !st.word.empty()) {
      // Dismiss back to typing without resolving anything.
      g_screen = SCR_TYPING;
      g_input = "";
      renderTyping();
    }
    delay(5);
    return;
  }

  // ── Typing mode ──
  if (st.tab) { showPending(); delay(5); return; }

  bool dirty = false;
  for (auto c : st.word) {
    if (c == '`') continue;   // reserved for force-setup; don't type it into a message
    if (g_input.length() < MAX_INPUT_CHARS) { g_input += c; dirty = true; }
  }
  if (st.del && g_input.length()) { g_input.remove(g_input.length() - 1); dirty = true; }
  if (st.enter) {
    String toSend = g_input;
    toSend.trim();
    if (toSend.length() == 0) {
      renderTyping();
    } else if (toSend.startsWith("/ir ")) {
      String name = toSend.substring(4);
      name.trim();
      const bool ok = sendIrCode(name);
      g_input = "";
      flashMessage(ok ? ("Sent: " + name) : ("Unknown code: " + name),
                   ok ? g_cv.color565(45, 190, 108) : g_cv.color565(224, 74, 74));
      delay(900);
      renderTyping();
    } else {
      sendChat(toSend);
    }
  } else if (dirty) {
    renderTyping();
  }

  delay(5);
}
