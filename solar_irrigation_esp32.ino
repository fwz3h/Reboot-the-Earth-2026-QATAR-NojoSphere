/***************************************************************************************************
 *  ESP32 SENSOR HUB
 *  -------------------------------------------------------------------------------------------
 *  16x2 parallel LCD (4-bit) + rotary-encoder menu + non-blocking sensors + WebSocket telemetry
 *
 *  Target board : ESP32 Dev Module (ESP32-WROOM-32 / DevKit V1 tested pattern)
 *  Core         : arduino-esp32 2.x or 3.x
 *
 *  LIBRARIES (Arduino Library Manager)
 *  -------------------------------------------------------------------------------------------
 *  - WebSockets          by Markus Sattler (links2004)   v2.3.x+     -> <WebSocketsServer.h>
 *  - ArduinoJson         by Benoit Blanchon              v6.19+ or v7.x (both supported here)
 *  - DHT sensor library  by Adafruit                     v1.4.x      -> <DHT.h>
 *      + Adafruit Unified Sensor (dependency of DHT)
 *  - LiquidCrystal       bundled with the arduino-esp32 core          -> <LiquidCrystal.h>
 *
 *  ARCHITECTURE: the ESP32 is the WebSocket SERVER (host). Your website - on any device on the
 *  same Wi-Fi - connects TO it by entering the ESP32's IP and port. The IP:port is shown on the
 *  LCD right after boot and on the serial monitor; mDNS also exposes <hostname>.local.
 *  Wi-Fi placeholder: search for "WIFI_SSID" in the CONFIG section.
 *  Payload is JSON, sent every 2 s, e.g.:
 *    {"device":"esp32-AABBCCDDEEFF","uptime_s":142,"heap_free":231456,"menu_idx":0,
 *     "ws_connected":true,"power_state":"on","wifi_rssi":-57,
 *     "dht":{"ok":true,"temp_c":24.0,"hum_pct":45},
 *     "ntc":{"ok":true,"adc":1850,"mv":1650,"r_ohm":10012,"temp_c":23.8,"temp_f":74.8,"d0_high":false},
 *     "light":{"ok":true,"adc":3200,"pct":78},
 *     "soil":{"ok":true,"adc":1800,"pct":55}}
 *  Invalid readings are reported with "ok":false and the value keys are omitted (JSON null-style).
 *
 *  CONTROLS
 *  -------------------------------------------------------------------------------------------
 *  - Power-on     : LCD shows the WebSocket address (IP:port) for a few seconds so you can
 *                   point your website at it, then switches to the sensor menu
 *  - No input     : LCD auto-advances to the next sensor screen every 15 s, hands-free
 *  - Rotate knob  : scroll through the 4 sensor screens (when powered on) - ONE screen per
 *                   detent click, every detected click, no missed steps on fast spins.
 *                   Auto-cycling resumes 30 s after the last knob move.
 *  - Short press  : POWER ON/OFF switch - standby toggle (or true deep sleep, see
 *                   POWER_OFF_USES_DEEP_SLEEP). While off, a "power_on" WebSocket text
 *                   command wakes the system remotely.
 *  - Long press   : toggle auto-rotate (carousel) mode
 *
 *  STANDBY BEHAVIOR: sensors, menu and display pause, telemetry stops (one final
 *  "power_state":"standby" frame is sent). Wi-Fi/WebSocket stay connected so the node can be
 *  woken remotely. NOTE: the LCD backlight stays lit - it is hardwired to 5V on this module.
 *  For true low-power off, set POWER_OFF_USES_DEEP_SLEEP 1 (EXT0 wake on SW press = reboot).
 *  - Status LED (GPIO 2): fast blink = Wi-Fi down, slow blink = WS down, brief flash = all good
 *
 *  CURRENT WIRING - "USER" PROFILE (default, REV B, all pins verified feasible)
 *  -------------------------------------------------------------------------------------------
 *   Encoder       CLK=25  DT=26  SW=27
 *   LCD 16x2      RS=14   E=13   D4=32  D5=33  D6=5   D7=21   (RW->GND, V0->10k pot, VDD->5V)
 *   NTC module    AO=36 (VP / ADC1_CH0)   DO=4 (digital threshold, optional read)
 *   DHT11         S=16
 *   Photoresistor S=34 (ADC1_CH6)
 *   Soil sensor   AO=35 (ADC1_CH7)
 *   Status LED    2 (on-board)
 *
 *  !! PIN FEASIBILITY NOTES (read before rewiring !!)
 *  -------------------------------------------------------------------------------------------
 *  1. GPIO 34-39 are INPUT-ONLY on classic ESP32. They cannot drive LCD data lines (your spec
 *     used 35/34 for D4/D5) but they are perfect ADC1 ANALOG INPUTS. The RECOMMENDED profile
 *     exploits this: LCD data moves to GPIO 21/23, and the three analog sensors land on the
 *     only ADC1 pins still free (34, 35, 39).
 *  2. ADC2 pins (GPIO 0,2,4,12,13,14,15,25,26,27) CANNOT be read by analogRead() while Wi-Fi
 *     is active - they return 0. Your NTC on GPIO 15 (ADC2) would read 0 with Wi-Fi enabled.
 *  3. GPIO 17 and GPIO 23 have NO ADC on classic ESP32 (GPIO 7 does not exist on classic ESP32
 *     at all - it is an ESP32-C3/S2/S3 pin). analogRead() there returns 0.
 *  4. GPIO 4 double-booking: only relevant in the LITERAL profile, where the LCD D4 fallback
 *     (GPIO 4) collides with the NTC digital output (D0 -> GPIO 4); the #error guard fires if
 *     both are enabled. In the USER and RECOMMENDED profiles GPIO 4 is free and NTC D0 is read.
 *  5. GPIO 12 (MTDI boot strap) is UNUSED in the USER profile - LCD E moved to GPIO 13, which
 *     eliminates the boot-strap risk entirely. LCD RS (GPIO 14) and D6 (GPIO 5) emit brief
 *     boot glitches, but the HD44780 ignores them because E stays low throughout reset.
 *  6. LCD in 4-bit mode: RW pin hardwired to GND, V0 to a 10k contrast pot, backlight via the
 *     module's A/K pins. HD44780 accepts 3.3 V logic when powered at 5 V (VIH = 2.2 V).
 *
 *  CONNECTING YOUR WEBSITE (both devices on the same Wi-Fi):
 *    1. Power the hub and watch the LCD: it shows its address, e.g. "192.168.1.42:8080".
 *    2. In your website, enter that IP + port and connect:
 *         const ws = new WebSocket("ws://192.168.1.42:8080");
 *    3. The hub broadcasts full telemetry JSON every 2 s (plus one frame the moment you
 *       connect). Send the text "power_on" / "power_off" to switch it remotely.
 *    mDNS: ws://solar-irrigation.local:8080 also works in most browsers.
 *    NOTE: if the website is served over HTTPS, browsers block plain ws:// (mixed content).
 *    Open it over HTTP or from a local file.
 **************************************************************************************************/

#include <WiFi.h>
#include <WebSocketsServer.h>   // ESP32 HOSTS the socket; the website connects to it
#include <ESPmDNS.h>            // optional <hostname>.local address discovery
#include <ArduinoJson.h>
#include <DHT.h>
#include <LiquidCrystal.h>
#include <esp_sleep.h>       // deep-sleep power-off mode (POWER_OFF_USES_DEEP_SLEEP)
#if POWER_OFF_USES_DEEP_SLEEP
#include <driver/rtc_io.h>   // keeps the SW pull-up alive in deep sleep (no phantom wakes)
#endif

/* =================================================================================================
 * 1. USER CONFIG - Wi-Fi & WebSocket server (EDIT TO MATCH YOUR NETWORK)
 * ===============================================================================================*/
static const char*     WIFI_SSID   = "Tartan";       // <-- your network name
static const char*     WIFI_PASS   = "Ev3nt$@CMUQ2026";   // <-- your network password
static const uint16_t  WS_PORT     = 8080;   // port your website connects to (ws://<esp-ip>:8080)
static const char*     MDNS_HOSTNAME = "solar-irrigation";   // also reachable as <name>.local

// Optional: fixed IP so your website can always target the same address.
// Match these to YOUR router's subnet, then set USE_STATIC_IP to 1.
#define USE_STATIC_IP   0
#if USE_STATIC_IP
static const IPAddress STATIC_IP(192, 168, 1, 50);
static const IPAddress STATIC_GW(192, 168, 1, 1);
static const IPAddress STATIC_SUBNET(255, 255, 255, 0);
#endif

static const uint32_t  WS_SEND_INTERVAL_MS   = 2000;   // telemetry cadence (spec: 2 s)
static const uint32_t  WIFI_RETRY_MS         = 10000;  // non-blocking reconnect attempt period

/* =================================================================================================
 * 2. PINOUT PROFILE
 * ------------------------------------------------------------------------------------------
 *  USER (default, REV B): the verified physical wiring. Encoder CLK/DT/SW = 25/26/27,
 *  LCD RS/E/D4-D7 = 14/13/32/33/5/21, NTC AO/DO = 36/4, DHT = 16, LDR = 34, soil = 35.
 *  All three analog inputs are ADC1 -> readings work with Wi-Fi on.
 *  Compile-time guards assert every pin in this profile.
 *
 *  RECOMMENDED: original self-selected safe pinout (kept for reference). Spec pins kept
 *  wherever feasible (RS 13, E 12, D6 33, D7 32, encoder 25/26/27, DHT 16);
 *  analog on the only free ADC1 pins (34, 35, 39); LCD D4/D5 on output-capable 23/21.
 *
 *  LITERAL: the original requested mapping with suggested GPIO 4/5 LCD fallback. Compiles
 *  and runs, but NTC (GPIO 15 = ADC2) reads 0 while Wi-Fi is on, and LDR (17) / soil (23)
 *  have no ADC - those menu rows will honestly show "no data".
 * ===============================================================================================*/
#define PINOUT_PROFILE_USER         1   // REV B verified wiring (default): all analog on ADC1
#define PINOUT_PROFILE_RECOMMENDED  2
#define PINOUT_PROFILE_LITERAL      3
#define PINOUT_PROFILE              PINOUT_PROFILE_USER

#if PINOUT_PROFILE == PINOUT_PROFILE_USER

  // REV B wiring (user-verified). Every analog input sits on ADC1, so all three analog
  // readings work with Wi-Fi active. LCD is fully off GPIO 12 (no MTDI strap risk).
  // 16x2 LCD, 4-bit parallel:
  #define LCD_RS_PIN   14     // brief clock glitch at reset - ignored, LCD E stays low
  #define LCD_E_PIN    13     // moved off GPIO 12 (MTDI strap) -> boot-strap risk eliminated
  #define LCD_D4_PIN   32     // ADC1_CH4 pin used as digital output - fine
  #define LCD_D5_PIN   33     // ADC1_CH5 pin used as digital output - fine
  #define LCD_D6_PIN   5      // strap pin (pulled high + boot glitch) - harmless while E is low
  #define LCD_D7_PIN   21

  // NTC thermistor module
  #define NTC_ADC_PIN  36     // VP/SVP, ADC1_CH0 (input-only) - reads correctly with Wi-Fi on
  #define NTC_D0_PIN   4      // digital threshold output (GPIO 4 free in this profile)

  // LDR photoresistor module
  #define LDR_ADC_PIN  34     // ADC1_CH6 (input-only) - analog only, which is all we need

  // Capacitive soil moisture sensor
  #define SOIL_ADC_PIN 35     // ADC1_CH7 (input-only) - reads correctly with Wi-Fi on

#elif PINOUT_PROFILE == PINOUT_PROFILE_RECOMMENDED

  // 16x2 LCD, 4-bit parallel (RS/E per spec; D4/D5 relocated off the input-only 35/34)
  #define LCD_RS_PIN   13
  #define LCD_E_PIN    12     // boot-strap pin (MTDI) - see note 5 in the header
  #define LCD_D4_PIN   23     // spec: 35 (input-only) -> fallback 4 conflicts w/ NTC D0 -> 23
  #define LCD_D5_PIN   21     // spec: 34 (input-only) -> GPIO 21 (free, no strap role)
  #define LCD_D6_PIN   33     // spec
  #define LCD_D7_PIN   32     // spec

  // NTC thermistor module: analog on ADC1, digital threshold output available (GPIO 15 freed up)
  #define NTC_ADC_PIN  35     // spec: 15 (ADC2, dead under Wi-Fi) -> GPIO 35 (ADC1_CH7)
  #define NTC_D0_PIN   15     // spec: 4 -> GPIO 15 (free in this profile); enable below if wanted

  // LDR photoresistor module: analog on ADC1 (GPIO 7 does not exist on classic ESP32)
  #define LDR_ADC_PIN  34     // spec: 7/17 -> GPIO 34 (ADC1_CH6, input-only = fine for ADC)

  #define SOIL_ADC_PIN 39     // spec: 23 (no ADC on classic ESP32) -> GPIO 39 (ADC1_CH3 / VN)

#elif PINOUT_PROFILE == PINOUT_PROFILE_LITERAL

  // 16x2 LCD, 4-bit parallel - literal spec + your suggested fallback for the input-only pins
  #define LCD_RS_PIN   13
  #define LCD_E_PIN    12
  #define LCD_D4_PIN   4      // spec fallback (GPIO 35 is input-only and cannot drive the bus)
  #define LCD_D5_PIN   5      // spec fallback (GPIO 34 is input-only)
  #define LCD_D6_PIN   33
  #define LCD_D7_PIN   32

  #define NTC_ADC_PIN  15     // ADC2: returns 0 while Wi-Fi is active
  #define NTC_D0_PIN   -1     // spec: 4, but GPIO 4 is LCD D4 here - conflict, kept disabled

  #define LDR_ADC_PIN  17     // NOT an ADC pin on classic ESP32 (reading will be 0)

  #define SOIL_ADC_PIN 23     // NOT an ADC pin on classic ESP32 (reading will be 0)

#else
  #error "PINOUT_PROFILE must be PINOUT_PROFILE_RECOMMENDED or PINOUT_PROFILE_LITERAL"
#endif

/* --- Remaining pins (identical in both profiles) ------------------------------------------------ */
#define ENCODER_CLK_PIN  25    // spec (ADC2 pin used digitally - fine)
#define ENCODER_DT_PIN   26    // spec
#define ENCODER_SW_PIN   27    // spec (has internal pull-up enabled)
#define DHT_PIN          16    // DHT11 signal (spec)

#define ENABLE_STATUS_LED 1
#define STATUS_LED_PIN    2    // on-board LED (strap-safe: leave low/floating at boot)

/* =================================================================================================
 * 3. FEATURE & SENSOR CALIBRATION CONFIG
 * ===============================================================================================*/
#define USE_NTC_DIGITAL        1     // NTC module D0 is wired to GPIO 4 in the USER profile
#define ENCODER_COUNTS_PER_DETENT 4  // quadrature counts per mechanical detent (KY-040 = 4)
                                     // One screen move per detent; sub-detent counts carry
                                     // over, so EVERY detected click moves exactly one screen.
#define ENCODER_INVERT         0     // 1 if the knob scrolls the wrong way
#define MENU_WRAPAROUND        1     // 1 = knob wraps past the ends, 0 = clamps
#define BUTTON_DEBOUNCE_MS     30
#define BUTTON_LONG_PRESS_MS   800

// --- Master power switch (encoder push button = ON/OFF) ---
#define POWER_OFF_USES_DEEP_SLEEP 0   // 0 = soft standby (Wi-Fi/WS stay up, remote "power_on"
                                      //     wakes it). 1 = true deep sleep: EXT0 wake on SW
                                      //     press, full reboot on wake, near-zero current.

#define CAROUSEL_INTERVAL_MS      15000  // LCD auto-advances to the next screen every 15 s
#define CAROUSEL_RESUME_AFTER_MS  30000  // after manual knob use, auto-cycling resumes this
                                         // long after the last interaction (long press =
                                         // explicit pause/resume override)
#define LCD_REFRESH_MS         200   // row-1 value refresh
#define LCD_SPLASH_MS          1500
#define IP_ADVERTISE_MS        8000   // how long the LCD shows "IP:port" after Wi-Fi connects

#define DHT_INTERVAL_MS        2500  // DHT11 needs >= 2 s between reads
#define NTC_INTERVAL_MS        300
#define LIGHT_INTERVAL_MS      300
#define SOIL_INTERVAL_MS       1000

#define ADC_FULL_SCALE         4095  // 12-bit, default 11 dB attenuation (~0-3.3 V)
#define ADC_DEADBAND           50    // readings this close to the rails are treated as faults

// NTC (beta model). Series/reference resistor on the module + NTC nominal.
#define NTC_R_SERIES           10000.0f
#define NTC_R_NOMINAL          10000.0f   // resistance at T_NOMINAL
#define NTC_T_NOMINAL_K        298.15f    // 25 C
#define NTC_BETA               3950.0f
#define NTC_VCC_MV             3300       // module supply rail in mV (measure VCC pin with a meter)
#define NTC_NTC_TO_GND         0          // 0 = NTC to VCC, fixed R to GND (mV rises with temp)
                                          // 1 = NTC to GND, fixed R to VCC (mV falls with temp)
                                          // If mV goes DOWN as you warm the NTC, flip this.

// CALIBRATION - how to make the display show ACTUAL degrees C:
//   1. Flash and let it run 5 min. Watch the [NTC] line on the serial monitor (mV, R, T)
//      and compare the NTC row on the LCD against the DHT11 row (or a real thermometer).
//   2. Set NTC_CAL_OFFSET_C = (real - displayed). Ex: shows 21.3 C, real 24.0 C -> 2.7f.
//   3. If the error GROWS with temperature (wrong slope), scale it:
//      NTC_CAL_GAIN = real_span / displayed_span between two known temperatures.
//   4. If T is unrealistic at ALL temperatures, the divider ratio is off: wrong NTC_NTC_TO_GND
//      polarity, or the module's fixed resistor is not 10k -> measure it and set NTC_R_SERIES.
#define NTC_CAL_OFFSET_C       0.0f
#define NTC_CAL_GAIN           1.0f
#define NTC_SMOOTHING          0.25f      // 0..1 EMA weight of each new reading (1 = no smoothing)
#define NTC_SERIAL_DEBUG       1          // periodic [NTC] mV/R/T line on the serial monitor

// LDR module. Most modules: voltage rises with light. Flip LDR_LIGHT_INCREASES if inverted.
#define LDR_LIGHT_INCREASES    1

// Capacitive soil sensor calibration (v1.2 @ 3.3 V typical; CALIBRATE with your unit!):
// record raw ADC in open air (dry) and submerged in water (wet), then edit these two values.
#define SOIL_DRY_ADC           2900
#define SOIL_WET_ADC           1300

/* =================================================================================================
 * 4. COMPILE-TIME PIN VALIDATION (catches wiring impossibilities before you flash)
 * ===============================================================================================*/
#define ESP32_PIN_FLASH_RANGE(p) ((p) >= 6 && (p) <= 11)                      // SPI flash - forbidden
#define ESP32_PIN_INPUT_ONLY(p)  ((p) >= 34 && (p) <= 39)                     // no output, no pull-ups
#define ESP32_PIN_IS_ADC1(p)     ((p) >= 32 && (p) <= 39)
#define ESP32_PIN_IS_ADC2(p)     ((p) == 0 || (p) == 2 || (p) == 4 || (p) == 12 || (p) == 13 \
                                  || (p) == 14 || (p) == 15 || (p) == 25 || (p) == 26 || (p) == 27)

#if PINOUT_PROFILE == PINOUT_PROFILE_LITERAL
  #pragma message("PINOUT_PROFILE=LITERAL: NTC(15) is ADC2 -> dead under Wi-Fi; LDR(17)/Soil(23) have no ADC. Those rows will show 'no data'. Use RECOMMENDED for a fully working build.")
#endif

#if ESP32_PIN_INPUT_ONLY(LCD_RS_PIN) || ESP32_PIN_INPUT_ONLY(LCD_E_PIN) \
 || ESP32_PIN_INPUT_ONLY(LCD_D4_PIN) || ESP32_PIN_INPUT_ONLY(LCD_D5_PIN) \
 || ESP32_PIN_INPUT_ONLY(LCD_D6_PIN) || ESP32_PIN_INPUT_ONLY(LCD_D7_PIN)
  #error "LCD pins must be output-capable: GPIO 34-39 are input-only on ESP32. Use a fallback pin (see PINOUT_PROFILE)."
#endif

#if ESP32_PIN_FLASH_RANGE(LCD_RS_PIN) || ESP32_PIN_FLASH_RANGE(LCD_E_PIN) \
 || ESP32_PIN_FLASH_RANGE(LCD_D4_PIN) || ESP32_PIN_FLASH_RANGE(LCD_D5_PIN) \
 || ESP32_PIN_FLASH_RANGE(LCD_D6_PIN) || ESP32_PIN_FLASH_RANGE(LCD_D7_PIN) \
 || ESP32_PIN_FLASH_RANGE(DHT_PIN)    || ESP32_PIN_FLASH_RANGE(STATUS_LED_PIN)
  #error "A pin assigned to an output/bidirectional role is inside GPIO 6-11 (SPI flash). Reassign it."
#endif

#if ESP32_PIN_INPUT_ONLY(DHT_PIN) \
 || ESP32_PIN_INPUT_ONLY(ENCODER_CLK_PIN) || ESP32_PIN_INPUT_ONLY(ENCODER_DT_PIN) \
 || ESP32_PIN_INPUT_ONLY(ENCODER_SW_PIN)
  #error "GPIO 34-39 are input-only AND have no internal pull-ups - unusable for DHT/trigger/encoder/pulled switch roles."
#endif

#if USE_NTC_DIGITAL && (NTC_D0_PIN == LCD_D4_PIN)
  #error "GPIO conflict: NTC module D0 and LCD D4 are both on the same pin (GPIO 4). Disable one."
#endif
#if USE_NTC_DIGITAL && (NTC_D0_PIN < 0)
  #error "USE_NTC_DIGITAL=1 but NTC_D0_PIN is unset (-1). Assign a free input pin."
#endif

#if ESP32_PIN_IS_ADC2(NTC_ADC_PIN)
  #pragma message("NTC_ADC_PIN is an ADC2 pin: analogRead() returns 0 while Wi-Fi is active. Prefer an ADC1 pin (GPIO 32-39).")
#endif
#if !ESP32_PIN_IS_ADC1(NTC_ADC_PIN) && !ESP32_PIN_IS_ADC2(NTC_ADC_PIN)
  #pragma message("NTC_ADC_PIN is not an ADC pin on classic ESP32 (e.g. GPIO 17/23). Reading will be 0.")
#endif
#if ESP32_PIN_IS_ADC2(LDR_ADC_PIN)
  #pragma message("LDR_ADC_PIN is an ADC2 pin: analogRead() returns 0 while Wi-Fi is active. Prefer an ADC1 pin (GPIO 32-39).")
#endif
#if !ESP32_PIN_IS_ADC1(LDR_ADC_PIN) && !ESP32_PIN_IS_ADC2(LDR_ADC_PIN)
  #pragma message("LDR_ADC_PIN is not an ADC pin on classic ESP32 (e.g. GPIO 17/23). Reading will be 0.")
#endif
#if ESP32_PIN_IS_ADC2(SOIL_ADC_PIN)
  #pragma message("SOIL_ADC_PIN is an ADC2 pin: analogRead() returns 0 while Wi-Fi is active. Prefer an ADC1 pin (GPIO 32-39).")
#endif
#if !ESP32_PIN_IS_ADC1(SOIL_ADC_PIN) && !ESP32_PIN_IS_ADC2(SOIL_ADC_PIN)
  #pragma message("SOIL_ADC_PIN is not an ADC pin on classic ESP32 (e.g. GPIO 17/23). Reading will be 0.")
#endif

#if LCD_E_PIN == 12
  #pragma message("LCD_E_PIN is GPIO 12 (MTDI boot strap). It must be LOW at reset. If the board fails to boot, move E to GPIO 14.")
#endif

/* =================================================================================================
 * 5. GLOBALS
 * ===============================================================================================*/
static const char* const MENU_NAMES[] = {
  "1.DHT11 Climate",
  "2.NTC Thermistor",
  "3.Light Level",
  "4.Soil Moisture",
};
static const uint8_t MENU_ITEM_COUNT = sizeof(MENU_NAMES) / sizeof(MENU_NAMES[0]);

LiquidCrystal      lcd(LCD_RS_PIN, LCD_E_PIN, LCD_D4_PIN, LCD_D5_PIN, LCD_D6_PIN, LCD_D7_PIN);
DHT                dht(DHT_PIN, DHT11);
WebSocketsServer   webSocket(WS_PORT);   // this ESP32 hosts; dashboards connect in

struct SensorState {
  // DHT11
  bool     dhtOk    = false;
  float    dhtTempC = NAN;
  float    dhtHum   = NAN;
  uint32_t dhtAtMs  = 0;
  // NTC
  bool     ntcOk    = false;
  float    ntcTempC = NAN;
  int      ntcAdc   = 0;
  uint32_t ntcMv    = 0;
  float    ntcROhm  = NAN;
  bool     ntcD0High= false;
  uint32_t ntcAtMs  = 0;
  // LDR
  bool     lightOk  = false;
  int      lightAdc = 0;
  uint8_t  lightPct = 0;
  uint32_t lightAtMs= 0;
  // Soil
  bool     soilOk   = false;
  int      soilAdc  = 0;
  uint8_t  soilPct  = 0;
  uint32_t soilAtMs = 0;
};
static SensorState sensorData;

static char     deviceId[24]           = "esp32";
static uint8_t  menuIndex              = 0;
static bool     carouselEnabled        = true;   // hands-free auto-cycle ON by default
static uint32_t lastUserInputMs        = 0;   // last knob interaction (0 = none since boot/on)
static bool     systemOn               = true;   // master power state (encoder SW toggles)

// Scheduler bookkeeping (all millis()-based, overflow-safe by unsigned subtraction)
static uint32_t lastDhtMs = 0, lastNtcMs = 0, lastLightMs = 0, lastSoilMs = 0;
static uint32_t lastLcdMs = 0, lastCarouselMs = 0, lastPublishMs = 0, lastWifiRetryMs = 0;

// LCD render bookkeeping
static int8_t   lcdMenuDrawn           = -1;   // forces a full draw on first render
static uint32_t splashUntilMs          = 0;
static uint32_t ipShowUntilMs          = 0;   // >0: LCD is showing the "IP:port" advert

// Rotary encoder (ISR-safe)
static portMUX_TYPE  encMux            = portMUX_INITIALIZER_UNLOCKED;
static volatile int32_t  encDelta      = 0;
static volatile uint8_t  encState      = 0;
static int32_t           encAccum      = 0;   // sub-detent carry so no click is ever dropped

// Push button (polled, debounced)
static bool     btnStable              = true; // true = released (INPUT_PULLUP, active-low)
static bool     btnLastReading         = true;
static uint32_t btnLastChangeMs        = 0;
static uint32_t btnPressedAtMs         = 0;

// One-shot publish request (long press)
static volatile bool forcePublish      = false;

// Full quadrature decoder table: index = (previousState << 2) | newState, state = (CLK<<1)|DT.
// Legal quarter-cycles yield +-1; a full mechanical detent nets +-4 (see ENCODER_COUNTS_PER_DETENT).
static const int8_t kQuadTable[16] = {
   0, -1,  1,  0,
   1,  0,  0, -1,
  -1,  0,  0,  1,
   0,  1, -1,  0
};

/* ===============================================================================================
 * 5b. FORWARD DECLARATIONS
 * -------------------------------------------------------------------------------------------
 *  The Arduino preprocessor auto-generates prototypes, but does so unreliably for IRAM_ATTR
 *  functions and in some edited/multi-part files - missing prototypes surface as confusing
 *  "'X' was not declared in this scope" errors at the CALL SITE (setup/loop) instead of the
 *  real problem. Declaring everything here makes the sketch immune to that entire class of
 *  failure, independent of function order.
 * ===============================================================================================*/
static void handleWifi(uint32_t now);
static void handleButton(uint32_t now);
static void handleEncoder(uint32_t now);
static void handleCarousel(uint32_t now);
static void renderLcd(uint32_t now);
static void readDht(uint32_t now);
static void readNtc(uint32_t now);
static void readLight(uint32_t now);
static void readSoil(uint32_t now);
static void publishTelemetry(uint32_t now);
static void handleStatusLed(uint32_t now);
static int32_t takeEncoderDelta();
static void moveMenu(int16_t steps);
static void lcdPrintPadded(uint8_t col, uint8_t row, const char* text);
static void formatMenuValue(uint8_t item, char* buf, size_t n);
void onWebSocketEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length);
void IRAM_ATTR encoderIsr();   // ISR: also exempt from Arduino auto-prototyping
static void startIpAdvert();
static void applyPowerState(bool on, const char* source);
#if POWER_OFF_USES_DEEP_SLEEP
static void enterDeepSleep();
#endif

// ArduinoJson v6 / v7 compatibility shim (nested object creation differs between majors)
#if ARDUINOJSON_VERSION_MAJOR >= 7
  #define JSON_OBJ(parent, key) parent[key].to<JsonObject>()
#else
  #define JSON_OBJ(parent, key) parent.createNestedObject(key)
#endif

// analogReadMilliVolts() exists from arduino-esp32 core 2.0.0 onward and applies the factory
// eFuse ADC calibration - this is what makes the NTC math produce TRUE voltages (and therefore
// true degrees C) despite the ESP32 ADC's well-known nonlinearity. On ancient 1.0.x cores we
// fall back to uncalibrated ratio math, which can drift several degrees.
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 2)
  #define NTC_HAS_MILLIVOLTS 1
#else
  #define NTC_HAS_MILLIVOLTS 0
#endif

/* =================================================================================================
 * 6. SETUP
 * ===============================================================================================*/
void setup() {
  Serial.begin(115200);
  delay(200);  // one-time: let the USB-CDC enumerate
  Serial.println();
  Serial.println(F("=== ESP32 Sensor Hub ==="));

  // Effective pinout report (verify against your wiring at a glance)
  Serial.printf("LCD   RS=%d E=%d D4=%d D5=%d D6=%d D7=%d\n",
                LCD_RS_PIN, LCD_E_PIN, LCD_D4_PIN, LCD_D5_PIN, LCD_D6_PIN, LCD_D7_PIN);
  Serial.printf("NTC   ADC=%d D0=%d(%s) | LDR ADC=%d | SOIL ADC=%d\n",
                NTC_ADC_PIN, NTC_D0_PIN, USE_NTC_DIGITAL ? "on" : "off", LDR_ADC_PIN, SOIL_ADC_PIN);
  Serial.printf("DHT=%d | ENC CLK=%d DT=%d SW=%d | LED=%d\n",
                DHT_PIN, ENCODER_CLK_PIN, ENCODER_DT_PIN, ENCODER_SW_PIN, STATUS_LED_PIN);

  // --- GPIOs ---
  pinMode(ENCODER_CLK_PIN, INPUT_PULLUP);
  pinMode(ENCODER_DT_PIN,  INPUT_PULLUP);
  pinMode(ENCODER_SW_PIN,  INPUT_PULLUP);
#if USE_NTC_DIGITAL
  pinMode(NTC_D0_PIN, INPUT);
#endif
#if ENABLE_STATUS_LED
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);
#endif

  // --- ADC ---
  analogReadResolution(12);                    // 0..4095, core default 11 dB attenuation (~3.3 V FS)

  // --- LCD (needs a settle time after power-up; this is the only deliberate blocking delay) ---
  delay(120);
  lcd.begin(16, 2);
  lcd.print(F("ESP32 Sensor Hub"));
  lcd.setCursor(0, 1);
  lcd.print(F("Booting..."));
  splashUntilMs = millis() + LCD_SPLASH_MS;

  // --- DHT11 ---
  dht.begin();

  // --- Rotary encoder: quadrature decode on BOTH edges of CLK and DT ---
  encState = ((uint8_t)digitalRead(ENCODER_CLK_PIN) << 1) | (uint8_t)digitalRead(ENCODER_DT_PIN);
  attachInterrupt(digitalPinToInterrupt(ENCODER_CLK_PIN), encoderIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENCODER_DT_PIN),  encoderIsr, CHANGE);

  // --- Device id from eFuse MAC (stable identifier for the server side) ---
  uint64_t mac = ESP.getEfuseMac();
  snprintf(deviceId, sizeof(deviceId), "esp32-%02X%02X%02X%02X%02X%02X",
           (unsigned)((mac >> 40) & 0xFF), (unsigned)((mac >> 32) & 0xFF),
           (unsigned)((mac >> 24) & 0xFF), (unsigned)((mac >> 16) & 0xFF),
           (unsigned)((mac >>  8) & 0xFF), (unsigned)(mac & 0xFF));

  // --- Wi-Fi (non-blocking: no connect-wait loop; the loop() state machine retries) ---
#if USE_STATIC_IP
  WiFi.config(STATIC_IP, STATIC_GW, STATIC_SUBNET);   // fixed address: site can hardcode it
#endif
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);            // lower latency for WebSocket traffic
  WiFi.setAutoReconnect(true);
  WiFi.setHostname(deviceId);      // must precede begin()
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("[WiFi] connecting to \"%s\" as %s ...\n", WIFI_SSID, deviceId);

  // --- WebSocket SERVER: your website connects TO this ESP32 over the LAN ---
  webSocket.begin();
  webSocket.onEvent(onWebSocketEvent);
  webSocket.enableHeartbeat(15000, 3000, 2);   // ping idle clients; dead ones are dropped
  Serial.printf("[WS] server listening on port %u - enter this ESP32's IP in your website\n", WS_PORT);
}

/* =================================================================================================
 * 7. MAIN LOOP - fully non-blocking cooperative scheduler (no delay())
 * ===============================================================================================*/
void loop() {
  const uint32_t now = millis();

  webSocket.loop();        // drives WS RX/TX, reconnects and heartbeat (also in standby)
  handleWifi(now);         // STA recovery if the AP drops for good
  handleButton(now);       // debounced encoder push-button = master ON/OFF switch

  if (systemOn) {          // standby: sensors, menu and display are paused
    handleEncoder(now);    // menu navigation from ISR delta
    readDht(now);
    readNtc(now);
    readLight(now);
    readSoil(now);
    handleCarousel(now);
    renderLcd(now);
  }

  publishTelemetry(now);   // in standby only the one-off state frame goes out
  handleStatusLed(now);
}

/* =================================================================================================
 * 8. ROTARY ENCODER & BUTTON
 * ===============================================================================================*/
void IRAM_ATTR encoderIsr() {
  // No time-based glitch filter: during a fast spin legitimate edges arrive <1 ms apart and
  // filtering them DROPS detents (the "missed screens" bug). The quadrature state table below
  // is inherently bounce-tolerant - illegal transitions score 0, and contact bounce between
  // two adjacent states nets out to zero movement.
  const uint8_t newState =
      ((uint8_t)digitalRead(ENCODER_CLK_PIN) << 1) | (uint8_t)digitalRead(ENCODER_DT_PIN);
  const int8_t step = kQuadTable[(encState << 2) | newState];
  encState = newState;

  if (step != 0) {
    portENTER_CRITICAL_ISR(&encMux);
    encDelta += step;
    portEXIT_CRITICAL_ISR(&encMux);
  }
}

static int32_t takeEncoderDelta() {
  portENTER_CRITICAL(&encMux);
  const int32_t d = encDelta;
  encDelta = 0;
  portEXIT_CRITICAL(&encMux);
  return d;
}

static void moveMenu(int16_t steps) {
  int16_t idx = (int16_t)menuIndex + steps;
#if MENU_WRAPAROUND
  idx %= (int16_t)MENU_ITEM_COUNT;
  if (idx < 0) idx += (int16_t)MENU_ITEM_COUNT;
#else
  if (idx < 0) idx = 0;
  if (idx > (int16_t)MENU_ITEM_COUNT - 1) idx = MENU_ITEM_COUNT - 1;
#endif
  if (idx != (int16_t)menuIndex) {
    menuIndex = (uint8_t)idx;
    Serial.printf("[Menu] -> %u: %s\n", menuIndex, MENU_NAMES[menuIndex]);
  }
}

static void handleEncoder(uint32_t now) {
  int32_t d = takeEncoderDelta();
#if ENCODER_INVERT
  d = -d;
#endif
  encAccum += d;

  // One menu step per completed detent; the remainder stays pending, so even when ISR events
  // arrive unevenly EVERY detected detent advances exactly one screen (no lost clicks).
  const int32_t steps = encAccum / ENCODER_COUNTS_PER_DETENT;
  if (steps == 0) return;
  encAccum -= steps * ENCODER_COUNTS_PER_DETENT;

  lastUserInputMs = now;               // manual input: auto-cycle pauses (resume window)
  moveMenu((int16_t)steps);
}

static void handleButton(uint32_t now) {
  const bool reading = (digitalRead(ENCODER_SW_PIN) == LOW);   // active-low with pull-up

  if (reading != btnLastReading) {             // raw edge -> restart the stability window
    btnLastReading  = reading;
    btnLastChangeMs = now;
  }

  if ((now - btnLastChangeMs) >= BUTTON_DEBOUNCE_MS && reading != btnStable) {
    btnStable = reading;
    if (btnStable) {                           // just pressed
      btnPressedAtMs = now;
      Serial.println(F("[Btn] pressed"));
    } else {                                   // just released
      const uint32_t heldMs = now - btnPressedAtMs;
      if (heldMs >= BUTTON_LONG_PRESS_MS) {
        carouselEnabled = !carouselEnabled;    // long press: toggle auto-rotate
        lastCarouselMs  = now;
        Serial.printf("[Btn] long press -> carousel %s\n", carouselEnabled ? "ON" : "OFF");
      } else {
#if POWER_OFF_USES_DEEP_SLEEP
        if (systemOn) enterDeepSleep();        // short press: true power off (wake = reboot)
#else
        applyPowerState(!systemOn, "button");  // short press: master ON/OFF switch
#endif
      }
    }
  }
}

/* --- LCD address advert: shown at every power-on and on every Wi-Fi (re)connect --------- */
static void startIpAdvert() {
  ipShowUntilMs = millis() + IP_ADVERTISE_MS;
}

/* --- Master power switch (encoder push button + remote commands) ----------------------- */
static void applyPowerState(bool on, const char* source) {
  if (on == systemOn) return;
  systemOn = on;
  Serial.printf("[Power] system %s (%s)\n", on ? "ON" : "OFF (standby)", source);

  (void)takeEncoderDelta();   // discard knob motion accumulated while powered off
  encAccum = 0;

  // Restart every sample cadence so the first screens after power-on show fresh values.
  lastDhtMs = lastNtcMs = lastLightMs = lastSoilMs = 0;
  lastLcdMs = lastCarouselMs = 0;
  lastUserInputMs = 0;                 // fresh power-on: 15 s auto-cycle starts immediately
  lcdMenuDrawn = -1;

  lcd.clear();
  if (on) {
    if (WiFi.status() == WL_CONNECTED) {
      startIpAdvert();                       // show "WS Server ready / <ip>:<port>"
    } else {
      lcdPrintPadded(0, 0, "SYSTEM ON");     // address not known yet - the Wi-Fi connect
      lcdPrintPadded(0, 1, "WiFi connecting"); // hook shows the advert as soon as it lands
    }
  } else {
    lcdPrintPadded(0, 0, "SYSTEM OFF");
    lcdPrintPadded(0, 1, "Press to start");
  }

  forcePublish = true;   // send one state-change frame (also the final frame on power-off)
}

#if POWER_OFF_USES_DEEP_SLEEP
static void enterDeepSleep() {
  Serial.println(F("[Power] entering deep sleep - press SW to wake (device reboots)"));
  lcd.clear();
  lcdPrintPadded(0, 0, "SYSTEM OFF");
  lcdPrintPadded(0, 1, "Sleeping...");
  delay(150);   // one-time: let the LCD refresh before the core powers down
  esp_sleep_enable_ext0_wakeup((gpio_num_t)ENCODER_SW_PIN, 0);  // wake when SW is pressed (LOW)
  rtc_gpio_pullup_en((gpio_num_t)ENCODER_SW_PIN);   // hold the pull-up through deep sleep,
  rtc_gpio_pulldown_dis((gpio_num_t)ENCODER_SW_PIN);// otherwise a floating SW wakes the chip
  esp_deep_sleep_start();                                       // never returns
}
#endif

static void handleCarousel(uint32_t now) {
  if (!carouselEnabled) return;
  // 0 = no manual input yet (or freshly powered on): cycling is active immediately.
  if (lastUserInputMs != 0 && now - lastUserInputMs < CAROUSEL_RESUME_AFTER_MS) return;
  if (now - lastCarouselMs >= CAROUSEL_INTERVAL_MS) {
    lastCarouselMs = now;
    moveMenu(+1);
  }
}

/* =================================================================================================
 * 9. SENSOR READS (each on its own cadence, all non-blocking)
 * ===============================================================================================*/
static void readDht(uint32_t now) {
  if (now - lastDhtMs < DHT_INTERVAL_MS) return;
  lastDhtMs = now;

  const float t = dht.readTemperature();       // C
  const float h = dht.readHumidity();          // %RH
  sensorData.dhtOk = !isnan(t) && !isnan(h);
  if (sensorData.dhtOk) {
    sensorData.dhtTempC = t;
    sensorData.dhtHum   = h;
  }
}

static void readNtc(uint32_t now) {
  if (now - lastNtcMs < NTC_INTERVAL_MS) return;
  lastNtcMs = now;

  // Raw counts for rail detection; eFuse-calibrated millivolts for the math (see NTC_HAS_MILLIVOLTS).
  const int adc = analogRead(NTC_ADC_PIN);
#if NTC_HAS_MILLIVOLTS
  const uint32_t mv = analogReadMilliVolts(NTC_ADC_PIN);
#else
  const uint32_t mv = (uint32_t)((int64_t)adc * (int64_t)NTC_VCC_MV / ADC_FULL_SCALE);
#endif
  sensorData.ntcAdc = adc;
  sensorData.ntcMv  = mv;
#if USE_NTC_DIGITAL
  sensorData.ntcD0High = (digitalRead(NTC_D0_PIN) == HIGH);
#endif

  const bool pinned = (adc < ADC_DEADBAND) || (adc > ADC_FULL_SCALE - ADC_DEADBAND);
  if (pinned) { sensorData.ntcOk = false; return; }   // open circuit / short to rail

  // Divider -> NTC resistance from the measured junction voltage (Vout = mv):
  const float vout = (float)mv;
  float rNtc;
#if NTC_NTC_TO_GND            // NTC to GND, fixed R to VCC: Vout = Vcc*R_ntc/(R_ntc+R_ser)
  rNtc = NTC_R_SERIES * vout / ((float)NTC_VCC_MV - vout);
#else                         // NTC to VCC, fixed R to GND: Vout = Vcc*R_ser/(R_ntc+R_ser)
  rNtc = NTC_R_SERIES * ((float)NTC_VCC_MV - vout) / vout;
#endif

  // Beta model -> Kelvin, then linear user calibration -> actual degrees C.
  const float invK = 1.0f / NTC_T_NOMINAL_K + logf(rNtc / NTC_R_NOMINAL) / NTC_BETA;
  float       c    = (1.0f / invK) - 273.15f;
  c = NTC_CAL_GAIN * c + NTC_CAL_OFFSET_C;

  // NOTE: at 11 dB attenuation the ESP32 ADC saturates near ~3160 mV, so an NTC-to-VCC module
  // loses accuracy only on very HOT readings close to the rail. Indoor range stays exact.
  sensorData.ntcROhm = rNtc;

  static bool firstSample = true;                     // EMA smoothing, seeded by the first sample
  const bool  ok = (c > -40.0f && c < 150.0f);
  if (ok) {
    if (firstSample) { sensorData.ntcTempC = c; firstSample = false; }
    else sensorData.ntcTempC = NTC_SMOOTHING * c + (1.0f - NTC_SMOOTHING) * sensorData.ntcTempC;
  }
  sensorData.ntcOk = ok;

#if NTC_SERIAL_DEBUG
  static uint32_t lastNtcDbg = 0;
  if (now - lastNtcDbg >= 2000) {
    lastNtcDbg = now;
    Serial.printf("[NTC] %lu mV, R=%.0f ohm, T=%.1f C (raw=%d)\n",
                  (unsigned long)mv, (double)rNtc, (double)sensorData.ntcTempC, adc);
  }
#endif
}

static void readLight(uint32_t now) {
  if (now - lastLightMs < LIGHT_INTERVAL_MS) return;
  lastLightMs = now;

  const int adc = analogRead(LDR_ADC_PIN);
  sensorData.lightAdc = adc;
  const bool pinned = (adc < ADC_DEADBAND) || (adc > ADC_FULL_SCALE - ADC_DEADBAND);
  if (pinned) { sensorData.lightOk = false; return; }

  float pct = (float)adc * 100.0f / (float)ADC_FULL_SCALE;
#if !LDR_LIGHT_INCREASES
  pct = 100.0f - pct;
#endif
  sensorData.lightPct = (uint8_t)constrain((int)(pct + 0.5f), 0, 100);
  sensorData.lightOk  = true;
}

static void readSoil(uint32_t now) {
  if (now - lastSoilMs < SOIL_INTERVAL_MS) return;
  lastSoilMs = now;

  const int adc = analogRead(SOIL_ADC_PIN);
  sensorData.soilAdc = adc;
  const bool pinned = (adc < ADC_DEADBAND) || (adc > ADC_FULL_SCALE - ADC_DEADBAND);
  if (pinned) { sensorData.soilOk = false; return; }

  // Dry -> raw SOIL_DRY_ADC (0%), wet -> raw SOIL_WET_ADC (100%). Calibrate the two constants!
  float pct = (float)(SOIL_DRY_ADC - adc) * 100.0f / (float)(SOIL_DRY_ADC - SOIL_WET_ADC);
  sensorData.soilPct = (uint8_t)constrain((int)(pct + 0.5f), 0, 100);
  sensorData.soilOk  = true;
}

/* =================================================================================================
 * 10. LCD RENDERING (overwrite-in-place, no clear() -> zero flicker)
 * ===============================================================================================*/
static void lcdPrintPadded(uint8_t col, uint8_t row, const char* text) {
  char buf[17];
  snprintf(buf, sizeof(buf), "%-16s", text);   // pad with spaces / hard-truncate at 16
  lcd.setCursor(col, row);
  lcd.print(buf);
}

static void formatMenuValue(uint8_t item, char* buf, size_t n) {
  switch (item) {
    case 0:  // DHT11
      if (sensorData.dhtOk) snprintf(buf, n, "T:%.1fC H:%.0f%%", sensorData.dhtTempC, sensorData.dhtHum);
      else                  snprintf(buf, n, "DHT11 no data");
      break;
    case 1:  // NTC
      if (sensorData.ntcOk) snprintf(buf, n, "%.1fC %umV", sensorData.ntcTempC, (unsigned)sensorData.ntcMv);
      else                  snprintf(buf, n, "NTC no data");
      break;
    case 2:  // Light
      if (sensorData.lightOk) snprintf(buf, n, "ADC:%d %u%%", sensorData.lightAdc, (unsigned)sensorData.lightPct);
      else                    snprintf(buf, n, "LDR no data");
      break;
    case 3:  // Soil
      if (sensorData.soilOk) snprintf(buf, n, "%u%%  ADC:%d", (unsigned)sensorData.soilPct, sensorData.soilAdc);
      else                   snprintf(buf, n, "Soil no data");
      break;
    default:
      snprintf(buf, n, "-");
      break;
  }
}

static void renderLcd(uint32_t now) {
  if (now < splashUntilMs) return;

  if (now < ipShowUntilMs) {                     // advertise "WS Server / IP:port" to the user
    static uint32_t lastIpDraw = 0;
    if (now - lastIpDraw >= 500) {
      lastIpDraw = now;
      if (WiFi.status() == WL_CONNECTED) {
        char l1[17];
        snprintf(l1, sizeof(l1), "%s:%u", WiFi.localIP().toString().c_str(), (unsigned)WS_PORT);
        lcdPrintPadded(0, 0, "WS Server ready");
        lcdPrintPadded(0, 1, l1);
      }
    }
    return;
  }
  if (ipShowUntilMs != 0) { ipShowUntilMs = 0; lcdMenuDrawn = -1; }   // repaint menu after ad

  const bool menuChanged = (lcdMenuDrawn != (int8_t)menuIndex);
  if (!menuChanged && (now - lastLcdMs < LCD_REFRESH_MS)) return;

  lastLcdMs    = now;
  lcdMenuDrawn = (int8_t)menuIndex;

  if (menuChanged) lcdPrintPadded(0, 0, MENU_NAMES[menuIndex]);   // row 0: static per screen

  char row1[17];
  formatMenuValue(menuIndex, row1, sizeof(row1));
  lcdPrintPadded(0, 1, row1);                                     // row 1: live value + unit
}

/* =================================================================================================
 * 11. Wi-Fi / LED / TELEMETRY
 * ===============================================================================================*/
static void handleWifi(uint32_t now) {
  const bool conn = (WiFi.status() == WL_CONNECTED);

#if USE_STATIC_IP == 0
  static bool mdnsStarted = false;
  if (conn && !mdnsStarted) {
    mdnsStarted = true;
    if (MDNS.begin(MDNS_HOSTNAME)) {
      MDNS.addService("ws", "tcp", WS_PORT);
      Serial.printf("[mDNS] also reachable as %s.local (WS port %u)\n", MDNS_HOSTNAME, WS_PORT);
    }
  }
#endif

  static bool wasConnected = false;
  if (conn && !wasConnected) {
    startIpAdvert();                         // advertise our address on the LCD
    forcePublish  = true;                    // greet any dashboard that is already open
  }
  wasConnected = conn;

  if (conn) return;
  if (now - lastWifiRetryMs < WIFI_RETRY_MS) return;
  lastWifiRetryMs = now;
  Serial.println(F("[WiFi] disconnected - retrying..."));
  WiFi.reconnect();
}

static void handleStatusLed(uint32_t now) {
  (void)now;
#if ENABLE_STATUS_LED
  if (!systemOn) {   // standby: distinctive double blink
    const uint32_t phase = now % 1600;
    digitalWrite(STATUS_LED_PIN, (phase < 40 || (phase >= 80 && phase < 120)) ? HIGH : LOW);
    return;
  }
  uint32_t period, onMs;
  const bool wifiUp = (WiFi.status() == WL_CONNECTED);
  if (!wifiUp)                       { period = 200;  onMs = 100; }  // fast blink: no Wi-Fi
  else if (webSocket.connectedClients() == 0) { period = 800;  onMs = 200; }  // slow blink: no dashboard
  else                               { period = 3000; onMs = 40;  }  // brief flash: all good
  digitalWrite(STATUS_LED_PIN, (now % period) < onMs ? HIGH : LOW);
#endif
}

void onWebSocketEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      Serial.printf("[WS] dashboard #%u connected (%s)\n", num, (char*)payload);
      forcePublish = true;                     // greet the new client with a full state frame
      break;
    case WStype_DISCONNECTED:
      Serial.printf("[WS] dashboard #%u disconnected\n", num);
      break;
    case WStype_TEXT: {
      Serial.printf("[WS] RX: %.*s\n", (int)length, (const char*)payload);
      char cmd[16] = {0};
      if (length < sizeof(cmd)) memcpy(cmd, payload, length);
      if      (!strcmp(cmd, "power_on"))  applyPowerState(true,  "ws command");
      else if (!strcmp(cmd, "power_off")) {
#if POWER_OFF_USES_DEEP_SLEEP
        enterDeepSleep();
#else
        applyPowerState(false, "ws command");
#endif
      }
      break;
    }
    case WStype_ERROR:
      Serial.println(F("[WS] error"));
      break;
    default:
      break;
  }
}

static void publishTelemetry(uint32_t now) {
  if (!systemOn && !forcePublish) return;   // standby: only the state-change frame goes out
  if (!forcePublish && (now - lastPublishMs < WS_SEND_INTERVAL_MS)) return;
  lastPublishMs = now;
  forcePublish  = false;

  const bool wifiUp = (WiFi.status() == WL_CONNECTED);
  const bool wsUp   = webSocket.connectedClients() > 0;

  // Pack all metrics into one JSON document (v6 and v7 supported via JSON_OBJ shim).
#if ARDUINOJSON_VERSION_MAJOR >= 7
  JsonDocument doc;
#else
  DynamicJsonDocument doc(2048);
#endif
  doc["device"]       = deviceId;
  doc["uptime_s"]     = (uint32_t)(millis() / 1000);
  doc["heap_free"]    = (uint32_t)ESP.getFreeHeap();
  doc["menu_idx"]     = menuIndex;
  doc["ws_connected"] = wsUp;
  doc["power_state"]  = systemOn ? "on" : "standby";
  if (wifiUp) doc["wifi_rssi"] = (int)WiFi.RSSI();

  { JsonObject o = JSON_OBJ(doc, "dht");
    o["ok"] = sensorData.dhtOk;
    if (sensorData.dhtOk) { o["temp_c"] = sensorData.dhtTempC; o["hum_pct"] = sensorData.dhtHum; } }

  { JsonObject o = JSON_OBJ(doc, "ntc");
    o["ok"]  = sensorData.ntcOk;
    o["adc"] = sensorData.ntcAdc;
    o["mv"]  = sensorData.ntcMv;
    if (sensorData.ntcOk) {
      o["temp_c"] = sensorData.ntcTempC;
      o["temp_f"] = sensorData.ntcTempC * 9.0f / 5.0f + 32.0f;
      o["r_ohm"]  = sensorData.ntcROhm;
    }
#if USE_NTC_DIGITAL
    o["d0_high"] = sensorData.ntcD0High;
#endif
  }

  { JsonObject o = JSON_OBJ(doc, "light");
    o["ok"]  = sensorData.lightOk;
    if (sensorData.lightOk) { o["adc"] = sensorData.lightAdc; o["pct"] = sensorData.lightPct; } }

  { JsonObject o = JSON_OBJ(doc, "soil");
    o["ok"]  = sensorData.soilOk;
    if (sensorData.soilOk) { o["adc"] = sensorData.soilAdc; o["pct"] = sensorData.soilPct; } }

  String json;
  json.reserve(768);
  serializeJson(doc, json);

  if (wsUp) {
    webSocket.broadcastTXT(json);   // to every connected dashboard
    Serial.println("[WS] TX: " + json);
  } else {
    Serial.println("[WS] (no dashboards connected) payload: " + json);
  }
}
