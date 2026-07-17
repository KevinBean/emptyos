/*
 * EmptyOS Worklog Capture BLE button for M5Stack StickS3.
 *
 * The device contains no Wi-Fi credentials, daemon address, or EmptyOS token.
 * A Mac bridge subscribes to the BLE characteristic, calls Worklog Capture on
 * localhost, then writes the result back for display on the StickS3.
 */

static const char* DEVICE_NAME = "EOS Worklog Button";
static const char* SERVICE_UUID = "74f71000-7d3a-4a6f-9b5b-3ca6e4313f52";
static const char* CAPTURE_UUID = "74f71001-7d3a-4a6f-9b5b-3ca6e4313f52";
static const uint8_t DISPLAY_BRIGHTNESS = 100;
static const uint32_t ACK_TIMEOUT_MS = 15000;

enum UiState { UI_ADVERTISING, UI_READY, UI_SENDING, UI_SUCCESS, UI_WARNING, UI_ERROR };

static NimBLEServer* server = nullptr;
static NimBLECharacteristic* captureCharacteristic = nullptr;
static volatile bool connected = false;
static volatile bool connectionStateChanged = false;
static volatile bool restartAdvertising = false;
static volatile bool ackReady = false;
static char ackBuffer[96] = {0};
static uint32_t sequenceNumber = 0;
static uint32_t sentAt = 0;
static bool awaitingAck = false;

static uint16_t stateColor(UiState state) {
  switch (state) {
    case UI_READY:   return M5.Display.color565(50, 178, 155);
    case UI_SENDING: return M5.Display.color565(246, 183, 56);
    case UI_SUCCESS: return M5.Display.color565(45, 190, 108);
    case UI_WARNING: return M5.Display.color565(240, 145, 45);
    case UI_ERROR:   return M5.Display.color565(224, 74, 74);
    default:         return M5.Display.color565(75, 150, 230);
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
  M5.Display.drawCenterString("WORKLOG BLE", width / 2, height - 19);
  Serial.printf("[ui] %s | %s\n", title.c_str(), detail.c_str());
}

static void drawConnectionState() {
  if (connected) drawScreen(UI_READY, "READY", "Tap to capture this Mac");
  else drawScreen(UI_ADVERTISING, "PAIRING", "Start the Mac bridge");
}

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer*, NimBLEConnInfo&) override {
    connected = true;
    connectionStateChanged = true;
    Serial.println("[ble] Mac connected");
  }

  void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int reason) override {
    connected = false;
    connectionStateChanged = true;
    awaitingAck = false;
    restartAdvertising = true;
    Serial.printf("[ble] Mac disconnected reason=%d\n", reason);
  }
};

class CaptureCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo&) override {
    const std::string value = characteristic->getValue();
    const size_t length = min(value.length(), sizeof(ackBuffer) - 1);
    memcpy(ackBuffer, value.data(), length);
    ackBuffer[length] = '\0';
    ackReady = true;
    Serial.printf("[ble] ack: %s\n", ackBuffer);
  }
};

static void startBluetooth() {
  NimBLEDevice::init(DEVICE_NAME);
  server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());
  server->advertiseOnDisconnect(true);

  NimBLEService* service = server->createService(SERVICE_UUID);
  captureCharacteristic = service->createCharacteristic(
    CAPTURE_UUID,
    NIMBLE_PROPERTY::READ |
    NIMBLE_PROPERTY::NOTIFY |
    NIMBLE_PROPERTY::WRITE
  );
  captureCharacteristic->setCallbacks(new CaptureCallbacks());
  captureCharacteristic->setValue("ready");
  server->start();

  NimBLEAdvertising* advertising = NimBLEDevice::getAdvertising();
  advertising->setName(DEVICE_NAME);
  advertising->addServiceUUID(SERVICE_UUID);
  advertising->enableScanResponse(true);
  const bool started = advertising->start();
  Serial.printf("[ble] advertising started=%d\n", (int)started);
  if (!started) {
    drawScreen(UI_ERROR, "BLE FAILED", "Advertising did not start");
    while (true) delay(1000);
  }
}

static void requestCapture() {
  if (!connected || awaitingAck) {
    if (!connected) drawScreen(UI_WARNING, "NO MAC", "Start the Mac bridge");
    return;
  }

  sequenceNumber++;
  const String payload = "capture:" + String(sequenceNumber);
  captureCharacteristic->setValue(payload.c_str());
  captureCharacteristic->notify();
  awaitingAck = true;
  sentAt = millis();
  drawScreen(UI_SENDING, "CAPTURING", "Waiting for EmptyOS...");
  Serial.printf("[ble] notify: %s\n", payload.c_str());
}

static void showAcknowledgement(const String& ack) {
  awaitingAck = false;
  if (ack.startsWith("ok:")) {
    String app = ack.substring(3);
    drawScreen(UI_SUCCESS, "CAPTURED", app.length() ? app : "Queued for review");
  } else if (ack == "warn:no-image") {
    drawScreen(UI_WARNING, "QUEUED", "No image - check Mac permission");
  } else if (ack == "error:auth") {
    drawScreen(UI_ERROR, "AUTH FAILED", "Check EmptyOS config");
  } else if (ack == "error:app-missing") {
    drawScreen(UI_ERROR, "APP MISSING", "Install Worklog Capture");
  } else if (ack == "error:daemon-offline") {
    drawScreen(UI_ERROR, "EOS OFFLINE", "Start EmptyOS on the Mac");
  } else {
    drawScreen(UI_ERROR, "CAPTURE FAILED", ack.substring(0, 40));
  }
  delay(1800);
  drawConnectionState();
}

void setup() {
  Serial.begin(115200);
  auto config = M5.config();
  M5.begin(config);
  M5.Display.setBrightness(DISPLAY_BRIGHTNESS);
  M5.Display.setTextWrap(true);
  drawScreen(UI_ADVERTISING, "STARTING", "Bluetooth Low Energy");
  startBluetooth();
  drawConnectionState();
}

void loop() {
  M5.update();

  if (connectionStateChanged) {
    connectionStateChanged = false;
    drawConnectionState();
  }

  if (restartAdvertising) {
    restartAdvertising = false;
    delay(250);
    server->startAdvertising();
    drawConnectionState();
  }

  if (ackReady) {
    ackReady = false;
    showAcknowledgement(String(ackBuffer));
  }

  if (connected && !awaitingAck && M5.BtnA.wasClicked()) requestCapture();
  else if (!connected && M5.BtnA.wasClicked()) {
    drawScreen(UI_WARNING, "NO MAC", "Start the Mac bridge");
    delay(1000);
    drawConnectionState();
  }

  if (awaitingAck && millis() - sentAt > ACK_TIMEOUT_MS) {
    awaitingAck = false;
    drawScreen(UI_ERROR, "NO REPLY", "Check EmptyOS on the Mac");
    delay(1800);
    drawConnectionState();
  }
  delay(10);
}
