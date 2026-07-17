/*
 * EmptyOS Worklog Capture button for M5Stack StickS3.
 *
 * Tap the front button to ask the EmptyOS daemon running on the Mac to capture
 * its current screen and app/browser context. The capture lands in the
 * worklog-capture review queue; this firmware never applies it to the vault.
 *
 * Hold the front button while booting to reopen the setup portal.
 */

#if __has_include("secrets.h")
  #include "secrets.h"
#endif
#ifndef SECRET_DAEMON_BASE
  #define SECRET_DAEMON_BASE ""
#endif
#ifndef SECRET_AUTH_TOKEN
  #define SECRET_AUTH_TOKEN ""
#endif

static const char* AP_NAME = "EOS-Worklog";
static const uint8_t DISPLAY_BRIGHTNESS = 100;
static const uint32_t REQUEST_TIMEOUT_MS = 15000;
static const uint32_t HEARTBEAT_INTERVAL_MS = 60000;

static String daemonBase;
static String authToken;
static String deviceId;
static Preferences prefs;
static WiFiClient tcp;
static WiFiClientSecure tls;
static bool paramsSaved = false;
static bool busy = false;
static uint32_t lastHeartbeat = 0;

enum UiState { UI_READY, UI_SENDING, UI_SUCCESS, UI_WARNING, UI_ERROR, UI_SETUP };

static uint16_t stateColor(UiState state) {
  switch (state) {
    case UI_SENDING: return M5.Display.color565(246, 183, 56);
    case UI_SUCCESS: return M5.Display.color565(45, 190, 108);
    case UI_WARNING: return M5.Display.color565(240, 145, 45);
    case UI_ERROR:   return M5.Display.color565(224, 74, 74);
    case UI_SETUP:   return M5.Display.color565(75, 150, 230);
    default:         return M5.Display.color565(50, 178, 155);
  }
}

static void drawScreen(UiState state, const String& title, const String& detail) {
  const int width = M5.Display.width();
  const int height = M5.Display.height();
  const uint16_t accent = stateColor(state);

  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.fillRoundRect(8, 8, width - 16, 8, 4, accent);
  M5.Display.setTextDatum(top_center);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(2);
  M5.Display.drawString(title, width / 2, 29);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(M5.Display.color565(190, 200, 210), TFT_BLACK);
  M5.Display.setTextWrap(true);
  M5.Display.drawCenterString(detail, width / 2, 62);

  M5.Display.setTextColor(accent, TFT_BLACK);
  M5.Display.drawCenterString("WORKLOG", width / 2, height - 19);
  Serial.printf("[ui] %s | %s\n", title.c_str(), detail.c_str());
}

static void drawReady() {
  drawScreen(UI_READY, "READY", WiFi.status() == WL_CONNECTED
             ? "Tap to capture this Mac"
             : "Wi-Fi disconnected");
}

static bool beginHttp(HTTPClient& http, const String& url) {
  if (url.startsWith("https://")) {
    tls.setInsecure();
    return http.begin(tls, url);
  }
  return http.begin(tcp, url);
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

static void registerDevice() {
  const String body = String("{\"id\":\"") + jsonEscape(deviceId) +
    "\",\"category\":\"controller\",\"board\":\"sticks3\"," +
    "\"name\":\"StickS3 Worklog Capture\"," +
    "\"units\":[\"button\",\"display\"]}";
  const int code = postJson("/devices/api/register", body);
  Serial.printf("[device] register -> %d\n", code);
}

static void heartbeatDevice() {
  const int code = postJson("/devices/api/devices/" + deviceId + "/heartbeat", "{}");
  Serial.printf("[device] heartbeat -> %d\n", code);
}

static void captureWorklog() {
  if (busy) return;
  busy = true;
  drawScreen(UI_SENDING, "CAPTURING", "Asking the Mac...");

  const String body = String("{\"source\":\"device\",\"note\":\"") +
    jsonEscape("StickS3 " + deviceId) + "\"}";
  String response;
  const int code = postJson("/worklog-capture/api/capture", body, &response);
  Serial.printf("[capture] HTTP %d | %s\n", code, response.c_str());

  if (code == 200) {
    JsonDocument doc;
    const DeserializationError err = deserializeJson(doc, response);
    if (!err && doc["ok"].as<bool>()) {
      const bool hasImage = doc["has_image"].as<bool>();
      const String app = doc["app"].as<String>();
      if (hasImage) {
        drawScreen(UI_SUCCESS, "CAPTURED", app.length() ? app : "Queued for review");
      } else {
        drawScreen(UI_WARNING, "QUEUED", "No image - check Mac permission");
      }
    } else {
      drawScreen(UI_ERROR, "BAD REPLY", "EmptyOS returned invalid data");
    }
  } else if (code == 401 || code == 403) {
    drawScreen(UI_ERROR, "AUTH FAILED", "Check the token in setup");
  } else if (code == 404) {
    drawScreen(UI_ERROR, "APP MISSING", "Install Worklog Capture");
  } else if (WiFi.status() != WL_CONNECTED) {
    drawScreen(UI_ERROR, "NO WI-FI", "Hold at boot for setup");
  } else {
    drawScreen(UI_ERROR, "MAC OFFLINE", "Check daemon URL and port");
  }

  delay(1800);
  drawReady();
  busy = false;
}

static void loadConfig() {
  prefs.begin("eos", true);
  daemonBase = prefs.getString("daemon", SECRET_DAEMON_BASE);
  authToken = prefs.getString("token", SECRET_AUTH_TOKEN);
  deviceId = prefs.getString("devid", "sticks3-worklog-01");
  prefs.end();
  while (daemonBase.endsWith("/")) daemonBase.remove(daemonBase.length() - 1);
}

static void saveConfig(const String& daemon, const String& token, const String& id) {
  prefs.begin("eos", false);
  prefs.putString("daemon", daemon);
  prefs.putString("token", token);
  prefs.putString("devid", id);
  prefs.end();
  daemonBase = daemon;
  authToken = token;
  deviceId = id;
  while (daemonBase.endsWith("/")) daemonBase.remove(daemonBase.length() - 1);
}

static void onSaveParams() {
  paramsSaved = true;
}

static void provision(bool forcePortal) {
  WiFiManager wm;
  wm.setConnectTimeout(20);
  wm.setConfigPortalTimeout(300);
  wm.setSaveParamsCallback(onSaveParams);
  WiFiManagerParameter daemonParam("daemon", "Daemon URL (http://ip:9000)", daemonBase.c_str(), 96);
  WiFiManagerParameter tokenParam("token", "Auth token", authToken.c_str(), 96);
  WiFiManagerParameter idParam("devid", "Device id", deviceId.c_str(), 40);
  wm.addParameter(&daemonParam);
  wm.addParameter(&tokenParam);
  wm.addParameter(&idParam);

  drawScreen(UI_SETUP, "SETUP", String("Join ") + AP_NAME);
  const bool connected = forcePortal ? wm.startConfigPortal(AP_NAME) : wm.autoConnect(AP_NAME);
  if (paramsSaved) saveConfig(daemonParam.getValue(), tokenParam.getValue(), idParam.getValue());
  drawScreen(connected ? UI_SUCCESS : UI_ERROR,
             connected ? "WI-FI OK" : "SETUP ENDED",
             connected ? WiFi.localIP().toString() : "Hold at boot to retry");
  delay(1000);
}

void setup() {
  Serial.begin(115200);
  auto config = M5.config();
  M5.begin(config);
  M5.Display.setBrightness(DISPLAY_BRIGHTNESS);
  M5.Display.setTextWrap(true);

  loadConfig();
  M5.update();
  bool forceSetup = false;
  if (M5.BtnA.isPressed()) {
    const uint32_t started = millis();
    while (M5.BtnA.isPressed() && millis() - started < 1500) {
      M5.update();
      delay(20);
    }
    forceSetup = millis() - started >= 1500;
  }

  provision(forceSetup);
  registerDevice();
  drawReady();
}

void loop() {
  M5.update();
  if (!busy && M5.BtnA.wasClicked()) captureWorklog();

  if (millis() - lastHeartbeat >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeat = millis();
    heartbeatDevice();
  }
  delay(10);
}

