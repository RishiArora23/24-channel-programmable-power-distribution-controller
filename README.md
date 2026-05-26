# 24-Channel Programmable Power Distribution Controller

An ESP32-based programmable firing controller for sequenced 24-channel power distribution. The system combines embedded firmware, a KiCad-designed PCB, and a PyQt5 desktop application to enable fully programmable, remotely triggered output sequences — with hardware safety interlocks and a real-time TFT display.

> ⚠️ **Safety Notice:** This system switches high-current loads via power MOSFETs. Always follow applicable electrical safety guidelines. Never connect live loads during programming or testing.

---

## How It Works

Each of the 24 output channels is driven by an **IRLZ44N N-Channel MOSFET** (55 V, 47 A) switched via a **3× cascaded shift register** chain. The ESP32 clocks channel states into the shift registers over SPI, allowing all 24 outputs to be independently toggled in any order or pattern. A 12 V rail MOSFET acts as a master power switch, controlled by software arm/disarm commands.

The desktop app (`main.py`) communicates with the ESP32 over **Wi-Fi (TCP port 8080)** or via **UDP auto-discovery** (port 4210). Sequences are authored graphically in the app, serialized to JSON, and uploaded to the ESP32 at runtime — no re-flashing required.

---

## System Architecture

```
[ PyQt5 Desktop App ] ──WiFi TCP/UDP──► [ ESP32 Firmware ]
         │                                      │
   Sequence Editor                    Shift Registers (×3)
   Group/Pattern UI                         │
   Arm / Fire Controls              [ 24× IRLZ44N MOSFETs ]
                                            │
                                    [ Output Terminals J1–J24 ]
```

---

## Hardware

**Key components (from KiCad schematic):**
- **MCU:** ESP32 (Arduino framework)
- **Output switches:** 24× IRLZ44N MOSFETs in TO-220 package
- **Channel indicators:** 24× LEDs with 220 Ω series resistors
- **Gate drive:** 10 kΩ pull-down resistors per MOSFET gate
- **Shift registers:** 3× 8-bit shift registers (SRCLK → pin 23, RCLK → pin 21, SER → pin 22, OE → pin 19, SRCLR → pin 18)
- **Rail safety:** MOSFET switch on pin 27, analog voltage sense on pin 34
- **Display:** ILI9341 TFT (HSPI: MOSI 13, CLK 14, MISO 12, CS 5, DC 4, RST 17)
- **Output connectors:** 24× 2-pin 5.08 mm terminal blocks (J1–J24)

**Project files:**

| File | Description |
|------|-------------|
| `*.kicad_sch` | Schematic (channel sub-sheets × 24 + top sheet) |
| `*.kicad_pcb` | PCB layout |
| `*.kicad_pro` | KiCad project file |

---

## Firmware (`*.ino`)

Written in Arduino C++ for ESP32. Key responsibilities:

- **WiFiManager** captive portal for first-time Wi-Fi setup (AP: `Pyro-Setup-AP`)
- TCP server on port **8080** for desktop app communication
- UDP listener on port **4210** for auto-discovery
- Shift register management: channels fire for **1000 ms** then auto-clear
- JSON sequence parser (`ArduinoJson`) supporting up to **20 groups × 24 steps**
- Built-in web UI accessible from any browser on the network (manual fire, arm/disarm, sequence status)
- Real-time TFT UI with state machine: `IDLE → ARMED → COUNTDOWN → EXECUTING → FINISHED`

**ESP32 Pin Summary:**

| Function | Pin |
|----------|-----|
| Rail MOSFET (arm) | 27 |
| Rail voltage sense | 34 |
| Shift reg SRCLK | 23 |
| Shift reg RCLK | 21 |
| Shift reg SER | 22 |
| Shift reg OE | 19 |
| Shift reg SRCLR | 18 |
| TFT MOSI/CLK/MISO/CS/DC/RST | 13/14/12/5/4/17 |

---

## Desktop App (`main.py`)

PyQt5 GUI application. Requires Python 3 and the following packages:

```bash
pip install PyQt5 pyserial
```

**Features:**
- Initialize a sequence with N cues (up to 24)
- Build named **groups** with patterns: Right Wave, Left Wave, Alternate, Center Burst, Inwards, All Fire
- Set inter-step gap (ms) per group
- Upload sequence as JSON to ESP32 over Wi-Fi
- Arm/disarm rail, monitor live rail voltage safety status
- Countdown display and per-channel LED flash feedback
- Undo/redo, save/load sequences as JSON files (`Ctrl+S` / `Ctrl+O`)
- **UDP Auto-Discovery** — finds the ESP32 on the local network automatically

**To run:**
```bash
python main.py
```

On first launch, connect the ESP32 to Wi-Fi via the `Pyro-Setup-AP` captive portal, then use **Auto Connect** in the app to discover it.

---

## Repository Structure

```
├── *.ino               # ESP32 Arduino firmware
├── main.py             # PyQt5 desktop control application
├── *.kicad_sch         # KiCad schematics (top + 24 channel sub-sheets)
├── *.kicad_pcb         # PCB layout
└── *.kicad_pro         # KiCad project
```

---

## License

This project is licensed under the [MIT License](LICENSE).
