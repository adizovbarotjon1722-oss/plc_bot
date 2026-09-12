#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <UniversalTelegramBot.h>
#include <Preferences.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <time.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <LittleFS.h>
#include <WebServer.h>
// ^ YANGI (PLC bot bilan integratsiya, faqat O'QISH uchun): bu kutubxona
// ESP32'da standart o'rnatilgan (qo'shimcha o'rnatish shart emas). Mavjud
// Telegram bot, rele, sirena va tugma mantig'iga HECH QANDAY ta'sir
// qilmaydi — faqat mahalliy tarmoqda oddiy JSON status sahifasi ochadi.

// MUHIM: bu struct ATAYLAB faylning ENG BOSHIGA ko'chirilgan (avval 14b-band
// ichida edi). Sabab: Arduino IDE avtomatik funksiya prototiplarini
// faylning eng yuqorisiga (include'lardan keyin, birinchi "haqiqiy" koddan
// oldin) qo'yadi. buttonPressedEdge(ButtonState &b) funksiyasi shu structni
// parametr sifatida oladi — agar struct pastda e'lon qilingan bo'lsa,
// avtomatik prototip ButtonState nima ekanini bilmay, "was not declared in
// this scope" xatosini beradi. Shu struct shu yerda turgani uchun ushbu
// muammo butunlay bartaraf bo'ladi.
struct ButtonState {
  int pin;
  bool lastReading;      // oxirgi XOM (filtrsiz) o'qilgan holat — sakrashni kuzatish uchun
  bool stableState;      // debounce'dan o'tgan, HOZIRGI barqaror holat
  unsigned long lastChangeTime;
};

// ============================================================================
// 1) SOZLAMALAR
// ============================================================================
const char* BOT_TOKEN = "8807858970:AAHqw6mPg1YVWmj1j9kP8GDDORoZPFoY4tc";

const char* WIFI_SSID     = "Guest";       // standart (zaxira) tarmoq
const char* WIFI_PASSWORD = "Yapp2016";    // standart (zaxira) parol

#define BTN_START_STOP   27
#define BTN_ALARM_RESET  26

// JISMONIY REJIM TUGMALARI
// 1-Rejim: Havo + Suv + Temp
// 2-Rejim: Faqat Havo
#define BTN_MODE1        16
#define BTN_MODE2        17

#define AIR_SENSOR_PIN   34
#define WATER_SENSOR_PIN 35
#define TEMP_SENSOR_PIN  4

#define RELAY_PIN        14

// YANGI (3-band): endi BITTA emas, UCHTA alohida siren chiqishi bor —
// har biri o'z sababiga qarab mustaqil yonadi/o'chadi:
//   SIREN_AIR_PIN   -> havo bosimi me'yordan chiqqanda
//   SIREN_WATER_PIN -> suv bosimi me'yordan chiqqanda (faqat 1-Rejim)
//   SIREN_TEMP_PIN  -> suv harorati me'yordan chiqqanda (faqat 1-Rejim)
// Agar sizning qurilmangizda boshqa bo'sh GPIO'lar band bo'lsa, faqat shu
// 3 ta qatorni o'zgartiring — qolgan kodga tegishli emas. Taxmin: sirenalar
// HIGH bilan yonadi; agar sizniki teskari (LOW bilan yonadigan) bo'lsa,
// applySirenPin() funksiyasidagi HIGH/LOW qiymatlarini almashtiring.
#define SIREN_AIR_PIN    25
#define SIREN_WATER_PIN  32
#define SIREN_TEMP_PIN  33

// YANGI: sozlamalar standart qiymatlari qachon "majburiy" qayta yozilishini
// belgilaydi. Shu raqamni oshirsangiz, qurilma KEYINGI ishga tushishda
// (hozirgi so'rovdagi) yangi standart qiymatlarni MAJBURIY qo'llaydi —
// hatto avval boshqa qiymatlar saqlangan bo'lsa ham. Shundan keyin,
// Telegram orqali o'zgartirgan qiymatlaringiz odatdagidek saqlanib qoladi
// (bu versiya raqami o'zgarmaguncha qayta "reset" bo'lmaydi).
#define CONFIG_VERSION   3

const char* ntpServer = "pool.ntp.org";
const long  gmtOffset_sec = 18000; // GMT+5
const int   daylightOffset_sec = 0;

WiFiClientSecure client;
UniversalTelegramBot bot(BOT_TOKEN, client);
Preferences prefs;
LiquidCrystal_I2C lcd(0x27, 20, 4);

// YANGI: faqat O'QISH uchun mahalliy status-server (80-port). Bu Python
// (PLC diagnostika) botiga joriy bosim/harorat/holatni ko'rsatish imkonini
// beradi. Hech qanday buyruq/boshqaruv qabul qilmaydi — faqat GET /status.
WebServer statusServer(80);

OneWire oneWire(TEMP_SENSOR_PIN);
DallasTemperature tempSensors(&oneWire);

// Wi-Fi: Telegram orqali o'zgartirilishi mumkin bo'lgan joriy tarmoq/parol
// (standart qiymatlar prefs bo'sh bo'lsa yuqoridagi WIFI_SSID/WIFI_PASSWORD'dan olinadi)
String currentSSID = "";
String currentPassword = "";
String tempNewSSID = ""; // Wi-Fi almashtirish oqimida SSID vaqtincha shu yerda saqlanadi

// Wi-Fi almashtirishda yangi tarmoq ISHLASHI aniq bo'lmaguncha eski tarmoq
// Preferences'dan o'chirilmaydi. Yangi tarmoq ulanmasa, avtomatik eski
// Wi-Fi'ga qaytiladi.
String previousSSID = "";
String previousPassword = "";
String wifiSwitchRequester = "";
bool wifiSwitchPending = false;
bool wifiRollbackNoticePending = false;
unsigned long wifiSwitchStartedAt = 0;
const unsigned long WIFI_SWITCH_TIMEOUT = 25000;

// Asosiy reply-klaviatura (band 1: bosish orqali ma'lumot olish, ortiqcha xabar yo'q)
String adminKeyboard   = "[[\"📊 Holat\", \"🛠️ Sozlamalar\"], [\"🚀 Start\", \"⏸️ Stop\"], [\"👥 Xodimlar\", \"🔑 Adminlar\"], [\"🌐 1-Rejim\", \"💨 2-Rejim\"], [\"📶 Wi-Fi\"]]";
String watcherKeyboard = "[[\"📊 Holat\"]]";
String guestKeyboard   = "[[\"🔓 Ruxsat so'rash\"]]";

// ============================================================================
// 2) TIZIM O'ZGARUVCHILARI
// ============================================================================
bool systemActive = true;
// monitoringStarted: 1-/2-Rejim tugmasi (yoki endi jismoniy Start tugmasi)
// bosilgandagina true bo'ladi. Faqat shu holatda avariya/avtomatik xabarlar
// Telegramga boradi.
bool monitoringStarted = false;
int currentMode = 1;        // 1: Havo+Suv+Temp | 2: Faqat Havo

// "Joriy/aktiv" limitlar — ekranda va tekshiruvda ishlatiladigan qiymatlar.
// Havo limitlari endi REJIMGA QARAB alohida saqlanadi (pastga qarang:
// m1AirMin/... va m2AirMin/...), chunki 1-Rejim va 2-Rejim uchun havo
// bosimi chegaralari boshqa-boshqa bo'lishi kerak edi. Rejim almashganda
// bu uchtasi (minAirP/normAirP/maxAirP) tegishli rejim qiymatlariga
// avtomatik almashtiriladi (loadAirForMode() orqali).
float minAirP = 7.0, normAirP = 9.0, maxAirP = 11.0;
float minWaterP = 1.5, normWaterP = 3.0, maxWaterP = 4.5;
float minWaterTemp = 5.0, normWaterTemp = 10.0, maxWaterTemp = 14.0;

// YANGI: har bir rejim uchun ALOHIDA havo bosimi limitlari
float m1AirMin = 10.0, m1AirNorm = 11.0, m1AirMax = 12.0;   // 1-Rejim standarti
float m2AirMin = 7.0,  m2AirNorm = 9.0,  m2AirMax = 10.5;   // 2-Rejim standarti

// 5-band: nol-kalibrlash siljishi (sensor haqiqiy 0 bosimda ham ozgina
// oqim ko'rsatishi mumkin — shu farqni chiqarib tashlash uchun)
float airZeroOffset = 0.0, waterZeroOffset = 1.0;
// ^ waterZeroOffset standart qiymati endi 1.0 — chunki suv sensoringiz
// mexanik manometrga nisbatan doimiy ravishda 1 bar ORTIQCHA ko'rsatib
// turgani aytilgan edi (2-band). Kerak bo'lsa "🎯 Suvni kalibrlash"
// tugmasi orqali istalgan vaqtda qaytadan aniqroq sozlashingiz mumkin.

// 5-band: kalibrlash uchun eng oxirgi XOM (offsetsiz) o'lchovlar
float rawAirBarCached = 0.0, rawWaterBarCached = 0.0;

const float DIVIDER_RATIO   = 0.6;
const float BURDEN_RESISTOR = 250.0;
const float AIR_SENSOR_MIN_BAR   = 0.0,  AIR_SENSOR_MAX_BAR   = 16.0;
const float WATER_SENSOR_MIN_BAR = 0.0,  WATER_SENSOR_MAX_BAR = 10.0;

struct Staff {
  String name;
  String id;
};

Staff superAdmin = {"Admin", "1047268578"};
Staff dayMech     = {"Javohir", "5569218743"};
Staff dayMaster   = {"Vosit",   "602455217"};
Staff nightMech   = {"Bexruz",  "1047268578"};
Staff nightMaster = {"Axbar",   "1047268578"};
Staff engineer    = {"Jumanazar", "5026106651"};
Staff manager     = {"Jasur",   "1047268578"};

#define MAX_ADMINS 5
#define MAX_WATCHERS 10
Staff admins[MAX_ADMINS];
int adminCount = 0;
Staff watchers[MAX_WATCHERS];
int watcherCount = 0;

// --- 4-band: soddalashtirilgan "qo'shilish so'rovi" tizimi uchun ---
#define MAX_PENDING 8
struct PendingReq { String id; String name; bool used; };
PendingReq pending[MAX_PENDING];
int pendingCount = 0;

// --- 6-band: chat orqali limit tahrirlash/kalibrlash/Wi-Fi holati — HAR BIR ADMIN
// uchun ALOHIDA sessiya (xavfsizlik: bir admin ikkinchisining kiritayotgan
// qiymatini "o'g'irlab" yubormasligi uchun global bitta o'zgaruvchi ishlatilmaydi)
#define MAX_EDIT_SESSIONS 6
String editSessionChat[MAX_EDIT_SESSIONS];
String editSessionField[MAX_EDIT_SESSIONS];

String getEditField(String chatId) {
  for (int i = 0; i < MAX_EDIT_SESSIONS; i++) if (editSessionChat[i] == chatId) return editSessionField[i];
  return "";
}
void setEditSession(String chatId, String field) {
  for (int i = 0; i < MAX_EDIT_SESSIONS; i++) {
    if (editSessionChat[i] == chatId) { editSessionField[i] = field; return; }
  }
  for (int i = 0; i < MAX_EDIT_SESSIONS; i++) {
    if (editSessionChat[i] == "") { editSessionChat[i] = chatId; editSessionField[i] = field; return; }
  }
  editSessionChat[0] = chatId; editSessionField[0] = field; // joy qolmasa, eskisini almashtiramiz
}
void clearEditSession(String chatId) {
  for (int i = 0; i < MAX_EDIT_SESSIONS; i++) if (editSessionChat[i] == chatId) { editSessionChat[i] = ""; editSessionField[i] = ""; }
}

float airPressure = 0.0, waterPressure = 0.0, waterTemp = 0.0;
bool airSensorOK = true, waterSensorOK = true, tempSensorOK = true;

// YANGI (diagnostika): har bir o'qishda hisoblangan HAQIQIY tok (mA) —
// avval bu qiymat "NOSOZ" holatida yo'qolib ketardi (adcToBar 0.0 qaytarardi).
// Endi chegaradan tashqarida bo'lsa ham HAQIQIY hisoblangan qiymat saqlanadi,
// shunda "🔍 Xom qiymatlar" bo'limida chegaradan qanchaga chiqib ketganini
// aniq ko'rish mumkin (masalan 21.8 mA — bu haqiqatan chegara yaqinida,
// shovqin/tolerantlik sababli bo'lishi mumkin).
float airCurrentMACached = 0.0, waterCurrentMACached = 0.0;

// YANGI (displey barqarorligi uchun): "NOSOZ" yozuvi endi FAQAT ketma-ket
// SENSOR_DISPLAY_CONFIRM marta yomon o'qilgandan keyin ko'rsatiladi — bitta
// tasodifiy shovqinli o'qish (masalan kompressor motori yoqilgan zahoti)
// displeyda yalt etib "NOSOZ" chiqarib yubormaydi. MUHIM: bu FAQAT
// ko'rsatish (LCD/Telegram matni) uchun — avariya/eskalatsiya mantig'i
// hamon asl airSensorOK/waterSensorOK/tempSensorOK'dan foydalanadi va bu
// o'zgarishdan ta'sirlanmaydi (xavfsizlik reaksiyasi sekinlashmaydi).
int airFaultStreak = 0, waterFaultStreak = 0, tempFaultStreak = 0;
const int SENSOR_DISPLAY_CONFIRM = 2;
bool airDisplayOK = true, waterDisplayOK = true, tempDisplayOK = true;

// --- 13-band: arzimas xato/shovqinni filtrlash uchun ketma-ket buzilish hisoblagichi ---
int airBadStreak = 0, waterBadStreak = 0, tempBadStreak = 0;
const int BAD_STREAK_LIMIT = 3; // 3 marta ketma-ket chegaradan tashqari bo'lsagina avariya

// --- 2-h hisobot uchun min/max statistikasi ---
float statAirMin = 999, statAirMax = -999;
float statWaterMin = 999, statWaterMax = -999;
float statTempMin = 999, statTempMax = -999;

bool alarmActive = false;
int escalationStage = 0;
unsigned long stageStartTime = 0;

// YANGI: har bir bosqich uchun ALOHIDA kutish vaqti — endi bitta umumiy
// ESCALATION_TIMEOUT o'rniga, tashkilotingizga mos ravishda HAR BIR
// bosqichni ALOHIDA sozlashingiz mumkin (millisekundlarda; 60000 = 1 daqiqa,
// 120000 = 2 daqiqa va h.k.). Masalan, mexanikka ko'proq vaqt, keyingi
// bosqichlarga kamroq berish mumkin.
const unsigned long STAGE1_WAIT = 60000;         // 1→2: Mexanikdan javob kutish
const unsigned long STAGE2_WAIT = 60000;         // 2→3: Smena boshlig'idan javob kutish
const unsigned long STAGE3_WAIT = 60000;         // 3→4: Inzhener/Menejerdan javob kutish
const unsigned long STAGE4_REPEAT_WAIT = 120000; // 4-bosqichdan keyin: Super Admin/adminlarga necha daqiqada 1 marta eslatma

// YANGI: avariya boshlanganda Super Adminga DARHOL (mexanik bilan bir
// vaqtda) qisqa xabardorlik xabari yuborilsinmi? true — ha (joriy xatti-
// harakat, Super Admin voqeadan xabardor bo'lib turadi, lekin bu ALARM
// emas, oddiy ma'lumot xabari). false qilsangiz, Super Admin faqat
// eskalatsiya davomida (3/4-bosqichda) xabar oladi — TO'LIQ ketma-ket
// zanjir bo'ladi, hech kim boshqasi bilan bir vaqtda xabar olmaydi.
const bool NOTIFY_SUPERADMIN_IMMEDIATELY = true;

// YANGI: "✅ Men hal qildim" tugmasi (yoki jismoniy reset tugmasi)
// bosilgandan keyin, hatto sensor HALI HAM yomon o'qishda davom etsa ham,
// shuncha vaqt ichida Telegram AVARIYA ZANJIRI qayta BOSHLANMAYDI — bu
// xodimga "muammoni tekshirib ko'rish" uchun tinch vaqt beradi va aynan
// shu YO'QLIGI SABABLI oldin "tugma bossam ham alarm o'chmayapti" holati
// yuz bergan edi (chunki eski kodda faqat 30s'lik umumiy MIN_ALERT_INTERVAL
// bor edi va u ORIGINAL avariya vaqtidan hisoblanardi, reset vaqtidan
// emas — reset bir necha daqiqadan keyin bosilsa, bu 30s allaqachon
// o'tib bo'lgan bo'lardi va tizim SONIYALAR ICHIDA qayta signal berardi).
// MUHIM: bu FAQAT Telegramga tegishli — jismoniy siren bunga UMUMAN
// bog'liq emas, u har doim haqiqiy sensor holatiga qarab ishlaydi (shu
// bilan xavfsizlik saqlanadi: birov noto'g'ri/vaqtidan oldin "hal qildim"
// desa ham, real muammo davom etsa, joyidagi odamlar sirendan buni sezadi).
const unsigned long POST_RESOLVE_GRACE = 120000; // 2 daqiqa
unsigned long resolveGraceUntil = 0;

// --- 5-band: xabarlar 30 sekundda 1 martadan ko'p bormasin (dastlabki himoya) ---
unsigned long lastAlertSentTime = 0;
const unsigned long MIN_ALERT_INTERVAL = 30000;

// YANGI (3-band): har bir siren HOZIRGI holati — faqat holat o'zgarganda
// digitalWrite chaqiramiz (keraksiz yozishlarni oldini olish uchun)
bool sirenAirOn = false, sirenWaterOn = false, sirenTempOn = false;

unsigned long lastBotCheck = 0, lastSensorRead = 0, last2HourReport = 0;
unsigned long lastWifiCheck = 0, lastLcdPage = 0, lastHourLog = 0;
unsigned long lastWifiBeginAttempt = 0; // oldingi WiFi.begin() urinishi hali tugamasdan yangisini chaqirmaslik uchun ("cannot set config" xatosining oldini olish)
const unsigned long WIFI_RECONNECT_MIN_GAP = 15000; // kamida 15s kutilmasa, qayta ulanishga urinilmaydi
const unsigned long WIFI_BOOT_FALLBACK_DELAY = 30000; // saqlangan Wi-Fi ulanmasa, zaxira Wi-Fi'ni sinash vaqti
bool wifiBootFallbackTried = false;
unsigned long wifiBootStartedAt = 0;
const unsigned long TWO_HOURS_MS = 7200000;
int lcdPage = 0; // 3-band: ekranda ketma-ket sahifalar

// --- 8-band: kuzatuvchilar /status'ni faqat 2 soatda 1 marta so'rashi mumkin ---
unsigned long watcherLastRequest[MAX_WATCHERS] = {0};

// --- 12-band: oylik hisobot faqat 1 marta yuborilsin (bir kunda takrorlanmasin) ---
int lastExportMonth = -1;

// ============================================================================
// 3) ROL VA RUXSAT FUNKSIYALARI
// ============================================================================
bool isDayShift() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) return true;
  return (timeinfo.tm_hour >= 8 && timeinfo.tm_hour < 20);
}

bool isSuperAdmin(String chatId) { return chatId == superAdmin.id; }

bool isAdmin(String chatId) {
  if (isSuperAdmin(chatId)) return true;
  for (int i = 0; i < adminCount; i++) if (admins[i].id == chatId) return true;
  return false;
}

int watcherIndex(String chatId) {
  for (int i = 0; i < watcherCount; i++) if (watchers[i].id == chatId) return i;
  return -1;
}
bool isWatcher(String chatId) { return watcherIndex(chatId) >= 0; }

bool isShiftStaff(String chatId) {
  return chatId == dayMech.id || chatId == dayMaster.id || chatId == nightMech.id ||
         chatId == nightMaster.id || chatId == engineer.id || chatId == manager.id;
}

bool isKnownUser(String chatId) {
  return isAdmin(chatId) || isWatcher(chatId) || isShiftStaff(chatId);
}

String keyboardFor(String chatId) {
  return isAdmin(chatId) ? adminKeyboard : watcherKeyboard;
}

// ============================================================================
// 4) SOZLAMALARNI SAQLASH / YUKLASH
// ============================================================================

// YANGI: joriy (aktiv) havo limitlarini joriy rejimning "xotira katakchasi"ga
// yozib qo'yadi — har safar saqlashdan OLDIN chaqiriladi, shunda 1-Rejimda
// turib o'zgartirgan qiymat 2-Rejimning qiymatini "bosib" ketmaydi.
void syncActiveAirToModeStorage() {
  if (currentMode == 1) { m1AirMin = minAirP; m1AirNorm = normAirP; m1AirMax = maxAirP; }
  else                  { m2AirMin = minAirP; m2AirNorm = normAirP; m2AirMax = maxAirP; }
}

// YANGI: rejim almashganda (yoki ishga tushganda) shu rejimga tegishli
// havo limitlarini "aktiv" o'zgaruvchilarga yuklaydi.
void loadAirForMode(int mode) {
  if (mode == 1) { minAirP = m1AirMin; normAirP = m1AirNorm; maxAirP = m1AirMax; }
  else           { minAirP = m2AirMin; normAirP = m2AirNorm; maxAirP = m2AirMax; }
}

void saveSettings() {
  syncActiveAirToModeStorage();
  prefs.putFloat("m1AirMin", m1AirMin); prefs.putFloat("m1AirNorm", m1AirNorm); prefs.putFloat("m1AirMax", m1AirMax);
  prefs.putFloat("m2AirMin", m2AirMin); prefs.putFloat("m2AirNorm", m2AirNorm); prefs.putFloat("m2AirMax", m2AirMax);
  prefs.putFloat("minWP", minWaterP); prefs.putFloat("normWP", normWaterP); prefs.putFloat("maxWP", maxWaterP);
  prefs.putFloat("minWT", minWaterTemp); prefs.putFloat("normWT", normWaterTemp); prefs.putFloat("maxWT", maxWaterTemp);
  prefs.putInt("mode", currentMode);
  prefs.putFloat("airOff", airZeroOffset); prefs.putFloat("watOff", waterZeroOffset);
  prefs.putString("wifiSSID", currentSSID); prefs.putString("wifiPass", currentPassword);
  prefs.putString("dMName", dayMech.name);       prefs.putString("dMID", dayMech.id);
  prefs.putString("dMasName", dayMaster.name);   prefs.putString("dMasID", dayMaster.id);
  prefs.putString("nMName", nightMech.name);     prefs.putString("nMID", nightMech.id);
  prefs.putString("nMasName", nightMaster.name); prefs.putString("nMasID", nightMaster.id);
  prefs.putString("engName", engineer.name);     prefs.putString("engID", engineer.id);
  prefs.putString("manName", manager.name);      prefs.putString("manID", manager.id);
}

void saveAdmins() {
  prefs.putInt("admCnt", adminCount);
  for (int i = 0; i < MAX_ADMINS; i++) {
    prefs.putString(("admN" + String(i)).c_str(), i < adminCount ? admins[i].name : "");
    prefs.putString(("admI" + String(i)).c_str(), i < adminCount ? admins[i].id : "");
  }
}

// YANGI: adminlar ro'yxatidan bittasini o'chirish (faqat Super Admin chaqiradi).
// Massivdagi bo'shliqni yopish uchun keyingi elementlar bir pog'ona
// suriladi, so'ng flashga (Preferences) darhol yozib qo'yiladi.
bool removeAdminById(String id) {
  for (int i = 0; i < adminCount; i++) {
    if (admins[i].id == id) {
      for (int j = i; j < adminCount - 1; j++) admins[j] = admins[j + 1];
      adminCount--;
      saveAdmins();
      return true;
    }
  }
  return false;
}

void saveWatchers() {
  prefs.putInt("wchCnt", watcherCount);
  for (int i = 0; i < MAX_WATCHERS; i++) {
    prefs.putString(("wchN" + String(i)).c_str(), i < watcherCount ? watchers[i].name : "");
    prefs.putString(("wchI" + String(i)).c_str(), i < watcherCount ? watchers[i].id : "");
  }
}

// YANGI: kuzatuvchilar ro'yxatidan bittasini o'chirish (faqat Super Admin
// chaqiradi) — removeAdminById() bilan bir xil mantiq, faqat watchers[]
// massivi uchun.
bool removeWatcherById(String id) {
  for (int i = 0; i < watcherCount; i++) {
    if (watchers[i].id == id) {
      for (int j = i; j < watcherCount - 1; j++) watchers[j] = watchers[j + 1];
      watcherCount--;
      saveWatchers();
      return true;
    }
  }
  return false;
}

void loadSettings() {
  // YANGI: agar bu firmware birinchi marta shu qurilmaga yuklangan bo'lsa
  // (yoki CONFIG_VERSION oshirilgan bo'lsa), quyidagi standart qiymatlar
  // MAJBURIY qo'llaniladi — hatto avval boshqacha saqlangan bo'lsa ham.
  int cfgVer = prefs.getInt("cfgVer", 0);
  bool freshDefaults = (cfgVer != CONFIG_VERSION);

  m1AirMin  = freshDefaults ? 10.0 : prefs.getFloat("m1AirMin", 10.0);
  m1AirNorm = freshDefaults ? 11.0 : prefs.getFloat("m1AirNorm", 11.0);
  m1AirMax  = freshDefaults ? 12.0 : prefs.getFloat("m1AirMax", 12.0);
  m2AirMin  = freshDefaults ? 7.0  : prefs.getFloat("m2AirMin", 7.0);
  m2AirNorm = freshDefaults ? 9.0  : prefs.getFloat("m2AirNorm", 9.0);
  m2AirMax  = freshDefaults ? 10.5 : prefs.getFloat("m2AirMax", 10.5);

  minWaterP = freshDefaults ? 3.5 : prefs.getFloat("minWP", 3.5);
  normWaterP = freshDefaults ? 4.2 : prefs.getFloat("normWP", 4.2);
  maxWaterP = freshDefaults ? 6.0 : prefs.getFloat("maxWP", 6.0);

  minWaterTemp = freshDefaults ? 5.0 : prefs.getFloat("minWT", 5.0);
  normWaterTemp = freshDefaults ? 10.0 : prefs.getFloat("normWT", 10.0);
  maxWaterTemp = freshDefaults ? 13.5 : prefs.getFloat("maxWT", 13.5);

  currentMode = prefs.getInt("mode", 1);
  loadAirForMode(currentMode);

  airZeroOffset = prefs.getFloat("airOff", 0.0);
  waterZeroOffset = freshDefaults ? 1.0 : prefs.getFloat("watOff", 1.0);

  // CONFIG_VERSION yangilanganda eski saqlangan Wi-Fi emas, yuqoridagi yangi
  // WIFI_SSID/WIFI_PASSWORD standartlari majburiy qo'llanadi. Keyingi safar
  // Telegram orqali o'zgartirilgan Wi-Fi odatdagidek Preferences’da saqlanadi.
  if (freshDefaults) {
    currentSSID = WIFI_SSID;
    currentPassword = WIFI_PASSWORD;
  } else {
    currentSSID = prefs.getString("wifiSSID", WIFI_SSID);
    currentPassword = prefs.getString("wifiPass", WIFI_PASSWORD);
  }
  dayMech.name     = prefs.getString("dMName", "Javohir");   dayMech.id     = prefs.getString("dMID", "5569218743");
  dayMaster.name   = prefs.getString("dMasName", "Vosit");   dayMaster.id   = prefs.getString("dMasID", "602455217");
  nightMech.name   = prefs.getString("nMName", "Bexruz");    nightMech.id   = prefs.getString("nMID", "1047268578");
  nightMaster.name = prefs.getString("nMasName", "Axbar");   nightMaster.id = prefs.getString("nMasID", "1047268578");
  engineer.name    = prefs.getString("engName", "Jumanazar"); engineer.id   = prefs.getString("engID", "5026106651");
  manager.name     = prefs.getString("manName", "Jasur");    manager.id     = prefs.getString("manID", "1047268578");

  adminCount = prefs.getInt("admCnt", 0);
  for (int i = 0; i < adminCount && i < MAX_ADMINS; i++) {
    admins[i].name = prefs.getString(("admN" + String(i)).c_str(), "");
    admins[i].id   = prefs.getString(("admI" + String(i)).c_str(), "");
  }
  watcherCount = prefs.getInt("wchCnt", 0);
  for (int i = 0; i < watcherCount && i < MAX_WATCHERS; i++) {
    watchers[i].name = prefs.getString(("wchN" + String(i)).c_str(), "");
    watchers[i].id   = prefs.getString(("wchI" + String(i)).c_str(), "");
  }

  if (freshDefaults) {
    prefs.putInt("cfgVer", CONFIG_VERSION);
    saveSettings(); // yangi standart qiymatlarni darhol flashga yozib qo'yamiz
  }
}

// ============================================================================
// 5) SENSOR O'QISH (4-20mA -> bar) — 13-band: filtrlangan, ishonchli
// ============================================================================
// YANGI: namunalar soni 12'dan 30'ga oshirildi (jami ~6ms, 1.5s tsiklga
// nisbatan arzimas) — bu elektr shovqin (masalan kompressor motori
// yonganda tarmoqdagi tebranish)ni yaxshiroq o'rtachalashtirib, tasodifiy
// "sakrash"larni kamaytiradi.
int averagedADC(int pin, int samples = 30) {
  long sum = 0;
  for (int i = 0; i < samples; i++) { sum += analogRead(pin); delayMicroseconds(200); }
  return sum / samples;
}

// YANGI: endi 5-parametr (outCurrentMA) orqali hisoblangan HAQIQIY tok (mA)
// tashqariga chiqariladi — diagnostika uchun ("🔍 Xom qiymatlar" bo'limida
// ko'rinadi). Chegaradan tashqarida bo'lsa ham (sensorOK=false bo'lganda
// ham) bar qiymati HAQIQIY hisoblab qaytariladi (avval bunday holatda
// shunchaki 0.0 qaytarilardi va chegaradan qanchaga chiqib ketgani
// ko'rinmas edi).
float adcToBar(int rawADC, float fsMinBar, float fsMaxBar, bool &sensorOK, float &outCurrentMA) {
  float adcVoltage = (rawADC / 4095.0) * 3.3;
  float burdenVoltage = adcVoltage / DIVIDER_RATIO;
  float currentMA = (burdenVoltage / BURDEN_RESISTOR) * 1000.0;
  outCurrentMA = currentMA;

  float bar = fsMinBar + (currentMA - 4.0) * (fsMaxBar - fsMinBar) / (20.0 - 4.0);

  // YANGI: chegaralarga (3.5/21.0) kichik "yostiqcha" (margin) qo'shildi —
  // 3.2/21.5 — bu rezistor tolerantligi yoki ADC'ning arzimas shovqinidan
  // kelib chiqadigan YOLG'ON "NOSOZ"larni kamaytiradi, haqiqiy uzilish
  // (0 mA) yoki qisqa tutashuv (>>21mA) hamon aniq ushlanadi.
  if (currentMA < 3.2 || currentMA > 21.5) {
    sensorOK = false;
    return bar; // diagnostika uchun haqiqiy (tashqaridagi) qiymat qaytariladi
  }
  sensorOK = true;
  if (bar < 0) bar = 0;
  return bar;
}

// 5-band: DS18B20 aylanish (conversion) vaqti ~750ms bo'ladi. Buni bloklovchi
// rejimda chaqirish har safar loop'ni sekinlashtirib, Telegram javoblarini
// kechiktiradi. Shu sabab: avval OLDINGI so'rovning natijasini o'qiymiz,
// keyin YANGI o'lchovni fonda (asinxron) boshlab qo'yamiz.
bool tempConversionPending = false;
bool tempSensorPresent = true; // setup()da DS18B20 haqiqatan topilgan-topilmaganini tekshiramiz

// YANGI: agar DS18B20 "topilmadi" deb belgilansa, uni HAR DOIM emas,
// balki har TEMP_SENSOR_RECHECK_INTERVAL da bir marta qayta skanerlaymiz —
// shunda vaqtinchalik uzilish (quvvat sakrashi, shovqin) tuzatilgandan
// keyin qurilmani qayta yuklamasdan ham sensor o'zi "topiladi".
unsigned long lastTempSensorRecheck = 0;
const unsigned long TEMP_SENSOR_RECHECK_INTERVAL = 30000; // 30 soniya

// YANGI: bitta o'qish yomon bo'lsa ham DISPLEY darhol "NOSOZ"ga o'tmaydi —
// SENSOR_DISPLAY_CONFIRM marta ketma-ket yomon o'qilgandan keyingina
// o'tadi; bitta yaxshi o'qish kelsa esa darhol "OK"ga qaytariladi (haqiqiy
// muammo davom etayotgan bo'lsa, keyingi yomon o'qish яна hisoblanadi).
void updateFaultStreak(bool sensorOK, int &streak, bool &displayOK) {
  if (sensorOK) {
    streak = 0;
    displayOK = true;
  } else {
    streak++;
    if (streak >= SENSOR_DISPLAY_CONFIRM) displayOK = false;
  }
}

void readSensors() {
  int rawAir = averagedADC(AIR_SENSOR_PIN);
  float rawAirBar = adcToBar(rawAir, AIR_SENSOR_MIN_BAR, AIR_SENSOR_MAX_BAR, airSensorOK, airCurrentMACached);
  rawAirBarCached = rawAirBar;
  updateFaultStreak(airSensorOK, airFaultStreak, airDisplayOK);
  if (airSensorOK) {
    airPressure = max(0.0f, rawAirBar - airZeroOffset); // yaxshi o'qish — darhol yangilanadi
  } else if (!airDisplayOK) {
    airPressure = 0.0; // TASDIQLANGAN nosozlik — 0 ko'rsatiladi
  }
  // aks holda (hali tasdiqlanmagan, bitta shovqinli o'qish): eski airPressure
  // qiymati saqlanib qoladi — displey shovqindan yalt etib o'zgarmaydi

  if (currentMode == 1) {
    int rawWater = averagedADC(WATER_SENSOR_PIN);
    float rawWaterBar = adcToBar(rawWater, WATER_SENSOR_MIN_BAR, WATER_SENSOR_MAX_BAR, waterSensorOK, waterCurrentMACached);
    rawWaterBarCached = rawWaterBar;
    updateFaultStreak(waterSensorOK, waterFaultStreak, waterDisplayOK);
    if (waterSensorOK) {
      waterPressure = max(0.0f, rawWaterBar - waterZeroOffset);
    } else if (!waterDisplayOK) {
      waterPressure = 0.0;
    }

    if (!tempSensorPresent) {
      // YANGI: sensor "topilmagan" bo'lsa, har TEMP_SENSOR_RECHECK_INTERVAL
      // sekundda busni qayta skanerlaymiz. Sim/rezistor tuzatilgan bo'lsa,
      // sensor shu yerda avtomatik "qaytadan topiladi" — qurilmani qayta
      // yuklash shart bo'lmaydi.
      if (millis() - lastTempSensorRecheck >= TEMP_SENSOR_RECHECK_INTERVAL) {
        lastTempSensorRecheck = millis();
        tempSensors.begin();
        tempSensorPresent = (tempSensors.getDeviceCount() > 0);
        if (tempSensorPresent) {
          tempSensors.setWaitForConversion(false);
          tempSensors.requestTemperatures();
          tempConversionPending = true;
          Serial.println("✅ DS18B20 qayta topildi (avtomatik qayta skanerlash orqali).");
        }
      }
      // Bus'da DS18B20 umuman topilmagan — "0.0 °C" deb noto'g'ri ko'rsatish
      // o'rniga ANIQ "NOSOZ" deb belgilaymiz
      tempSensorOK = false; waterTemp = 0.0;
    } else if (tempConversionPending) {
      float t = tempSensors.getTempCByIndex(0);
      if (t == -127.00 || t == 85.00 || t < 0.0 || t > 50.0) {
        tempSensorOK = false; waterTemp = 0.0;
      } else {
        tempSensorOK = true; waterTemp = t;
      }
    }
    tempSensors.requestTemperatures(); // asinxron: darhol qaytadi, bloklamaydi
    tempConversionPending = true;
    updateFaultStreak(tempSensorOK, tempFaultStreak, tempDisplayOK);
  } else {
    waterSensorOK = true; tempSensorOK = true;
    waterFaultStreak = 0; waterDisplayOK = true;
    tempFaultStreak = 0; tempDisplayOK = true;
  }

  // Statistika (2 soatlik hisobot uchun)
  if (airSensorOK) { statAirMin = min(statAirMin, airPressure); statAirMax = max(statAirMax, airPressure); }
  if (currentMode == 1 && waterSensorOK) { statWaterMin = min(statWaterMin, waterPressure); statWaterMax = max(statWaterMax, waterPressure); }
  if (currentMode == 1 && tempSensorOK) { statTempMin = min(statTempMin, waterTemp); statTempMax = max(statTempMax, waterTemp); }
}

void resetStats() {
  statAirMin = 999; statAirMax = -999;
  statWaterMin = 999; statWaterMax = -999;
  statTempMin = 999; statTempMax = -999;
}

void applyRelay() {
  digitalWrite(RELAY_PIN, systemActive ? HIGH : LOW);
}

// YANGI (3-band): bitta jismoniy siren chiqishini yoqish/o'chirish — faqat
// holat haqiqatan o'zgarganda digitalWrite chaqiradi.
void applySirenPin(int pin, bool &stateVar, bool on) {
  if (stateVar == on) return; // holat o'zgarmagan — hech narsa qilmaymiz
  stateVar = on;
  digitalWrite(pin, on ? HIGH : LOW);
}

// YANGI (3-band): 3 ta sirenani BIR VAQTDA, HAR BIRINI O'Z SABABIGA QARAB
// mustaqil boshqaradi. Bu funksiya Telegramdan/Wi-Fi'dan MUSTAQIL ishlaydi —
// faqat mahalliy digitalWrite() bo'lgani uchun tarmoq ishlamay qolsa ham
// sirenalar baribir haqiqiy muammo paytida yonadi va muammo tuzatilgach
// (sensor qiymati me'yorga qaytgach) o'zi o'chadi.
void updateSirens(bool airBad, bool waterBad, bool tempBad) {
  applySirenPin(SIREN_AIR_PIN, sirenAirOn, airBad);
  applySirenPin(SIREN_WATER_PIN, sirenWaterOn, waterBad);
  applySirenPin(SIREN_TEMP_PIN, sirenTempOn, tempBad);
}

void allSirensOff() {
  updateSirens(false, false, false);
}

// YANGI: Wi-Fi'ga xavfsiz qayta ulanish. Oddiy WiFi.disconnect()+WiFi.begin()
// ba'zan ESP32 drayverini "yarim holatda" qoldirib, "wifi:sta is connecting,
// cannot set config" xatosi bilan ULANISH BUTUNLAY TO'XTAB QOLISHIGA olib
// kelishi mumkin edi (bu — botning "qotib qolishi"ning haqiqiy sabablaridan
// biri edi). To'liq disconnect(true) + qisqa kutish + begin() shu holatni
// tozalab, drayverni har safar toza holatdan qayta boshlaydi.
void wifiReconnect() {
  // Oldingi ulanishni tozalaymiz, lekin saqlangan Wi-Fi konfiguratsiyasini
  // flashdan o'chirmaymiz.
  WiFi.disconnect(false, false);
  delay(150);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  WiFi.begin(currentSSID.c_str(), currentPassword.c_str());
  lastWifiBeginAttempt = millis();
}

void startWifiSwitch(String newSSID, String newPassword, String requesterChatId) {
  // Eski ishlayotgan tarmoqni xotirada saqlaymiz. Yangi tarmoq tasdiqlanmaguncha
  // Preferences'ga yangi ma'lumot yozilmaydi.
  previousSSID = currentSSID;
  previousPassword = currentPassword;
  wifiSwitchRequester = requesterChatId;

  currentSSID = newSSID;
  currentPassword = newPassword;
  wifiSwitchPending = true;
  wifiSwitchStartedAt = millis();

  wifiReconnect();
}

void serviceWifiConnection() {
  // Yangi Wi-Fi muvaffaqiyatli ulangan bo'lsa, shundan keyingina saqlaymiz.
  if (wifiSwitchPending) {
    if (WiFi.status() == WL_CONNECTED) {
      prefs.putString("wifiSSID", currentSSID);
      prefs.putString("wifiPass", currentPassword);

      String requester = wifiSwitchRequester;
      wifiSwitchPending = false;
      previousSSID = "";
      previousPassword = "";
      wifiSwitchRequester = "";

      sendTG(requester, "✅ <b>Yangi Wi-Fi muvaffaqiyatli ulandi!</b>\n🌐 Tarmoq: <code>" + currentSSID + "</code>", adminKeyboard);
      return;
    }

    // Yangi Wi-Fi ulanmasa, eski ishlayotgan tarmoqqa avtomatik qaytamiz.
    if (millis() - wifiSwitchStartedAt >= WIFI_SWITCH_TIMEOUT) {
      String failedSSID = currentSSID;
      currentSSID = previousSSID;
      currentPassword = previousPassword;
      wifiSwitchPending = false;
      wifiRollbackNoticePending = true;
      wifiReconnect();

      Serial.println("⚠️ Yangi Wi-Fi ulanmay qoldi, eski Wi-Fi'ga qaytilmoqda: " + currentSSID);
      Serial.println("⚠️ Ulanmagan yangi Wi-Fi: " + failedSSID);
      return;
    }
  }

  // Eski Wi-Fi'ga qaytgandan keyin foydalanuvchiga xabar beramiz.
  if (wifiRollbackNoticePending && WiFi.status() == WL_CONNECTED) {
    wifiRollbackNoticePending = false;
    sendTG(wifiSwitchRequester.length() ? wifiSwitchRequester : superAdmin.id,
           "⚠️ <b>Yangi Wi-Fi'ga ulanib bo'lmadi.</b>\n🔙 Eski tarmoq qayta tiklandi: <code>" + currentSSID + "</code>\nYangi SSID/parolni tekshirib qayta urinib ko'ring.",
           adminKeyboard);
    wifiSwitchRequester = "";
  }

  // Qurilma ishga tushganda saqlangan Wi-Fi mavjud bo'lmasa, dastur qotib
  // qolmasligi uchun 30 soniyadan keyin standart (zaxira) Wi-Fi'ni bir marta
  // sinab ko'ramiz. Bu saqlangan Wi-Fi ma'lumotlarini o'chirmaydi.
  if (!wifiBootFallbackTried && !wifiSwitchPending &&
      WiFi.status() != WL_CONNECTED &&
      millis() - wifiBootStartedAt >= WIFI_BOOT_FALLBACK_DELAY &&
      currentSSID != String(WIFI_SSID)) {
    wifiBootFallbackTried = true;
    currentSSID = WIFI_SSID;
    currentPassword = WIFI_PASSWORD;
    wifiReconnect();
    Serial.println("📶 Zaxira Wi-Fi sinab ko'rilmoqda: " + currentSSID);
  }
}

// ============================================================================
// 6) LCD EKRAN — 3-band: har 30 soniyada sahifalar ketma-ket almashadi
// ============================================================================
void updateLCDDisplay() {
  lcd.clear();
  if (!systemActive) {
    lcd.setCursor(1, 1); lcd.print("TIZIM TO'XTATILDI");
    return;
  }

  Staff curMech = isDayShift() ? dayMech : nightMech;

  if (!monitoringStarted) {
    lcd.setCursor(0, 0); lcd.print("Tizim: YOQILGAN");
    lcd.setCursor(0, 1); lcd.print("Nazorat: KUTILMOQDA");
    lcd.setCursor(0, 2); lcd.print("Telegramda 1/2-Rejim");
    lcd.setCursor(0, 3); lcd.print("tugmasini bosing");
    return;
  }

  if (lcdPage == 0 || currentMode == 2) {
    lcd.setCursor(0, 0); lcd.print("Rejim:" + String(currentMode) + " " + curMech.name);
    lcd.setCursor(0, 1); lcd.print(!airDisplayOK ? "Havo: NOSOZ" : "Havo: " + String(airPressure, 1) + " bar");
    if (currentMode == 1) {
      lcd.setCursor(0, 2); lcd.print(!waterDisplayOK ? "Suv : NOSOZ" : "Suv : " + String(waterPressure, 1) + " bar");
      lcd.setCursor(0, 3); lcd.print(!tempDisplayOK ? "Temp: NOSOZ" : "Temp: " + String(waterTemp, 1) + " C");
    } else {
      lcd.setCursor(0, 2); lcd.print("Holat: ISHLAMOQDA");
      lcd.setCursor(0, 3); lcd.print(alarmActive ? "!! AVARIYA !!" : "Barchasi normal");
    }
  } else if (lcdPage == 1) {
    lcd.setCursor(0, 0); lcd.print("--- LIMITLAR ---");
    lcd.setCursor(0, 1); lcd.print("Havo:" + String(minAirP,1) + "-" + String(maxAirP,1));
    lcd.setCursor(0, 2); lcd.print("Suv :" + String(minWaterP,1) + "-" + String(maxWaterP,1));
    lcd.setCursor(0, 3); lcd.print("Temp:" + String(minWaterTemp,1) + "-" + String(maxWaterTemp,1));
  } else {
    struct tm ti; getLocalTime(&ti);
    char buf[20]; strftime(buf, sizeof(buf), "%H:%M %d.%m", &ti);
    lcd.setCursor(0, 0); lcd.print("Vaqt: " + String(buf));
    lcd.setCursor(0, 1); lcd.print(isDayShift() ? "Smena: KUNDUZGI" : "Smena: TUNGI");
    lcd.setCursor(0, 2); lcd.print("Mex: " + curMech.name);
    lcd.setCursor(0, 3); lcd.print(alarmActive ? "!! AVARIYA !!" : "Holat: NORMAL");
  }
}

void cycleLcdPage() {
  lcdPage = (lcdPage + 1) % 3;
  updateLCDDisplay();
}

// ============================================================================
// 7) TELEGRAMGA ISHONCHLI YUBORISH — NAVBAT (QUEUE) ASOSIDA, BLOKLAMAYDIGAN
//
// sendTG()/sendInline() endi Telegramga TO'G'RIDAN-TO'G'RI yozmaydi — ular
// faqat xabarni navbatga qo'yadi va darhol qaytadi. Haqiqiy yuborish
// loop()dagi processMessageQueue() orqali amalga oshadi: har bir loop
// aylanishida navbatdan FAQAT BITTA xabar, FAQAT BITTA marta yuborishga
// urinadi. Shu bilan qancha odamga xabar ketishi kerak bo'lmasin (masalan
// avariya eskalatsiyasi yoki "hal qilindi" xabarlari BARCHA tegishli
// xodimlarga), loop() HECH QACHON ketma-ket bloklovchi so'rovlar sababli
// "qotib" qolmaydi — na muammo YUZAGA KELGANDA, na u HAL QILINGANDA.
// ============================================================================
#define MSG_QUEUE_SIZE 32
struct QueuedMsg {
  String chatId;
  String text;
  String keyboard;
  bool isInline;
  int attempts;
};
QueuedMsg msgQueue[MSG_QUEUE_SIZE];
int msgQueueHead = 0;
int msgQueueCount = 0;
unsigned long lastQueueSend = 0;
const unsigned long QUEUE_SEND_MIN_GAP = 200;
const int MSG_MAX_ATTEMPTS = 3;
bool queueOverflowWarned = false;

// YANGI: bitta ID doim ishlamay qolsa (masalan, botni bloklagan yoki
// eskirgan/noto'g'ri ID), Super Adminga HAR SAFAR emas, balki eng ko'pi
// bilan FAILURE_NOTICE_COOLDOWN muddatida BIR MARTA ogohlantirish
// yuboriladi — shu bilan bir xil ID haqida qayta-qayta bir xil xabar
// bilan "spam" qilinmaydi.
#define MAX_FAILURE_NOTICES 10
String failureNoticeChatId[MAX_FAILURE_NOTICES];
unsigned long failureNoticeTime[MAX_FAILURE_NOTICES];
const unsigned long FAILURE_NOTICE_COOLDOWN = 3600000UL; // 1 soat

bool shouldNotifyFailure(String chatId) {
  unsigned long now = millis();
  int freeIdx = -1;
  int oldestIdx = 0;
  unsigned long oldestTime = 0xFFFFFFFFUL;
  for (int i = 0; i < MAX_FAILURE_NOTICES; i++) {
    if (failureNoticeChatId[i] == chatId) {
      if (now - failureNoticeTime[i] < FAILURE_NOTICE_COOLDOWN) return false;
      failureNoticeTime[i] = now;
      return true;
    }
    if (failureNoticeChatId[i] == "" && freeIdx < 0) freeIdx = i;
    if (failureNoticeTime[i] < oldestTime) { oldestTime = failureNoticeTime[i]; oldestIdx = i; }
  }
  int idx = (freeIdx >= 0) ? freeIdx : oldestIdx;
  failureNoticeChatId[idx] = chatId;
  failureNoticeTime[idx] = now;
  return true;
}

void queueMessage(String chatId, String text, String keyboard, bool isInline) {
  if (chatId.length() < 3) return;
  if (msgQueueCount >= MSG_QUEUE_SIZE) {
    msgQueueHead = (msgQueueHead + 1) % MSG_QUEUE_SIZE;
    msgQueueCount--;
    if (!queueOverflowWarned) {
      Serial.println("⚠️ Xabarlar navbati to'lib ketdi — eng eski xabar(lar) tashlab yuborilmoqda.");
      queueOverflowWarned = true;
    }
  }
  int idx = (msgQueueHead + msgQueueCount) % MSG_QUEUE_SIZE;
  msgQueue[idx].chatId = chatId;
  msgQueue[idx].text = text;
  msgQueue[idx].keyboard = keyboard;
  msgQueue[idx].isInline = isInline;
  msgQueue[idx].attempts = 0;
  msgQueueCount++;
}

void processMessageQueue() {
  if (msgQueueCount == 0) return;
  if (WiFi.status() != WL_CONNECTED) return;
  unsigned long now = millis();
  if (now - lastQueueSend < QUEUE_SEND_MIN_GAP) return;
  lastQueueSend = now;

  QueuedMsg &m = msgQueue[msgQueueHead];
  bool ok = m.isInline
    ? bot.sendMessageWithInlineKeyboard(m.chatId, m.text, "HTML", m.keyboard)
    : bot.sendMessageWithReplyKeyboard(m.chatId, m.text, "HTML", m.keyboard, true);

  if (ok) {
    msgQueueHead = (msgQueueHead + 1) % MSG_QUEUE_SIZE;
    msgQueueCount--;
    queueOverflowWarned = false;
    return;
  }

  m.attempts++;
  Serial.println("⚠️ Xabar yetkazilmadi (urinish " + String(m.attempts) + "/" + String(MSG_MAX_ATTEMPTS) + ") -> ID: " + m.chatId);
  if (m.attempts >= MSG_MAX_ATTEMPTS) {
    Serial.println("⚠️ Xabar bekor qilindi (3 marta urinildi, yetkazilmadi) -> ID: " + m.chatId);
    String failedId = m.chatId;
    msgQueueHead = (msgQueueHead + 1) % MSG_QUEUE_SIZE;
    msgQueueCount--;
    if (failedId != superAdmin.id && shouldNotifyFailure(failedId)) {
      queueMessage(superAdmin.id,
        "⚠️ <b>Diqqat:</b> ID <code>" + failedId + "</code> ga xabar yuborib bo'lmadi (3 marta urinildi).\n<i>Bu ID doim ishlamasa, uni 🔑 Adminlar bo'limidan (Admin/Kuzatuvchi o'chirish) ro'yxatdan chiqarib tashlashingiz mumkin.</i>",
        adminKeyboard, false);
    }
  }
}

bool sendTG(String chatId, String msg, String keyboard) {
  if (chatId.length() < 3) return false;
  queueMessage(chatId, msg, keyboard, false);
  return true;
}

bool sendInline(String chatId, String msg, String inlineKeyboardJson) {
  if (chatId.length() < 3) return false;
  queueMessage(chatId, msg, inlineKeyboardJson, true);
  return true;
}

// ============================================================================
// 8) HISOBOT VA AVARIYA XABARLARI
// ============================================================================
String shiftInfoBlock() {
  Staff curMech = isDayShift() ? dayMech : nightMech;
  Staff curMaster = isDayShift() ? dayMaster : nightMaster;
  String s = "👷 Mexanik: <b>" + curMech.name + "</b>\n";
  s += "🧑‍💼 Smena boshlig'i: <b>" + curMaster.name + "</b>\n";
  s += (isDayShift() ? "🌞 Smena: Kunduzgi (08:00-20:00)\n" : "🌙 Smena: Tungi (20:00-08:00)\n");
  return s;
}

String sensorDataBlock() {
  String s = "💨 Havo: <code>" + (airDisplayOK ? String(airPressure, 1) + " bar" : String("NOSOZ")) + "</code>\n";
  if (currentMode == 1) {
    s += "💧 Suv bosimi: <code>" + (waterDisplayOK ? String(waterPressure, 1) + " bar" : String("NOSOZ")) + "</code>\n";
    s += "🌡️ Suv harorati: <code>" + (tempDisplayOK ? String(waterTemp, 1) + " °C" : String("NOSOZ")) + "</code>\n";
  }
  return s;
}

String statBlock() {
  String s = "📈 <b>Ushbu davr statistikasi:</b>\n";
  if (statAirMax > -999) s += "💨 Havo: " + String(statAirMin,1) + " – " + String(statAirMax,1) + " bar\n";
  if (currentMode == 1 && statWaterMax > -999) s += "💧 Suv: " + String(statWaterMin,1) + " – " + String(statWaterMax,1) + " bar\n";
  if (currentMode == 1 && statTempMax > -999) s += "🌡️ Temp: " + String(statTempMin,1) + " – " + String(statTempMax,1) + " °C\n";
  return s;
}

String buildEmergencyReason() {
  String r = "";
  if (!airDisplayOK) r += "• 💨 Havo bosimi SENSORI nosoz (sim uzilgan yoki signal chegaradan tashqari)\n";
  else if (airPressure < minAirP) r += "• 💨 Havo bosimi PAST: " + String(airPressure, 1) + " bar (min: " + String(minAirP, 1) + " bar)\n";
  else if (airPressure > maxAirP) r += "• 💨 Havo bosimi YUQORI: " + String(airPressure, 1) + " bar (max: " + String(maxAirP, 1) + " bar)\n";

  if (currentMode == 1) {
    if (!waterDisplayOK) r += "• 💧 Suv bosimi SENSORI nosoz\n";
    else if (waterPressure < minWaterP) r += "• 💧 Suv bosimi PAST: " + String(waterPressure, 1) + " bar (min: " + String(minWaterP, 1) + " bar)\n";
    else if (waterPressure > maxWaterP) r += "• 💧 Suv bosimi YUQORI: " + String(waterPressure, 1) + " bar (max: " + String(maxWaterP, 1) + " bar)\n";

    if (!tempDisplayOK) r += "• 🌡️ Harorat SENSORI nosoz\n";
    else if (waterTemp < minWaterTemp) r += "• 🌡️ Harorat PAST: " + String(waterTemp, 1) + " °C (min: " + String(minWaterTemp, 1) + " °C)\n";
    else if (waterTemp > maxWaterTemp) r += "• 🌡️ Harorat YUQORI: " + String(waterTemp, 1) + " °C (max: " + String(maxWaterTemp, 1) + " °C)\n";
  }
  if (r == "") r = "• Noma'lum sabab (qo'lda tekshiring)\n";
  return r;
}

// 2 soatlik avto-hisobot FAQAT Super Adminga yuboriladi (admin/inzhener/
// menedjer/kuzatuvchilarga ODDIY hisobot bormaydi — ular faqat AVARIYA
// bo'lganda xabar oladi, sendAlarmMessage()/broadcastResolved() orqali).
void sendPeriodicReport() {
  String msg = "⏱️ <b>2 SOATLIK AVTO-HISOBOT</b>\n━━━━━━━━━━━━━━━\n";
  msg += "⚙️ Rejim: <b>" + String(currentMode) + "</b>\n";
  msg += shiftInfoBlock();
  msg += "━━━━━━━━━━━━━━━\n" + sensorDataBlock();
  msg += "━━━━━━━━━━━━━━━\n" + statBlock();
  msg += (alarmActive ? "🔴 <b>Diqqat: hozir avariya faol!</b>\n" : "🟢 <b>Barcha ko'rsatkichlar normal.</b>\n");

  sendTG(superAdmin.id, msg, adminKeyboard);

  resetStats();
}

void sendAlarmMessage(String targetID, String title) {
  if (targetID.length() < 3) return;
  String msg = "🚨 <b>AVARIYA OGOHLANTIRISH!</b>\n📌 " + title + "\n━━━━━━━━━━━━━━━\n";
  msg += "❗ <b>Sabab:</b>\n" + buildEmergencyReason();
  msg += "━━━━━━━━━━━━━━━\n" + sensorDataBlock();
  msg += "\n🔴 <i>Muammoni bartaraf etgach, pastdagi tugmani bosing!</i>";
  String kb = "[[{\"text\":\"✅ Men hal qildim (Reset)\",\"callback_data\":\"reset_alarm\"}]]";
  sendInline(targetID, msg, kb);
}

void broadcastResolved(String resolverName) {
  String msg = "✅ <b>AVARIYA HAL QILINDI!</b>\n👤 Kim: <b>" + resolverName + "</b>\n🔕 Eskalatsiya to'xtatildi, boshqa xodimlarga xabar borishi bekor qilindi.\n⏳ Keyingi " + String(POST_RESOLVE_GRACE / 60000) + " daqiqa ichida muammo yana takrorlansa, avariya zanjiri qaytadan boshlanadi.";
  Staff curMech = isDayShift() ? dayMech : nightMech;
  Staff curMaster = isDayShift() ? dayMaster : nightMaster;
  sendTG(superAdmin.id, msg, adminKeyboard);
  for (int i = 0; i < adminCount; i++) sendTG(admins[i].id, msg, adminKeyboard);
  sendTG(curMech.id, msg, watcherKeyboard);
  sendTG(curMaster.id, msg, watcherKeyboard);
  sendTG(engineer.id, msg, watcherKeyboard);
  sendTG(manager.id, msg, watcherKeyboard);
}

// YANGI: avariyani "hal qilindi" deb belgilash — Telegram orqali
// ("✅ Men hal qildim" tugmasi) VA jismoniy BTN_ALARM_RESET tugmasi
// UCHUN BITTA UMUMIY funksiya. Bu yerda:
//  - alarmActive/escalationStage tozalanadi (Telegram eskalatsiyasi to'xtaydi)
//  - resolveGraceUntil o'rnatiladi: shu vaqtgacha (POST_RESOLVE_GRACE)
//    hatto sensor hali ham yomon o'qisa ham, YANGI Telegram avariya
//    zanjiri BOSHLANMAYDI — aynan shu YO'QLIGI SABABLI oldin "tugma
//    bossam ham darhol qayta signal beryapti" holati yuzaga kelgan edi.
//  - MUHIM: badStreak hisoblagichlariga VA sirenalarga UMUMAN tegilmaydi —
//    ular faqat haqiqiy sensor holatiga qarab ishlashda davom etadi, shu
//    bilan xavfsizlik saqlanadi (kimdir xato yoki vaqtidan oldin "hal
//    qildim" desa ham, joyidagi odamlar buni sirendan sezishadi).
void resolveAlarm(String resolverName) {
  alarmActive = false;
  escalationStage = 0;
  resolveGraceUntil = millis() + POST_RESOLVE_GRACE;
  updateLCDDisplay();
  broadcastResolved(resolverName);
}

void triggerEmergency() {
  unsigned long now = millis();
  if (now - lastAlertSentTime < MIN_ALERT_INTERVAL) return; // 5-band: 30s cheklov
  if (now < resolveGraceUntil) return; // YANGI: hozirgina "hal qilindi" deb belgilangan — bir oz kutamiz

  alarmActive = true;
  escalationStage = 1;
  stageStartTime = now;
  lastAlertSentTime = now;
  // Eslatma: sirenalar bu yerda emas — ular loop()da updateSirens() orqali
  // har 1.5s'da haqiqiy sensor holatiga qarab mustaqil boshqariladi.

  // 1-BOSQICH: faqat joriy smena mexanigiga boradi — boshqa hech kimga hali bormaydi
  Staff currentMech = isDayShift() ? dayMech : nightMech;
  sendAlarmMessage(currentMech.id, "1-Bosqich: Smena Mexanigi (" + currentMech.name + ")");
  // YANGI: Super Adminga darhol xabar berish ENDI ixtiyoriy — yuqoridagi
  // NOTIFY_SUPERADMIN_IMMEDIATELY konstantasi orqali boshqariladi. false
  // qilsangiz, zanjir TO'LIQ ketma-ket bo'ladi (hech kim bir vaqtda
  // xabar olmaydi) va Super Admin faqat keyingi bosqichlarda xabardor bo'ladi.
  if (NOTIFY_SUPERADMIN_IMMEDIATELY) {
    sendTG(superAdmin.id, "ℹ️ Avariya boshlandi, " + currentMech.name + "ga xabar berildi. " + String(STAGE1_WAIT / 60000) + " daqiqada javob bo'lmasa, keyingi bosqichga o'tadi.", adminKeyboard);
  }
}



// ============================================================================
// 9) TARIX LOGI VA OYLIK HISOBOT (12-band) — LittleFS, faqat soatlik o'rtacha
// ============================================================================
void logHourlyAverage() {
  File f = LittleFS.open("/history.csv", FILE_APPEND);
  if (!f) { Serial.println("LittleFS: log ochilmadi"); return; }
  struct tm ti; getLocalTime(&ti);
  char stamp[20]; strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M", &ti);
  String line = String(stamp) + "," + String(airPressure,1) + "," +
                String(waterPressure,1) + "," + String(waterTemp,1) + "," +
                String(alarmActive ? 1 : 0) + "\n";
  f.print(line);
  f.close();

  File check = LittleFS.open("/history.csv", FILE_READ);
  if (check && check.size() > 500000) {
    check.close();
    LittleFS.remove("/history_old.csv");
    LittleFS.rename("/history.csv", "/history_old.csv");
  } else if (check) {
    check.close();
  }
}

bool sendHistoryFileToTelegram(String chatId) {
  File f = LittleFS.open("/history.csv", FILE_READ);
  if (!f) return false;

  WiFiClientSecure tgClient;
  tgClient.setInsecure();
  tgClient.setTimeout(8000);
  if (!tgClient.connect("api.telegram.org", 443)) { f.close(); return false; }

  String boundary = "----ESP32Boundary7MA4YWxk";
  String head = "--" + boundary + "\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n" + chatId + "\r\n";
  head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\nOylik zavod monitoring tarixi (soatlik o'rtacha)\r\n";
  head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"document\"; filename=\"tarix.csv\"\r\nContent-Type: text/csv\r\n\r\n";
  String tail = "\r\n--" + boundary + "--\r\n";

  int contentLength = head.length() + f.size() + tail.length();

  tgClient.println("POST /bot" + String(BOT_TOKEN) + "/sendDocument HTTP/1.1");
  tgClient.println("Host: api.telegram.org");
  tgClient.println("Content-Type: multipart/form-data; boundary=" + boundary);
  tgClient.println("Content-Length: " + String(contentLength));
  tgClient.println("Connection: close");
  tgClient.println();
  tgClient.print(head);

  uint8_t buf[512];
  while (f.available()) {
    size_t n = f.read(buf, sizeof(buf));
    tgClient.write(buf, n);
  }
  f.close();
  tgClient.print(tail);

  unsigned long t0 = millis();
  while (tgClient.connected() && millis() - t0 < 8000) { if (tgClient.available()) tgClient.read(); }
  tgClient.stop();
  return true;
}

void checkBotToken() {
  WiFiClientSecure testClient;
  testClient.setInsecure();
  testClient.setTimeout(8000);
  Serial.println("🔍 BOT_TOKEN tekshirilmoqda (getMe so'rovi)...");
  if (!testClient.connect("api.telegram.org", 443)) {
    Serial.println("❌ api.telegram.org serveriga ulanib bo'lmadi (tarmoq/DNS muammosi bo'lishi mumkin).");
    return;
  }
  testClient.println("GET /bot" + String(BOT_TOKEN) + "/getMe HTTP/1.1");
  testClient.println("Host: api.telegram.org");
  testClient.println("Connection: close");
  testClient.println();

  unsigned long t0 = millis();
  String response = "";
  while ((testClient.connected() || testClient.available()) && millis() - t0 < 8000) {
    while (testClient.available()) {
      response += (char)testClient.read();
    }
  }
  testClient.stop();

  Serial.println("📩 Telegram javobi (getMe):");
  Serial.println("--------------------------------------------------");
  Serial.println(response);
  Serial.println("--------------------------------------------------");
  if (response.indexOf("\"ok\":true") >= 0) {
    Serial.println("✅ Token ishlayapti, bot Telegram bilan bemalol gaplashadi.");
  } else if (response.indexOf("401") >= 0 || response.indexOf("Unauthorized") >= 0) {
    Serial.println("❌ TOKEN BEKOR QILINGAN yoki NOTO'G'RI (401 Unauthorized). @BotFather orqali yangi token oling.");
  } else if (response.length() == 0) {
    Serial.println("❌ Javob umuman kelmadi — bu tarmoq/HTTPS ulanishi cheklangan degani (masalan, Guest tarmoq Telegram'ni bloklagan bo'lishi mumkin).");
  } else {
    Serial.println("⚠️ Kutilmagan javob — yuqoridagi matnni menga yuboring, birga tahlil qilamiz.");
  }
}

void checkMonthlyExport() {
  struct tm ti;
  if (!getLocalTime(&ti)) return;
  if (ti.tm_mday == 25 && ti.tm_hour == 9 && lastExportMonth != ti.tm_mon) {
    bool ok = sendHistoryFileToTelegram(superAdmin.id);
    sendTG(superAdmin.id, ok ? "📊 <b>Oylik tarixiy fayl yuborildi.</b>" : "⚠️ Oylik fayl yuborishda xatolik.", adminKeyboard);
    lastExportMonth = ti.tm_mon;
    LittleFS.remove("/history_old.csv");
    LittleFS.rename("/history.csv", "/history_old.csv");
  }
}

// ============================================================================
// 10) INLINE TUGMALAR
// ============================================================================
String dataInlineKeyboard() {
  return "[[{\"text\":\"📊 Aktual ma'lumot\",\"callback_data\":\"data_now\"},{\"text\":\"🛠️ Limitlar\",\"callback_data\":\"limits_now\"}]]";
}

void sendMainMenu(String chatId) {
  String msg = "📋 <b>Bosh menyu</b>\nKerakli tugmani bosing:";
  sendInline(chatId, msg, dataInlineKeyboard());
}

// ============================================================================
// 11) SODDALASHTIRILGAN QO'SHILISH OQIMI (4, 9-band)
// ============================================================================
void addPendingRequest(String id, String name) {
  for (int i = 0; i < pendingCount; i++) if (pending[i].id == id) return;
  if (pendingCount >= MAX_PENDING) return;
  pending[pendingCount].id = id;
  pending[pendingCount].name = name;
  pending[pendingCount].used = false;
  pendingCount++;

  String msg = "🆕 <b>Yangi so'rov</b>\n👤 " + name + "\n🆔 <code>" + id + "</code>\n\nQanday rol berilsin?";
  String kb = "[["
    "{\"text\":\"🔑 Admin\",\"callback_data\":\"role_admin_" + id + "\"},"
    "{\"text\":\"👁️ Kuzatuvchi\",\"callback_data\":\"role_watch_" + id + "\"}],["
    "{\"text\":\"❌ Rad etish\",\"callback_data\":\"role_deny_" + id + "\"}]]";
  sendInline(superAdmin.id, msg, kb);
  for (int i = 0; i < adminCount; i++) sendInline(admins[i].id, msg, kb);
}

String findPendingName(String id) {
  for (int i = 0; i < pendingCount; i++) if (pending[i].id == id) return pending[i].name;
  return "Foydalanuvchi";
}

void removePending(String id) {
  for (int i = 0; i < pendingCount; i++) {
    if (pending[i].id == id) {
      for (int j = i; j < pendingCount - 1; j++) pending[j] = pending[j+1];
      pendingCount--;
      return;
    }
  }
}

void offerStaffRoleAssignment(String targetChatId, String name, String id) {
  String msg = "👥 <b>" + name + "</b> (<code>" + id + "</code>) qaysi lavozimga tayinlansin?";
  String kb = "[["
    "{\"text\":\"☀️ Kunduzgi mexanik\",\"callback_data\":\"staff_dmech_" + id + "\"},"
    "{\"text\":\"☀️ Kunduzgi master\",\"callback_data\":\"staff_dmaster_" + id + "\"}],["
    "{\"text\":\"🌙 Tungi mexanik\",\"callback_data\":\"staff_nmech_" + id + "\"},"
    "{\"text\":\"🌙 Tungi master\",\"callback_data\":\"staff_nmaster_" + id + "\"}],["
    "{\"text\":\"🌐 Inzhener\",\"callback_data\":\"staff_eng_" + id + "\"},"
    "{\"text\":\"🌐 Menedjer\",\"callback_data\":\"staff_mgr_" + id + "\"}]]";
  sendInline(targetChatId, msg, kb);
}

void applyStaffRole(String role, String id, String name) {
  if (role == "dmech")        { dayMech.name = name;     dayMech.id = id; }
  else if (role == "dmaster") { dayMaster.name = name;   dayMaster.id = id; }
  else if (role == "nmech")   { nightMech.name = name;   nightMech.id = id; }
  else if (role == "nmaster") { nightMaster.name = name; nightMaster.id = id; }
  else if (role == "eng")     { engineer.name = name;    engineer.id = id; }
  else if (role == "mgr")     { manager.name = name;     manager.id = id; }
  saveSettings();
}

String staffAssignInlineKeyboard() {
  return "[["
    "{\"text\":\"☀️ Kunduzgi mexanik\",\"callback_data\":\"chgpos_dmech\"},"
    "{\"text\":\"☀️ Kunduzgi master\",\"callback_data\":\"chgpos_dmaster\"}],["
    "{\"text\":\"🌙 Tungi mexanik\",\"callback_data\":\"chgpos_nmech\"},"
    "{\"text\":\"🌙 Tungi master\",\"callback_data\":\"chgpos_nmaster\"}],["
    "{\"text\":\"🌐 Inzhener\",\"callback_data\":\"chgpos_eng\"},"
    "{\"text\":\"🌐 Menedjer\",\"callback_data\":\"chgpos_mgr\"}]]";
}

// ============================================================================
// 12) LIMIT TAHRIRLASH MENYUSI (6-band) — rejimga mos
// ============================================================================
void sendLimitsMenu(String chatId) {
  String msg = "🛠️ <b>Limitlarni sozlash</b>\nRejim: <b>" + String(currentMode) + "</b>\nO'zgartirmoqchi bo'lgan parametrni tanlang:";
  String kb = "[[{\"text\":\"💨 Havo min\",\"callback_data\":\"edit_AIR_MIN\"},{\"text\":\"💨 Havo norma\",\"callback_data\":\"edit_AIR_NORM\"},{\"text\":\"💨 Havo max\",\"callback_data\":\"edit_AIR_MAX\"}]";
  if (currentMode == 1) {
    kb += ",[{\"text\":\"💧 Suv min\",\"callback_data\":\"edit_WATER_MIN\"},{\"text\":\"💧 Suv norma\",\"callback_data\":\"edit_WATER_NORM\"},{\"text\":\"💧 Suv max\",\"callback_data\":\"edit_WATER_MAX\"}]";
    kb += ",[{\"text\":\"🌡️ Temp min\",\"callback_data\":\"edit_TEMP_MIN\"},{\"text\":\"🌡️ Temp norma\",\"callback_data\":\"edit_TEMP_NORM\"},{\"text\":\"🌡️ Temp max\",\"callback_data\":\"edit_TEMP_MAX\"}]";
  }
  kb += "]";
  sendInline(chatId, msg, kb);

  String kb2 = "[[{\"text\":\"🎯 Havoni kalibrlash\",\"callback_data\":\"calib_air\"}";
  if (currentMode == 1) kb2 += ",{\"text\":\"🎯 Suvni kalibrlash\",\"callback_data\":\"calib_water\"}";
  kb2 += "]]";
  sendInline(chatId, "🎯 <b>Kalibrlash:</b>", kb2);

  sendInline(chatId, "🔍 <b>Diagnostika:</b>", "[[{\"text\":\"🔍 Xom (kalibrsiz) qiymatlar\",\"callback_data\":\"raw_now\"}]]");
}

float* fieldPointer(String field) {
  if (field == "AIR_MIN") return &minAirP;
  if (field == "AIR_NORM") return &normAirP;
  if (field == "AIR_MAX") return &maxAirP;
  if (field == "WATER_MIN") return &minWaterP;
  if (field == "WATER_NORM") return &normWaterP;
  if (field == "WATER_MAX") return &maxWaterP;
  if (field == "TEMP_MIN") return &minWaterTemp;
  if (field == "TEMP_NORM") return &normWaterTemp;
  if (field == "TEMP_MAX") return &maxWaterTemp;
  return nullptr;
}

String removeAdminInlineKeyboard() {
  String kb = "[";
  for (int i = 0; i < adminCount; i++) {
    kb += String(i > 0 ? "," : "") + "[{\"text\":\"❌ " + admins[i].name + " (" + admins[i].id + ")\",\"callback_data\":\"rmadm_" + admins[i].id + "\"}]";
  }
  kb += "]";
  return kb;
}

// YANGI: kuzatuvchilar ro'yxatini har biri o'z "o'chirish" tugmasi bilan
// ko'rsatadi — removeAdminInlineKeyboard() bilan bir xil mantiq.
String removeWatcherInlineKeyboard() {
  String kb = "[";
  for (int i = 0; i < watcherCount; i++) {
    kb += String(i > 0 ? "," : "") + "[{\"text\":\"❌ " + watchers[i].name + " (" + watchers[i].id + ")\",\"callback_data\":\"rmwch_" + watchers[i].id + "\"}]";
  }
  kb += "]";
  return kb;
}

// ============================================================================
// 12b) WI-FI'NI TELEGRAM ORQALI BOSHQARISH
// ============================================================================
void sendWifiMenu(String chatId) {
  String msg = "📶 <b>Wi-Fi holati</b>\n━━━━━━━━━━━━━━━\n";
  msg += "🌐 Tarmoq: <b>" + currentSSID + "</b>\n";
  if (WiFi.status() == WL_CONNECTED) {
    long rssi = WiFi.RSSI();
    String quality = (rssi > -60) ? "🟢 Kuchli" : ((rssi > -75) ? "🟡 O'rtacha" : "🔴 Zaif");
    msg += "📶 Signal: <code>" + String(rssi) + " dBm</code> (" + quality + ")\n";
    msg += "🔗 Holat: <b>ULANGAN ✅</b>\n";
  } else {
    msg += "🔗 Holat: <b>ULANMAGAN ❌</b>\n";
  }
  String kb = "[[{\"text\":\"🔄 Boshqa Wi-Fi'ga o'tish\",\"callback_data\":\"wifi_change\"}]]";
  sendInline(chatId, msg, kb);
}

// ============================================================================
// 13) CALLBACK (INLINE TUGMA BOSILGANDA) ISHLOV BERISH
// ============================================================================
void handleCallback(String chatId, String data, String queryId) {
  bot.answerCallbackQuery(queryId);

  if (!isKnownUser(chatId)) {
    // YANGI: nomaʼlum/ro'yxatdan o'chirilgan foydalanuvchi tugma bossa,
    // avvalgidek jim qolish o'rniga hech bo'lmasa Serial'da izini
    // qoldiramiz — shu bilan "tugma ishlamayapti" holatlarini diagnostika
    // qilish osonlashadi.
    Serial.println("⚠️ Callback rad etildi (noma'lum foydalanuvchi) -> ID: " + chatId + ", data: " + data);
    return;
  }

  if (data == "data_now") {
    String msg = "📊 <b>AKTUAL MA'LUMOT</b>\n━━━━━━━━━━━━━━━\n" + sensorDataBlock();
    sendTG(chatId, msg, keyboardFor(chatId));
  }
  else if (data == "limits_now") {
    String msg = "🛠️ <b>JORIY LIMITLAR</b>\n💨 Havo: " + String(minAirP,1) + "-" + String(maxAirP,1) + " bar (norma " + String(normAirP,1) + ")\n";
    if (currentMode == 1) {
      msg += "💧 Suv: " + String(minWaterP,1) + "-" + String(maxWaterP,1) + " bar (norma " + String(normWaterP,1) + ")\n";
      msg += "🌡️ Temp: " + String(minWaterTemp,1) + "-" + String(maxWaterTemp,1) + " °C (norma " + String(normWaterTemp,1) + ")\n";
    }
    sendTG(chatId, msg, keyboardFor(chatId));
  }
  else if (data == "raw_now" && isAdmin(chatId)) {
    String msg = "🔍 <b>DIAGNOSTIKA (xom ma'lumot)</b>\n━━━━━━━━━━━━━━━\n";
    msg += "💨 Havo: Tok=<code>" + String(airCurrentMACached,2) + " mA</code>, Xom=<code>" + String(rawAirBarCached,2) + "</code> bar, Siljima=<code>" + String(airZeroOffset,2) + "</code>, Kalibrlangan=<code>" + String(airPressure,2) + "</code> bar\nHozirgi o'qish: " + (airSensorOK ? "OK ✅" : "NOSOZ ❌") + " | Displeyda: " + (airDisplayOK ? "OK ✅" : "NOSOZ ❌ (tasdiqlangan)") + "\n";
    if (currentMode == 1) {
      msg += "💧 Suv: Tok=<code>" + String(waterCurrentMACached,2) + " mA</code>, Xom=<code>" + String(rawWaterBarCached,2) + "</code> bar, Siljima=<code>" + String(waterZeroOffset,2) + "</code>, Kalibrlangan=<code>" + String(waterPressure,2) + "</code> bar\nHozirgi o'qish: " + (waterSensorOK ? "OK ✅" : "NOSOZ ❌") + " | Displeyda: " + (waterDisplayOK ? "OK ✅" : "NOSOZ ❌ (tasdiqlangan)") + "\n";
      msg += "🌡️ Temp: <code>" + String(waterTemp,2) + "</code> °C, DS18B20 topilgan: " + (tempSensorPresent ? "HA ✅" : "YO'Q ❌") + ", Displeyda: " + (tempDisplayOK ? "OK ✅" : "NOSOZ ❌") + "\n";
    }
    msg += "\n💡 Sensor OK oralig'i: 3.2-21.5 mA. Agar Tok shu oraliqdan tashqariga chiqib turgan bo'lsa (masalan 21.6+ yoki 3.1-) — bu haqiqiy sim/sensor muammosi.\nAgar Tok doim OK oralig'ida-yu, baribir NOSOZ chiqsa — Serial Monitor orqali kuzating.\nAgar Xom qiymat ham 0 bo'lsa — bu sim/sensor ulanishi muammosi.\nAgar Xom normal-u Kalibrlangan 0 bo'lsa — kalibrlashni qayta bajaring.";
    sendTG(chatId, msg, adminKeyboard);
  }
  else if (data.startsWith("role_") && isAdmin(chatId)) {
    String rest = data.substring(5);
    int us = rest.indexOf('_');
    String action = rest.substring(0, us);
    String id = rest.substring(us + 1);
    String name = findPendingName(id);

    if (action == "admin") {
      if (!isSuperAdmin(chatId)) {
        sendTG(chatId, "⛔ Yangi admin qo'shishga faqat Super Admin vakolatli. So'rov navbatda qoladi.", adminKeyboard);
        return;
      }
      if (adminCount < MAX_ADMINS) {
        admins[adminCount].name = name; admins[adminCount].id = id; adminCount++;
        saveAdmins();
        sendTG(chatId, "✅ " + name + " admin etib tayinlandi.", adminKeyboard);
        sendTG(id, "🔑 <b>Sizga Admin huquqi berildi!</b>", adminKeyboard);
      } else {
        sendTG(chatId, "⚠️ Maksimal admin soniga yetildi.", adminKeyboard);
      }
    } else if (action == "watch" && watcherCount < MAX_WATCHERS) {
      watchers[watcherCount].name = name; watchers[watcherCount].id = id; watcherCount++;
      saveWatchers();
      sendTG(chatId, "✅ " + name + " kuzatuvchi etib qo'shildi.", adminKeyboard);
      sendTG(id, "👁️ Sizga kuzatuvchi huquqi berildi. /status'dan 2 soatda 1 marta foydalanishingiz mumkin, shundan tashqari botga kelgan xabarlarni kuzatib turasiz.", watcherKeyboard);
      offerStaffRoleAssignment(chatId, name, id);
    } else if (action == "deny") {
      sendTG(chatId, "❌ So'rov rad etildi: " + name, adminKeyboard);
      sendTG(id, "❌ Sizning so'rovingiz rad etildi.", guestKeyboard);
    }
    removePending(id);
  }
  else if (data.startsWith("staff_") && isAdmin(chatId)) {
    String rest = data.substring(6);
    int us = rest.indexOf('_');
    String role = rest.substring(0, us);
    String id = rest.substring(us + 1);
    String name = "Xodim";
    for (int i = 0; i < adminCount; i++) if (admins[i].id == id) name = admins[i].name;
    for (int i = 0; i < watcherCount; i++) if (watchers[i].id == id) name = watchers[i].name;
    applyStaffRole(role, id, name);
    sendTG(chatId, "✅ " + name + " smena lavozimiga tayinlandi.", adminKeyboard);
  }
  else if (data.startsWith("chgpos_") && isAdmin(chatId)) {
    String role = data.substring(7);
    setEditSession(chatId, "ASSIGN_" + role);
    sendTG(chatId, "✏️ Yangi xodimning Telegram ID raqamini yuboring.\nIsm ham qo'shmoqchi bo'lsangiz: <code>ID Ism</code> formatida yuboring (masalan: <code>123456789 Vali</code>).\n<i>ID'ni bilish uchun xodim botga /start yozsin — ID unga ko'rsatiladi.</i>", adminKeyboard);
  }
  else if (data == "add_admin_id" && isSuperAdmin(chatId)) {
    setEditSession(chatId, "ADD_ADMIN_ID");
    sendTG(chatId, "✏️ Yangi adminning Telegram ID raqamini yuboring.\nIsm ham qo'shmoqchi bo'lsangiz: <code>ID Ism</code> formatida yuboring (masalan: <code>123456789 Vali</code>).", adminKeyboard);
  }
  else if (data == "remove_admin_menu" && isSuperAdmin(chatId)) {
    if (adminCount == 0) {
      sendTG(chatId, "ℹ️ Hozircha o'chirish uchun qo'shimcha admin yo'q.", adminKeyboard);
    } else {
      sendInline(chatId, "➖ <b>Qaysi adminni o'chirmoqchisiz?</b>", removeAdminInlineKeyboard());
    }
  }
  else if (data == "remove_watcher_menu" && isSuperAdmin(chatId)) {
    if (watcherCount == 0) {
      sendTG(chatId, "ℹ️ Hozircha o'chirish uchun kuzatuvchi yo'q.", adminKeyboard);
    } else {
      sendInline(chatId, "➖ <b>Qaysi kuzatuvchini o'chirmoqchisiz?</b>", removeWatcherInlineKeyboard());
    }
  }
  else if (data.startsWith("rmadm_") && isSuperAdmin(chatId)) {
    String id = data.substring(6);
    String name = "Foydalanuvchi";
    for (int i = 0; i < adminCount; i++) if (admins[i].id == id) name = admins[i].name;
    if (removeAdminById(id)) {
      sendTG(chatId, "✅ " + name + " (<code>" + id + "</code>) admin ro'yxatidan o'chirildi.", adminKeyboard);
      sendTG(id, "ℹ️ Sizning Admin huquqingiz bekor qilindi.", guestKeyboard);
    } else {
      sendTG(chatId, "⚠️ Bu foydalanuvchi admin ro'yxatida topilmadi (allaqachon o'chirilgan bo'lishi mumkin).", adminKeyboard);
    }
  }
  else if (data.startsWith("rmwch_") && isSuperAdmin(chatId)) {
    String id = data.substring(6);
    String name = "Foydalanuvchi";
    for (int i = 0; i < watcherCount; i++) if (watchers[i].id == id) name = watchers[i].name;
    if (removeWatcherById(id)) {
      sendTG(chatId, "✅ " + name + " (<code>" + id + "</code>) kuzatuvchilar ro'yxatidan o'chirildi.", adminKeyboard);
      sendTG(id, "ℹ️ Sizning kuzatuvchi huquqingiz bekor qilindi.", guestKeyboard);
    } else {
      sendTG(chatId, "⚠️ Bu foydalanuvchi kuzatuvchilar ro'yxatida topilmadi (allaqachon o'chirilgan bo'lishi mumkin).", adminKeyboard);
    }
  }
  else if (data.startsWith("edit_") && isAdmin(chatId)) {
    String field = data.substring(5);
    setEditSession(chatId, field);
    sendTG(chatId, "✏️ Yangi qiymatni raqam bilan yuboring (masalan: 9.5)\n<i>(" + field + ")</i>", adminKeyboard);
  }
  else if (data == "calib_air" && isAdmin(chatId)) {
    setEditSession(chatId, "CALIB_AIR");
    sendTG(chatId, "🎯 <b>Havo bosimi kalibrlash</b>\nHozir mexanik (analog) manometrda ko'rinayotgan HAQIQIY havo bosimini bar da yuboring (0-16 oralig'ida).\nMasalan: <code>0</code>", adminKeyboard);
  }
  else if (data == "calib_water" && isAdmin(chatId)) {
    setEditSession(chatId, "CALIB_WATER");
    sendTG(chatId, "🎯 <b>Suv bosimi kalibrlash</b>\nHozir mexanik (analog) manometrda ko'rinayotgan HAQIQIY suv bosimini bar da yuboring (0-10 oralig'ida).\nMasalan: <code>3.6</code>", adminKeyboard);
  }
  else if (data == "wifi_change") {
    if (!isSuperAdmin(chatId)) {
      sendTG(chatId, "⛔ Wi-Fi tarmog'ini faqat Super Admin o'zgartira oladi (xavfsizlik sababli).", adminKeyboard);
    } else {
      setEditSession(chatId, "WIFI_SSID");
      tempNewSSID = "";
      sendTG(chatId, "📶 Yangi Wi-Fi nomini (SSID) yuboring:", adminKeyboard);
    }
  }
  else if (data == "reset_alarm") {
    // Izoh: bu tugmani KIM bosishidan qat'iy nazar (admin, smena xodimi
    // yoki kuzatuvchi) avariya darhol reset bo'ladi — ortiqcha rol
    // cheklovi yo'q. Endi HAQIQIY reset ishi resolveAlarm() funksiyasida
    // markazlashtirilgan (POST_RESOLVE_GRACE bilan birga).
    if (alarmActive) {
      String resolverName = "Xodim";
      if (isSuperAdmin(chatId)) resolverName = superAdmin.name;
      else { for (int a=0;a<adminCount;a++) if (admins[a].id==chatId) resolverName = admins[a].name; }
      for (int w=0; w<watcherCount; w++) if (watchers[w].id==chatId) resolverName = watchers[w].name;
      if (chatId == dayMech.id) resolverName = dayMech.name;
      if (chatId == dayMaster.id) resolverName = dayMaster.name;
      if (chatId == nightMech.id) resolverName = nightMech.name;
      if (chatId == nightMaster.id) resolverName = nightMaster.name;
      if (chatId == engineer.id) resolverName = engineer.name;
      if (chatId == manager.id) resolverName = manager.name;
      resolveAlarm(resolverName);
    } else {
      sendTG(chatId, "ℹ️ Hozircha faol avariya yo'q.", keyboardFor(chatId));
    }
  }
}

// ============================================================================
// JISMONIY 1/2-REJIM TUGMALARI VA SERIAL MONITOR
// ============================================================================
void startSelectedMode(int mode, const String &source) {
  currentMode = mode;
  loadAirForMode(mode);
  saveSettings();
  resetStats();

  // YANGI: rejim almashganda badStreak hisoblagichlarini ham tozalaymiz —
  // shunda eski rejimdan qolgan hisoblagich yangi rejimda chalkash
  // (yolg'on) signalga sabab bo'lmaydi. Har ikkala rejimda ham barqaror,
  // "toza" holatdan boshlanadi.
  airBadStreak = 0; waterBadStreak = 0; tempBadStreak = 0;

  monitoringStarted = true;
  last2HourReport = millis();
  lastHourLog = millis();

  alarmActive = false;
  escalationStage = 0;
  resolveGraceUntil = 0; // yangi rejim/monitoring boshlanishi — eski "grace" muddati endi keraksiz
  updateLCDDisplay();

  String modeName = (mode == 1) ? "1-Rejim: Havo + Suv + Temp" : "2-Rejim: Faqat Havo";
  String msg = "▶️ <b>" + source + " orqali ishga tushirildi.</b>\n"
               "⚙️ <b>" + modeName + "</b> tugma orqali tanlandi.\n"
               "✅ Avtomatik nazorat va ogohlantirishlar endi faol.";
  sendTG(superAdmin.id, msg, adminKeyboard);

  Serial.println("==================================================");
  Serial.println("▶️ MONITORING ISHGA TUSHDI");
  Serial.println("Manba: " + source);
  Serial.println("Tanlangan rejim: " + String(mode));
  Serial.println("1-Rejim = Havo + Suv + Temp");
  Serial.println("2-Rejim = Faqat Havo");
  Serial.println("Avtomatik nazorat: FAOL");
  Serial.println("==================================================");
}

void printSerialMonitoring() {
  Serial.println("--------------------------------------------------");
  Serial.println("📊 JORIY MONITORING");
  Serial.println("Tizim: " + String(systemActive ? "ISHLAMOQDA" : "TO'XTATILGAN"));
  Serial.println("Nazorat: " + String(monitoringStarted ? "FAOL" : "KUTILMOQDA"));
  Serial.println("Rejim: " + String(currentMode));

  // YANGI: har doim Tok (mA) ham chiqariladi — "NOSOZ" payti aynan qaysi
  // chegaradan (3.2 dan past yoki 21.5 dan yuqori) chiqib ketganini shu
  // yerdan darhol ko'rish mumkin.
  Serial.println("💨 Havo: " + String(airPressure, 1) + " bar (Tok=" + String(airCurrentMACached, 2) + " mA)" + (airSensorOK ? "" : " ⚠️ CHEGARADAN TASHQARI") + (airDisplayOK ? "" : " -> DISPLEYDA: NOSOZ"));

  if (currentMode == 1) {
    Serial.println("💧 Suv: " + String(waterPressure, 1) + " bar (Tok=" + String(waterCurrentMACached, 2) + " mA)" + (waterSensorOK ? "" : " ⚠️ CHEGARADAN TASHQARI") + (waterDisplayOK ? "" : " -> DISPLEYDA: NOSOZ"));

    if (tempSensorOK)
      Serial.println("🌡️ Suv harorati: " + String(waterTemp, 1) + " °C");
    else
      Serial.println("🌡️ Suv harorati: NOSOZ" + String(tempDisplayOK ? "" : " -> DISPLEYDA: NOSOZ"));
  }

  Serial.println("Avariya: " + String(alarmActive ? "FAOL" : "YO'Q"));
  Serial.println("Siren havo: " + String(sirenAirOn ? "ON" : "OFF"));
  Serial.println("Siren suv: " + String(sirenWaterOn ? "ON" : "OFF"));
  Serial.println("Siren temp: " + String(sirenTempOn ? "ON" : "OFF"));
  Serial.println("--------------------------------------------------");
}

// ============================================================================
// 14) TELEGRAM MATNLI BUYRUQLARI VA REPLY TUGMALARI
// ============================================================================
void handleTelegramMessages(int numNewMessages) {
  for (int i = 0; i < numNewMessages; i++) {

    if (bot.messages[i].type == "callback_query") {
      handleCallback(String(bot.messages[i].chat_id), bot.messages[i].text, bot.messages[i].query_id);
      continue;
    }

    String chat_id = String(bot.messages[i].chat_id);
    String text = bot.messages[i].text;
    text.trim();
    text.replace(',', '.');
    String senderName = bot.messages[i].from_name.length() ? bot.messages[i].from_name : "Foydalanuvchi";

    String activeField = getEditField(chat_id);
    if (activeField.length() > 0) {

      if (activeField == "WIFI_SSID") {
        if (text.length() < 1) {
          sendTG(chat_id, "⚠️ Bo'sh SSID bo'lmaydi. Qaytadan yuboring.", adminKeyboard);
        } else {
          tempNewSSID = text;
          setEditSession(chat_id, "WIFI_PASS");
          sendTG(chat_id, "🔑 Endi parolni yuboring. Parol yo'q tarmoq bo'lsa, shunchaki <code>-</code> deb yuboring.", adminKeyboard);
        }
        continue;
      }
      if (activeField == "WIFI_PASS") {
        String pass = (text == "-") ? "" : text;
        String newSSID = tempNewSSID;
        clearEditSession(chat_id);
        tempNewSSID = "";

        sendTG(chat_id, "🔄 <b>Yangi Wi-Fi tekshirilmoqda...</b>\n🌐 Tarmoq: <code>" + newSSID + "</code>\n⏳ Ulanish muvaffaqiyatli bo'lsa saqlanadi, bo'lmasa eski Wi-Fi'ga avtomatik qaytiladi.", adminKeyboard);
        startWifiSwitch(newSSID, pass, chat_id);
        continue;
      }

      bool looksNumeric = text.length() > 0 && (isDigit(text[0]) || text[0] == '-' || text[0] == '.' );
      bool isMenuButton = (text.startsWith("/") || text == "📊 Holat" || text == "🛠️ Sozlamalar" ||
                            text == "🚀 Start" || text == "⏸️ Stop" || text == "👥 Xodimlar" ||
                            text == "🔑 Adminlar" || text == "🌐 1-Rejim" || text == "💨 2-Rejim" ||
                            text == "📶 Wi-Fi");
      if (isMenuButton) {
        clearEditSession(chat_id);
      } else if (looksNumeric) {
        float val = text.toFloat();

        if (activeField == "CALIB_AIR") {
          if (val < AIR_SENSOR_MIN_BAR || val > AIR_SENSOR_MAX_BAR) {
            sendTG(chat_id, "⚠️ Qiymat " + String(AIR_SENSOR_MIN_BAR,1) + "-" + String(AIR_SENSOR_MAX_BAR,1) + " bar oralig'ida bo'lishi kerak. Qaytadan yuboring.", adminKeyboard);
            continue;
          }
          airZeroOffset = rawAirBarCached - val;
          airPressure = max(0.0f, rawAirBarCached - airZeroOffset);
          saveSettings();
          sendTG(chat_id, "✅ Havo bosimi kalibrlandi. Endi ko'rsatiladigan qiymat: <b>" + String(val, 1) + " bar</b>", adminKeyboard);
        } else if (activeField == "CALIB_WATER") {
          if (currentMode != 1) {
            sendTG(chat_id, "⚠️ Suv kalibrlash faqat 1-Rejimda mavjud. Avval 1-Rejimga o'ting.", adminKeyboard);
            clearEditSession(chat_id);
            continue;
          }
          if (val < WATER_SENSOR_MIN_BAR || val > WATER_SENSOR_MAX_BAR) {
            sendTG(chat_id, "⚠️ Qiymat " + String(WATER_SENSOR_MIN_BAR,1) + "-" + String(WATER_SENSOR_MAX_BAR,1) + " bar oralig'ida bo'lishi kerak. Qaytadan yuboring.", adminKeyboard);
            continue;
          }
          waterZeroOffset = rawWaterBarCached - val;
          waterPressure = max(0.0f, rawWaterBarCached - waterZeroOffset);
          saveSettings();
          sendTG(chat_id, "✅ Suv bosimi kalibrlandi. Endi ko'rsatiladigan qiymat: <b>" + String(val, 1) + " bar</b>", adminKeyboard);
        } else if (activeField.startsWith("ASSIGN_") || activeField == "ADD_ADMIN_ID") {
          String idPart = text;
          String namePart = (activeField == "ADD_ADMIN_ID") ? "Admin" : "Xodim";
          int sp = text.indexOf(' ');
          if (sp > 0) { idPart = text.substring(0, sp); namePart = text.substring(sp + 1); namePart.trim(); }
          idPart.trim();
          bool idValid = idPart.length() >= 5;
          for (int c = 0; c < idPart.length() && idValid; c++) if (!isDigit(idPart[c])) idValid = false;
          if (!idValid) {
            sendTG(chat_id, "⚠️ Noto'g'ri ID. Faqat raqamlardan iborat Telegram ID yuboring (masalan: 123456789), xohlasangiz ortidan ism: <code>123456789 Vali</code>", adminKeyboard);
            continue;
          }
          if (activeField == "ADD_ADMIN_ID") {
            if (isAdmin(idPart)) {
              sendTG(chat_id, "ℹ️ Bu foydalanuvchi allaqachon admin.", adminKeyboard);
            } else if (adminCount >= MAX_ADMINS) {
              sendTG(chat_id, "⚠️ Maksimal admin soniga (" + String(MAX_ADMINS) + ") yetildi.", adminKeyboard);
            } else {
              admins[adminCount].name = namePart; admins[adminCount].id = idPart; adminCount++;
              saveAdmins();
              sendTG(chat_id, "✅ " + namePart + " admin etib tayinlandi.", adminKeyboard);
              sendTG(idPart, "🔑 <b>Sizga Admin huquqi berildi!</b>", adminKeyboard);
            }
          } else {
            String role = activeField.substring(7);
            applyStaffRole(role, idPart, namePart);
            sendTG(chat_id, "✅ Lavozim yangilandi: <b>" + namePart + "</b> (<code>" + idPart + "</code>)", adminKeyboard);
          }
        } else {
          float* p = fieldPointer(activeField);
          if (p) {
            *p = val;
            saveSettings();
            sendTG(chat_id, "✅ Yangilandi: <b>" + activeField + "</b> = " + String(val, 1), adminKeyboard);
          }
        }
        clearEditSession(chat_id);
        continue;
      } else {
        sendTG(chat_id, "⚠️ Iltimos, raqam yuboring (masalan: 9.5). Bekor qilish uchun /menu bosing.", adminKeyboard);
        continue;
      }
    }

    if (!isKnownUser(chat_id)) {
      if (text == "🔓 Ruxsat so'rash" || text.startsWith("/start")) {
        addPendingRequest(chat_id, senderName);
        sendTG(chat_id, "⏳ So'rovingiz administratorga yuborildi. Tasdiqlanishini kuting.\n🆔 <code>" + chat_id + "</code>", guestKeyboard);
      } else {
        sendTG(chat_id, "⛔ Sizda ushbu botdan foydalanish huquqi yo'q.\n🆔 <code>" + chat_id + "</code>", guestKeyboard);
      }
      continue;
    }

    if (!systemActive && !(text.startsWith("/start") || text == "🚀 Start")) {
      sendTG(chat_id, "⏸️ Tizim to'xtatilgan holatda. Davom etish uchun Start bosing.", keyboardFor(chat_id));
      continue;
    }

    bool admin = isAdmin(chat_id);

    if (!admin) {
      if (text == "📊 Holat" || text.startsWith("/status")) {
        int wi = watcherIndex(chat_id);
        if (wi >= 0) {
          unsigned long now = millis();
          if (now - watcherLastRequest[wi] < TWO_HOURS_MS) {
            unsigned long waitMs = TWO_HOURS_MS - (now - watcherLastRequest[wi]);
            unsigned long waitMin = waitMs / 60000;
            sendTG(chat_id, "⏳ Siz holatni faqat 2 soatda 1 marta so'rashingiz mumkin.\nQayta so'rash uchun: ~" + String(waitMin) + " daqiqa qoldi.\nShu orada bot yuboradigan xabarlarni kuzatib turing.", watcherKeyboard);
            continue;
          }
          watcherLastRequest[wi] = now;
        }
      } else {
        sendTG(chat_id, "⛔ Sizga faqat 'Holat' tugmasidan foydalanish ruxsat etilgan.", watcherKeyboard);
        continue;
      }
    }

    if (text.startsWith("/start") || text == "🚀 Start") {
      systemActive = true;
      monitoringStarted = false;
      applyRelay(); updateLCDDisplay();
      sendTG(chat_id, "🚀 <b>Tizim Yoqildi!</b>\nXodimlar/Sozlamalar bilan ishlashingiz mumkin.\n👉 Avtomatik nazorat va ogohlantirishlarni boshlash uchun <b>1-Rejim</b> yoki <b>2-Rejim</b> tugmasini bosing.", keyboardFor(chat_id));
    }
    else if (text.startsWith("/stop") || text == "⏸️ Stop") {
      if (!admin) { sendTG(chat_id, "⛔ Ruxsat yo'q.", watcherKeyboard); continue; }
      systemActive = false; monitoringStarted = false;
      applyRelay(); updateLCDDisplay();
      alarmActive = false; escalationStage = 0;
      allSirensOff();
      sendTG(chat_id, "⏸️ <b>Tizim To'xtatildi!</b>\nBarcha funksiyalar (nazorat, xabarlar, sozlamalar) to'xtatildi. Davom etish uchun Start bosing.", keyboardFor(chat_id));
    }
    else if (text.startsWith("/mode1") || text == "🌐 1-Rejim") {
      if (!admin) { sendTG(chat_id, "⛔ Ruxsat yo'q.", watcherKeyboard); continue; }
      startSelectedMode(1, "Telegram");
      if (chat_id != superAdmin.id) {
        sendTG(chat_id, "🔄 <b>1-Rejim: Havo + Suv + Temp</b>\n✅ Avtomatik nazorat va ogohlantirishlar endi faol.", adminKeyboard);
      }
    }
    else if (text.startsWith("/mode2") || text == "💨 2-Rejim") {
      if (!admin) { sendTG(chat_id, "⛔ Ruxsat yo'q.", watcherKeyboard); continue; }
      startSelectedMode(2, "Telegram");
      if (chat_id != superAdmin.id) {
        sendTG(chat_id, "🔄 <b>2-Rejim: Faqat Havo</b>\n✅ Avtomatik nazorat va ogohlantirishlar endi faol.", adminKeyboard);
      }
    }
    else if (text == "📊 Holat" || text.startsWith("/status")) {
      String msg = "📊 <b>JORIY HOLAT</b>\n━━━━━━━━━━━━━━━\n";
      msg += "⚙️ Rejim: <b>" + String(currentMode) + "</b> | Holat: <b>" + (systemActive ? "ISHLAMOQDA ✅" : "TO'XTATILGAN ⏸️") + "</b>\n";
      msg += shiftInfoBlock();
      msg += "━━━━━━━━━━━━━━━\n" + sensorDataBlock();
      sendTG(chat_id, msg, keyboardFor(chat_id));
    }
    else if (text == "🛠️ Sozlamalar" || text.startsWith("/control")) {
      if (!admin) { sendTG(chat_id, "⛔ Ruxsat yo'q.", watcherKeyboard); continue; }
      sendLimitsMenu(chat_id);
    }
    else if (text == "👥 Xodimlar" || text.startsWith("/staff")) {
      bool showId = admin;
      String msg = "👥 <b>BIRIKTIRILGAN XODIMLAR</b>\n\n☀️ <b>KUNDUZGI SMENA:</b>\n";
      msg += "• Mex: <b>" + dayMech.name + "</b>" + (showId ? " (<code>" + dayMech.id + "</code>)" : "") + "\n";
      msg += "• Master: <b>" + dayMaster.name + "</b>" + (showId ? " (<code>" + dayMaster.id + "</code>)" : "") + "\n\n";
      msg += "🌙 <b>TUNGI SMENA:</b>\n";
      msg += "• Mex: <b>" + nightMech.name + "</b>" + (showId ? " (<code>" + nightMech.id + "</code>)" : "") + "\n";
      msg += "• Master: <b>" + nightMaster.name + "</b>" + (showId ? " (<code>" + nightMaster.id + "</code>)" : "") + "\n\n";
      msg += "🌐 <b>DOIMIY (24/7):</b>\n";
      msg += "• Inzhener: <b>" + engineer.name + "</b>" + (showId ? " (<code>" + engineer.id + "</code>)" : "") + "\n";
      msg += "• Menedjer: <b>" + manager.name + "</b>" + (showId ? " (<code>" + manager.id + "</code>)" : "") + "\n";
      if (admin) msg += "\n💡 Yangi xodim tayinlash uchun uni avval 'Ruxsat so'rash' orqali botga qo'shing — so'rov kelganda lavozimini tugma bilan tanlaysiz. Mavjud lavozimni o'zgartirish uchun pastdagi tugmalardan foydalaning.";
      sendTG(chat_id, msg, keyboardFor(chat_id));
      if (admin) sendInline(chat_id, "✏️ Lavozimni o'zgartirish uchun tanlang:", staffAssignInlineKeyboard());
    }
    else if (text.startsWith("/admins") || text == "🔑 Adminlar") {
      if (!admin) { sendTG(chat_id, "⛔ Ruxsat yo'q.", watcherKeyboard); continue; }
      String msg = "🔑 <b>ADMINLAR RO'YXATI</b>\n\n👑 " + superAdmin.name + " (<code>" + superAdmin.id + "</code>)\n";
      for (int a = 0; a < adminCount; a++) msg += "• " + admins[a].name + " (<code>" + admins[a].id + "</code>)\n";
      if (adminCount == 0) msg += "<i>(qo'shimcha admin yo'q)</i>\n";
      msg += "\n👁️ <b>KUZATUVCHILAR RO'YXATI</b>\n";
      for (int w = 0; w < watcherCount; w++) msg += "• " + watchers[w].name + " (<code>" + watchers[w].id + "</code>)\n";
      if (watcherCount == 0) msg += "<i>(kuzatuvchi yo'q)</i>\n";
      msg += "\n💡 Yangi admin qo'shish: foydalanuvchi 'Ruxsat so'rash' tugmasini bossin, so'ng sizga so'rov keladi — 🔑 Admin tugmasini bosasiz. Yoki pastdagi tugma orqali to'g'ridan-to'g'ri ID bilan qo'shing.";
      sendTG(chat_id, msg, adminKeyboard);
      if (isSuperAdmin(chat_id)) {
        String kb2 = "[[{\"text\":\"➕ Yangi admin qo'shish (ID orqali)\",\"callback_data\":\"add_admin_id\"}],"
                     "[{\"text\":\"➖ Admin o'chirish\",\"callback_data\":\"remove_admin_menu\"}],"
                     "[{\"text\":\"➖ Kuzatuvchi o'chirish\",\"callback_data\":\"remove_watcher_menu\"}]]";
        sendInline(chat_id, "Ro'yxatlarni boshqarish:", kb2);
      }
    }
    else if (text.startsWith("/wifireset")) {
      if (!isSuperAdmin(chat_id)) { sendTG(chat_id, "⛔ Bu buyruq faqat Super Admin uchun.", adminKeyboard); continue; }
      sendTG(chat_id, "🔄 Wi-Fi qayta ulanmoqda (fonda, tizim ishlashda davom etadi)...", adminKeyboard);
      wifiReconnect();
    }
    else if (text == "📶 Wi-Fi" || text.startsWith("/wifi")) {
      if (!admin) { sendTG(chat_id, "⛔ Ruxsat yo'q.", watcherKeyboard); continue; }
      sendWifiMenu(chat_id);
    }
    else if (text.startsWith("/menu")) {
      sendMainMenu(chat_id);
    }
  }
}

// ============================================================================
// 14b) JISMONIY TUGMALAR: QIRRA (EDGE) ANIQLASH VA DEBOUNCE
//
// MUHIM TUZATISH: avvalgi kodda tugmalar faqat "digitalRead == LOW" +
// vaqt asosidagi debounce (300ms) bilan tekshirilardi. Bu degani: agar
// tugma 300ms dan UZOQROQ bosib turilsa, u HALI HAM "bosilgan" (LOW)
// holatda bo'lgani uchun harakat YANA BIR MARTA (va yana, va yana — har
// 300ms'da) ishga tushardi. Natijada: Start/Stop tugmasini biroz uzoqroq
// ushlab tursangiz, tizim yoqilib-o'chib ketishi (miltillash), yoki
// 1-/2-Rejim tugmasini ushlab tursangiz, monitoring bir necha marta
// qayta ishga tushib, Telegramga bir nechta bir xil xabar ketishi mumkin
// edi — bu "tugma normal ishlamayapti" taassurotining asosiy sababi edi.
//
// YECHIM: endi har bir tugma uchun HAQIQIY holat o'tishi (avvalgi holat
// HIGH, joriy holat LOW — ya'ni "hozirgina bosildi") kuzatiladi, VA bu
// o'tish 50ms davomida barqaror turgandan keyingina "bosildi" deb
// hisoblanadi (mexanik "sakrash"/bounce'ni yo'qotish uchun). Natijada har
// bir bosish — necha soniya ushlab turilishidan qat'iy nazar — FAQAT
// BIR MARTA ishlaydi, tugma qo'yib yuborilgandan keyin esa (LOW->HIGH)
// hech qanday harakat qilinmaydi (faqat "bosish" pallasi ishga tushiradi,
// "qo'yib yuborish" emas).
//
// ULANISH: barcha 4 tugma ham INPUT_PULLUP rejimida ishlaydi — bu degani
// GPIO ICHKI pull-up rezistori orqali "bo'sh holatda" HIGH (3.3V) bo'lib
// turadi, tugma bosilganda esa GND'ga ulanib LOW bo'ladi. Demak:
//   - Tugmaning BIR OYOG'I -> tegishli GPIO pinga (27/26/16/17)
//   - Tugmaning IKKINCHI OYOG'I -> GND (umumiy "minus"/negativ liniyaga)
// Boshqa hech narsa (tashqi rezistor, 3.3V ulash) kerak emas — internal
// pull-up buni o'zi qiladi. Agar tugma sxemangizda BUNING TESKARISI
// (ya'ni bosilganda 3.3V/HIGH beradigan, "active-HIGH" turi) bo'lsa,
// pastdagi buttonPressedEdge() funksiyasidagi "LOW" so'zlarini "HIGH"ga
// almashtiring va pinMode'ni INPUT_PULLDOWN qiling.
// ============================================================================
const unsigned long BUTTON_DEBOUNCE_MS = 50;

ButtonState btnStartStop  = { BTN_START_STOP,  HIGH, HIGH, 0 };
ButtonState btnAlarmReset = { BTN_ALARM_RESET, HIGH, HIGH, 0 };
ButtonState btnMode1      = { BTN_MODE1,       HIGH, HIGH, 0 };
ButtonState btnMode2      = { BTN_MODE2,       HIGH, HIGH, 0 };

// Faqat HAQIQIY "bosish payti" (HIGH->LOW o'tish, 50ms barqaror turgach)da
// true qaytaradi — bir marta bosishda bir marta true, tugma qancha uzoq
// ushlab turilmasin qayta-qayta true qaytarmaydi.
bool buttonPressedEdge(ButtonState &b) {
  bool reading = digitalRead(b.pin);
  if (reading != b.lastReading) {
    b.lastChangeTime = millis();
    b.lastReading = reading;
  }
  if (millis() - b.lastChangeTime > BUTTON_DEBOUNCE_MS) {
    if (reading != b.stableState) {
      b.stableState = reading;
      if (b.stableState == LOW) return true; // faqat bosilgan pallada
    }
  }
  return false;
}

// ============================================================================
// 15) SETUP VA MAIN LOOP
// ============================================================================
// ============================================================================
// YANGI: faqat o'qish uchun JSON status endpoint (PLC bot bilan integratsiya)
// ============================================================================
// GET /status -> joriy sensor qiymatlari va tizim holatini JSON qilib
// qaytaradi. Hech qanday sozlamani o'zgartirmaydi, hech qanday buyruqni
// qabul qilmaydi — butunlay xavfsiz, faqat "o'qish".
void handleStatusRequest() {
  String json = "{";
  json += "\"system_active\":" + String(systemActive ? "true" : "false") + ",";
  json += "\"monitoring_started\":" + String(monitoringStarted ? "true" : "false") + ",";
  json += "\"mode\":" + String(currentMode) + ",";
  json += "\"alarm_active\":" + String(alarmActive ? "true" : "false") + ",";
  json += "\"escalation_stage\":" + String(escalationStage) + ",";
  json += "\"air_pressure_bar\":" + String(airPressure, 2) + ",";
  json += "\"water_pressure_bar\":" + String(waterPressure, 2) + ",";
  json += "\"water_temp_c\":" + String(waterTemp, 2) + ",";
  json += "\"air_sensor_ok\":" + String(airSensorOK ? "true" : "false") + ",";
  json += "\"water_sensor_ok\":" + String(waterSensorOK ? "true" : "false") + ",";
  json += "\"temp_sensor_ok\":" + String(tempSensorOK ? "true" : "false") + ",";
  json += "\"limits\":{";
  json += "\"air_min\":" + String(minAirP, 2) + ",\"air_norm\":" + String(normAirP, 2) + ",\"air_max\":" + String(maxAirP, 2) + ",";
  json += "\"water_min\":" + String(minWaterP, 2) + ",\"water_norm\":" + String(normWaterP, 2) + ",\"water_max\":" + String(maxWaterP, 2) + ",";
  json += "\"temp_min\":" + String(minWaterTemp, 2) + ",\"temp_norm\":" + String(normWaterTemp, 2) + ",\"temp_max\":" + String(maxWaterTemp, 2);
  json += "},";
  json += "\"wifi_ssid\":\"" + WiFi.SSID() + "\",";
  json += "\"uptime_sec\":" + String(millis() / 1000);
  json += "}";

  statusServer.sendHeader("Access-Control-Allow-Origin", "*");
  statusServer.send(200, "application/json", json);
}

void setup() {
  disableCore1WDT();

  Serial.begin(115200);

  pinMode(BTN_START_STOP, INPUT_PULLUP);
  pinMode(BTN_ALARM_RESET, INPUT_PULLUP);
  pinMode(BTN_MODE1, INPUT_PULLUP);
  pinMode(BTN_MODE2, INPUT_PULLUP);
  pinMode(RELAY_PIN, OUTPUT);
  pinMode(SIREN_AIR_PIN, OUTPUT);
  pinMode(SIREN_WATER_PIN, OUTPUT);
  pinMode(SIREN_TEMP_PIN, OUTPUT);
  digitalWrite(SIREN_AIR_PIN, LOW);
  digitalWrite(SIREN_WATER_PIN, LOW);
  digitalWrite(SIREN_TEMP_PIN, LOW);
  analogReadResolution(12);

  // YANGI: tugma holatlarini HOZIRGI (real) pin qiymatlaridan boshlaymiz —
  // shunda ishga tushgan zahoti (agar tugma tasodifan bosilgan holatda
  // qolib ketgan bo'lsa ham) yolg'on "bosildi" signali kelmaydi.
  btnStartStop.lastReading  = digitalRead(BTN_START_STOP);
  btnStartStop.stableState  = btnStartStop.lastReading;
  btnAlarmReset.lastReading = digitalRead(BTN_ALARM_RESET);
  btnAlarmReset.stableState = btnAlarmReset.lastReading;
  btnMode1.lastReading      = digitalRead(BTN_MODE1);
  btnMode1.stableState      = btnMode1.lastReading;
  btnMode2.lastReading      = digitalRead(BTN_MODE2);
  btnMode2.stableState      = btnMode2.lastReading;

  Wire.begin(21, 22);
  lcd.init(); lcd.backlight();
  lcd.setCursor(0, 0); lcd.print("Wi-Fi ulanmoqda...");

  if (!LittleFS.begin(true)) Serial.println("LittleFS ishga tushmadi!");

  prefs.begin("factory", false);
  loadSettings();
  tempSensors.begin();
  tempSensorPresent = (tempSensors.getDeviceCount() > 0);
  if (!tempSensorPresent) Serial.println("⚠️ DS18B20 topilmadi! Simlarni tekshiring.");
  tempSensors.setWaitForConversion(false);
  tempSensors.requestTemperatures();
  applyRelay();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  wifiBootStartedAt = millis();
  wifiReconnect();

  client.setInsecure();
  client.setTimeout(6000);
  bot.longPoll = 0;

  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);

  updateLCDDisplay();

  unsigned long firstWifiWait = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - firstWifiWait < 8000) {
    delay(50);
  }
  if (WiFi.status() == WL_CONNECTED) {
    lastWifiBeginAttempt = millis();
    Serial.println("Wi-Fi ulandi: " + WiFi.localIP().toString());
    checkBotToken();
    sendTG(superAdmin.id, "🚀 <b>Zavod Monitoring Tizimi Ishga Tushdi!</b>\nWi-Fi: <code>" + WiFi.SSID() + "</code>", adminKeyboard);

    // YANGI: faqat o'qish uchun status-serverni ishga tushiramiz (Wi-Fi
    // ulangandan keyin, chunki IP-manzil shu payt aniq bo'ladi).
    statusServer.on("/status", HTTP_GET, handleStatusRequest);
    statusServer.begin();
    Serial.println("📡 Status-server ishga tushdi: http://" + WiFi.localIP().toString() + "/status");
  } else {
    Serial.println("⚠️ Wi-Fi hozircha ulanmagan. Tizim qotmasdan ishlashda davom etadi va fonda qayta ulanadi.");
  }
  last2HourReport = millis();
  lastHourLog = millis();
  resetStats();
}

void loop() {
  unsigned long now = millis();

  // YANGI: status-so'rovlarga javob berish (juda tez, boshqa hech narsani
  // kutdirmaydi — faqat kimdir /status manziliga so'rov yuborgandagina
  // ishlaydi).
  statusServer.handleClient();

  serviceWifiConnection();

  if (now - lastWifiCheck > 10000) {
    lastWifiCheck = now;
    if (WiFi.status() != WL_CONNECTED && (now - lastWifiBeginAttempt > WIFI_RECONNECT_MIN_GAP)) {
      wifiReconnect();
    }
  }

  processMessageQueue();

  // YANGI: 4 tugma endi buttonPressedEdge() orqali — har biri MUSTAQIL,
  // har biri FAQAT bosilgan zahoti (bir marta) ishga tushadi, tugma qancha
  // uzoq ushlab turilmasin takrorlanmaydi. Batafsil izoh yuqorida, 14b
  // bo'limida.
  if (buttonPressedEdge(btnStartStop)) {
    systemActive = !systemActive;
    applyRelay();
    if (systemActive) {
      monitoringStarted = false;
      alarmActive = false;
      escalationStage = 0;
      allSirensOff();
      updateLCDDisplay();
      sendTG(superAdmin.id, "🔘 <b>Knopka: Tizim ishga tushdi!</b>\n👉 Endi avtomatik ish jarayonini boshlash uchun <b>1-Rejim</b> yoki <b>2-Rejim</b> tugmasini bosing.", adminKeyboard);
      Serial.println("🔘 START/STOP tugmasi: TIZIM ISHGA TUSHDI.");
      Serial.println("👉 Monitoringni boshlash uchun BTN_MODE1 yoki BTN_MODE2 bosing.");
    } else {
      monitoringStarted = false;
      alarmActive = false; escalationStage = 0;
      allSirensOff();
      updateLCDDisplay();
      sendTG(superAdmin.id, "🔘 <b>Knopka: Tizim to'xtatildi!</b>", adminKeyboard);
    }
  }

  if (buttonPressedEdge(btnMode1)) {
    if (systemActive) {
      startSelectedMode(1, "1-Rejim tugmasi");
    } else {
      sendTG(superAdmin.id, "⏸️ Tizim to'xtatilgan. Avval Start tugmasini bosing.", adminKeyboard);
      Serial.println("⚠️ 1-Rejim tugmasi bosildi, lekin tizim STOP holatida.");
    }
  }

  if (buttonPressedEdge(btnMode2)) {
    if (systemActive) {
      startSelectedMode(2, "2-Rejim tugmasi");
    } else {
      sendTG(superAdmin.id, "⏸️ Tizim to'xtatilgan. Avval Start tugmasini bosing.", adminKeyboard);
      Serial.println("⚠️ 2-Rejim tugmasi bosildi, lekin tizim STOP holatida.");
    }
  }

  if (buttonPressedEdge(btnAlarmReset)) {
    if (alarmActive) {
      resolveAlarm("Uskuna tugmasi (mahalliy reset)");
      // Eslatma: bu ham faqat Telegram eskalatsiyasini to'xtatadi;
      // sirenalar haqiqiy sensor holatiga qarab davom etadi.
    }
  }

  if (now - lastBotCheck > 1000 && WiFi.status() == WL_CONNECTED) {
    int numNew = bot.getUpdates(bot.last_message_received + 1);
    if (numNew > 0) handleTelegramMessages(numNew);
    lastBotCheck = now;
  }

  if (systemActive && monitoringStarted && (now - last2HourReport >= TWO_HOURS_MS)) {
    sendPeriodicReport();
    last2HourReport = now;
  }

  if (systemActive && monitoringStarted && (now - lastHourLog >= 3600000)) {
    logHourlyAverage();
    lastHourLog = now;
  }

  if (now - lastLcdPage >= 30000) {
    cycleLcdPage();
    lastLcdPage = now;
  }

  static unsigned long lastExportCheck = 0;
  if (now - lastExportCheck > 60000) {
    checkMonthlyExport();
    lastExportCheck = now;
  }

  if (systemActive) {
    if (now - lastSensorRead > 1500) {
      readSensors();
      if (lcdPage == 0) updateLCDDisplay();
      printSerialMonitoring();

      bool airBad = (!airSensorOK || airPressure < minAirP || airPressure > maxAirP);
      airBadStreak = airBad ? airBadStreak + 1 : 0;

      bool waterTempBad = false;
      if (currentMode == 1) {
        bool waterBad = (!waterSensorOK || waterPressure < minWaterP || waterPressure > maxWaterP);
        bool tBad = (!tempSensorOK || waterTemp < minWaterTemp || waterTemp > maxWaterTemp);
        waterBadStreak = waterBad ? waterBadStreak + 1 : 0;
        tempBadStreak = tBad ? tempBadStreak + 1 : 0;
        waterTempBad = (waterBadStreak >= BAD_STREAK_LIMIT) || (tempBadStreak >= BAD_STREAK_LIMIT);
      } else {
        waterBadStreak = 0; tempBadStreak = 0;
      }

      bool isEmergency = (airBadStreak >= BAD_STREAK_LIMIT) || waterTempBad;

      if (monitoringStarted) {
        bool airSirenOn   = (airBadStreak >= BAD_STREAK_LIMIT);
        bool waterSirenOn = (currentMode == 1) && (waterBadStreak >= BAD_STREAK_LIMIT);
        bool tempSirenOn  = (currentMode == 1) && (tempBadStreak >= BAD_STREAK_LIMIT);
        updateSirens(airSirenOn, waterSirenOn, tempSirenOn);
      } else {
        allSirensOff();
      }

      if (isEmergency && !alarmActive && monitoringStarted) {
        triggerEmergency();
      }

      lastSensorRead = now;
    }

    // YANGI: eskalatsiya bosqichlari endi HAR BIRI O'Z ALOHIDA kutish
    // vaqtiga ega (STAGE1_WAIT/STAGE2_WAIT/STAGE3_WAIT/STAGE4_REPEAT_WAIT) —
    // yuqoridagi konstantalar orqali sozlanadi.
    if (monitoringStarted && alarmActive) {
      unsigned long stageWait;
      if (escalationStage == 1) stageWait = STAGE1_WAIT;
      else if (escalationStage == 2) stageWait = STAGE2_WAIT;
      else if (escalationStage == 3) stageWait = STAGE3_WAIT;
      else stageWait = STAGE4_REPEAT_WAIT;

      if (now - stageStartTime >= stageWait) {
        stageStartTime = now;
        escalationStage++;

        Staff currentMaster = isDayShift() ? dayMaster : nightMaster;

        if (escalationStage == 2) {
          sendAlarmMessage(currentMaster.id, "2-Bosqich: Smena Boshlig'i (" + currentMaster.name + ")");
        } else if (escalationStage == 3) {
          sendAlarmMessage(engineer.id, "3-Bosqich: Inzhener (" + engineer.name + ")");
          sendAlarmMessage(manager.id, "3-Bosqich: Menedjer (" + manager.name + ")");
        } else {
          sendAlarmMessage(superAdmin.id, "⏳ Hali hal qilinmadi! Super Admin (" + String(escalationStage) + "-eslatma)");
          for (int i = 0; i < adminCount; i++)
            sendAlarmMessage(admins[i].id, "⏳ Hali hal qilinmadi! Admin (" + String(escalationStage) + "-eslatma)");
        }
      }
    }
  } else {
    allSirensOff();
  }
}
