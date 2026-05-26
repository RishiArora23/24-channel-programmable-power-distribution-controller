// ==========================================================================
// SECTION 1: Includes and Initial Definitions
// ==========================================================================
#include <Arduino.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>
#include <ArduinoJson.h>
#include <climits>
#include <WiFi.h>
#include <WiFiUdp.h>       
#include <WebServer.h>     
#include <DNSServer.h>     
#include <WiFiManager.h>   

// ==========================================================================
// SECTION 2: Hardware Configuration & Constants
// ==========================================================================
WiFiServer server(8080); 
WiFiClient client;
WiFiUDP udp;
WebServer webServer(80); 

const unsigned int UDP_PORT = 4210; 
const char* DISCOVERY_PACKET = "PYRO_DISCOVERY_REQUEST";
const char* DISCOVERY_REPLY = "I_AM_PYRO_CONTROLLER";

// --- SAFETY & POWER PINS ---
#define PIN_RAIL_MOSFET  27  
#define PIN_RAIL_SENSE   34  

// --- TFT Pins (HSPI) ---
#define TFT_MOSI 13
#define TFT_CLK  14
#define TFT_MISO 12
#define TFT_CS   5
#define TFT_DC   4
#define TFT_RST  17

SPIClass hspi(HSPI);
Adafruit_ILI9341 tft = Adafruit_ILI9341(&hspi, TFT_DC, TFT_CS, TFT_RST);

// --- Shift Register Pins ---
#define SRCLK_PIN 23
#define RCLK_PIN 21
#define SER_PIN 22
#define OE_PIN 19
#define SRCLR_PIN 18

// --- System Limits ---
#define NUM_SHIFT_REGISTERS 3
#define CUES_PER_SR 8
#define TOTAL_CUES (NUM_SHIFT_REGISTERS * CUES_PER_SR)
#define MAX_GROUPS 20
#define MAX_STEPS_PER_GROUP TOTAL_CUES
#define SERIAL_BUFFER_SIZE 8192 
const unsigned long CUE_ON_DURATION_MS = 1000;
const int POST_SEQUENCE_TIMEOUT_SEC = 15; 

const char* JSON_SEQUENCE_START_CMD = "UPLOAD_JSON_START";
const char* JSON_SEQUENCE_END_CMD = "END_JSON_PAYLOAD";

// ==========================================================================
// SECTION 3: Data Structures
// ==========================================================================
struct FiringEventStep {
    int cuesToFire[TOTAL_CUES];
    byte numCuesInThisEvent;
};
struct Group {
    String name;
    String pattern;
    int gap_ms;
    FiringEventStep firingEvents[MAX_STEPS_PER_GROUP];
    byte numFiringEventsInGroup;
};
Group sequenceGroups[MAX_GROUPS];
byte totalGroupsInSequence = 0;

// ==========================================================================
// SECTION 4: State Machine & Variables
// ==========================================================================
enum SystemState { 
    IDLE, 
    WAITING_FOR_JSON, 
    ARMED, 
    COUNTDOWN, 
    EXECUTING_SEQUENCE, 
    SEQUENCE_FINISHED, 
    ERROR_STATE 
};
SystemState currentState = IDLE;

bool sequenceIsLoaded = false;
unsigned long nextStepTimestamp = 0;
unsigned long sequenceStartTime = 0;
unsigned long sequenceFinishedTime = 0; 
byte currentGroupIndex = 0;
byte currentStepIndex = 0;
int totalCuesFiredCount = 0;

// Stats
int statTotalCues = 0;
unsigned long statTotalDurationMs = 0;

// Safety Variables
bool isSoftwareArmed = false; 
bool isRailLive = false;      
unsigned long lastSafetyCheck = 0;

byte shiftRegisterStates[NUM_SHIFT_REGISTERS] = {0};
unsigned long cueClearTimestamps[TOTAL_CUES] = {0};

char serialInputBuffer[SERIAL_BUFFER_SIZE];
byte serialInputIndex = 0;
String jsonDataBuffer = "";

// Display Tracking
bool lastWifiState = false;
bool lastClientState = false;
int lastDrawnGroupIdx = -1;
int lastDrawnStepIdx = -1;
String lastCountdownStr = "";
bool lastRailLiveState = false;
int lastPostSeqTimerVal = -1;

// ==========================================================================
// SECTION 4.5: Web Interface HTML
// ==========================================================================
const char index_html[] PROGMEM = R"rawliteral(
<!DOCTYPE HTML><html><head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pyro Remote</title>
  <style>
    body { background-color: #121212; color: white; font-family: sans-serif; text-align: center; margin:0; }
    .header { background-color: #1f1f1f; padding: 15px; font-size: 24px; font-weight: bold; border-bottom: 2px solid #333; }
    .tab-bar { display: flex; justify-content: space-around; background: #2c2c2c; padding: 10px 0; }
    .tab { flex: 1; padding: 10px; cursor: pointer; font-size: 18px; border-bottom: 3px solid transparent; }
    .tab.active { border-bottom: 3px solid #ff9800; color: #ff9800; }
    .content { display: none; padding: 20px; }
    .content.active { display: block; }
    
    .card { background: #333; border-radius: 10px; padding: 15px; margin: 15px 0; }
    .status-box { font-size: 20px; font-weight: bold; padding: 10px; border-radius: 5px; margin-top: 5px; }
    .safe { background-color: #4CAF50; color: white; }
    .danger { background-color: #F44336; color: white; }
    
    .btn { background: #555; color: white; border: none; padding: 15px 30px; font-size: 18px; border-radius: 5px; width: 80%; margin: 10px 0; cursor: pointer; }
    .btn-arm { background: #F44336; font-weight: bold; }
    .btn-disarm { background: #4CAF50; font-weight: bold; }
    .btn-start { background: #FF9800; font-weight: bold; color: black; }
    .btn-disabled { background: #444; color: #888; cursor: not-allowed; }
    
    .cue-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 15px; }
    .cue-btn { background: #444; border: 2px solid #666; color: white; padding: 15px; font-size: 16px; border-radius: 5px; }
    .cue-btn:active { background: #ff9800; border-color: #ff9800; }
  </style>
</head><body>
  <div class="header">Pyro System Remote</div>
  
  <div class="tab-bar">
    <div class="tab active" onclick="showTab('dashboard')">Dashboard</div>
    <div class="tab" onclick="showTab('manual')">Manual Fire</div>
  </div>

  <div id="dashboard" class="content active">
    <div class="card">
      <div>System Arm State</div>
      <div id="armState" class="status-box safe">DISARMED</div>
    </div>
    <div class="card">
      <div>Rail Voltage (Safety)</div>
      <div id="railState" class="status-box safe">SAFE</div>
    </div>
    
    <button id="toggleArmBtn" class="btn btn-arm" onclick="toggleArm()">ARM SYSTEM</button>
    
    <div class="card">
      <div>Sequence Status</div>
      <div id="seqStatus" style="font-size: 18px; color: #ccc; margin-top:5px;">IDLE</div>
      <div id="uploadStatus" style="font-size: 14px; color: #888; margin-top:5px;">No Sequence Loaded</div>
    </div>
    
    <!-- NEW START BUTTON -->
    <button id="startSeqBtn" class="btn btn-disabled" onclick="startSequence()" disabled>START SEQUENCE</button>
  </div>

  <div id="manual" class="content">
    <div style="color: #FF9800; font-size: 14px;">Tap to Fire Immediately (500ms Pulse)</div>
    <div class="cue-grid" id="cueGrid"></div>
  </div>

<script>
  const grid = document.getElementById('cueGrid');
  for(let i=1; i<=24; i++) {
    let b = document.createElement('button');
    b.className = 'cue-btn';
    b.innerText = i;
    b.onclick = () => fireCue(i);
    grid.appendChild(b);
  }

  function showTab(id) {
    document.querySelectorAll('.content').forEach(c => c.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    event.target.classList.add('active');
  }

  function updateStatus() {
    fetch('/api/status').then(r => r.json()).then(d => {
      const armEl = document.getElementById('armState');
      const armBtn = document.getElementById('toggleArmBtn');
      const startBtn = document.getElementById('startSeqBtn');
      const uploadEl = document.getElementById('uploadStatus');
      
      if(d.armed) {
        armEl.innerText = "ARMED";
        armEl.className = "status-box danger";
        armBtn.innerText = "DISARM SYSTEM";
        armBtn.className = "btn btn-disarm";
      } else {
        armEl.innerText = "DISARMED";
        armEl.className = "status-box safe";
        armBtn.innerText = "ARM SYSTEM";
        armBtn.className = "btn btn-arm";
      }

      const railEl = document.getElementById('railState');
      if(d.railLive) {
        railEl.innerText = "LIVE (DANGER)";
        railEl.className = "status-box danger";
      } else {
        railEl.innerText = "SAFE";
        railEl.className = "status-box safe";
      }
      
      document.getElementById('seqStatus').innerText = d.stateStr; // Use text version
      
      if(d.hasSeq) {
        uploadEl.innerText = "Sequence Loaded";
        uploadEl.style.color = "#4CAF50";
        
        // FIXED LOGIC: Enable Start Button if Loaded, regardless of Arm state (for Test Mode)
        // Only disable if already executing
        if(d.stateStr !== "EXECUTING") {
            startBtn.disabled = false;
            startBtn.className = "btn btn-start";
        } else {
            startBtn.disabled = true;
            startBtn.className = "btn btn-disabled";
        }
      } else {
        uploadEl.innerText = "No Sequence Loaded";
        uploadEl.style.color = "#888";
        startBtn.disabled = true;
        startBtn.className = "btn btn-disabled";
      }
    });
  }

  function toggleArm() { fetch('/api/toggleArm').then(() => setTimeout(updateStatus, 200)); }
  function fireCue(num) { fetch('/api/fire?cue=' + num); }
  function startSequence() { fetch('/api/startSeq'); } 

  setInterval(updateStatus, 1000);
  updateStatus();
</script>
</body></html>
)rawliteral";


// ==========================================================================
// SECTION 5: Function Prototypes
// ==========================================================================
void setupHardware();
void checkRailSafety();
void handleUdpDiscovery(); 
void handleWebServer(); 
void drawHeaderIcons(bool forceRedraw);
void drawSequenceSummary(); 
void drawTimelineList(int activeGroupIdx, int activeStepIdx);
void drawFullScreenCountdown(String numStr);
void drawSequenceCompleteScreen(int secondsLeft);
void handleCommunication();
void processCommand(const String& cmd);
bool parseSequenceFromJson(const String& jsonString);
void executeNow();
void executeSequenceStep();
void fireCue(int cueNumber);
void checkAndClearCues();
void updateShiftRegisters();
void clearAllOutputs();
void resetSequenceState();
void enterErrorState(const String& reason);
String getEventDescription(int gIdx, int sIdx);
String getStateString(); // New Helper

// ==========================================================================
// SECTION 6: Setup Function
// ==========================================================================
void setup() {
    Serial.begin(115200);
    Serial.println("\n\n--- Pyro Control ESP32 (V17 Web Start Fixed) ---");

    setupHardware();
    
    hspi.begin(TFT_CLK, TFT_MISO, TFT_MOSI, TFT_CS); 
    tft.begin();
    tft.setRotation(1);
    tft.fillScreen(ILI9341_BLACK);
    
    tft.setTextColor(ILI9341_ORANGE);
    tft.setTextSize(2);
    tft.setCursor(50, 80);
    tft.print("PYRO SYSTEM BOOT");
    tft.setCursor(50, 110);
    tft.setTextSize(1);
    tft.print("Connecting to WiFi...");

    WiFiManager wifiManager;
    bool res = wifiManager.autoConnect("Pyro-Setup-AP", "password"); 
    
    if(!res) {
        tft.setTextColor(ILI9341_RED);
        tft.setCursor(50, 130);
        tft.print("Failed to connect. Restarting...");
        delay(3000);
        ESP.restart();
    } 
    
    udp.begin(UDP_PORT);
    server.begin();
    
    // --- Setup Web Server Routes ---
    webServer.on("/", HTTP_GET, []() {
        webServer.send(200, "text/html", index_html);
    });
    
    webServer.on("/api/status", HTTP_GET, []() {
        String json = "{";
        json += "\"armed\":" + String(isSoftwareArmed ? "true" : "false") + ",";
        json += "\"railLive\":" + String(isRailLive ? "true" : "false") + ",";
        json += "\"stateStr\":\"" + getStateString() + "\","; // Human readable
        json += "\"hasSeq\":" + String(sequenceIsLoaded ? "true" : "false");
        json += "}";
        webServer.send(200, "application/json", json);
    });

    webServer.on("/api/toggleArm", HTTP_GET, []() {
        if (isSoftwareArmed) {
            digitalWrite(PIN_RAIL_MOSFET, LOW);
            isSoftwareArmed = false;
        } else {
            digitalWrite(PIN_RAIL_MOSFET, HIGH);
            isSoftwareArmed = true;
        }
        if(client.connected()) client.println(isSoftwareArmed ? "ACK ARM" : "ACK DISARM");
        webServer.send(200, "text/plain", "OK");
    });

    webServer.on("/api/fire", HTTP_GET, []() {
        if (webServer.hasArg("cue")) {
            int cue = webServer.arg("cue").toInt();
            if (currentState != EXECUTING_SEQUENCE) {
                fireCue(cue);
                updateShiftRegisters();
                webServer.send(200, "text/plain", "FIRED");
            } else {
                webServer.send(403, "text/plain", "BUSY");
            }
        } else {
            webServer.send(400, "text/plain", "Missing cue param");
        }
    });
    
    // NEW ENDPOINT: Start Sequence
    webServer.on("/api/startSeq", HTTP_GET, []() {
        // FIXED LOGIC: Allow start if sequence loaded, even if not armed (Test Mode)
        if (sequenceIsLoaded && currentState != EXECUTING_SEQUENCE) {
            executeNow();
            webServer.send(200, "text/plain", "STARTED");
        } else {
            webServer.send(400, "text/plain", "CANNOT START");
        }
    });
    
    webServer.begin();
    // -------------------------------
    
    delay(500);
    
    tft.fillScreen(ILI9341_BLACK);
    drawHeaderIcons(true); 
    
    tft.setTextColor(ILI9341_GREEN);
    tft.setTextSize(2);
    tft.setCursor(10, 50);
    tft.print("IP: ");
    tft.println(WiFi.localIP());
    
    resetSequenceState(); 
}

// ==========================================================================
// SECTION 7: Main Loop
// ==========================================================================
void loop() {
    webServer.handleClient(); 
    handleCommunication();
    handleUdpDiscovery(); 
    checkAndClearCues();
    checkRailSafety(); 
    
    static unsigned long lastIconUpdate = 0;
    if (millis() - lastIconUpdate > 1000) {
        drawHeaderIcons(false);
        lastIconUpdate = millis();
    }

    if (currentState == EXECUTING_SEQUENCE) {
        executeSequenceStep();
    } else if (currentState == SEQUENCE_FINISHED) {
        unsigned long elapsed = (millis() - sequenceFinishedTime) / 1000;
        int remaining = POST_SEQUENCE_TIMEOUT_SEC - elapsed;
        if (remaining < 0) resetSequenceState(); 
        else drawSequenceCompleteScreen(remaining);
    }
}

// ==========================================================================
// SECTION 8: Hardware & UDP Init
// ==========================================================================
void setupHardware() {
    pinMode(SRCLK_PIN, OUTPUT);
    pinMode(RCLK_PIN, OUTPUT);
    pinMode(SER_PIN, OUTPUT);
    pinMode(OE_PIN, OUTPUT);
    pinMode(SRCLR_PIN, OUTPUT);
    
    pinMode(PIN_RAIL_MOSFET, OUTPUT);
    digitalWrite(PIN_RAIL_MOSFET, LOW); 
    pinMode(PIN_RAIL_SENSE, INPUT); 

    digitalWrite(SRCLR_PIN, HIGH);
    digitalWrite(OE_PIN, LOW);
    digitalWrite(RCLK_PIN, LOW);
    digitalWrite(SRCLK_PIN, LOW);
    digitalWrite(SER_PIN, LOW);

    clearAllOutputs();
}

void handleUdpDiscovery() {
    int packetSize = udp.parsePacket();
    if (packetSize) {
        char packetBuffer[255];
        int len = udp.read(packetBuffer, 255);
        if (len > 0) packetBuffer[len] = 0;

        if (strcmp(packetBuffer, DISCOVERY_PACKET) == 0) {
            udp.beginPacket(udp.remoteIP(), udp.remotePort());
            udp.print(DISCOVERY_REPLY);
            udp.endPacket();
        }
    }
}

void clearAllOutputs() {
    byte cleanState[NUM_SHIFT_REGISTERS] = {0}; 
    digitalWrite(OE_PIN, HIGH);
    digitalWrite(RCLK_PIN, LOW);
    shiftOut(SER_PIN, SRCLK_PIN, MSBFIRST, cleanState[2]);
    shiftOut(SER_PIN, SRCLK_PIN, MSBFIRST, cleanState[1]);
    shiftOut(SER_PIN, SRCLK_PIN, MSBFIRST, cleanState[0]);
    digitalWrite(RCLK_PIN, HIGH);
    digitalWrite(OE_PIN, LOW);

    memset(shiftRegisterStates, 0, sizeof(shiftRegisterStates));
    memset(cueClearTimestamps, 0, sizeof(cueClearTimestamps));
}

void updateShiftRegisters() {
    digitalWrite(OE_PIN, HIGH);
    digitalWrite(RCLK_PIN, LOW);
    shiftOut(SER_PIN, SRCLK_PIN, MSBFIRST, shiftRegisterStates[2]);
    shiftOut(SER_PIN, SRCLK_PIN, MSBFIRST, shiftRegisterStates[1]);
    shiftOut(SER_PIN, SRCLK_PIN, MSBFIRST, shiftRegisterStates[0]);
    digitalWrite(RCLK_PIN, HIGH);
    digitalWrite(OE_PIN, LOW);
}

// ==========================================================================
// SECTION 9: TFT UI & Safety Logic
// ==========================================================================
String getStateString() {
    switch(currentState) {
        case IDLE: return "IDLE";
        case WAITING_FOR_JSON: return "WAITING...";
        case ARMED: return "ARMED / READY";
        case COUNTDOWN: return "COUNTDOWN";
        case EXECUTING_SEQUENCE: return "EXECUTING";
        case SEQUENCE_FINISHED: return "FINISHED";
        case ERROR_STATE: return "ERROR";
        default: return "UNKNOWN";
    }
}

void checkRailSafety() {
    if (millis() - lastSafetyCheck < 200) return; 
    lastSafetyCheck = millis();

    int val = analogRead(PIN_RAIL_SENSE);
    bool liveNow = (val > 500);
    
    if (liveNow != isRailLive) {
        isRailLive = liveNow;
        if (client.connected()) {
            client.print("RAIL:");
            client.println(isRailLive ? "LIVE" : "SAFE");
        }
        drawHeaderIcons(true);
    }
}

void drawHeaderIcons(bool forceRedraw) {
    if (currentState == COUNTDOWN) return;

    bool wifiNow = (WiFi.status() == WL_CONNECTED);
    bool clientNow = client.connected();
    
    if (!forceRedraw && wifiNow == lastWifiState && clientNow == lastClientState && isRailLive == lastRailLiveState) return;

    int headerH = 30;
    if (forceRedraw) {
        tft.fillRect(0, 0, 320, headerH, 0x2124); 
        tft.drawFastHLine(0, headerH, 320, ILI9341_WHITE);
        tft.setTextColor(ILI9341_WHITE);
        tft.setTextSize(2);
        tft.setCursor(10, 7);
        tft.print("PYRO SYS");
    }
    
    int rX = 150; int rY = 7;
    if (isRailLive) {
        tft.fillRoundRect(rX, rY, 70, 16, 4, ILI9341_RED);
        tft.setTextColor(ILI9341_WHITE);
        tft.setTextSize(1);
        tft.setCursor(rX + 8, rY + 4);
        tft.print("RAIL LIVE");
    } else {
        tft.drawRoundRect(rX, rY, 70, 16, 4, 0x7BEF); 
        tft.setTextColor(0x7BEF);
        tft.setTextSize(1);
        tft.setCursor(rX + 8, rY + 4);
        tft.print("RAIL SAFE");
    }

    int wX = 290; int wY = 22;
    uint16_t wColor = wifiNow ? ILI9341_GREEN : ILI9341_RED;
    tft.fillRect(wX-10, 0, 30, headerH, 0x2124); 
    tft.fillCircle(wX, wY, 2, wColor);
    tft.drawCircle(wX, wY-4, 6, wColor);
    tft.drawCircle(wX, wY-4, 10, wColor);
    tft.fillRect(wX-12, wY+1, 24, 10, 0x2124); 

    int pX = 250; int pY = 15;
    uint16_t pColor = clientNow ? ILI9341_CYAN : ILI9341_RED;
    tft.drawRect(pX, pY-8, 20, 14, pColor);
    tft.fillRect(pX+2, pY-6, 16, 10, pColor); 
    tft.drawFastHLine(pX+6, pY+8, 8, pColor);
    tft.drawFastHLine(pX+4, pY+9, 12, pColor);

    lastWifiState = wifiNow;
    lastClientState = clientNow;
    lastRailLiveState = isRailLive;
}

void drawFullScreenCountdown(String numStr) {
    if (lastCountdownStr == "") tft.fillScreen(ILI9341_BLACK);
    if (numStr == lastCountdownStr) return;
    
    tft.fillScreen(ILI9341_BLACK); 
    tft.setTextColor(ILI9341_YELLOW);
    tft.setTextSize(14); 
    
    int16_t x1, y1; uint16_t w, h;
    tft.getTextBounds(numStr, 0, 0, &x1, &y1, &w, &h);
    int x = (320 - w) / 2;
    int y = (240 - h) / 2 + h/2 - 20;
    
    tft.setCursor(x, y);
    tft.print(numStr);
    
    tft.setTextSize(2);
    if (isRailLive) {
        tft.setTextColor(ILI9341_RED);
        tft.setCursor(60, 210);
        tft.print("DANGER: RAIL LIVE");
    } else {
        tft.setTextColor(ILI9341_GREEN);
        tft.setCursor(70, 210);
        tft.print("TEST MODE (SAFE)");
    }
    
    lastCountdownStr = numStr;
}

void drawSequenceSummary() {
    tft.fillScreen(ILI9341_BLACK);
    drawHeaderIcons(true);

    tft.setTextColor(ILI9341_ORANGE);
    tft.setTextSize(2);
    tft.setCursor(10, 50);
    tft.print("SEQUENCE SYNOPSIS");
    tft.drawFastHLine(10, 70, 200, ILI9341_ORANGE);

    tft.setTextColor(ILI9341_WHITE);
    tft.setTextSize(2);
    tft.setCursor(20, 90); tft.print("Groups: "); tft.print(totalGroupsInSequence);
    tft.setCursor(20, 120); tft.print("Total Cues: "); tft.print(statTotalCues);
    tft.setCursor(20, 150); tft.print("Est. Time: "); tft.print(statTotalDurationMs / 1000.0, 1); tft.print("s");

    tft.fillRect(0, 200, 320, 40, 0x2124); 
    tft.setTextColor(ILI9341_GREEN);
    tft.setCursor(80, 212);
    tft.print("SYSTEM ARMED");
}

String getEventDescription(int gIdx, int sIdx) {
    if (gIdx >= totalGroupsInSequence) return "";
    Group& g = sequenceGroups[gIdx];
    String desc = g.name.substring(0, 9) + ": ";
    if (g.pattern == "Pause") desc += "Pause " + String(g.gap_ms) + "ms";
    else {
        if (sIdx < g.numFiringEventsInGroup) {
            FiringEventStep& step = g.firingEvents[sIdx];
            desc += "Fire ";
            for(int i=0; i<step.numCuesInThisEvent; i++) {
                desc += String(step.cuesToFire[i]);
                if (i < step.numCuesInThisEvent - 1) desc += ",";
            }
        }
    }
    return desc;
}

void drawTimelineList(int activeGroupIdx, int activeStepIdx) {
    bool freshLayout = false;
    if (lastCountdownStr != "" || lastDrawnGroupIdx == -1) {
        lastCountdownStr = "";
        tft.fillScreen(ILI9341_BLACK);
        drawHeaderIcons(true);
        freshLayout = true;
    }

    if (!freshLayout && activeGroupIdx == lastDrawnGroupIdx && activeStepIdx == lastDrawnStepIdx) return;

    int startY = 40; int boxH = 30; int gap = 8; int maxItems = 5;
    int tempG = activeGroupIdx; int tempS = activeStepIdx;
    
    for (int i = 0; i < maxItems; i++) {
        int y = startY + (i * (boxH + gap));
        tft.fillRect(10, y, 300, boxH, ILI9341_BLACK);

        if (tempG >= totalGroupsInSequence) continue;
        
        uint16_t boxColor = (i == 0) ? ILI9341_GREEN : 0x4208; 
        uint16_t textColor = (i == 0) ? ILI9341_BLACK : ILI9341_WHITE;
        
        tft.fillRect(10, y, 300, boxH, boxColor);
        tft.drawRect(10, y, 300, boxH, ILI9341_WHITE);
        
        tft.setTextColor(textColor);
        tft.setTextSize(2);
        tft.setCursor(20, y + 8);
        tft.print(getEventDescription(tempG, tempS));
        
        Group& g = sequenceGroups[tempG];
        if (g.pattern == "Pause") { tempG++; tempS = 0; }
        else {
            tempS++;
            if (tempS >= g.numFiringEventsInGroup) { tempG++; tempS = 0; }
        }
    }
    lastDrawnGroupIdx = activeGroupIdx; lastDrawnStepIdx = activeStepIdx;
}

void drawSequenceCompleteScreen(int secondsLeft) {
    if (lastPostSeqTimerVal == -1) {
        tft.fillScreen(ILI9341_BLACK);
        tft.drawRect(5, 5, 310, 230, ILI9341_GREEN);
        tft.drawRect(7, 7, 306, 226, ILI9341_GREEN);
        
        tft.setTextColor(ILI9341_GREEN);
        tft.setTextSize(3);
        tft.setCursor(30, 40);
        tft.print("COMPLETE!");
        
        tft.setTextColor(ILI9341_WHITE);
        tft.setTextSize(2);
        tft.setCursor(40, 80); tft.print("Cues Fired: "); tft.print(totalCuesFiredCount);
        tft.setCursor(40, 110); tft.print("Duration: ");
        float duration = (sequenceFinishedTime - sequenceStartTime) / 1000.0;
        tft.print(duration, 1); tft.print("s");

        if (isRailLive) {
            tft.fillRect(0, 150, 320, 40, ILI9341_RED);
            tft.setTextColor(ILI9341_WHITE);
            tft.setCursor(60, 160);
            tft.print("WARNING: RAIL LIVE");
        } else {
            tft.fillRect(0, 150, 320, 40, ILI9341_BLUE);
            tft.setTextColor(ILI9341_WHITE);
            tft.setCursor(60, 160);
            tft.print("SYSTEM DISARMED");
        }
    }

    if (secondsLeft != lastPostSeqTimerVal) {
        tft.fillRect(0, 200, 320, 30, ILI9341_BLACK); 
        tft.setTextColor(ILI9341_ORANGE);
        tft.setTextSize(2);
        tft.setCursor(60, 205);
        tft.print("Closing in... ");
        tft.print(secondsLeft);
        tft.print("s");
        lastPostSeqTimerVal = secondsLeft;
    }
}

// ==========================================================================
// SECTION 10: Communications
// ==========================================================================
void handleCommunication() {
    if (!client.connected()) {
        client = server.available();
        if (client) {
            Serial.println("Client Connected");
            if (currentState == ARMED || currentState == EXECUTING_SEQUENCE) {
                client.println("STATUS:ARMED");
            } else {
                client.println("STATUS:IDLE");
            }
            client.print("RAIL:"); client.println(isRailLive ? "LIVE" : "SAFE");
        } 
    } else {
        while (client.available() > 0) {
            char c = client.read();
            if (currentState == WAITING_FOR_JSON) {
                if (jsonDataBuffer.length() < SERIAL_BUFFER_SIZE - 1) {
                    jsonDataBuffer += c;
                    if (jsonDataBuffer.endsWith(String(JSON_SEQUENCE_END_CMD) + "\n")) {
                        int markerPos = jsonDataBuffer.lastIndexOf(JSON_SEQUENCE_END_CMD);
                        String json = jsonDataBuffer.substring(0, markerPos);
                        json.trim();
                        jsonDataBuffer = "";
                        if (parseSequenceFromJson(json)) {
                            currentState = ARMED;
                            client.println("JSON_PARSE_OK");
                            drawSequenceSummary(); 
                        } else {
                            currentState = IDLE;
                        }
                    }
                } else {
                    enterErrorState("Buffer Overflow");
                    jsonDataBuffer = "";
                }
            } else {
                if (c == '\n') {
                    serialInputBuffer[serialInputIndex] = '\0';
                    if (serialInputIndex > 0) processCommand(String(serialInputBuffer));
                    serialInputIndex = 0;
                } else if (c != '\r' && serialInputIndex < SERIAL_BUFFER_SIZE - 1) {
                    serialInputBuffer[serialInputIndex++] = c;
                }
            }
        }
    }
}

void processCommand(const String& cmd) {
    String cleanCmd = cmd;
    cleanCmd.trim();
    Serial.println("CMD: " + cleanCmd); 

    if (cleanCmd == "ARM_SYSTEM") {
        digitalWrite(PIN_RAIL_MOSFET, HIGH); 
        isSoftwareArmed = true;
        client.println("ACK ARM");
        Serial.println("Software ARMED (MOSFET ON)");
        return;
    }
    if (cleanCmd == "DISARM_SYSTEM") {
        digitalWrite(PIN_RAIL_MOSFET, LOW); 
        isSoftwareArmed = false;
        client.println("ACK DISARM");
        Serial.println("Software DISARMED (MOSFET OFF)");
        return;
    }

    if (cleanCmd == JSON_SEQUENCE_START_CMD) {
        resetSequenceState();
        currentState = WAITING_FOR_JSON;
        tft.fillScreen(ILI9341_BLACK);
        tft.setCursor(10, 100); tft.setTextColor(ILI9341_YELLOW); tft.setTextSize(2);
        tft.print("Downloading...");
        client.println("ACK_UPLOAD_JSON_START");
        return;
    }
    
    if (cleanCmd.startsWith("CMD_COUNTDOWN:")) {
        String val = cleanCmd.substring(14);
        currentState = COUNTDOWN; 
        drawFullScreenCountdown(val);
        return;
    }
    
    if (cleanCmd == "EXECUTE_NOW") {
        executeNow();
        return;
    }
    
    if (cleanCmd == "ABORT_SEQUENCE") {
        resetSequenceState();
        tft.fillScreen(ILI9341_BLACK);
        drawHeaderIcons(true);
        tft.setCursor(10, 100); tft.setTextColor(ILI9341_MAGENTA); tft.setTextSize(3);
        tft.print("ABORTED!");
        client.println("ACK ABORT");
        return;
    }
}

// ==========================================================================
// SECTION 11: Logic (No Persistence)
// ==========================================================================
bool parseSequenceFromJson(const String& jsonString) {
    DynamicJsonDocument doc(8192);
    DeserializationError error = deserializeJson(doc, jsonString);
    if (error) return false;
    
    JsonArrayConst groups = doc["groups"];
    totalGroupsInSequence = 0;
    
    // Reset Stats
    statTotalCues = 0;
    statTotalDurationMs = 0;
    
    for (JsonObjectConst g : groups) {
        if (totalGroupsInSequence >= MAX_GROUPS) break;
        
        Group& newG = sequenceGroups[totalGroupsInSequence];
        newG.name = g["name"].as<String>();
        newG.pattern = g["pattern"].as<String>();
        newG.gap_ms = g["gap_ms"].as<int>();
        newG.numFiringEventsInGroup = 0;
        
        if (newG.pattern == "Pause") {
            statTotalDurationMs += newG.gap_ms; // Add Pause time
        }
        
        if (newG.pattern != "Pause") {
            JsonArrayConst steps = g["steps"];
            for (JsonArrayConst step : steps) {
                if (newG.numFiringEventsInGroup >= MAX_STEPS_PER_GROUP) break;
                FiringEventStep& ev = newG.firingEvents[newG.numFiringEventsInGroup];
                ev.numCuesInThisEvent = 0;
                for (int cue : step) {
                    if (ev.numCuesInThisEvent < TOTAL_CUES) {
                        ev.cuesToFire[ev.numCuesInThisEvent++] = cue;
                        statTotalCues++; // Increment Stats
                    }
                }
                if (ev.numCuesInThisEvent > 0) {
                    newG.numFiringEventsInGroup++;
                    statTotalDurationMs += newG.gap_ms; // Add time for this firing step
                }
            }
        }
        totalGroupsInSequence++;
    }
    sequenceIsLoaded = true;
    return true;
}

void executeNow() {
    if (sequenceIsLoaded) {
        clearAllOutputs();
        currentState = EXECUTING_SEQUENCE;
        sequenceStartTime = millis();
        nextStepTimestamp = millis() + 50;
        totalCuesFiredCount = 0;
        
        // Reset display triggers so timeline draws fresh
        lastDrawnGroupIdx = -1;
        lastCountdownStr = "DONE"; 
        
        if(client.connected()) client.println("ACK EXECUTE");
    }
}

void fireCue(int cueNumber) {
    if (cueNumber < 1 || cueNumber > TOTAL_CUES) return;
    int idx = cueNumber - 1;
    int sr = idx / CUES_PER_SR;
    int pin = idx % CUES_PER_SR;
    shiftRegisterStates[sr] |= (1 << pin);
    cueClearTimestamps[idx] = millis() + CUE_ON_DURATION_MS;
    totalCuesFiredCount++;
    if(client.connected()) {
        client.print("FIRED_CUE:");
        client.println(cueNumber);
    }
}

void checkAndClearCues() {
    unsigned long now = millis();
    bool changed = false;
    for(int i=0; i<TOTAL_CUES; i++) {
        if(cueClearTimestamps[i] != 0 && now >= cueClearTimestamps[i]) {
            int sr = i / CUES_PER_SR;
            int pin = i % CUES_PER_SR;
            shiftRegisterStates[sr] &= ~(1 << pin);
            cueClearTimestamps[i] = 0;
            changed = true;
        }
    }
    if(changed) updateShiftRegisters();
}

void executeSequenceStep() {
    if (millis() < nextStepTimestamp) return;
    
    if (currentGroupIndex >= totalGroupsInSequence) {
        currentState = SEQUENCE_FINISHED;
        sequenceFinishedTime = millis(); 
        lastPostSeqTimerVal = -1; 
        if(client.connected()) client.println("SEQUENCE_COMPLETE");
        return;
    }
    
    drawTimelineList(currentGroupIndex, currentStepIndex);
    
    Group& g = sequenceGroups[currentGroupIndex];
    
    if (g.pattern == "Pause") {
        nextStepTimestamp = millis() + g.gap_ms;
        currentGroupIndex++;
        currentStepIndex = 0;
        return;
    }
    
    if (currentStepIndex < g.numFiringEventsInGroup) {
        FiringEventStep& ev = g.firingEvents[currentStepIndex];
        for(int i=0; i<ev.numCuesInThisEvent; i++) fireCue(ev.cuesToFire[i]);
        updateShiftRegisters();
        nextStepTimestamp = millis() + g.gap_ms;
        currentStepIndex++;
    } else {
        nextStepTimestamp = millis() + g.gap_ms;
        currentGroupIndex++;
        currentStepIndex = 0;
    }
}

void resetSequenceState() {
    sequenceIsLoaded = false;
    totalGroupsInSequence = 0;
    currentGroupIndex = 0;
    currentStepIndex = 0;
    totalCuesFiredCount = 0;
    lastCountdownStr = "";
    currentState = IDLE;
    tft.fillScreen(ILI9341_BLACK);
    drawHeaderIcons(true);
}

void enterErrorState(const String& reason) {
    currentState = ERROR_STATE;
    clearAllOutputs();
    tft.fillScreen(ILI9341_RED);
    tft.setTextColor(ILI9341_WHITE);
    tft.setCursor(10, 100);
    tft.print("ERROR: " + reason);
}