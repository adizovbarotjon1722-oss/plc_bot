# -*- coding: utf-8 -*-
"""
bot.py
------
Ishlab chiqarish liniyalaridagi PLC xatoliklarini aniqlashda yordam beruvchi
Telegram bot. 3 tilni qo'llab-quvvatlaydi (o'zbek, ingliz, xitoy) — xodim
tilni tanlaydi, bot interfeysi shu tilda ko'rsatiladi; shu bilan birga AI
javobi xodim savolini qaysi (shu 3 tildan biri) tilda yozgan bo'lsa, o'sha
tilda ham javob berishga harakat qiladi.

Bir nechta uskuna/liniyani qo'llab-quvvatlaydi, har biri uchun faqat kerakli
PLC taglarni topib (butun ro'yxatni emas) AI'ga yuboradi — bu token sarfini
keskin kamaytiradi va tezlik/aniqlikni oshiradi.

AI qism 3 ta bepul provayderni ketma-ket sinaydi (Gemini -> Groq ->
OpenRouter). Biror provayder limitga uchrasa, vaqtincha "dam oladi" va
avtomatik ravishda navbatdagisi ishlatiladi. Agar barcha AI'lar band
bo'lsa, bot baribir bazadan topgan xom ma'lumotni ko'rsatadi.

Shuningdek alohida "Sun'iy intellekt" bo'limi bor — PLC bilan bog'liq
bo'lmagan har qanday savolga ham javob beradi.
"""

import os
import sys
import atexit
import re
import json
import time
import uuid
import asyncio
import logging
import traceback
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from collections import Counter

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    PicklePersistence,
)

import google.genai as genai

try:
    import pymupdf
except ImportError:
    pymupdf = None
from google.genai import types as genai_types

try:
    from groq import Groq
except ImportError:
    Groq = None

# ---------------------------------------------------------------------------
# Sozlamalar
# ---------------------------------------------------------------------------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
# --- Qo'shimcha AI'lar (OpenAI, Claude, DeepSeek, Mistral, Cerebras, Together, xAI, Cohere) ---
# Har bir provayder FAQAT .env'da API kaliti bo'lsa faollashadi. Biri chetlasa,
# keyingisi avomatik ishga tushadi (failover). Kalit yo'q bo'lsa — umuman qo'shilmaydi.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "llama-3.3-70b")
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
TOGETHER_MODEL = os.getenv("TOGETHER_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free")
XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-2-latest")
COHERE_API_KEY = os.getenv("COHERE_API_KEY")
COHERE_MODEL = os.getenv("COHERE_MODEL", "command-r-08-2024")
# AI HTTP so'rovi uchun kutish vaqti (soniya)
AI_HTTP_TIMEOUT = int(os.getenv("AI_HTTP_TIMEOUT", "30"))
LINES_CONFIG_PATH = os.getenv("LINES_CONFIG_PATH", "lines.json")
PERSISTENCE_PATH = os.getenv("PERSISTENCE_PATH", "bot_state.pickle")
MAX_CANDIDATE_TAGS = int(os.getenv("MAX_CANDIDATE_TAGS", "25"))
CACHE_PATH = os.getenv("CACHE_PATH", "answer_cache.json")
CACHE_TTL_HOURS = float(os.getenv("CACHE_TTL_HOURS", "72"))
MIN_LOCAL_CANDIDATES = int(os.getenv("MIN_LOCAL_CANDIDATES", "3"))
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "3"))
RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_PER_DAY = int(os.getenv("RATE_LIMIT_PER_DAY", "30"))

# --- Kirishni cheklash (ixtiyoriy) ---
ADMIN_USER_IDS = {int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()}
ALLOWED_USERS_PATH = os.getenv("ALLOWED_USERS_PATH", "allowed_users.json")

# --- Haftalik statistika ---
STATS_CHAT_ID = os.getenv("STATS_CHAT_ID", "").strip()
STATS_CHAT_ID = int(STATS_CHAT_ID) if STATS_CHAT_ID.lstrip("-").isdigit() else None

# --- Ma'lumot sifati (mos kelmagan so'rovlar) ---
NO_MATCH_LOG_PATH = os.getenv("NO_MATCH_LOG_PATH", "no_match.log")
PENDING_REG_PATH = os.getenv("PENDING_REG_PATH", "pending_registrations.json")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))

# --- Ichki kutubxona (faqat admin yuklaydi, faqat ruxsatli foydalanuvchilar ko'radi) ---
LIBRARY_DOCS_PATH = os.getenv("LIBRARY_DOCS_PATH", "library_docs.json")
LIBRARY_FILES_DIR = os.getenv("LIBRARY_FILES_DIR", "library_files")
MAX_LIBRARY_FILE_MB = float(os.getenv("MAX_LIBRARY_FILE_MB", "50"))
# Telegram Bot API fayl YUKLAB OLISH chegarasi 20 MB — bundan katta fayl
# diskka saqlanmaydi, faqat file_id orqali qayta yuboriladi (50 MB gacha ishlaydi).
TG_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024
# Xavfli fayl turlari (dastur/skript) — kutubxonaga yuklash qat'iyan taqiqlangan.
LIB_DENY_EXT = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".msi", ".dll", ".vbs",
    ".vbe", ".js", ".jse", ".wsf", ".wsh", ".ps1", ".psm1", ".sh", ".apk",
    ".jar", ".lnk", ".reg", ".hta", ".cpl", ".inf", ".sys", ".msp", ".gadget",
}
# Registratsiya spam himoyasi
REG_RATE_LIMIT_PER_HOUR = int(os.getenv("REG_RATE_LIMIT_PER_HOUR", "3"))

# --- AI token sarfini nazorat qilish ---
AI_MAX_OUTPUT_TOKENS = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "400"))
AI_MAX_INPUT_CHARS = int(os.getenv("AI_MAX_INPUT_CHARS", "1500"))


# --- Bot salomatligini kuzatish (ixtiyoriy, masalan healthchecks.io) ---
HEALTHCHECK_PING_URL = os.getenv("HEALTHCHECK_PING_URL", "").strip()
HEALTHCHECK_INTERVAL_MIN = int(os.getenv("HEALTHCHECK_INTERVAL_MIN", "5"))

# --- ESP32 zavod monitoring integratsiyasi (ixtiyoriy) ---
# ESP32'dagi /status endpoint manzili, masalan: http://192.168.1.50/status
ESP32_STATUS_URL = os.getenv("ESP32_STATUS_URL", "").strip()
ESP32_TIMEOUT_SEC = int(os.getenv("ESP32_TIMEOUT_SEC", "5"))
# SSRF himoyasi: faqat http/https sxemasi ruxsat etilgan
if ESP32_STATUS_URL and not ESP32_STATUS_URL.startswith(("http://", "https://")):
    ESP32_STATUS_URL = ""

# --- BITTA TELEGRAM BOT REJIMI (ESP32 relay) ---
# Xabarlarni faqat shu Python bot qabul qiladi. ESP32'ga tegishli buyruq/tugmalar
# ESP32'dagi /tg endpoint'iga maxfiy kalit bilan yetkaziladi (relay). Kalit
# ESP32 sketch'dagi TG_RELAY_KEY bilan bir xil bo'lishi kerak.
ESP32_CMD_KEY = os.getenv("ESP32_CMD_KEY", "plc-esp32-relay-2026").strip()
# /tg manzili ESP32_STATUS_URL'dan olinadi (masalan .../status -> .../tg)
ESP32_CMD_URL = ""
if ESP32_STATUS_URL and ESP32_CMD_KEY:
    ESP32_CMD_URL = ESP32_STATUS_URL.rsplit("/", 1)[0] + "/tg"
# ESP32'ning Telegram tugma/buyruqlari (relay qilinadi) — sketch'dagi matnlar bilan bir xil
ESP32_TEXT_COMMANDS = {
    "📊 Holat", "🛠️ Sozlamalar", "🚀 Start", "⏸️ Stop", "👥 Xodimlar",
    "🔑 Adminlar", "🌐 1-Rejim", "💨 2-Rejim", "📶 Wi-Fi", "🔓 Ruxsat so'rash",
    "/status", "/control", "/staff", "/admins", "/wifi", "/wifireset", "/menu",
    "/stop", "/mode1", "/mode2",
}

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN topilmadi. .env faylni tekshiring.")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY topilmadi. .env faylni tekshiring.")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("plc-fault-bot")


def atomic_json_write(path: str, data, indent=None) -> None:
    """Race-safe JSON write: temp file + os.replace."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def atomic_text_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


QUERY_LOG_PATH = os.getenv("QUERY_LOG_PATH", "queries.log")

genai_client = genai.Client(api_key=GEMINI_API_KEY)

groq_client = Groq(api_key=GROQ_API_KEY) if (GROQ_API_KEY and Groq) else None
if GROQ_API_KEY and not Groq:
    logger.warning("GROQ_API_KEY berilgan, lekin 'groq' kutubxonasi o'rnatilmagan (faqat ovoz transkripsiya uchun kerak; matnli AI urllib orqali ishlaydi).")

# Eslatma: matnli AI generatsiyasi endi SDK'siz, urllib orqali bajariladi
# (quyidagi _openai_compatible_chat). OpenRouter uchun alohida openai klienti shart emas.

# ---------------------------------------------------------------------------
# Kirishni cheklash: agar ADMIN_USER_IDS bo'sh bo'lsa, bot hammaga ochiq
# (orqaga moslik uchun standart holat). ADMIN_USER_IDS to'ldirilsa, faqat
# adminlar va ular ruxsat bergan foydalanuvchilar botdan foydalana oladi.
# ---------------------------------------------------------------------------

# Majburiy kirish nazorati: ADMIN_USER_IDS bo'sh bo'lsa bot ishga tushmaydi.
ACCESS_CONTROL_ENABLED = True
if not ADMIN_USER_IDS:
    raise RuntimeError(
        "XAVFSIZLIK: ADMIN_USER_IDS .env da bo'sh. "
        "Botga kirish uchun kamida bitta admin Telegram ID kiriting "
        "(masalan ADMIN_USER_IDS=123456789)."
    )

# ALLOWED_USERS: {user_id (int): {"name": str, "added_at": iso-str}}
# Eski formatdan (ID'lar ro'yxati) ham avtomatik o'tkaziladi.
try:
    with open(ALLOWED_USERS_PATH, "r", encoding="utf-8") as f:
        _raw = json.load(f)
    if isinstance(_raw, list):
        ALLOWED_USERS = {int(uid): {"name": str(uid), "added_at": ""} for uid in _raw}
    else:
        ALLOWED_USERS = {int(uid): info for uid, info in _raw.items()}
except (FileNotFoundError, json.JSONDecodeError):
    ALLOWED_USERS = {}


def _save_allowed_users():
    try:
        atomic_json_write(
            ALLOWED_USERS_PATH,
            {str(uid): info for uid, info in ALLOWED_USERS.items()},
        )
    except Exception as e:
        logger.warning("allowed_users saqlashda xatolik: %s", e)



# ---------------------------------------------------------------------------
# Ichki kutubxona: admin yuklaydi, faqat ruxsat berilgan foydalanuvchilar ko'radi
# ---------------------------------------------------------------------------

os.makedirs(LIBRARY_FILES_DIR, exist_ok=True)

try:
    with open(LIBRARY_DOCS_PATH, "r", encoding="utf-8") as f:
        _lib_raw = json.load(f)
    LIBRARY_DOCS = _lib_raw.get("docs", []) if isinstance(_lib_raw, dict) else (_lib_raw if isinstance(_lib_raw, list) else [])
except (FileNotFoundError, json.JSONDecodeError):
    LIBRARY_DOCS = []

# --- Ingliz tili kursi (yangi xodimlarning til malakasini oshirish uchun) ---
ENG_COURSE_PATH = os.getenv("ENG_COURSE_PATH", "english_course.json")
ENG_QUIZ_PASS = float(os.getenv("ENG_QUIZ_PASS", "0.6"))
try:
    with open(ENG_COURSE_PATH, "r", encoding="utf-8") as f:
        _course_raw = json.load(f)
    ENG_COURSE = _course_raw.get("lessons", []) if isinstance(_course_raw, dict) else (
        _course_raw if isinstance(_course_raw, list) else []
    )
except (FileNotFoundError, json.JSONDecodeError):
    ENG_COURSE = []

_reg_request_times = {}  # uid -> [timestamps] spam himoyasi


def _save_library_docs():
    try:
        atomic_json_write(LIBRARY_DOCS_PATH, {"docs": LIBRARY_DOCS}, indent=2)
    except Exception as e:
        logger.warning("library_docs saqlashda xatolik: %s", e)


def library_list_visible():
    """Ruxsatli foydalanuvchiga ko'rinadigan hujjatlar (visible=True)."""
    return [d for d in LIBRARY_DOCS if d.get("visible", True)]


def library_get(doc_id: str):
    for d in LIBRARY_DOCS:
        if d.get("id") == doc_id:
            return d
    return None


def library_add(doc: dict):
    LIBRARY_DOCS.append(doc)
    _save_library_docs()


def library_delete(doc_id: str) -> bool:
    global LIBRARY_DOCS
    before = len(LIBRARY_DOCS)
    LIBRARY_DOCS = [d for d in LIBRARY_DOCS if d.get("id") != doc_id]
    if len(LIBRARY_DOCS) < before:
        _save_library_docs()
        return True
    return False


def check_reg_rate_limit(user_id: int) -> bool:
    """Registratsiya spam: soatiga REG_RATE_LIMIT_PER_HOUR dan oshmasin."""
    now = time.time()
    hits = [ts for ts in _reg_request_times.get(user_id, []) if now - ts < 3600]
    if len(hits) >= REG_RATE_LIMIT_PER_HOUR:
        _reg_request_times[user_id] = hits
        return False
    hits.append(now)
    _reg_request_times[user_id] = hits
    return True


_denied_hits = {}  # uid -> [timestamps] — ruxsatsiz urinishlar


def should_answer_denied(user_id: int) -> bool:
    """Ruxsatsiz foydalanuvchiga javob berish kerakmi? Soatiga 5 martadan
    ko'p urinish qilganlarga jim e'tibor berilmaydi (spam/probing himoyasi —
    bot haqida ma'lumot yig'ishni qiyinlashtiradi)."""
    now = time.time()
    hits = [ts for ts in _denied_hits.get(user_id, []) if now - ts < 3600]
    hits.append(now)
    _denied_hits[user_id] = hits
    if len(hits) == 6:
        logger.warning("Ruxsatsiz foydalanuvchi %s spam qilmoqda — javoblar o'chirildi", user_id)
    return len(hits) <= 5


def sanitize_filename(name: str) -> str:
    """Xavfli belgilarni olib tashlash — path traversal himoyasi."""
    name = os.path.basename(name or "file")
    name = re.sub(r"[^\w.\- ()\u0400-\u04FF\u4e00-\u9fff]+", "_", name, flags=re.UNICODE)
    return name[:120] or "file"


def safe_callback_data(prefix: str, *parts) -> str:
    """Callback data 64 baytdan oshmasin (Telegram limiti)."""
    raw = prefix + ":" + ":".join(str(p) for p in parts)
    return raw[:64]


def is_authorized(user_id: int) -> bool:
    # Doimo ruxsat tekshiruvi — begona foydalanuvchilar hech narsa ko'rmaydi
    return user_id in ADMIN_USER_IDS or user_id in ALLOWED_USERS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def employee_name(user_id: int) -> str:
    info = ALLOWED_USERS.get(user_id)
    return info["name"] if info else str(user_id)


def employee_lang(context: ContextTypes.DEFAULT_TYPE, uid: int) -> str:
    try:
        return context.application.user_data.get(uid, {}).get("lang", "uz")
    except Exception:
        return "uz"


# ---------------------------------------------------------------------------
# Tillar va tarjimalar
# ---------------------------------------------------------------------------

LANG_NAME = {"uz": "o'zbek", "en": "ingliz (English)", "zh": "xitoy (中文)"}
LANG_BUTTON = {"uz": "🇺🇿 O'zbek tili", "en": "🇬🇧 English", "zh": "🇨🇳 中文"}
LANG_BUTTON_TO_CODE = {v: k for k, v in LANG_BUTTON.items()}
LANG_CHANGE_LABEL = "🌐 Til / Language / 语言"

KIND_LABELS = {
    "uz": {"input": "Kiruvchi signal (I)", "output": "Chiquvchi signal (Q)",
           "memory": "Ichki bit (M)", "db": "Data blok (DB)", "other": "Boshqa"},
    "en": {"input": "Input signal (I)", "output": "Output signal (Q)",
           "memory": "Internal bit (M)", "db": "Data block (DB)", "other": "Other"},
    "zh": {"input": "输入信号 (I)", "output": "输出信号 (Q)",
           "memory": "内部位 (M)", "db": "数据块 (DB)", "other": "其他"},
}

HEADER_LABELS = {
    "uz": {"address": "Ehtimoliy manzil", "what": "Nima bu", "action": "Nima qilish kerak"},
    "en": {"address": "Likely address", "what": "What this is", "action": "What to do"},
    "zh": {"address": "可能的地址", "what": "这是什么", "action": "该怎么做"},
}

TEXT = {
    "uz": {
        "choose_lang": "Tilni tanlang / Please choose language / 请选择语言:",
        "lang_selected": "✅ Til tanlandi: {label}",
        "greeting_after_lang": (
            "Qaysi uskuna/liniya haqida ekanini tanlang, yoki erkin savol uchun "
            "\"{ai}\" tugmasini bosing:"
        ),
        "returning_greeting": (
            "Salom! Hozir tanlangan uskuna: *{machine}*.\n\n"
            "Muammoni yozing yoki manzilni kiriting (masalan I0.1). "
            "Boshqasiga o'tish uchun pastdagi tugmalardan birini bosing."
        ),
        "choose_machine_prompt": "Qaysi uskuna/liniyani tanlaysiz?",
        "machine_selected": (
            "✅ Tanlandi: *{machine}*\n\n"
            "Endi uskunada ko'rgan muammoni yozing (masalan \"3-stansiyada "
            "konveyer ishlamayapti\") yoki shunchaki manzilni yozing (masalan I0.1)."
        ),
        "ai_mode_intro": (
            "🤖 Sun'iy intellekt bo'limi. Endi istalgan savolingizni yozing — "
            "PLC bilan bog'liq bo'lishi shart emas."
        ),
        "no_machine_selected": (
            "Qaysi uskuna/liniya haqida ekanini tanlang, yoki erkin savol uchun "
            "\"{ai}\" tugmasini bosing:"
        ),
        "addr_not_found": "'{addr}' manzili '{machine}' bazasida topilmadi. Boshqa uskuna tanlaganmisiz, tekshirib ko'ring.",
        "no_match": "Aniq mos signal topa olmadim. Iltimos, qaysi stansiya/robot ekanini yoki aniq manzilni (masalan I0.1) yozing.",
        "ai_busy_general": "Kechirasiz, hozircha barcha AI xizmatlari band. Bir necha daqiqadan so'ng qayta urinib ko'ring.",
        "tag_usage": "Foydalanish: /tag %I0.5",
        "tag_not_found": "'{addr}' manzili '{machine}' bazasida topilmadi.",
        "choose_machine_first_tag": "Avval uskunani tanlang:",
        "status_header": "*AI provayderlar holati:*",
        "status_ready": "✅ {name} — tayyor",
        "status_cooldown": "⏳ {name} — ~{sec} soniyadan keyin qayta urinadi",
        "raw_fallback_header": "⚠️ Hozircha AI xizmatlari band, lekin bazadan topilgan ma'lumot ({machine}):\n",
        "raw_fallback_footer": "\nBiroz vaqtdan so'ng qayta yozib ko'ring — AI ishga tushsa, to'liq tushuntirish beradi.",
        "station_n": "{n}-stansiya",
        "general_scope": "umumiy",
        "unexpected_error": "Kutilmagan xatolik yuz berdi. Iltimos, qayta urinib ko'ring.",
        "tag_details": (
            "📍 **{addr}** ({dtype})\n"
            "Uskuna: {machine}\n"
            "Turi: {kind}\n"
            "Tegishli: {scope} | Guruh: {group}\n"
            "Nomi: {name}\n"
            "Izoh: {comment}"
        ),
        "access_denied": "⛔ Kirish taqiqlangan. Ro'yxatdan o'ting: /register Ism +99890... — admin tasdiqlashi shart.",
        "user_added": "✅ Foydalanuvchi {uid} ro'yxatga qo'shildi.",
        "user_removed": "✅ Foydalanuvchi {uid} ro'yxatdan o'chirildi.",
        "admin_only": "Bu buyruq faqat administrator uchun.",
        "adduser_usage": "Foydalanish: /adduser <telegram_id> <ism (ixtiyoriy)>",
        "users_panel_title": "👥 Foydalanuvchilar boshqaruvi\nRo'yxatdan o'tganlar: {count} ta. O'chirish uchun 🗑 bosing.",
        "users_panel_admins": "👑 Adminlar: {admins}",
        "users_panel_pending": "⏳ Tasdiq kutayotgan so'rovlar: {count}",
        "users_panel_empty": "Hozircha ro'yxatdan o'tgan foydalanuvchi yo'q.",
        "users_del_confirm": "🗑 {name} ({uid}) o'chirilsinmi?",
        "users_yes": "✅ Ha, o'chirish",
        "users_no": "↩️ Orqaga",
        "users_deleted": "🗑 Foydalanuvchi o'chirildi: {uid}",
        "users_close": "✖️ Yopish",
        "users_page": "{page}/{total}",
        "feedback_prompt": "💬 Bu javob foydali bo'ldimi?",
        "feedback_thanks_up": "Rahmat! ✅",
        "feedback_thanks_down": "Xabar uchun rahmat, buni yaxshilashga harakat qilamiz. 🙏",
        "photo_processing": "🖼 Rasmni o'qiyapman...",
        "photo_extracted": "📷 Rasmdan o'qildi: \"{text}\"",
        "esp32_fetching": "📡 ESP32'dan ma'lumot olinmoqda...",
        "esp32_unreachable": "⚠️ ESP32 qurilmasiga ulanib bo'lmadi. Wi-Fi yoki qurilma o'chgan bo'lishi mumkin.",
        "esp32_not_configured": "Bu bo'lim hali sozlanmagan (administrator ESP32_STATUS_URL'ni to'ldirishi kerak).",
        "esp32_status": (
            "🏭 *Zavod monitoring holati*\n\n"
            "⚙️ Tizim: {active}\n"
            "🔘 Rejim: {mode}\n"
            "🚨 Signalizatsiya: {alarm}\n\n"
            "💨 Havo bosimi: *{air} bar* (norma: {air_norm}, chegara: {air_min}–{air_max})\n"
            "💧 Suv bosimi: *{water} bar* (norma: {water_norm}, chegara: {water_min}–{water_max})\n"
            "🌡 Suv harorati: *{temp} °C* (norma: {temp_norm}, chegara: {temp_min}–{temp_max})\n\n"
            "Sensorlar: havo {air_sensor} | suv {water_sensor} | harorat {temp_sensor}\n"
            "📶 Wi-Fi: {wifi} | Ishlab turgan vaqt: {uptime}"
        ),
        "esp32_on": "yoqilgan ✅", "esp32_off": "o'chirilgan ⏸️",
        "esp32_alarm_yes": "faol 🚨", "esp32_alarm_no": "yo'q ✅",
        "esp32_sensor_ok": "✅", "esp32_sensor_bad": "❌",
        "help_text": (
            "ℹ️ *Botdan qanday foydalanish:*\n\n"
            "1️⃣ Pastdagi tugmalardan uskuna/liniyani tanlang.\n"
            "2️⃣ Muammoni yozing (masalan \"konveyer ishlamayapti\") yoki "
            "manzilni yozing (masalan I0.1).\n"
            "📷 Rasm (HMI ekrani/indikator) va 🎤 ovozli xabar ham qabul qilinadi.\n\n"
            "📚 *Kutubxona* — kitob/qo'llanmalar va qo'llanma bo'yicha qidiruv.\n"
            "🇬🇧 *Ingliz tili kursi* — yangi xodimlar uchun texnik ingliz tili darslari.\n"
            "🤖 *Sun'iy intellekt* — PLC'ga bog'liq bo'lmagan savollar.\n"
            "🏭 *Zavod monitoring* — kompressor/chiller holati.\n"
            "🌐 *Til* — istalgan vaqtda tilni almashtirish.\n"
            "❓ *Yordam* — shu xabar.\n\n"
            "Tezkor buyruqlar: /tag %I0.5 · /find <kalit so'z> · /register <ism> <telefon>"
        ),
        "already_registered": "Siz allaqachon ro'yxatdasiz.",
        "register_usage": "Foydalanish: /register <ismingiz> <telefon_raqamingiz>\nMasalan: /register Aziz Karimov +998901234567",
        "register_admin_notice": "🆕 *Yangi ro'yxatdan o'tish so'rovi*\nIsm: {name}\nTelefon: {phone}\nID: {uid}",
        "register_sent": "✅ So'rovingiz adminga yuborildi. Tasdiqlangach xabar beriladi.",
        "register_already_handled": "Bu so'rov allaqachon ko'rib chiqilgan.",
        "register_approved_admin": "✅ {name} qabul qilindi.",
        "register_approved_user": "🎉 Tabriklaymiz! So'rovingiz qabul qilindi, endi botdan foydalanishingiz mumkin. /start yozing.",
        "register_rejected_admin": "❌ {name} rad etildi.",
        "register_rejected_user": "Kechirasiz, so'rovingiz rad etildi. Administratorga murojaat qiling.",
        "voice_processing": "🎤 Ovozli xabar tinglanmoqda...",
        "voice_failed": "Ovozli xabarni tushuna olmadim. Iltimos, matn bilan yozing.",
        "voice_too_long": "🎤 Ovozli xabar juda uzun (max 2 daqiqa). Qisqaroq yuboring yoki matn bilan yozing.",
        "voice_transcribed": "🎤 Eshitdim: \"{text}\"",
        "schematic_page_caption": "🔌 Elektr sxemasi — {machine}, {page}-sahifa",
        "library_select_machine_first": "Avval uskunani tanlang, so'ng qayta \"📚 Qo'llanma\" tugmasini bosing.",
        "library_no_manual_for_machine": "\"{machine}\" uchun qo'llanma hali yuklanmagan.",
        "library_none_available": "Hozircha hech qanday qo'llanma yuklanmagan.",
        "library_ask_topic": "📚 *{machine} qo'llanmasi*\nQaysi mavzuni qidiryapsiz? (masalan: \"moylash\", \"xavfsizlik to'ri sozlash\")",
        "library_no_results": "Qo'llanmadan bu mavzu bo'yicha hech narsa topa olmadim. Boshqacha so'z bilan yozib ko'ring.",
        "library_found": "📖 {count} ta tegishli sahifa topildi:",
        "lib_menu_title": "📚 *Kutubxona*\nHujjatni ochish uchun ustiga bosing:",
        "lib_empty": "Kutubxona hozircha bo'sh. Admin hujjat yuklagach paydo bo'ladi.",
        "lib_item": "• *{title}*\n  {desc}\n  ID: `{id}`",
        "lib_download_hint": "Hujjatni olish: /libget <ID>",
        "lib_get_usage": "Foydalanish: /libget <hujjat_id>",
        "lib_not_found": "Hujjat topilmadi yoki o'chirilgan.",
        "lib_no_access": "⛔ Bu bo'lim faqat ruxsat berilgan xodimlar uchun.",
        "lib_manual_search_btn": "🔎 Qo'llanmadan qidirish: {machine}",
        "lib_back_btn": "⬅️ Orqaga",
        "lib_del_confirm": "🗑 O'chirilsinmi: *{title}*",
        "lib_yes": "✅ Ha", "lib_no": "❌ Yo'q",
        "lib_admin_menu": (
            "🔐 *Admin — Kutubxona boshqaruvi*\n\n"
            "• Yuklash: /libadd <sarlavha> — so'ng istalgan turdagi kitob/qo'llanma "
            "faylini yuboring (PDF, DOC, EPUB, XLSX, TXT, rasm…, max {mb} MB)\n"
            "• Ro'yxat va o'chirish: /liblist\n"
            "• O'chirish: /libdel <ID>\n"
            "• Foydalanuvchi kutubxonasi: 📚 Kutubxona"
        ),
        "lib_add_usage": "Foydalanish:\n1) /libadd <sarlavha>\n2) Keyin istalgan hujjat/kitob faylini yuboring.",
        "lib_add_waiting": "✅ Sarlavha qabul qilindi: *{title}*\nEndi faylni yuboring — istalgan turdagi kitob/qo'llanma (PDF, DOC, EPUB, XLSX, TXT, rasm va b., max {mb} MB).",
        "lib_add_done": "✅ Kutubxonaga qo'shildi.\nSarlavha: *{title}*\nID: `{id}`",
        "lib_add_too_big": "Fayl juda katta (max {mb} MB).",
        "lib_add_bad_type": "Fayl yuklanmadi. Iltimos, hujjat/kitob faylini yuboring.",
        "lib_denied_type": "⛔ Xavfsizlik: dastur/skript fayllarini yuklash taqiqlangan. Faqat hujjat/kitob fayllari (PDF, DOC, EPUB, rasm…).",
        "lib_upload_admin_only": "📚 Kutubxonaga faqat administrator fayl yuklay oladi. Yuklatmoqchi bo'lsangiz, adminga murojaat qiling.",
        "lib_del_usage": "Foydalanish: /libdel <hujjat_id>",
        "lib_del_done": "🗑 O'chirildi: {title}",
        "lib_del_fail": "O'chirib bo'lmadi — ID topilmadi.",
        "lib_list_admin": "📋 *Kutubxona (admin)* — jami {n} ta:",
        "eng_menu_title": "🇬🇧 *Ingliz tili kursi*\n\nYangi xodimlar uchun texnik ingliz tili: zavod atamalari, xavfsizlik va kundalik ish muloqoti.\n\nBajarilgan darslar: {done}/{total}\n\nDarsni tanlang:",
        "eng_lesson_header": "📘 *{n}-dars: {title}*\n_{title_en}_",
        "eng_words_header": "📌 *Yangi so'zlar:*",
        "eng_sentences_header": "💬 *Jumlalar:*",
        "eng_quiz_btn": "📝 Testni boshlash",
        "eng_back_btn": "⬅️ Orqaga",
        "eng_retry_btn": "🔄 Testni qayta topshirish",
        "eng_quiz_q": "❓ *{q}*",
        "eng_quiz_correct": "✅ To'g'ri!",
        "eng_quiz_wrong": "❌ Noto'g'ri. To'g'ri javob: *{a}*",
        "eng_quiz_next": "Keyingi savol ▶️",
        "eng_quiz_result": "🏁 *Natija: {score}/{total}*\n{verdict}",
        "eng_verdict_pass": "A'lo! Dars yakunlandi ✅",
        "eng_verdict_fail": "Kamida 60% to'g'ri javob kerak — darsni qayta ko'rib, testni yana sinab ko'ring.",
        "eng_course_empty": "Ingliz tili kursi hozircha mavjud emas.",
        "access_denied_detail": "⛔ *Kirish taqiqlangan*\n\nBu bot faqat zavod xodimlari uchun.\nFoydalanish uchun admin ruxsati shart.\n\nRo'yxatdan o'tish:\n`/register Ism Familiya +998901234567`\n\nAdmin tasdiqlagach bot ochiladi.",
        "reg_rate_limited": "⏳ Juda ko'p so'rov yubordingiz. 1 soatdan keyin qayta urinib ko'ring.",
        "security_blocked": "⛔ Xavfsizlik: so'rov rad etildi.",

        "manual_page_caption": "📖 Qo'llanma — {machine}, {page}-sahifa",
        "manual_page_text": "📖 {page}-sahifa:\n{text}",
        "addcomment_usage": "Foydalanish: /addcomment <uskuna_id> <manzil> <izoh matni>\nMasalan: /addcomment gem %I0.1 Konveyer old sensori",
        "addcomment_bad_machine": "Noto'g'ri uskuna ID. Mavjudlari: {ids}",
        "addcomment_save_error": "Saqlashda xatolik yuz berdi, qaytadan urinib ko'ring.",
        "addcomment_done": "✅ {machine} — {addr} uchun izoh yangilandi: \"{comment}\"",
        "topfaults_empty": "So'nggi {days} kunda hali ma'lumot yo'q.",
        "topfaults_header": "📈 *So'nggi {days} kunlik statistika:*",
        "topfaults_by_machine": "*Uskuna bo'yicha:*",
        "topfaults_by_address": "*Eng ko'p so'ralgan manzillar:*",
        "rate_limited": "⏳ Bir daqiqada juda ko'p savol yubordingiz. Biroz kuting, yoki aniq manzilni bilsangiz /tag %I0.5 dan foydalaning (AI'siz, darhol javob beradi).",
        "find_usage": "Foydalanish: /find <kalit so'z>\nMasalan: /find konveyer",
        "find_no_results": "'{query}' bo'yicha hech narsa topilmadi.",
        "find_results_header": "🔎 '{query}' bo'yicha {count} ta natija:",
        "setphone_usage": "Foydalanish: /setphone <telegram_id> <telefon_raqam>",
        "phone_updated": "✅ {uid} uchun telefon raqami yangilandi: {phone}",
        "user_not_found": "Bunday ID ro'yxatda topilmadi.",

        # --- Yangi modullar: Ruxsat, ESP32 boshqaruv, Ingliz tili va Admin panel ---
        "req_access_btn": "Ruxsat so'rash (Ro'yxatdan o'tish)",
        "req_prompt": "📝 *Botdan foydalanish uchun ruxsat so'rash*\n\nIltimos, Ism va Familiyangiz hamda telefon raqamingizni yuboring.\nFormat: `/register Ism Familiya +998901234567`\nMasalan:\n`/register Aziz Karimov +998901234567`\n\nAdmin tasdiqlashi bilan barcha imkoniyatlar ochiladi.",
        "esp_refresh": "Yangilash",
        "esp_start": "Start (Yoqish)",
        "esp_stop": "Stop (To'xtatish)",
        "esp_mode1": "1-Rejim (Havo+Suv+Temp)",
        "esp_mode2": "2-Rejim (Faqat Havo)",
        "esp_reset_alarm": "Sirenani o'chirish",
        "esp_limits": "Limitlar",
        "esp_cmd_sent": "✅ Buyruq ESP32'ga yuborildi.",
        "esp_cmd_fail": "❌ ESP32 bilan aloqa o'rnatilmadi.",
        "eng_flashcards_btn": "🗂 Lug'at kartochkalari",
        "eng_ai_tutor_btn": "🤖 AI bilan inglizcha suhbatlashish",
        "eng_stats_btn": "📊 Mening natijalarim",
        "eng_fc_card": "🗂 *So'z kartochkasi* — {title}\n\n🇬🇧 *{en}*\n🇺🇿 {uz}\n🇨🇳 {zh}\n\n({cur}/{total})",
        "eng_fc_prev": "⬅️ Oldingisi",
        "eng_fc_next": "Keyingisi ➡️",
        "eng_stats_text": "📊 *Sizning Ingliz tili kursidagi natijalaringiz:*\n\n✅ Bajarilgan darslar: *{done}/{total}* ({pct}%)\n{badge}",
        "eng_tutor_intro": "🤖 *AI Ingliz tili murabbiyi (English Tutor)*\n\nMen bilan ingliz tilida erkin suhbatlashing yoki zavod atamalari bo'yicha savol bering. Xatolaringiz bo'lsa, xushmuomalalik bilan to'g'irlab boraman.\n\nSuhbatdan chiqish uchun /exit yoki pastdagi menyu tugmasini bosing.\n\n_Start typing in English: (masalan: Hello, what does 'solenoid valve' mean?)_",
        "eng_tutor_exit": "English Tutor rejimidan chiqildi. Asosiy menyudasiz.",
        "admin_panel_title": "⚙️ *Boshqaruv Paneli (Admin Hub)*\n━━━━━━━━━━━━━━━━━━━━\n👑 Adminlar: *{admins}*\n👥 Xodimlar: *{users}* ta\n⏳ Kutilayotgan arizalar: *{pending}* ta\n📚 Kutubxona: *{docs}* ta\n🏭 ESP32: *{esp32}*\n━━━━━━━━━━━━━━━━━━━━",
        "admin_btn_users": "👥 Xodimlar ro'yxati",
        "admin_btn_pending": "⏳ Kutilayotgan arizalar",
        "admin_btn_ai": "🤖 AI tizimlar holati",
        "admin_btn_esp32": "🏭 ESP32 Zavod boshqaruvi",
        "admin_btn_stats": "📊 Tizim statistikasi",
        "admin_btn_adduser": "➕ Yangi xodim qo'shish",
        "admin_uview_text": "👤 *Xodim ma'lumotlari:*\n\n• Ism: *{name}*\n• Telegram ID: `{uid}`\n• Telefon: `{phone}`\n• Qo'shilgan: `{added}`",
        "admin_udel_btn": "🗑 Xodimni o'chirish",
        "admin_back_btn": "⬅️ Orqaga",
        "admin_udel_confirm": "⚠️ Rostdan ham *{name}* (`{uid}`) ro'yxatdan o'chirilsinmi?",
        "admin_udel_yes": "✅ Ha, o'chirish",
        "admin_udel_no": "❌ Bekor qilish",
        "admin_pending_empty": "⏳ Hozircha kutilayotgan yangi arizalar yo'q.",
        "admin_pending_item": "🆕 *Ariza:*\n👤 Ism: *{name}*\n📞 Tel: `{phone}`\n🆔 ID: `{uid}`\n📅 Sana: `{date}`",
    },
    "en": {
        "choose_lang": "Tilni tanlang / Please choose language / 请选择语言:",
        "lang_selected": "✅ Language selected: {label}",
        "greeting_after_lang": (
            "Choose which machine/line you need help with, or tap "
            "\"{ai}\" to ask a free-form question:"
        ),
        "returning_greeting": (
            "Hi! Currently selected machine: *{machine}*.\n\n"
            "Describe the problem or enter an address (e.g. I0.1). "
            "Tap a button below to switch."
        ),
        "choose_machine_prompt": "Which machine/line do you want?",
        "machine_selected": (
            "✅ Selected: *{machine}*\n\n"
            "Now describe the problem you see on the equipment (e.g. \"conveyor "
            "not moving at station 3\") or just type an address (e.g. I0.1)."
        ),
        "ai_mode_intro": (
            "🤖 AI Assistant mode. Ask anything — it doesn't have to be "
            "related to PLC."
        ),
        "no_machine_selected": (
            "Choose which machine/line you need help with, or tap "
            "\"{ai}\" to ask a free-form question:"
        ),
        "addr_not_found": "Address '{addr}' was not found in the '{machine}' database. Check that you selected the right machine.",
        "no_match": "I couldn't find a clear match. Please specify the station/robot, or type the exact address (e.g. I0.1).",
        "ai_busy_general": "Sorry, all AI services are currently busy. Please try again in a few minutes.",
        "tag_usage": "Usage: /tag %I0.5",
        "tag_not_found": "Address '{addr}' was not found in the '{machine}' database.",
        "choose_machine_first_tag": "Please select a machine first:",
        "status_header": "*AI provider status:*",
        "status_ready": "✅ {name} — ready",
        "status_cooldown": "⏳ {name} — retrying in ~{sec}s",
        "raw_fallback_header": "⚠️ All AI services are currently busy, but here's what was found in the database ({machine}):\n",
        "raw_fallback_footer": "\nPlease try again shortly — once AI is available it will give a full explanation.",
        "station_n": "Station {n}",
        "general_scope": "general",
        "unexpected_error": "An unexpected error occurred. Please try again.",
        "tag_details": (
            "📍 **{addr}** ({dtype})\n"
            "Machine: {machine}\n"
            "Type: {kind}\n"
            "Scope: {scope} | Group: {group}\n"
            "Name: {name}\n"
            "Comment: {comment}"
        ),
        "access_denied": "Sorry, you are not authorized to use this bot. Please contact the administrator.",
        "user_added": "✅ User {uid} added.",
        "user_removed": "✅ User {uid} removed.",
        "admin_only": "This command is for administrators only.",
        "adduser_usage": "Usage: /adduser <telegram_id> <name (optional)>",
        "users_panel_title": "👥 User Management\nRegistered: {count}. Tap 🗑 to remove.",
        "users_panel_admins": "👑 Admins: {admins}",
        "users_panel_pending": "⏳ Pending requests: {count}",
        "users_panel_empty": "No registered users yet.",
        "users_del_confirm": "Remove {name} ({uid})?",
        "users_yes": "✅ Yes, remove",
        "users_no": "↩️ Back",
        "users_deleted": "🗑 User removed: {uid}",
        "users_close": "✖️ Close",
        "users_page": "{page}/{total}",
        "feedback_prompt": "💬 Was this answer helpful?",
        "feedback_thanks_up": "Thanks! ✅",
        "feedback_thanks_down": "Thanks for the feedback, we'll try to improve. 🙏",
        "photo_processing": "🖼 Reading the photo...",
        "photo_extracted": "📷 Read from photo: \"{text}\"",
        "esp32_fetching": "📡 Fetching data from ESP32...",
        "esp32_unreachable": "⚠️ Could not reach the ESP32 device. Wi-Fi or the device may be down.",
        "esp32_not_configured": "This section isn't set up yet (admin needs to set ESP32_STATUS_URL).",
        "esp32_status": (
            "🏭 *Factory monitoring status*\n\n"
            "⚙️ System: {active}\n"
            "🔘 Mode: {mode}\n"
            "🚨 Alarm: {alarm}\n\n"
            "💨 Air pressure: *{air} bar* (normal: {air_norm}, range: {air_min}–{air_max})\n"
            "💧 Water pressure: *{water} bar* (normal: {water_norm}, range: {water_min}–{water_max})\n"
            "🌡 Water temperature: *{temp} °C* (normal: {temp_norm}, range: {temp_min}–{temp_max})\n\n"
            "Sensors: air {air_sensor} | water {water_sensor} | temp {temp_sensor}\n"
            "📶 Wi-Fi: {wifi} | Uptime: {uptime}"
        ),
        "esp32_on": "ON ✅", "esp32_off": "OFF ⏸️",
        "esp32_alarm_yes": "active 🚨", "esp32_alarm_no": "none ✅",
        "esp32_sensor_ok": "✅", "esp32_sensor_bad": "❌",
        "help_text": (
            "ℹ️ *How to use this bot:*\n\n"
            "1️⃣ Pick a machine/line from the buttons below.\n"
            "2️⃣ Describe the problem (e.g. \"conveyor not moving\") or type "
            "an address (e.g. I0.1).\n"
            "📷 Photos (HMI screen/indicator) and 🎤 voice messages are supported.\n\n"
            "📚 *Library* — books/manuals and manual search by topic.\n"
            "🇬🇧 *English Course* — technical English lessons for new employees.\n"
            "🤖 *AI Assistant* — for questions unrelated to PLC.\n"
            "🏭 *Factory monitoring* — compressor/chiller status.\n"
            "🌐 *Language* — switch language anytime.\n"
            "❓ *Help* — this message.\n\n"
            "Quick commands: /tag %I0.5 · /find <keyword> · /register <name> <phone>"
        ),
        "already_registered": "You're already registered.",
        "register_usage": "Usage: /register <your name> <your phone number>\nExample: /register John Smith +998901234567",
        "register_admin_notice": "🆕 *New registration request*\nName: {name}\nPhone: {phone}\nID: {uid}",
        "register_sent": "✅ Your request was sent to the admin. You'll be notified once it's reviewed.",
        "register_already_handled": "This request has already been handled.",
        "register_approved_admin": "✅ {name} approved.",
        "register_approved_user": "🎉 Congrats! Your request was approved, you can now use the bot. Type /start.",
        "register_rejected_admin": "❌ {name} rejected.",
        "register_rejected_user": "Sorry, your request was rejected. Please contact the administrator.",
        "voice_processing": "🎤 Listening to the voice message...",
        "voice_failed": "I couldn't understand the voice message. Please type it instead.",
        "voice_too_long": "🎤 Voice message is too long (max 2 minutes). Send a shorter one or type it.",
        "voice_transcribed": "🎤 Heard: \"{text}\"",
        "schematic_page_caption": "🔌 Electrical schematic — {machine}, page {page}",
        "library_select_machine_first": "Please select a machine first, then tap \"📚 Manual\" again.",
        "library_no_manual_for_machine": "No manual has been loaded for \"{machine}\" yet.",
        "library_none_available": "No manual is loaded yet.",
        "library_ask_topic": "📚 *{machine} manual*\nWhat topic are you looking for? (e.g. \"lubrication\", \"light curtain setup\")",
        "library_no_results": "I couldn't find anything on that topic in the manual. Try different wording.",
        "library_found": "📖 Found {count} relevant page(s):",
        "lib_menu_title": "📚 *Library*\nTap a document to open it:",
        "lib_empty": "Library is empty. Documents appear after admin upload.",
        "lib_item": "• *{title}*\n  {desc}\n  ID: `{id}`",
        "lib_download_hint": "Get a file: /libget <ID>",
        "lib_get_usage": "Usage: /libget <doc_id>",
        "lib_not_found": "Document not found or removed.",
        "lib_no_access": "⛔ This section is for authorized staff only.",
        "lib_manual_search_btn": "🔎 Search manual: {machine}",
        "lib_back_btn": "⬅️ Back",
        "lib_del_confirm": "🗑 Delete: *{title}*",
        "lib_yes": "✅ Yes", "lib_no": "❌ No",
        "lib_admin_menu": (
            "🔐 *Admin — Library management*\n\n"
            "• Upload: /libadd <title>, then send any book/manual file "
            "(PDF, DOC, EPUB, XLSX, TXT, image…, max {mb} MB)\n"
            "• List & delete: /liblist\n"
            "• Delete: /libdel <ID>\n"
            "• User library: 📚 Library"
        ),
        "lib_add_usage": "Usage:\n1) /libadd <title>\n2) Then send any document/book file.",
        "lib_add_waiting": "✅ Title accepted: *{title}*\nNow send the file — any book/manual (PDF, DOC, EPUB, XLSX, TXT, image, etc., max {mb} MB).",
        "lib_add_done": "✅ Added to library.\nTitle: *{title}*\nID: `{id}`",
        "lib_add_too_big": "File too large (max {mb} MB).",
        "lib_add_bad_type": "File was not added. Please send a document/book file.",
        "lib_denied_type": "⛔ Security: executable/script files are forbidden. Only document/book files (PDF, DOC, EPUB, image…).",
        "lib_upload_admin_only": "📚 Only the administrator can upload files to the library. Contact the admin if you want a file added.",
        "lib_del_usage": "Usage: /libdel <doc_id>",
        "lib_del_done": "🗑 Deleted: {title}",
        "lib_del_fail": "Could not delete — ID not found.",
        "lib_list_admin": "📋 *Library (admin)* — {n} total:",
        "eng_menu_title": "🇬🇧 *English Course*\n\nTechnical English for new employees: factory terms, safety and daily work communication.\n\nCompleted lessons: {done}/{total}\n\nChoose a lesson:",
        "eng_lesson_header": "📘 *Lesson {n}: {title}*\n_{title_en}_",
        "eng_words_header": "📌 *New words:*",
        "eng_sentences_header": "💬 *Sentences:*",
        "eng_quiz_btn": "📝 Start quiz",
        "eng_back_btn": "⬅️ Back",
        "eng_retry_btn": "🔄 Retake quiz",
        "eng_quiz_q": "❓ *{q}*",
        "eng_quiz_correct": "✅ Correct!",
        "eng_quiz_wrong": "❌ Wrong. Correct answer: *{a}*",
        "eng_quiz_next": "Next question ▶️",
        "eng_quiz_result": "🏁 *Result: {score}/{total}*\n{verdict}",
        "eng_verdict_pass": "Great! Lesson completed ✅",
        "eng_verdict_fail": "You need at least 60% correct — review the lesson and try the quiz again.",
        "eng_course_empty": "The English course is not available yet.",
        "access_denied_detail": "⛔ *Access denied*\n\nThis bot is for factory staff only.\nAdmin approval is required.\n\nRegister:\n`/register Full Name +998901234567`\n\nThe bot opens after admin approval.",
        "reg_rate_limited": "⏳ Too many requests. Try again in 1 hour.",
        "security_blocked": "⛔ Security: request rejected.",

        "manual_page_caption": "📖 Manual — {machine}, page {page}",
        "manual_page_text": "📖 Page {page}:\n{text}",
        "addcomment_usage": "Usage: /addcomment <machine_id> <address> <comment text>\nExample: /addcomment gem %I0.1 Conveyor entry sensor",
        "addcomment_bad_machine": "Invalid machine ID. Available: {ids}",
        "addcomment_save_error": "There was an error saving. Please try again.",
        "addcomment_done": "✅ {machine} — comment updated for {addr}: \"{comment}\"",
        "topfaults_empty": "No data yet for the last {days} days.",
        "topfaults_header": "📈 *Stats for the last {days} days:*",
        "topfaults_by_machine": "*By machine:*",
        "topfaults_by_address": "*Most-queried addresses:*",
        "rate_limited": "⏳ You've sent too many questions in one minute. Please wait a bit, or use /tag %I0.5 if you know the exact address (no AI needed, instant reply).",
        "find_usage": "Usage: /find <keyword>\nExample: /find conveyor",
        "find_no_results": "No results found for '{query}'.",
        "find_results_header": "🔎 {count} result(s) for '{query}':",
        "setphone_usage": "Usage: /setphone <telegram_id> <phone_number>",
        "phone_updated": "✅ Phone number updated for {uid}: {phone}",
        "user_not_found": "No such ID found in the list.",

        # --- New modules: Access, ESP32 control, English course, Admin panel ---
        "req_access_btn": "Request Access (Registration)",
        "req_prompt": "📝 *Request Bot Access*\n\nPlease send your full name and phone number.\nFormat: `/register Full Name +998901234567`\nExample:\n`/register John Smith +998901234567`\n\nThe bot will be unlocked once approved by an administrator.",
        "esp_refresh": "Refresh",
        "esp_start": "Start",
        "esp_stop": "Stop",
        "esp_mode1": "Mode 1 (Air+Water+Temp)",
        "esp_mode2": "Mode 2 (Air Only)",
        "esp_reset_alarm": "Reset Alarm/Siren",
        "esp_limits": "Limits",
        "esp_cmd_sent": "✅ Command sent to ESP32.",
        "esp_cmd_fail": "❌ Could not reach ESP32 device.",
        "eng_flashcards_btn": "🗂 Flashcards",
        "eng_ai_tutor_btn": "🤖 Practice English with AI",
        "eng_stats_btn": "📊 My Progress",
        "eng_fc_card": "🗂 *Vocabulary Flashcard* — {title}\n\n🇬🇧 *{en}*\n🇺🇿 {uz}\n🇨🇳 {zh}\n\n({cur}/{total})",
        "eng_fc_prev": "⬅️ Previous",
        "eng_fc_next": "Next ➡️",
        "eng_stats_text": "📊 *Your English Course Progress:*\n\n✅ Completed Lessons: *{done}/{total}* ({pct}%)\n{badge}",
        "eng_tutor_intro": "🤖 *AI English Language Tutor*\n\nPractice your technical or conversational English here! Ask questions about vocabulary or practice speaking. I will kindly correct mistakes.\n\nType /exit or tap any menu button to exit tutor mode.\n\n_Start typing in English: (e.g., Hello, how are you today?)_",
        "eng_tutor_exit": "Exited English Tutor mode. Back to main menu.",
        "admin_panel_title": "⚙️ *Admin Hub*\n━━━━━━━━━━━━━━━━━━━━\n👑 Admins: *{admins}*\n👥 Staff: *{users}*\n⏳ Pending Requests: *{pending}*\n📚 Library Files: *{docs}*\n🏭 ESP32: *{esp32}*\n━━━━━━━━━━━━━━━━━━━━",
        "admin_btn_users": "👥 Staff List",
        "admin_btn_pending": "⏳ Pending Requests",
        "admin_btn_ai": "🤖 AI Providers Status",
        "admin_btn_esp32": "🏭 ESP32 Factory Hub",
        "admin_btn_stats": "📊 System Statistics",
        "admin_btn_adduser": "➕ Add User Manually",
        "admin_uview_text": "👤 *Staff Details:*\n\n• Name: *{name}*\n• Telegram ID: `{uid}`\n• Phone: `{phone}`\n• Added: `{added}`",
        "admin_udel_btn": "🗑 Remove Staff",
        "admin_back_btn": "⬅️ Back",
        "admin_udel_confirm": "⚠️ Are you sure you want to remove *{name}* (`{uid}`)?",
        "admin_udel_yes": "✅ Yes, remove",
        "admin_udel_no": "❌ Cancel",
        "admin_pending_empty": "⏳ No pending requests right now.",
        "admin_pending_item": "🆕 *Request:*\n👤 Name: *{name}*\n📞 Phone: `{phone}`\n🆔 ID: `{uid}`\n📅 Date: `{date}`",
    },
    "zh": {
        "choose_lang": "Tilni tanlang / Please choose language / 请选择语言:",
        "lang_selected": "✅ 已选择语言：{label}",
        "greeting_after_lang": (
            "请选择您需要帮助的设备/产线，或点击\"{ai}\"提出自由提问："
        ),
        "returning_greeting": (
            "您好！当前选择的设备：*{machine}*。\n\n"
            "请描述问题，或输入地址（例如 I0.1）。"
            "点击下方按钮可切换设备。"
        ),
        "choose_machine_prompt": "您要选择哪个设备/产线？",
        "machine_selected": (
            "✅ 已选择：*{machine}*\n\n"
            "现在请描述设备上出现的问题（例如\"3工位输送带不动\"），"
            "或直接输入地址（例如 I0.1）。"
        ),
        "ai_mode_intro": "🤖 人工智能助手模式。请输入任何问题——不必与PLC相关。",
        "no_machine_selected": (
            "请选择您需要帮助的设备/产线，或点击\"{ai}\"提出自由提问："
        ),
        "addr_not_found": "在'{machine}'数据库中未找到地址'{addr}'。请检查是否选择了正确的设备。",
        "no_match": "未能找到明确匹配的信号。请说明是哪个工位/机器人，或输入准确地址（例如 I0.1）。",
        "ai_busy_general": "抱歉，所有AI服务目前都很忙。请几分钟后重试。",
        "tag_usage": "用法：/tag %I0.5",
        "tag_not_found": "在'{machine}'数据库中未找到地址'{addr}'。",
        "choose_machine_first_tag": "请先选择设备：",
        "status_header": "*AI服务提供商状态：*",
        "status_ready": "✅ {name} — 就绪",
        "status_cooldown": "⏳ {name} — 约{sec}秒后重试",
        "raw_fallback_header": "⚠️ 目前所有AI服务都很忙，但数据库中找到以下信息（{machine}）：\n",
        "raw_fallback_footer": "\n请稍后重试——AI恢复后将提供完整说明。",
        "station_n": "第{n}工位",
        "general_scope": "通用",
        "unexpected_error": "发生了意外错误。请重试。",
        "tag_details": (
            "📍 **{addr}** ({dtype})\n"
            "设备：{machine}\n"
            "类型：{kind}\n"
            "范围：{scope} | 分组：{group}\n"
            "名称：{name}\n"
            "注释：{comment}"
        ),
        "access_denied": "抱歉，您没有使用此机器人的权限。请联系管理员。",
        "user_added": "✅ 已添加用户 {uid}。",
        "user_removed": "✅ 已移除用户 {uid}。",
        "admin_only": "此命令仅限管理员使用。",
        "adduser_usage": "用法：/adduser <telegram_id> <姓名（可选）>",
        "users_panel_title": "👥 用户管理\n已注册：{count}。点击 🗑 删除。",
        "users_panel_admins": "👑 管理员：{admins}",
        "users_panel_pending": "⏳ 待处理请求：{count}",
        "users_panel_empty": "暂无注册用户。",
        "users_del_confirm": "删除 {name}（{uid}）？",
        "users_yes": "✅ 是，删除",
        "users_no": "↩️ 返回",
        "users_deleted": "🗑 已删除用户：{uid}",
        "users_close": "✖️ 关闭",
        "users_page": "{page}/{total}",
        "feedback_prompt": "💬 这个回答有帮助吗？",
        "feedback_thanks_up": "谢谢！✅",
        "feedback_thanks_down": "感谢反馈，我们会努力改进。🙏",
        "photo_processing": "🖼 正在读取图片...",
        "photo_extracted": "📷 从图片中读取：\"{text}\"",
        "esp32_fetching": "📡 正在从ESP32获取数据...",
        "esp32_unreachable": "⚠️ 无法连接到ESP32设备。可能是Wi-Fi或设备已关闭。",
        "esp32_not_configured": "此功能尚未配置（管理员需要设置ESP32_STATUS_URL）。",
        "esp32_status": (
            "🏭 *工厂监控状态*\n\n"
            "⚙️ 系统：{active}\n"
            "🔘 模式：{mode}\n"
            "🚨 报警：{alarm}\n\n"
            "💨 空气压力：*{air} bar*（正常值：{air_norm}，范围：{air_min}–{air_max}）\n"
            "💧 水压：*{water} bar*（正常值：{water_norm}，范围：{water_min}–{water_max}）\n"
            "🌡 水温：*{temp} °C*（正常值：{temp_norm}，范围：{temp_min}–{temp_max}）\n\n"
            "传感器：空气 {air_sensor} | 水 {water_sensor} | 温度 {temp_sensor}\n"
            "📶 Wi-Fi：{wifi} | 运行时间：{uptime}"
        ),
        "esp32_on": "已开启 ✅", "esp32_off": "已关闭 ⏸️",
        "esp32_alarm_yes": "报警中 🚨", "esp32_alarm_no": "无 ✅",
        "esp32_sensor_ok": "✅", "esp32_sensor_bad": "❌",
        "help_text": (
            "ℹ️ *如何使用本机器人：*\n\n"
            "1️⃣ 从下方按钮选择设备/产线。\n"
            "2️⃣ 描述问题（例如\"输送带不动\"），或输入地址（例如 I0.1）。\n"
            "📷 支持照片（HMI屏幕/指示灯）和 🎤 语音消息。\n\n"
            "📚 *资料库* — 书籍/手册及按主题搜索手册。\n"
            "🇬🇧 *英语课程* — 为新员工开设的技术英语课程。\n"
            "🤖 *人工智能* — 与PLC无关的问题。\n"
            "🏭 *工厂监控* — 压缩机/冷水机状态。\n"
            "🌐 *语言* — 随时切换语言。\n"
            "❓ *帮助* — 本条消息。\n\n"
            "快捷命令：/tag %I0.5 · /find <关键词> · /register <姓名> <电话>"
        ),
        "already_registered": "您已经注册过了。",
        "register_usage": "用法：/register <姓名> <电话号码>\n例如：/register 张三 +998901234567",
        "register_admin_notice": "🆕 *新的注册请求*\n姓名：{name}\n电话：{phone}\nID：{uid}",
        "register_sent": "✅ 您的请求已发送给管理员，审核后会通知您。",
        "register_already_handled": "此请求已被处理。",
        "register_approved_admin": "✅ 已批准{name}。",
        "register_approved_user": "🎉 恭喜！您的请求已被批准，现在可以使用机器人了。请输入 /start。",
        "register_rejected_admin": "❌ 已拒绝{name}。",
        "register_rejected_user": "抱歉，您的请求被拒绝。请联系管理员。",
        "voice_processing": "🎤 正在听取语音消息...",
        "voice_failed": "无法理解该语音消息。请改为输入文字。",
        "voice_too_long": "🎤 语音消息太长（最多2分钟）。请发送更短的语音或输入文字。",
        "voice_transcribed": "🎤 听到：\"{text}\"",
        "schematic_page_caption": "🔌 电气原理图 — {machine}，第{page}页",
        "library_select_machine_first": "请先选择设备，然后再次点击\"📚 手册\"。",
        "library_no_manual_for_machine": "\"{machine}\"尚未上传手册。",
        "library_none_available": "目前还没有上传任何手册。",
        "library_ask_topic": "📚 *{machine}手册*\n您要查找什么主题？（例如：\"润滑\"、\"光幕设置\"）",
        "library_no_results": "未能在手册中找到该主题的相关内容。请尝试其他措辞。",
        "library_found": "📖 找到{count}个相关页面：",
        "lib_menu_title": "📚 *资料库*\n点击文档即可打开：",
        "lib_empty": "资料库为空。管理员上传后显示。",
        "lib_item": "• *{title}*\n  {desc}\n  ID: `{id}`",
        "lib_download_hint": "获取文件：/libget <ID>",
        "lib_get_usage": "用法：/libget <文档ID>",
        "lib_not_found": "未找到该文档或已删除。",
        "lib_no_access": "⛔ 此分区仅限授权员工。",
        "lib_manual_search_btn": "🔎 搜索手册：{machine}",
        "lib_back_btn": "⬅️ 返回",
        "lib_del_confirm": "🗑 删除：*{title}*",
        "lib_yes": "✅ 是", "lib_no": "❌ 否",
        "lib_admin_menu": "🔐 *管理员 — 资料库管理*\n\n• 上传：/libadd <标题>，然后发送任何书籍/手册文件（PDF、DOC、EPUB、XLSX、图片等，最大 {mb} MB）\n• 列表与删除：/liblist\n• 删除：/libdel <ID>",
        "lib_add_usage": "用法：\n1) /libadd <标题>\n2) 然后发送任何文档/书籍文件。",
        "lib_add_waiting": "✅ 标题已接受：*{title}*\n请发送文件——任何书籍/手册（PDF、DOC、EPUB、XLSX、TXT、图片等，最大 {mb} MB）。",
        "lib_add_done": "✅ 已加入资料库。\n标题：*{title}*\nID：`{id}`",
        "lib_add_too_big": "文件过大（最大 {mb} MB）。",
        "lib_add_bad_type": "文件未添加。请发送文档/书籍文件。",
        "lib_denied_type": "⛔ 安全：禁止上传可执行程序/脚本文件。仅允许文档/书籍文件（PDF、DOC、EPUB、图片等）。",
        "lib_upload_admin_only": "📚 只有管理员可以上传文件到资料库。如需添加文件，请联系管理员。",
        "lib_del_usage": "用法：/libdel <文档ID>",
        "lib_del_done": "🗑 已删除：{title}",
        "lib_del_fail": "无法删除 — 未找到ID。",
        "lib_list_admin": "📋 *资料库（管理员）* — 共 {n} 个：",
        "eng_menu_title": "🇬🇧 *英语课程*\n\n面向新员工的技术英语：工厂术语、安全和日常工作交流。\n\n已完成课程：{done}/{total}\n\n请选择课程：",
        "eng_lesson_header": "📘 *第{n}课：{title}*\n_{title_en}_",
        "eng_words_header": "📌 *生词：*",
        "eng_sentences_header": "💬 *句子：*",
        "eng_quiz_btn": "📝 开始测验",
        "eng_back_btn": "⬅️ 返回",
        "eng_retry_btn": "🔄 重新测验",
        "eng_quiz_q": "❓ *{q}*",
        "eng_quiz_correct": "✅ 正确！",
        "eng_quiz_wrong": "❌ 错误。正确答案：*{a}*",
        "eng_quiz_next": "下一题 ▶️",
        "eng_quiz_result": "🏁 *成绩：{score}/{total}*\n{verdict}",
        "eng_verdict_pass": "很好！课程完成 ✅",
        "eng_verdict_fail": "至少需要60%正确——请复习课程后再测验。",
        "eng_course_empty": "英语课程暂不可用。",
        "access_denied_detail": "⛔ *禁止访问*\n\n本机器人仅供工厂员工使用。\n需要管理员批准。\n\n注册：\n`/register 姓名 +998901234567`",
        "reg_rate_limited": "⏳ 请求过多。请1小时后再试。",
        "security_blocked": "⛔ 安全：请求被拒绝。",

        "manual_page_caption": "📖 手册 — {machine}，第{page}页",
        "manual_page_text": "📖 第{page}页：\n{text}",
        "addcomment_usage": "用法：/addcomment <设备ID> <地址> <注释文本>\n例如：/addcomment gem %I0.1 输送带入口传感器",
        "addcomment_bad_machine": "设备ID无效。可用的有：{ids}",
        "addcomment_save_error": "保存时出错，请重试。",
        "addcomment_done": "✅ {machine} — {addr}的注释已更新：\"{comment}\"",
        "topfaults_empty": "最近{days}天还没有数据。",
        "topfaults_header": "📈 *最近{days}天统计：*",
        "topfaults_by_machine": "*按设备：*",
        "topfaults_by_address": "*被查询最多的地址：*",
        "rate_limited": "⏳ 您在一分钟内发送的问题太多了。请稍等，或者如果您知道确切地址，可使用 /tag %I0.5（无需AI，立即回复）。",
        "find_usage": "用法：/find <关键词>\n例如：/find 输送带",
        "find_no_results": "未找到与'{query}'相关的结果。",
        "find_results_header": "🔎 找到{count}条与'{query}'相关的结果：",
        "setphone_usage": "用法：/setphone <telegram_id> <电话号码>",
        "phone_updated": "✅ 已更新{uid}的电话号码：{phone}",
        "user_not_found": "未在列表中找到该ID。",

        # --- 新功能模块：权限、ESP32控制、英语学习与管理员面板 ---
        "req_access_btn": "申请权限（注册）",
        "req_prompt": "📝 *申请机器人使用权限*\n\n请发送您的姓名和电话号码。\n格式：`/register 姓名 +998901234567`\n例如：\n`/register 张三 +998901234567`\n\n管理员批准后将自动开通使用权限。",
        "esp_refresh": "刷新状态",
        "esp_start": "启动 (Start)",
        "esp_stop": "停止 (Stop)",
        "esp_mode1": "模式1 (气压+水压+温度)",
        "esp_mode2": "模式2 (仅气压)",
        "esp_reset_alarm": "消除警报/警笛",
        "esp_limits": "阈值设置",
        "esp_cmd_sent": "✅ 命令已发送给ESP32。",
        "esp_cmd_fail": "❌ 无法连接到ESP32设备。",
        "eng_flashcards_btn": "🗂 生词卡片",
        "eng_ai_tutor_btn": "🤖 与AI练习英语对话",
        "eng_stats_btn": "📊 我的学习进度",
        "eng_fc_card": "🗂 *生词卡片* — {title}\n\n🇬🇧 *{en}*\n🇺🇿 {uz}\n🇨🇳 {zh}\n\n({cur}/{total})",
        "eng_fc_prev": "⬅️ 上一个",
        "eng_fc_next": "下一个 ➡️",
        "eng_stats_text": "📊 *您的英语学习进度：*\n\n✅ 已完成课程：*{done}/{total}* ({pct}%)\n{badge}",
        "eng_tutor_intro": "🤖 *AI 英语辅导老师*\n\n在这里练习您的技术或日常英语！您可以提问词汇或直接对话，我会耐心纠正语法错误。\n\n输入 /exit 或点击底部菜单按钮可随时退出。\n\n_Start typing in English: (例如：Hello, how do I check the water pressure?)_",
        "eng_tutor_exit": "已退出英语辅导模式，返回主菜单。",
        "admin_panel_title": "⚙️ *管理员控制台 (Admin Hub)*\n━━━━━━━━━━━━━━━━━━━━\n👑 管理员：*{admins}*\n👥 员工：*{users}* 人\n⏳ 待审核申请：*{pending}* 个\n📚 资料库文档：*{docs}* 份\n🏭 ESP32状态：*{esp32}*\n━━━━━━━━━━━━━━━━━━━━",
        "admin_btn_users": "👥 员工列表",
        "admin_btn_pending": "⏳ 待审核申请",
        "admin_btn_ai": "🤖 AI系统状态",
        "admin_btn_esp32": "🏭 ESP32工厂控制",
        "admin_btn_stats": "📊 系统运行统计",
        "admin_btn_adduser": "➕ 手动添加员工",
        "admin_uview_text": "👤 *员工详细信息：*\n\n• 姓名：*{name}*\n• Telegram ID：`{uid}`\n• 电话：`{phone}`\n• 注册时间：`{added}`",
        "admin_udel_btn": "🗑 删除员工",
        "admin_back_btn": "⬅️ 返回",
        "admin_udel_confirm": "⚠️ 确定要删除员工 *{name}* (`{uid}`) 吗？",
        "admin_udel_yes": "✅ 确定删除",
        "admin_udel_no": "❌ 取消",
        "admin_pending_empty": "⏳ 当前暂无待审核的申请。",
        "admin_pending_item": "🆕 *新申请：*\n👤 姓名：*{name}*\n📞 电话：`{phone}`\n🆔 ID：`{uid}`\n📅 时间：`{date}`",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    lang = lang if lang in TEXT else "uz"
    template = TEXT[lang].get(key) or TEXT["uz"].get(key, key)
    try:
        return template.format(**kwargs)
    except Exception:
        return template


def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang") or "uz"


MACHINE_MENU_LABEL = "🔀 Uskunani tanlash/almashtirish"
AI_CHAT_LABEL = "🤖 Sun'iy intellekt (erkin savol) / AI Assistant / 人工智能"
ESP32_MENU_LABEL = "🏭 Zavod monitoring (kompressor/chiller)"
LIBRARY_MENU_LABEL = "📚 Kutubxona / Library / 资料库"
ADMIN_PANEL_LABEL = "⚙️ Admin Panel / 管理员面板"
ADMIN_LIBRARY_LABEL = "🔐 Admin: Kutubxona boshqaruvi"
HELP_BTN_LABEL = "ℹ️ Yordam / Help / 帮助"
ENG_COURSE_LABEL = "🇬🇧 Ingliz tili kursi / English Course / 英语课程"

# ---------------------------------------------------------------------------
# Uskunalar (liniyalar) konfiguratsiyasini yuklash
# ---------------------------------------------------------------------------

with open(LINES_CONFIG_PATH, "r", encoding="utf-8") as f:
    LINES_CONFIG = json.load(f)

if not LINES_CONFIG:
    raise RuntimeError(f"{LINES_CONFIG_PATH} bo'sh yoki topilmadi.")


def build_tag_block(tags):
    lines = []
    for tag in tags:
        st = f"ST{tag['station']}" if tag.get("station") else "-"
        # Token tejash: nom/izoh kesiladi (uzun xitoy izohlari keraksiz)
        lines.append(
            f"{tag['address']}\t{tag['kind']}\t{st}\t{(tag.get('group') or '')[:30]}\t{tag['data_type']}\t"
            f"{(tag.get('name') or '')[:60]}\t{(tag.get('comment') or '')[:80]}"
        )
    return "\n".join(lines)


def build_diagnosis_prompt(machine_label: str, tag_block: str, found_all: bool, lang: str, manual_excerpt: str = "") -> str:
    scope_note = (
        "The FULL tag list is given below."
        if found_all
        else "Below is only the SUBSET of tags judged most relevant to the "
             "employee's message (not the full list)."
    )
    hl = HEADER_LABELS.get(lang, HEADER_LABELS["uz"])
    manual_block = ""
    if manual_excerpt:
        manual_block = f"""
REAL MANUAL EXCERPT (prefer it over generic guesses, do not contradict):
---
{manual_excerpt}
---
"""
    return f"""You are a senior PLC diagnostics engineer for the "{machine_label}" equipment.
{scope_note} Each row: address TAB kind TAB station number (if known) TAB
group/location TAB data type TAB tag name TAB comment. Names/comments may be
in Chinese or English — understand them naturally regardless of language.

An employee describes a problem in Uzbek, English, Chinese, or a mix.
Answer STRICTLY CONCISE and TECHNICAL — an expert talking to a technician,
not a lecture. HARD LIMIT: ~90 words total. No filler, no disclaimers, no
repeating the question, no generic advice.

Rules:
1. Name the exact matching PLC tag address(es) — the single most likely one first.
2. One short line: what this signal physically is.
3. Max 4 most likely root causes, shortest first, a few words each.
4. Max 4 concrete check steps in order (what tool, what reading is good/bad).
5. If nothing matches: ONE short sentence asking which station/indicator — nothing else.
{manual_block}
LANGUAGE RULE (important): Your default reply language is {LANG_NAME.get(lang, "o'zbek")}.
However, if the employee's message is clearly written in one of the other
two supported languages (Uzbek, English, or Chinese), reply in THAT language
instead, matching what they used. Never mix languages within one reply.
Never produce a reply in any language other than these three.

Format your reply exactly like this (translate the bold labels into the
reply's language; use these labels for {LANG_NAME.get(lang, "o'zbek")}):

🔧 **{hl['address']}:** <address(es)>
📍 **{hl['what']}:** <1 short line>
✅ **{hl['action']}:** <max 4 short numbered steps>

TAG LIST:
---
{tag_block}
---
"""


KEYWORD_SYSTEM_PROMPT = (
    "You are a keyword extractor for an industrial PLC diagnostics system. "
    "The employee describes a problem they see on equipment, in Uzbek, "
    "English, or Chinese. From that description, extract 3-6 short technical "
    "keywords/phrases (English and/or Chinese) useful for searching a PLC tag "
    "list (e.g.: conveyor, motor, sensor, cylinder, light curtain, robot, "
    "welding, pressure, cooling, valve, safety, heater). Return ONLY a "
    "comma-separated list of keywords, no explanation."
)


def general_ai_prompt(lang: str) -> str:
    default_lang_name = LANG_NAME.get(lang, LANG_NAME["uz"])
    return (
        "You are a helpful, accurate AI assistant inside a factory Telegram bot. "
        "ANSWER CONCISELY: max ~80 words unless the user explicitly asks for "
        "detail or code. Be direct — no preamble, no filler, no closing remarks. "
        "Your default reply language is "
        f"{default_lang_name}. However, if the employee's message is "
        "clearly written in one of the other two supported languages (Uzbek, "
        "English, or Chinese), reply in THAT language instead. Never mix "
        "languages within one reply. Never reply in any language other than "
        "these three."
    )


class MachineLine:
    def __init__(self, cfg: dict):
        self.id = cfg["id"]
        self.label = cfg["label"]
        self.kb_file = cfg["kb_file"]
        with open(self.kb_file, "r", encoding="utf-8") as f:
            self.tags = json.load(f)
        logger.info("Yuklandi: '%s' -> %d ta tag", self.label, len(self.tags))

        # Elektr sxemasi (ixtiyoriy): agar lines.json'da "schematic_index"
        # ko'rsatilgan bo'lsa, shu PDF'dagi manzil->sahifa indeksini yuklaymiz.
        self.schematic_pdf = None
        self.schematic_addr_pages = {}
        idx_path = cfg.get("schematic_index")
        if idx_path and os.path.exists(idx_path):
            try:
                with open(idx_path, "r", encoding="utf-8") as f:
                    sch = json.load(f)
                self.schematic_pdf = sch.get("pdf_path")
                self.schematic_addr_pages = sch.get("addresses", {})
                if self.schematic_pdf and not os.path.exists(self.schematic_pdf):
                    logger.warning("'%s' uchun sxema PDF topilmadi: %s", self.label, self.schematic_pdf)
                    self.schematic_pdf = None
                else:
                    logger.info("'%s' uchun elektr sxemasi yuklandi (%d ta manzil)",
                                self.label, len(self.schematic_addr_pages))
            except Exception as e:
                logger.warning("Sxema indeksini yuklashda xatolik (%s): %s", self.label, e)

        # Qo'llanma/kutubxona (ixtiyoriy): agar lines.json'da "manual_index"
        # ko'rsatilgan bo'lsa, shu PDF'dagi har bir sahifa matnini yuklaymiz.
        self.manual_pdf = None
        self.manual_pages = {}  # "1" -> "sahifa matni"
        man_path = cfg.get("manual_index")
        if man_path and os.path.exists(man_path):
            try:
                with open(man_path, "r", encoding="utf-8") as f:
                    man = json.load(f)
                self.manual_pdf = man.get("pdf_path")
                self.manual_pages = man.get("pages", {})
                if self.manual_pdf and not os.path.exists(self.manual_pdf):
                    logger.warning("'%s' uchun qo'llanma PDF topilmadi: %s", self.label, self.manual_pdf)
                    self.manual_pdf = None
                else:
                    logger.info("'%s' uchun qo'llanma yuklandi (%d sahifa)",
                                self.label, len(self.manual_pages))
            except Exception as e:
                logger.warning("Qo'llanma indeksini yuklashda xatolik (%s): %s", self.label, e)

    def find_tag(self, address: str):
        """Exact tag lookup. Accepts I0.1, %I0.1, DB1.DBX0.0, etc."""
        address = address.strip()
        if not address.startswith("%"):
            address = "%" + address
        target = address.lower()
        # also try without leading % for comparison flexibility
        target_nopct = target.lstrip("%")
        for tg in self.tags:
            a = (tg.get("address") or "").lower()
            if a == target or a.lstrip("%") == target_nopct:
                return tg
        return None

    def schematic_pages_for(self, addresses: list) -> list:
        """Berilgan manzillar ro'yxati uchun sxemadagi tegishli sahifa
        raqamlarini (takrorlanmas, tartiblangan) qaytaradi."""
        if not self.schematic_pdf:
            return []
        pages = set()
        for addr in addresses:
            key = addr.upper().lstrip("%")
            for p in self.schematic_addr_pages.get(key, []):
                pages.add(p)
        return sorted(pages)

    def search_manual(self, keywords: set, limit: int = 3):
        """Qo'llanma sahifalari orasidan kalit so'zlarga eng mos kelganlarini
        topadi. [(sahifa_raqami, matn), ...] qaytaradi, mos kelish darajasi
        bo'yicha tartiblangan."""
        if not self.manual_pages or not keywords:
            return []
        scored = []
        for page_str, text in self.manual_pages.items():
            low = text.lower()
            score = sum(1 for kw in keywords if kw and kw in low)
            if score > 0:
                scored.append((score, int(page_str), text))
        scored.sort(key=lambda x: -x[0])
        return [(p, txt) for _, p, txt in scored[:limit]]


LINES = {}
LABEL_TO_ID = {}
for cfg in LINES_CONFIG:
    line = MachineLine(cfg)
    LINES[line.id] = line
    LABEL_TO_ID[line.label] = line.id


def language_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[LANG_BUTTON["uz"]], [LANG_BUTTON["en"]], [LANG_BUTTON["zh"]]], resize_keyboard=True)


def machine_keyboard(user_id: int = None) -> ReplyKeyboardMarkup:
    """Asosiy menyu — aniq bo'limlar, admin uchun qo'shimcha tugmalar."""
    rows = [[line.label] for line in LINES.values()]
    # Yordamchi bo'limlar — 2 tadan qatorlarga bo'lish
    util = [LIBRARY_MENU_LABEL, ENG_COURSE_LABEL, HELP_BTN_LABEL]
    if ESP32_STATUS_URL:
        util.append(ESP32_MENU_LABEL)
    for i in range(0, len(util), 2):
        rows.append(util[i:i + 2])
    rows.append([AI_CHAT_LABEL])
    rows.append([LANG_CHANGE_LABEL, MACHINE_MENU_LABEL])
    if user_id and is_admin(user_id):
        rows.append([ADMIN_PANEL_LABEL, ADMIN_LIBRARY_LABEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def unauthorized_keyboard(lang: str = "uz") -> InlineKeyboardMarkup:
    """Ruxsatsiz foydalanuvchilar uchun qulay tugmalar."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 " + t(lang, "req_access_btn"), callback_data="req:start")],
        [InlineKeyboardButton("🌐 " + LANG_CHANGE_LABEL, callback_data="req:lang")],
    ])


def kb_for(update: Update) -> ReplyKeyboardMarkup:
    uid = update.effective_user.id if update and update.effective_user else None
    return machine_keyboard(uid)


def get_selected_line(context: ContextTypes.DEFAULT_TYPE):
    line_id = context.user_data.get("line_id")
    return LINES.get(line_id) if line_id else None


# ---------------------------------------------------------------------------
# Tag qidiruv (kerakli taglarni topish — butun ro'yxatni yubormaslik uchun)
# ---------------------------------------------------------------------------

ADDR_RE = re.compile(
    r"^%?(?:"
    r"[A-Za-z]{1,4}\d+(?:\.\d+)?"           # %I0.1, Q53.2, M110.3, T1, IW64
    r"|DB\d+\.DB[XBWD]\d+(?:\.\d+)?"      # DB1.DBX0.0, DB10.DBW2
    r")$",
    re.IGNORECASE,
)
ADDR_SEARCH_RE = re.compile(r"%?\b[IQM]\d+\.\d+\b", re.IGNORECASE)
WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ\u4e00-\u9fff]+")

# Ko'p ishlatiladigan o'zbek/rus so'zlarini inglizcha texnik atamalarga
# moslashtiruvchi lug'at. Bu orqali ko'pgina so'rovlarda AI'ga alohida
# "kalit so'z ajratish" so'rovi yuborilmasdan, to'g'ridan-to'g'ri mahalliy
# qidiruv orqali natija topiladi — bu bitta AI so'rovini butunlay tejaydi.
GLOSSARY = {
    "konveyer": ["conveyor"], "konveer": ["conveyor"], "tasma": ["conveyor", "belt"],
    "motor": ["motor"], "dvigatel": ["motor"],
    "sensor": ["sensor"], "datchik": ["sensor"],
    "silindr": ["cylinder"], "cilindr": ["cylinder"],
    "klapan": ["valve"], "ventil": ["valve"],
    "robot": ["robot"],
    "payvand": ["weld", "welding"], "svarka": ["weld", "welding"], "payvandlash": ["welding"],
    "bosim": ["pressure"], "davleniye": ["pressure"], "davlenie": ["pressure"],
    "sovutish": ["cooling", "water"], "suv": ["water", "cooling"], "sovutuvchi": ["cooling"],
    "isitish": ["heat", "heater"], "isitgich": ["heater"],
    "xavfsizlik": ["safety"], "tor": ["light curtain", "curtain"], "parda": ["curtain", "light curtain"],
    "ushlagich": ["gripper"], "grip": ["gripper"],
    "kabel": ["cable"],
    "rele": ["relay"],
    "stansiya": ["station"], "stantsiya": ["station"], "uchastka": ["station"],
    "nasos": ["pump"],
    "signal": ["signal"],
    "harakat": ["motion", "move"],
    "eshik": ["door"], "kapok": ["cover", "door"],
    "tugma": ["button"],
    "lampa": ["lamp", "light"], "chiroq": ["lamp", "light"],
    "gaz": ["gas"], "havo": ["air"],
    "flesh": ["flash", "deflash"], "flash": ["flash", "deflash"],
    "zolotnik": ["valve"], "datчik": ["sensor"],
    # Russian (Cyrillic) — local search for operators typing in Russian
    "конвейер": ["conveyor"], "конвеер": ["conveyor"], "лента": ["conveyor", "belt"],
    "мотор": ["motor"], "двигатель": ["motor"],
    "датчик": ["sensor"], "сенсор": ["sensor"],
    "цилиндр": ["cylinder"],
    "клапан": ["valve"], "вентиль": ["valve"],
    "робот": ["robot"],
    "сварка": ["weld", "welding"], "сваривание": ["welding"],
    "давление": ["pressure"],
    "охлаждение": ["cooling", "water"], "вода": ["water", "cooling"],
    "нагрев": ["heat", "heater"], "нагреватель": ["heater"],
    "безопасность": ["safety"], "штора": ["curtain", "light curtain"],
    "захват": ["gripper"],
    "кабель": ["cable"],
    "реле": ["relay"],
    "станция": ["station"], "участок": ["station"],
    "насос": ["pump"],
    "сигнал": ["signal"],
    "движение": ["motion", "move"],
    "дверь": ["door"], "крышка": ["cover", "door"],
    "кнопка": ["button"],
    "лампа": ["lamp", "light"], "индикатор": ["lamp", "light"],
    "газ": ["gas"], "воздух": ["air"],
    "облой": ["flash", "deflash"],
    "авария": ["fault", "alarm", "error"], "ошибка": ["fault", "alarm", "error"],
    "не работает": ["fault", "alarm"], "стоит": ["stop", "fault"],
    "температура": ["temperature", "heat"],
}


def looks_like_address(text: str) -> bool:
    return bool(ADDR_RE.match(text.strip()))


def local_keywords(text: str):
    words = {w.lower() for w in WORD_RE.findall(text) if len(w) >= 3}
    expanded = set(words)
    for w in words:
        for gk, terms in GLOSSARY.items():
            if gk in w or w in gk:
                expanded.update(terms)
    return expanded


def score_tag(tag: dict, keywords: set) -> int:
    hay = f"{tag.get('name','')} {tag.get('comment','')} {tag.get('group','')}".lower()
    return sum(1 for k in keywords if k and k in hay)


def local_search(tags, keywords, limit=MAX_CANDIDATE_TAGS):
    if not keywords:
        return []
    scored = [(score_tag(tg, keywords), tg) for tg in tags]
    scored = [x for x in scored if x[0] > 0]
    scored.sort(key=lambda x: -x[0])
    return [tg for _, tg in scored[:limit]]


# ---------------------------------------------------------------------------
# Elektr sxemasi: PDF'ning tegishli sahifasini rasmga aylantirib yuborish
# ---------------------------------------------------------------------------

MAX_SCHEMATIC_PAGES = int(os.getenv("MAX_SCHEMATIC_PAGES", "3"))


def _render_pdf_page_sync(pdf_path: str, page_num: int) -> bytes:
    """page_num — 1-based sahifa raqami. PNG bytes qaytaradi."""
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=200)
        return pix.tobytes("png")
    finally:
        doc.close()


async def render_pdf_page(pdf_path: str, page_num: int):
    if not pymupdf:
        return None
    try:
        return await asyncio.to_thread(_render_pdf_page_sync, pdf_path, page_num)
    except Exception as e:
        logger.warning("PDF sahifasini render qilishda xatolik (%s, %d): %s", pdf_path, page_num, e)
        return None


async def send_schematic_pages(update: Update, context: ContextTypes.DEFAULT_TYPE, ln, addresses: list, lang: str):
    """Berilgan manzillarga mos sxema sahifalarini (topilsa) rasm sifatida yuboradi."""
    pages = ln.schematic_pages_for(addresses)
    if not pages:
        return
    pages = pages[:MAX_SCHEMATIC_PAGES]
    for page_num in pages:
        img_bytes = await render_pdf_page(ln.schematic_pdf, page_num)
        if img_bytes:
            try:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=img_bytes,
                    caption=t(lang, "schematic_page_caption", page=page_num, machine=ln.label),
                )
            except Exception as e:
                logger.warning("Sxema sahifasini yuborishda xatolik: %s", e)


async def send_manual_pages(update: Update, context: ContextTypes.DEFAULT_TYPE, ln, pages: list, lang: str):
    """Tashxis chiqarishda ishlatilgan qo'llanma sahifalarini rasm sifatida yuboradi."""
    if not ln.manual_pdf or not pages:
        return
    for page_num in pages[:MAX_SCHEMATIC_PAGES]:
        img_bytes = await render_pdf_page(ln.manual_pdf, page_num)
        if img_bytes:
            try:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=img_bytes,
                    caption=t(lang, "manual_page_caption", page=page_num, machine=ln.label),
                )
            except Exception as e:
                logger.warning("Qo'llanma sahifasini yuborishda xatolik: %s", e)


# ---------------------------------------------------------------------------
# Kutubxona/Qo'llanma bo'limi: xodim uskuna qo'llanmasidan mavzu bo'yicha
# to'g'ridan-to'g'ri qidirishi mumkin (AI'siz, haqiqiy sahifa ko'rsatiladi).
# ---------------------------------------------------------------------------

MAX_LIBRARY_RESULTS = int(os.getenv("MAX_LIBRARY_RESULTS", "3"))


async def handle_library_query(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    lang = get_lang(context)
    ln = get_selected_line(context)
    if ln is None or not ln.manual_pdf:
        await update.message.reply_text(t(lang, "library_none_available"), reply_markup=kb_for(update))
        return

    results = ln.search_manual(local_keywords(user_text), limit=MAX_LIBRARY_RESULTS)
    if not results:
        await update.message.reply_text(t(lang, "library_no_results"), reply_markup=kb_for(update))
        return

    await update.message.reply_text(t(lang, "library_found", count=len(results)))
    for page_num, text in results:
        img_bytes = await render_pdf_page(ln.manual_pdf, page_num)
        if img_bytes:
            try:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=img_bytes,
                    caption=t(lang, "manual_page_caption", page=page_num, machine=ln.label),
                )
            except Exception as e:
                logger.warning("Qo'llanma sahifasini yuborishda xatolik: %s", e)
        else:
            await update.message.reply_text(
                t(lang, "manual_page_text", page=page_num, text=text[:800])
            )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t(lang, "library_ask_topic", machine=ln.label),
        reply_markup=kb_for(update),
    )




# ---------------------------------------------------------------------------
# Kutubxona: foydalanuvchi ko'rish (inline menyu) + admin yuklash/o'chirish
# Barcha turdagi kitob/qo'llanma fayllari qabul qilinadi; xavfli turlar
# (dastur/skript) LIB_DENY_EXT orqali taqiqlangan.
# ---------------------------------------------------------------------------

LIB_PAGE_SIZE = 6


def _lib_ext(doc: dict) -> str:
    return os.path.splitext(doc.get("filename") or "")[1].lower()


def _lib_button_label(doc: dict) -> str:
    """Inline tugma matni: 📄 Sarlavha (EXT) — Telegram limiti 64 belgi."""
    title = (doc.get("title") or "?")[:40]
    ext = _lib_ext(doc).upper().lstrip(".")
    label = f"📄 {title}"
    if ext:
        label += f" [{ext}]"
    return label[:64]


def build_library_keyboard(docs, page: int, lang: str, machine_label: str = None):
    """Kutubxona inline klaviaturasi: hujjat tugmalari + sahifa navigatsiyasi
    + (agar uskuna tanlangan bo'lsa) qo'llanma qidiruvi tugmasi."""
    total_pages = max(1, (len(docs) + LIB_PAGE_SIZE - 1) // LIB_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    rows = []
    for d in docs[page * LIB_PAGE_SIZE:(page + 1) * LIB_PAGE_SIZE]:
        rows.append([InlineKeyboardButton(
            _lib_button_label(d), callback_data=safe_callback_data("libget", d.get("id", ""))
        )])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"libpg:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="libnoop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"libpg:{page + 1}"))
        rows.append(nav)
    if machine_label:
        rows.append([InlineKeyboardButton(
            t(lang, "lib_manual_search_btn", machine=machine_label)[:64], callback_data="libman:"
        )])
    return InlineKeyboardMarkup(rows), page


async def _send_library_doc(context: ContextTypes.DEFAULT_TYPE, chat_id: int, doc: dict, lang: str) -> bool:
    """Hujjatni chatga yuboradi. Avval file_id (Telegram'da tayyor nusxa),
    u bo'lmasa/yaroqsiz bo'lsa lokal fayldan."""
    file_id = doc.get("file_id")
    local = doc.get("local_path")
    caption = f"📄 {doc.get('title', '')}"[:1024]
    if file_id:
        try:
            await context.bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
            return True
        except Exception as e:
            logger.warning("libget file_id yuborish xato (lokalga o'tamiz): %s", e)
    if local and os.path.isfile(local):
        # path traversal himoyasi
        if not os.path.abspath(local).startswith(os.path.abspath(LIBRARY_FILES_DIR)):
            return False
        try:
            with open(local, "rb") as f:
                await context.bot.send_document(
                    chat_id=chat_id, document=f,
                    filename=sanitize_filename(doc.get("filename") or os.path.basename(local)),
                    caption=caption,
                )
            return True
        except Exception as e:
            logger.warning("libget lokal fayl yuborish xato: %s", e)
    return False


async def show_user_library(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ruxsatli foydalanuvchiga kutubxona — inline menyu ko'rinishida."""
    lang = get_lang(context)
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(lang, "access_denied_detail"), parse_mode="Markdown")
        return
    docs = library_list_visible()
    ln = get_selected_line(context)
    machine_label = ln.label if (ln and ln.manual_pdf) else None
    if not docs:
        kb = None
        if machine_label:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                t(lang, "lib_manual_search_btn", machine=machine_label)[:64], callback_data="libman:"
            )]])
        await update.message.reply_text(t(lang, "lib_empty"), reply_markup=kb or kb_for(update))
        return
    kb, _ = build_library_keyboard(docs, 0, lang, machine_label)
    await safe_reply_text(update, t(lang, "lib_menu_title"), reply_markup=kb)


async def show_admin_library_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"), reply_markup=kb_for(update))
        return
    await update.message.reply_text(
        t(lang, "lib_admin_menu", mb=int(MAX_LIBRARY_FILE_MB)),
        parse_mode="Markdown", reply_markup=kb_for(update)
    )


async def libadd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    title = " ".join(context.args).strip() if context.args else ""
    if not title:
        await update.message.reply_text(t(lang, "lib_add_usage"))
        return
    # Markdown/injection: sarlavhani soddalashtirish
    title = re.sub(r"[\r\n`*_[\]]", " ", title)[:200].strip()
    if not title:
        await update.message.reply_text(t(lang, "lib_add_usage"))
        return
    context.user_data["mode"] = "lib_admin_upload"
    context.user_data["lib_pending_title"] = title
    await update.message.reply_text(
        t(lang, "lib_add_waiting", title=title, mb=int(MAX_LIBRARY_FILE_MB)),
        parse_mode="Markdown",
        reply_markup=kb_for(update),
    )


async def liblist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin ro'yxati — har bir hujjat yonida o'chirish va yuborish tugmasi."""
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    if not LIBRARY_DOCS:
        await update.message.reply_text(t(lang, "lib_empty"))
        return
    lines = [t(lang, "lib_list_admin", n=len(LIBRARY_DOCS))]
    rows = []
    for d in LIBRARY_DOCS:
        vis = "✅" if d.get("visible", True) else "🙈"
        lines.append(f"{vis} `{d.get('id')}` — *{(d.get('title') or '?')[:60]}*")
        rows.append([
            InlineKeyboardButton(f"🗑 {(d.get('title') or '?')[:30]}",
                                 callback_data=safe_callback_data("libdelask", d.get("id", ""))),
            InlineKeyboardButton("📥", callback_data=safe_callback_data("libget", d.get("id", ""))),
        ])
    await safe_reply_text(update, "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


def _delete_library_doc(doc: dict):
    """Hujjatni ro'yxatdan va diskdan o'chiradi."""
    local = doc.get("local_path")
    if local and os.path.isfile(local) and os.path.abspath(local).startswith(os.path.abspath(LIBRARY_FILES_DIR)):
        try:
            os.remove(local)
        except Exception as e:
            logger.warning("library file delete: %s", e)
    library_delete(doc.get("id"))


async def libdel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    if not context.args:
        await update.message.reply_text(t(lang, "lib_del_usage"))
        return
    doc_id = re.sub(r"[^a-f0-9]", "", context.args[0].strip().lower())[:16]
    doc = library_get(doc_id)
    if not doc:
        await update.message.reply_text(t(lang, "lib_del_fail"))
        return
    title = doc.get("title", doc_id)
    _delete_library_doc(doc)
    await update.message.reply_text(t(lang, "lib_del_done", title=title), reply_markup=kb_for(update))


async def libget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(lang, "access_denied_detail"), parse_mode="Markdown")
        return
    if not context.args:
        await update.message.reply_text(t(lang, "lib_get_usage"))
        return
    doc_id = re.sub(r"[^a-f0-9]", "", context.args[0].strip().lower())[:16]
    doc = library_get(doc_id)
    if not doc or not doc.get("visible", True):
        await update.message.reply_text(t(lang, "lib_not_found"))
        return
    if not await _send_library_doc(context, update.effective_chat.id, doc, lang):
        await update.message.reply_text(t(lang, "lib_not_found"))


async def handle_library_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kutubxonaga hujjat yuklash. Ishonchli oqim:
    - /libadd <sarlavha> qilingan bo'lsa — shu sarlavha bilan qo'shiladi;
    - AKS HOLDA admin yuborgan istalgan hujjat ham avtomatik qo'shiladi
      (sarlavha = caption yoki fayl nomi) — fayl "yo'qolib qolmaydi".
    Begona foydalanuvchi jim o'tkaziladi, oddiy xodimga tushuntirish beriladi."""
    lang = get_lang(context)
    uid = update.effective_user.id
    if not is_authorized(uid):
        return
    if not is_admin(uid):
        await update.message.reply_text(t(lang, "lib_upload_admin_only"))
        return

    doc = update.message.document
    if not doc:
        return

    fname = sanitize_filename(doc.file_name or "file")
    title = None
    if context.user_data.get("mode") == "lib_admin_upload":
        title = context.user_data.get("lib_pending_title")
    if not title:
        cap = re.sub(r"[\r\n`*_[\]]", " ", (update.message.caption or "").strip())
        title = (cap or os.path.splitext(fname)[0] or fname)[:200]

    # Xavfsizlik: xavfli fayl turlari (dastur/skript) taqiqlangan
    ext = os.path.splitext(fname)[1].lower()
    if ext in LIB_DENY_EXT:
        logger.warning("Xavfli fayl turi rad etildi (uid=%s, file=%s)", uid, fname)
        await update.message.reply_text(t(lang, "lib_denied_type"))
        return

    # O'lcham tekshiruvi
    if doc.file_size and doc.file_size > MAX_LIBRARY_FILE_MB * 1024 * 1024:
        await update.message.reply_text(t(lang, "lib_add_too_big", mb=int(MAX_LIBRARY_FILE_MB)))
        return

    doc_id = uuid.uuid4().hex[:10]
    local_path = None
    # 20 MB gacha — diskka nusxa olamiz (file_id ishlamasa zaxira bo'ladi);
    # kattaroq fayllar faqat Telegram file_id orqali saqlanadi.
    if not doc.file_size or doc.file_size <= TG_DOWNLOAD_MAX_BYTES:
        local_name = f"{doc_id}_{fname}"
        candidate = os.path.join(LIBRARY_FILES_DIR, local_name)
        try:
            tg_file = await context.bot.get_file(doc.file_id)
            await tg_file.download_to_drive(candidate)
            local_path = candidate
        except Exception as e:
            logger.warning("library download failed: %s", e)

    entry = {
        "id": doc_id,
        "title": title,
        "description": re.sub(r"[\r\n`*_[\]]", " ", (update.message.caption or ""))[:300],
        "category": "manual",
        "filename": fname,
        "file_id": doc.file_id,
        "local_path": local_path,
        "mime": (doc.mime_type or "").lower(),
        "size": doc.file_size,
        "added_by": uid,
        "added_at": datetime.now().isoformat(),
        "visible": True,
    }
    library_add(entry)
    context.user_data["mode"] = None
    context.user_data.pop("lib_pending_title", None)
    await update.message.reply_text(
        t(lang, "lib_add_done", title=title, id=doc_id),
        parse_mode="Markdown",
        reply_markup=kb_for(update),
    )


async def handle_library_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/libadd rejimida admin rasm yuborsa — uni ham kutubxonaga qo'shamiz
    (aks holda rasm diagnostikaga tushib ketardi)."""
    lang = get_lang(context)
    title = (context.user_data.get("lib_pending_title") or "Photo")[:200]
    photo = update.message.photo[-1]
    if photo.file_size and photo.file_size > MAX_LIBRARY_FILE_MB * 1024 * 1024:
        await update.message.reply_text(t(lang, "lib_add_too_big", mb=int(MAX_LIBRARY_FILE_MB)))
        return
    doc_id = uuid.uuid4().hex[:10]
    fname = f"{doc_id}_photo.jpg"
    local_path = os.path.join(LIBRARY_FILES_DIR, fname)
    try:
        tg_file = await photo.get_file()
        await tg_file.download_to_drive(local_path)
    except Exception as e:
        logger.warning("library photo download failed: %s", e)
        local_path = None

    entry = {
        "id": doc_id,
        "title": title,
        "description": "",
        "category": "manual",
        "filename": fname,
        "file_id": photo.file_id,
        "local_path": local_path,
        "mime": "image/jpeg",
        "size": photo.file_size,
        "added_by": update.effective_user.id,
        "added_at": datetime.now().isoformat(),
        "visible": True,
    }
    library_add(entry)
    context.user_data["mode"] = None
    context.user_data.pop("lib_pending_title", None)
    await update.message.reply_text(
        t(lang, "lib_add_done", title=title, id=doc_id),
        parse_mode="Markdown",
        reply_markup=kb_for(update),
    )


# ---------------------------------------------------------------------------
# 🇬🇧 Ingliz tili kursi: darslar (so'zlar + jumlalar) va testlar.
# Ma'lumot english_course.json'dan yuklanadi; progress user_data'da saqlanadi
# (PicklePersistence tufayli bot qayta ishga tushsa ham yo'qolmaydi).
# ---------------------------------------------------------------------------

def _eng_done_list(context) -> list:
    return context.user_data.setdefault("eng_done", [])


def _eng_lesson_by_id(lesson_id: str):
    for i, lesson in enumerate(ENG_COURSE):
        if lesson.get("id") == lesson_id:
            return i, lesson
    return None, None


def build_eng_lesson_text(lesson: dict, idx: int, lang: str) -> str:
    lines = [t(lang, "eng_lesson_header", n=idx + 1,
               title=lesson.get("title_uz", ""), title_en=lesson.get("title_en", ""))]
    lines.append("")
    lines.append(t(lang, "eng_words_header"))
    for w in lesson.get("words", []):
        lines.append(f"• *{w.get('en', '')}* — {w.get('uz', '')} / {w.get('zh', '')}")
    if lesson.get("sentences"):
        lines.append("")
        lines.append(t(lang, "eng_sentences_header"))
        for s in lesson["sentences"]:
            lines.append(f"• _{s.get('en', '')}_\n  {s.get('uz', '')}")
    return "\n".join(lines)


def build_eng_menu_keyboard(context, lang: str = "uz") -> InlineKeyboardMarkup:
    done = set(_eng_done_list(context))
    rows = [
        [
            InlineKeyboardButton(t(lang, "eng_stats_btn"), callback_data="eng:stats"),
            InlineKeyboardButton(t(lang, "eng_ai_tutor_btn"), callback_data="eng:tutor"),
        ]
    ]
    for i, lesson in enumerate(ENG_COURSE):
        mark = " ✅" if lesson.get("id") in done else ""
        label = f"{i + 1}. {lesson.get('title_uz', lesson.get('title_en', ''))[:40]}{mark}"
        rows.append([InlineKeyboardButton(label[:64], callback_data=safe_callback_data("eng:l", lesson.get("id", "")))])
    return InlineKeyboardMarkup(rows)


def _eng_menu_text(context, lang: str) -> str:
    done = set(_eng_done_list(context)) & {l.get("id") for l in ENG_COURSE}
    return t(lang, "eng_menu_title", done=len(done), total=len(ENG_COURSE))


async def show_eng_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not ENG_COURSE:
        await update.message.reply_text(t(lang, "eng_course_empty"), reply_markup=kb_for(update))
        return
    await safe_reply_text(
        update, _eng_menu_text(context, lang),
        reply_markup=build_eng_menu_keyboard(context, lang),
    )


async def _eng_show_result(query, context, lang: str, lesson_id: str, quiz: list):
    total = len(quiz)
    score = int(context.user_data.get("eng_score", 0))
    passed = total > 0 and score / total >= ENG_QUIZ_PASS
    if passed:
        done = _eng_done_list(context)
        if lesson_id not in done:
            done.append(lesson_id)
        context.user_data["eng_score"] = 0
    verdict = t(lang, "eng_verdict_pass") if passed else t(lang, "eng_verdict_fail")
    text = t(lang, "eng_quiz_result", score=score, total=total, verdict=verdict)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "eng_retry_btn"), callback_data=f"eng:q:{lesson_id}:0")],
        [InlineKeyboardButton(t(lang, "eng_back_btn"), callback_data=f"eng:l:{lesson_id}")],
    ])
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass


ENGLISH_TUTOR_PROMPT = (
    "You are a friendly and helpful English Language Tutor for industrial engineers and factory staff. "
    "The user wants to practice English communication or ask questions about industrial English. "
    "Instructions:\n"
    "1. Always reply in clear, professional English.\n"
    "2. If the user makes any grammatical, spelling, or vocabulary mistakes, provide a gentle correction and short explanation under a '💡 Correction / Maslahat:' section.\n"
    "3. Keep responses conversational, concise (2-4 sentences), and ask a question to continue the dialogue.\n"
    "4. If the user asks in Uzbek or Chinese, explain in that language first, then provide the English phrase."
)


async def handle_english_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Kurs inline callbacklari:
    eng:menu | eng:l:<id> | eng:q:<id>:<n> | eng:a:<id>:<n>:<k> | eng:fc:<id>:<k> | eng:stats | eng:reset | eng:tutor"""
    query = update.callback_query
    lang = get_lang(context)
    if not ENG_COURSE:
        await query.answer(text=t(lang, "eng_course_empty"), show_alert=True)
        return

    parts = data.split(":")

    if parts[1] == "menu":
        await query.answer()
        try:
            await query.edit_message_text(
                _eng_menu_text(context, lang), parse_mode="Markdown",
                reply_markup=build_eng_menu_keyboard(context, lang), disable_web_page_preview=True,
            )
        except Exception:
            pass
        return

    if parts[1] == "tutor":
        await query.answer()
        context.user_data["mode"] = "eng_tutor"
        try:
            await query.message.reply_text(t(lang, "eng_tutor_intro"), parse_mode="Markdown")
        except Exception:
            pass
        return

    if parts[1] == "stats":
        await query.answer()
        done = set(_eng_done_list(context)) & {l.get("id") for l in ENG_COURSE}
        total = len(ENG_COURSE)
        pct = int((len(done) / total) * 100) if total else 0
        badge = "🏆 *Tabriklaymiz! Barcha darslarni to'liq yakunladingiz!*" if pct == 100 else "💪 O'rganishda davom eting!"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Qayta boshlash (Reset)", callback_data="eng:reset")],
            [InlineKeyboardButton(t(lang, "eng_back_btn"), callback_data="eng:menu")],
        ])
        text = t(lang, "eng_stats_text", done=len(done), total=total, pct=pct, badge=badge)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        return

    if parts[1] == "reset":
        await query.answer("Natijalar qayta boshlandi!")
        context.user_data["eng_done"] = []
        try:
            await query.edit_message_text(
                _eng_menu_text(context, lang), parse_mode="Markdown",
                reply_markup=build_eng_menu_keyboard(context, lang),
            )
        except Exception:
            pass
        return

    lesson_id = parts[2]
    idx, lesson = _eng_lesson_by_id(lesson_id)
    if lesson is None:
        await query.answer(text=t(lang, "eng_course_empty"), show_alert=True)
        return

    if parts[1] == "l":
        await query.answer()
        text = build_eng_lesson_text(lesson, idx, lang)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "eng_quiz_btn"), callback_data=f"eng:q:{lesson_id}:0")],
            [InlineKeyboardButton(t(lang, "eng_flashcards_btn"), callback_data=f"eng:fc:{lesson_id}:0")],
            [InlineKeyboardButton(t(lang, "eng_back_btn"), callback_data="eng:menu")],
        ])
        try:
            await query.edit_message_text(
                text, parse_mode="Markdown", reply_markup=kb, disable_web_page_preview=True
            )
        except Exception:
            try:
                await query.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                pass
        return

    if parts[1] == "fc":
        await query.answer()
        words = lesson.get("words", [])
        if not words:
            return
        w_idx = max(0, min(int(parts[3]) if len(parts) > 3 else 0, len(words) - 1))
        w = words[w_idx]
        title = lesson.get("title_uz", lesson.get("title_en", ""))
        text = t(lang, "eng_fc_card", title=title, en=w.get("en", ""), uz=w.get("uz", ""), zh=w.get("zh", ""), cur=w_idx + 1, total=len(words))
        nav = []
        if w_idx > 0:
            nav.append(InlineKeyboardButton(t(lang, "eng_fc_prev"), callback_data=f"eng:fc:{lesson_id}:{w_idx - 1}"))
        if w_idx < len(words) - 1:
            nav.append(InlineKeyboardButton(t(lang, "eng_fc_next"), callback_data=f"eng:fc:{lesson_id}:{w_idx + 1}"))
        rows = []
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(t(lang, "eng_back_btn"), callback_data=f"eng:l:{lesson_id}")])
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        except Exception:
            pass
        return

    quiz = lesson.get("quiz", [])

    if parts[1] == "q":
        n = int(parts[3])
        if n == 0:
            context.user_data["eng_score"] = 0
        if n >= len(quiz):
            await _eng_show_result(query, context, lang, lesson_id, quiz)
            return
        q = quiz[n]
        rows = [[InlineKeyboardButton(str(opt)[:60], callback_data=f"eng:a:{lesson_id}:{n}:{k}")]
                for k, opt in enumerate(q.get("options", []))]
        rows.append([InlineKeyboardButton(t(lang, "eng_back_btn"), callback_data=f"eng:l:{lesson_id}")])
        text = t(lang, "eng_quiz_q", q=q.get("q", "")) + f"\n\n({n + 1}/{len(quiz)})"
        await query.answer()
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        except Exception:
            pass
        return

    if parts[1] == "a":
        n = int(parts[3])
        k = int(parts[4])
        if n >= len(quiz):
            await query.answer()
            return
        q = quiz[n]
        correct_idx = int(q.get("correct", 0))
        is_correct = (k == correct_idx)
        if is_correct:
            context.user_data["eng_score"] = int(context.user_data.get("eng_score", 0)) + 1
        await query.answer()
        msg = t(lang, "eng_quiz_correct") if is_correct else t(lang, "eng_quiz_wrong", a=q["options"][correct_idx])
        if n + 1 < len(quiz):
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                t(lang, "eng_quiz_next"), callback_data=f"eng:q:{lesson_id}:{n + 1}"
            )]])
            try:
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                pass
        else:
            await _eng_show_result(query, context, lang, lesson_id, quiz)
        return

    await query.answer()


def format_raw_tags(tags, machine_label: str, lang: str) -> str:
    lines = [t(lang, "raw_fallback_header", machine=machine_label)]
    kind_labels = KIND_LABELS.get(lang, KIND_LABELS["uz"])
    for tg in tags[:10]:
        scope = t(lang, "station_n", n=tg["station"]) if tg.get("station") else t(lang, "general_scope")
        lines.append(
            f"📍 {tg['address']} ({tg['data_type']}) | {kind_labels.get(tg['kind'], tg['kind'])}\n"
            f"   {scope} | {tg.get('group') or '—'}\n"
            f"   {tg.get('name') or '—'} | {tg.get('comment') or '—'}\n"
        )
    lines.append(t(lang, "raw_fallback_footer"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AI provayderlar (avtomatik almashinuv + vaqtinchalik "dam olish")
# ---------------------------------------------------------------------------

_provider_cooldown_until = {}

# ---------------------------------------------------------------------------
# Foydalanuvchi boshiga so'rov chegarasi (bepul AI limitlarini adolatli
# taqsimlash uchun) — adminlar bundan mustasno.
# ---------------------------------------------------------------------------

_user_request_times = {}  # user_id -> [timestamp, timestamp, ...] (oxirgi 24 soat)


def check_rate_limit(user_id: int) -> bool:
    """True — ruxsat berilsin, False — hozircha limit tugagan (daqiqalik yoki kunlik)."""
    if is_admin(user_id):
        return True
    now = time.time()
    # Xotira tozaligi: uzoq vaqt faol bo'lmagan foydalanuvchilarni o'chiramiz
    if len(_user_request_times) > 2000:
        for uid in [u for u, ts_list in _user_request_times.items() if not ts_list or now - ts_list[-1] > 86400]:
            _user_request_times.pop(uid, None)
    all_hits = [ts for ts in _user_request_times.get(user_id, []) if now - ts < 86400]
    minute_hits = [ts for ts in all_hits if now - ts < RATE_LIMIT_WINDOW_SEC]
    if len(minute_hits) >= RATE_LIMIT_PER_MIN or len(all_hits) >= RATE_LIMIT_PER_DAY:
        _user_request_times[user_id] = all_hits
        return False
    all_hits.append(now)
    _user_request_times[user_id] = all_hits
    return True


def _provider_ready(name: str) -> bool:
    return time.time() >= _provider_cooldown_until.get(name, 0)


def _set_cooldown(name: str, seconds: float):
    _provider_cooldown_until[name] = time.time() + seconds
    logger.info("'%s' provayderi %.0f soniyaga dam oladi", name, seconds)


def _cooldown_seconds_for_error(e: Exception) -> float:
    s = str(e).lower()
    if "resource_exhausted" in s or ("quota" in s and ("day" in s or "free_tier_requests" in s)):
        return 6 * 3600
    if "429" in s or "rate" in s or "tpm" in s or "payload too large" in s or "too large" in s:
        return 60
    return 300


def _gemini_generate(system_prompt: str, user_text: str) -> str:
    resp = genai_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_text,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=AI_MAX_OUTPUT_TOKENS,
            temperature=0.3,
        ),
    )
    return (resp.text or "").strip()


def _openai_compatible_chat(base_url, api_key, model, system_prompt, user_text, extra_headers=None):
    """OpenAI-mos chat/completions API'sini urllib orqali chaqiradi (SDK shart emas).
    Groq, OpenRouter, DeepSeek, Mistral, Cerebras, Together — barchasi shu formatda."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": AI_MAX_OUTPUT_TOKENS,
        "temperature": 0.3,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    }
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base_url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=AI_HTTP_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8", "ignore"))
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("provayder bo'sh javob qaytardi")
    return (choices[0].get("message", {}).get("content") or "").strip()


def _anthropic_chat(api_key, model, system_prompt, user_text):
    """Anthropic Claude Messages API (urllib orqali, SDK shart emas)."""
    payload = {
        "model": model,
        "max_tokens": AI_MAX_OUTPUT_TOKENS,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_text}],
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=AI_HTTP_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8", "ignore"))
    content = body.get("content") or []
    if not content:
        raise RuntimeError("Claude provayderi bo'sh javob qaytardi")
    return (content[0].get("text") or "").strip()


def _openrouter_chat_cascade(api_key, primary_model, system_prompt, user_text):
    """OpenRouter orqali avtomatik zaxira modellari bilan so'rov yuborish.
    Agar asosiy bepul model band bo'lsa, zaxiradagi bepul modellarni ketma-ket sinab ko'radi."""
    models_to_try = [primary_model]
    for b_mod in [
        "meta-llama/llama-3.3-70b-instruct:free",
        "deepseek/deepseek-r1:free",
        "google/gemini-2.0-flash-exp:free",
        "qwen/qwen-2.5-72b-instruct:free",
    ]:
        if b_mod not in models_to_try:
            models_to_try.append(b_mod)

    last_err = None
    for mod in models_to_try:
        try:
            return _openai_compatible_chat(
                "https://openrouter.ai/api/v1/chat/completions",
                api_key, mod, system_prompt, user_text,
                {"HTTP-Referer": "https://t.me/plc_fault_bot", "X-Title": "PLC Fault Bot"},
            )
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("OpenRouter modellari javob bermadi")


def _make_openai_provider(base_url, api_key, model, extra_headers=None):
    def _gen(system_prompt: str, user_text: str) -> str:
        return _openai_compatible_chat(base_url, api_key, model, system_prompt, user_text, extra_headers)
    return _gen


def _make_anthropic_provider(api_key, model):
    def _gen(system_prompt: str, user_text: str) -> str:
        return _anthropic_chat(api_key, model, system_prompt, user_text)
    return _gen


def _make_openrouter_provider(api_key, model):
    def _gen(system_prompt: str, user_text: str) -> str:
        return _openrouter_chat_cascade(api_key, model, system_prompt, user_text)
    return _gen


# Barcha AI'lar ustuvorlik tartibida:
# 1. Gemini (SDK) — eng tezkor va asosiy
# 2. Groq (Llama 3.3 70B / Mixtral) — chaqmoqdek tez bepul zaxira
# 3. OpenAI (GPT-4o-mini / GPT-4o) — rasmiy OpenAI
# 4. Anthropic Claude (Claude 3.5 Haiku / Sonnet)
# 5. DeepSeek (DeepSeek-V3 / R1)
# 6. Mistral AI (Mistral Small / Large)
# 7. Cerebras (Llama-3.3 70b)
# 8. Together AI
# 9. xAI (Grok-2)
# 10. Cohere (Command R)
# 11. OpenRouter (Ko'p modelli bepul kaskad)
AI_PROVIDERS = [("Gemini", _gemini_generate)]

if GROQ_API_KEY:
    AI_PROVIDERS.append(("Groq", _make_openai_provider("https://api.groq.com/openai/v1/chat/completions", GROQ_API_KEY, GROQ_MODEL)))

if OPENAI_API_KEY:
    AI_PROVIDERS.append(("OpenAI", _make_openai_provider("https://api.openai.com/v1/chat/completions", OPENAI_API_KEY, OPENAI_MODEL)))

if ANTHROPIC_API_KEY:
    AI_PROVIDERS.append(("Claude", _make_anthropic_provider(ANTHROPIC_API_KEY, ANTHROPIC_MODEL)))

if DEEPSEEK_API_KEY:
    AI_PROVIDERS.append(("DeepSeek", _make_openai_provider("https://api.deepseek.com/chat/completions", DEEPSEEK_API_KEY, DEEPSEEK_MODEL)))

if MISTRAL_API_KEY:
    AI_PROVIDERS.append(("Mistral", _make_openai_provider("https://api.mistral.ai/v1/chat/completions", MISTRAL_API_KEY, MISTRAL_MODEL)))

if CEREBRAS_API_KEY:
    AI_PROVIDERS.append(("Cerebras", _make_openai_provider("https://api.cerebras.ai/v1/chat/completions", CEREBRAS_API_KEY, CEREBRAS_MODEL)))

if TOGETHER_API_KEY:
    AI_PROVIDERS.append(("Together", _make_openai_provider("https://api.together.xyz/v1/chat/completions", TOGETHER_API_KEY, TOGETHER_MODEL)))

if XAI_API_KEY:
    AI_PROVIDERS.append(("xAI", _make_openai_provider("https://api.x.ai/v1/chat/completions", XAI_API_KEY, XAI_MODEL)))

if COHERE_API_KEY:
    AI_PROVIDERS.append(("Cohere", _make_openai_provider("https://api.cohere.com/v2/chat", COHERE_API_KEY, COHERE_MODEL)))

if OPENROUTER_API_KEY:
    AI_PROVIDERS.append(("OpenRouter", _make_openrouter_provider(OPENROUTER_API_KEY, OPENROUTER_MODEL)))



def _generate_sync(system_prompt: str, user_text: str):
    any_tried = False
    for name, fn in AI_PROVIDERS:
        if not _provider_ready(name):
            continue
        any_tried = True
        try:
            result = fn(system_prompt, user_text)
            if result:
                return result
            raise RuntimeError("bo'sh javob qaytdi")
        except Exception as e:
            logger.warning("%s provayderi ishlamadi: %s", name, e)
            _set_cooldown(name, _cooldown_seconds_for_error(e))
            continue
    if not any_tried:
        logger.warning("Barcha AI provayderlar hozircha dam olmoqda")
    return None


LANG_REMINDER = {
    "uz": "\n\n[TIZIM: Javobni albatta O'ZBEK tilida yoz — faqat agar yuqoridagi savol aniq ingliz yoki xitoy tilida yozilgan bo'lsa, o'sha tilda javob ber. Boshqa hech qanday tilda yozma.]",
    "en": "\n\n[SYSTEM: Reply in ENGLISH — unless the message above is clearly written in Uzbek or Chinese, in which case reply in that language instead. Do not use any other language.]",
    "zh": "\n\n[系统：请务必用中文回答——除非上面的消息明显是用乌兹别克语或英语写的，此时请改用该语言回答。不要使用任何其他语言。]",
}



async def safe_reply_text(update: Update, text: str, reply_markup=None, prefer_markdown: bool = True):
    """Send text; try Markdown, fall back to plain text if Telegram rejects formatting.
    Also split messages longer than Telegram limit (~4096).
    """
    if not text:
        return
    # Telegram hard limit
    MAX_LEN = 4000
    chunks = []
    while text:
        if len(text) <= MAX_LEN:
            chunks.append(text)
            break
        # split on nearest newline
        cut = text.rfind("\n", 0, MAX_LEN)
        if cut < MAX_LEN // 2:
            cut = MAX_LEN
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")

    for i, chunk in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        if prefer_markdown:
            try:
                await update.message.reply_text(chunk, parse_mode="Markdown", reply_markup=markup)
                continue
            except Exception as e:
                logger.warning("Markdown yuborish muvaffaqiyatsiz, plain text: %s", e)
        await update.message.reply_text(chunk, reply_markup=markup)


async def ask_ai(system_prompt: str, user_text: str, lang: str = None):
    # Token abuse himoyasi: foydalanuvchi matni chegaradan uzun bo'lsa kesiladi
    user_text = user_text[:AI_MAX_INPUT_CHARS]
    if lang:
        user_text = user_text + LANG_REMINDER.get(lang, LANG_REMINDER["uz"])
    return await asyncio.to_thread(_generate_sync, system_prompt, user_text)


def log_query(update: Update, line_id: str, text: str, answer: str):
    try:
        with open(QUERY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "time": datetime.now().isoformat(),
                        "user_id": update.effective_user.id,
                        "username": update.effective_user.username,
                        "line": line_id,
                        "question": text,
                        "answer": answer,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception as e:
        logger.warning("Log yozishda xatolik: %s", e)


# ---------------------------------------------------------------------------
# Javoblarni keshlash — bir xil savol qayta so'ralsa, AI'ga murojaat qilmasdan
# darhol javob beriladi (token va vaqt tejaydi, chunki zavodda bir xil
# muammolar tez-tez takrorlanadi).
# ---------------------------------------------------------------------------

try:
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        ANSWER_CACHE = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    ANSWER_CACHE = {}


def _cache_key(scope: str, lang: str, text: str) -> str:
    return f"{scope}:{lang}:{text.strip().lower()}"


def cache_get(scope: str, lang: str, text: str):
    entry = ANSWER_CACHE.get(_cache_key(scope, lang, text))
    if not entry:
        return None
    ts, answer = entry
    if time.time() - ts > CACHE_TTL_HOURS * 3600:
        return None
    return answer


def cache_set(scope: str, lang: str, text: str, answer: str):
    ANSWER_CACHE[_cache_key(scope, lang, text)] = [time.time(), answer]
    # Kesh cheksiz o'smasligi uchun: avval muddati o'tganlarni, keyin eng
    # eskilarini tozalaymiz.
    if len(ANSWER_CACHE) > 800:
        now = time.time()
        ttl = CACHE_TTL_HOURS * 3600
        for k in [k for k, v in ANSWER_CACHE.items() if now - v[0] > ttl]:
            ANSWER_CACHE.pop(k, None)
        while len(ANSWER_CACHE) > 800:
            oldest = min(ANSWER_CACHE, key=lambda k: ANSWER_CACHE[k][0])
            ANSWER_CACHE.pop(oldest, None)
    try:
        atomic_json_write(CACHE_PATH, ANSWER_CACHE)
    except Exception as e:
        logger.warning("Keshni saqlashda xatolik: %s", e)


# ---------------------------------------------------------------------------
# Fikr-mulohaza (👍/👎) va "Hal bo'ldimi?" tugmalari
# ---------------------------------------------------------------------------

# answer_id -> {"line_id", "lang", "question", "answer"} — feedback/eskalatsiya
# tugmasi bosilganda kerakli ma'lumotni topish uchun vaqtinchalik xotira.
PENDING_ANSWERS = {}


def build_feedback_keyboard(answer_id: str, lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👍", callback_data=f"fb:up:{answer_id}"),
         InlineKeyboardButton("👎", callback_data=f"fb:down:{answer_id}")],
    ])


def register_answer(line_id: str, lang: str, question: str, answer: str) -> str:
    answer_id = uuid.uuid4().hex[:12]
    PENDING_ANSWERS[answer_id] = {
        "line_id": line_id, "lang": lang, "question": question, "answer": answer,
    }
    # Xotira cheksiz o'smasligi uchun eski yozuvlarni tozalab boramiz.
    if len(PENDING_ANSWERS) > 500:
        for old_key in list(PENDING_ANSWERS.keys())[:100]:
            PENDING_ANSWERS.pop(old_key, None)
    return answer_id


def log_feedback(answer_id: str, kind: str, value: str):
    try:
        with open("feedback.log", "a", encoding="utf-8") as f:
            info = PENDING_ANSWERS.get(answer_id, {})
            f.write(json.dumps({
                "time": datetime.now().isoformat(),
                "answer_id": answer_id,
                "kind": kind,
                "value": value,
                "line": info.get("line_id"),
                "question": info.get("question"),
            }, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Feedback logga yozishda xatolik: %s", e)


async def handle_library_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Kutubxona inline tugmalari: fayl olish, sahifa, qo'llanma qidiruvi,
    admin o'chirish (tasdiqlash bilan)."""
    query = update.callback_query
    lang = get_lang(context)
    uid = update.effective_user.id
    chat_id = query.message.chat_id if query and query.message else update.effective_chat.id

    if data == "libnoop":
        await query.answer()
        return

    if data.startswith("libget:"):
        doc_id = re.sub(r"[^a-f0-9]", "", data.split(":", 1)[1])[:16]
        doc = library_get(doc_id)
        if not doc or not doc.get("visible", True):
            await query.answer(text=t(lang, "lib_not_found"), show_alert=True)
            return
        await query.answer()
        if not await _send_library_doc(context, chat_id, doc, lang):
            try:
                await context.bot.send_message(chat_id=chat_id, text=t(lang, "lib_not_found"))
            except Exception:
                pass
        return

    if data.startswith("libpg:") or data.startswith("libmenu:"):
        try:
            page = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            page = 0
        docs = library_list_visible()
        ln = get_selected_line(context)
        machine_label = ln.label if (ln and ln.manual_pdf) else None
        kb, _ = build_library_keyboard(docs, page, lang, machine_label)
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    if data == "libman:":
        ln = get_selected_line(context)
        if ln is None or not ln.manual_pdf:
            await query.answer(text=t(lang, "library_none_available"), show_alert=True)
            return
        await query.answer()
        context.user_data["mode"] = "library_wait"
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=t(lang, "library_ask_topic", machine=ln.label),
                reply_markup=kb_for(update),
            )
        except Exception:
            pass
        return

    if data.startswith(("libdelask:", "libdelyes:", "libdelno:")):
        if not is_admin(uid):
            await query.answer(text=t(lang, "admin_only"), show_alert=True)
            return
        doc_id = re.sub(r"[^a-f0-9]", "", data.split(":", 1)[1])[:16]
        doc = library_get(doc_id)
        if not doc:
            await query.answer(text=t(lang, "lib_del_fail"), show_alert=True)
            return
        title = (doc.get("title") or doc_id)[:60]
        if data.startswith("libdelask:"):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(t(lang, "lib_yes"), callback_data=safe_callback_data("libdelyes", doc_id)),
                InlineKeyboardButton(t(lang, "lib_no"), callback_data=safe_callback_data("libdelno", doc_id)),
            ]])
            await query.answer()
            try:
                await query.edit_message_text(
                    t(lang, "lib_del_confirm", title=title), parse_mode="Markdown", reply_markup=kb
                )
            except Exception:
                pass
            return
        if data.startswith("libdelno:"):
            await query.answer()
            try:
                await query.edit_message_text("❌")
            except Exception:
                pass
            return
        await query.answer()
        _delete_library_doc(doc)
        try:
            await query.edit_message_text(t(lang, "lib_del_done", title=title))
        except Exception:
            pass
        return

    await query.answer()


async def handle_req_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Ruxsatsiz foydalanuvchilarning ro'yxatdan o'tish yoki til so'rovlari."""
    query = update.callback_query
    lang = get_lang(context)
    await query.answer()
    if data == "req:start":
        await query.message.reply_text(t(lang, "req_prompt"), parse_mode="Markdown")
    elif data == "req:lang":
        await query.message.reply_text(TEXT["uz"]["choose_lang"], reply_markup=language_keyboard())


async def handle_feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    lang = context.user_data.get("lang", "uz")
    uid = update.effective_user.id

    # 1. Ruxsatsiz foydalanuvchilar so'rovlari (ruxsat tekshiruvidan oldin)
    if data.startswith("req:"):
        await handle_req_callback(update, context, data)
        return

    # 2. XAVFSIZLIK: har qanday boshqa tugma bosilishidan oldin ruxsatni tekshiramiz
    if not is_authorized(uid):
        await query.answer(text=t(lang, "security_blocked"), show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    # 3. Ro'yxatdan o'tish arizasini tasdiqlash/rad etish (reg:) — faqat admin
    if data.startswith("reg:"):
        if not is_admin(uid):
            await query.answer(text=t(lang, "admin_only"), show_alert=True)
            return
        _, action, reg_id = data.split(":", 2)
        await handle_registration_callback(update, context, action, reg_id)
        return

    # 4. Admin paneli (adm: va usr:)
    if data.startswith("adm:") or data.startswith("usr"):
        await handle_admin_panel_callback(update, context, data)
        return

    # 5. ESP32 boshqaruvi (esp:)
    if data.startswith("esp:"):
        await handle_esp32_callback(update, context, data)
        return

    # 6. Kutubxona inline tugmalari
    if data.startswith("lib"):
        await handle_library_callback(update, context, data)
        return

    # 7. Ingliz tili kursi inline tugmalari
    if data.startswith("eng:"):
        await handle_english_callback(update, context, data)
        return

    await query.answer()

    # 8. AI javobiga feedback (fb:)
    parts = data.split(":", 2)
    if len(parts) == 3 and parts[0] == "fb":
        _, value, answer_id = parts
        log_feedback(answer_id, "feedback", value)
        msg = t(lang, "feedback_thanks_up") if value == "up" else t(lang, "feedback_thanks_down")
        await query.answer(text=msg, show_alert=False)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    # 9. Qolgan barcha inline tugmalar ESP32 relay orqali uzatiladi
    if ESP32_CMD_URL:
        res = await esp32_relay(
            "callback", update.effective_chat.id, data,
            from_name=(update.effective_user.full_name or "")[:64],
            query_id=query.id,
        )
        if res:
            context.user_data["esp32_session"] = bool(res.get("session_open"))


# ---------------------------------------------------------------------------
# Ma'lumot sifati: mos kelmagan so'rovlarni qayd etish (keyinchalik TIA
# Portal'dagi tag izohlarini to'ldirish uchun foydali ro'yxat bo'ladi)
# ---------------------------------------------------------------------------

def log_no_match(line_id: str, lang: str, text: str):
    try:
        with open(NO_MATCH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": datetime.now().isoformat(), "line": line_id, "lang": lang, "text": text,
            }, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("no_match logga yozishda xatolik: %s", e)


# ---------------------------------------------------------------------------
# Rasm orqali murojaat: HMI ekrani/indikator suratidan matnni o'qish
# ---------------------------------------------------------------------------

IMAGE_ANALYZE_PROMPT = (
    "This is a photo taken by a factory worker of an industrial HMI screen, "
    "control panel, indicator lamp, or physical equipment fault. Respond in "
    "EXACTLY this two-line format, nothing else:\n"
    "TEXT: <any visible error code/alarm/fault text on screen, or NONE if there is no readable text>\n"
    "KEYWORDS: <3-6 short English and/or Chinese technical keywords describing "
    "the visible component and/or fault, comma separated (e.g. conveyor, motor, "
    "sensor, light curtain, valve, robot arm, cable, red light, error icon)>"
)


def _analyze_image_sync(image_bytes: bytes) -> str:
    image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    resp = genai_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[image_part, IMAGE_ANALYZE_PROMPT],
    )
    return (resp.text or "").strip()


async def analyze_image(image_bytes: bytes):
    """Rasmdan (1) o'qiladigan matn (bo'lsa) va (2) qidiruv uchun kalit
    so'zlarni ajratib oladi — hatto matn umuman bo'lmasa ham (masalan faqat
    yonib turgan lampa), kalit so'zlar orqali tegishli taglarni topish
    imkonini beradi."""
    try:
        raw = await asyncio.to_thread(_analyze_image_sync, image_bytes)
    except Exception as e:
        logger.warning("Rasmni tahlil qilishda xatolik: %s", e)
        _set_cooldown("Gemini", _cooldown_seconds_for_error(e))
        return None, set()

    text_val, keywords = None, set()
    for line in raw.splitlines():
        upper = line.strip().upper()
        if upper.startswith("TEXT:"):
            v = line.split(":", 1)[1].strip()
            text_val = None if (not v or v.upper() == "NONE") else v
        elif upper.startswith("KEYWORDS:"):
            v = line.split(":", 1)[1].strip()
            keywords = {w.strip().lower() for w in v.split(",") if w.strip()}
    return text_val, keywords


def _generate_vision_sync(system_prompt: str, image_bytes: bytes, caption: str) -> str:
    image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    resp = genai_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[image_part, caption],
        config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
    )
    return (resp.text or "").strip()


async def ask_ai_vision(system_prompt: str, image_bytes: bytes, lang: str):
    """Yakuniy diagnostikani rasmning O'ZIDAN (matn transkriptidan emas)
    to'g'ridan-to'g'ri chiqaradi — indikator rangi, ekran tuzilishi kabi
    OCR orqali yo'qoladigan tafsilotlarni ham hisobga oladi. Faqat Gemini
    orqali ishlaydi (bizning bepul provayderlar orasida rasmni tushunadigan
    yagona model); u band bo'lsa, chaqiruvchi matn-asosidagi zaxiraga o'tadi."""
    caption = "Diagnose the fault visible in this photo." + LANG_REMINDER.get(lang, LANG_REMINDER["uz"])
    try:
        return await asyncio.to_thread(_generate_vision_sync, system_prompt, image_bytes, caption)
    except Exception as e:
        logger.warning("Vision diagnostika xatosi: %s", e)
        _set_cooldown("Gemini", _cooldown_seconds_for_error(e))
        return None


# ---------------------------------------------------------------------------
# ESP32 zavod monitoring integratsiyasi (faqat o'qish, boshqaruv yo'q)
# ---------------------------------------------------------------------------

def _fetch_esp32_status_sync():
    req = urllib.request.Request(ESP32_STATUS_URL, headers={"User-Agent": "plc-bot"})
    with urllib.request.urlopen(req, timeout=ESP32_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def fetch_esp32_status():
    try:
        return await asyncio.to_thread(_fetch_esp32_status_sync)
    except Exception as e:
        logger.warning("ESP32 status olishda xatolik: %s", e)
        return None


# --- BITTA BOT RELAY: ESP32'ga tegishli xabar/tugmalarni /tg orqali yetkazish ---
def _esp32_relay_sync(params: dict):
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(ESP32_CMD_URL + "?" + query, headers={"User-Agent": "plc-bot"})
    with urllib.request.urlopen(req, timeout=ESP32_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


async def esp32_relay(msg_type: str, chat_id, text: str, from_name: str = "", query_id: str = ""):
    """ESP32'ga xabar/callback/start relay qiladi.
    Qaytaradi: {"ok":..,"handled":..} yoki None (relay sozlanmagan/xatolik).
    handled=True bo'lsa — ESP32 xabarni o'zi qayta ishlagan (Python AI'ga yubormaydi)."""
    if not ESP32_CMD_URL:
        return None
    params = {
        "key": ESP32_CMD_KEY, "type": msg_type,
        "chat_id": str(chat_id), "text": text, "from": from_name,
    }
    if query_id:
        params["query_id"] = query_id
    try:
        return await asyncio.to_thread(_esp32_relay_sync, params)
    except Exception as e:
        logger.warning("ESP32 relay xatosi: %s", e)
        return None


UPTIME_UNITS = {
    "uz": ("soat", "daqiqa"), "en": ("h", "m"), "zh": ("小时", "分钟"),
}


def format_esp32_status(data: dict, lang: str) -> str:
    lim = data.get("limits", {})
    uptime_sec = int(data.get("uptime_sec", 0))
    hour_u, min_u = UPTIME_UNITS.get(lang, UPTIME_UNITS["uz"])
    uptime = f"{uptime_sec // 3600} {hour_u} {(uptime_sec % 3600) // 60} {min_u}"
    return t(
        lang, "esp32_status",
        active=t(lang, "esp32_on") if data.get("system_active") else t(lang, "esp32_off"),
        mode=data.get("mode", "?"),
        alarm=t(lang, "esp32_alarm_yes") if data.get("alarm_active") else t(lang, "esp32_alarm_no"),
        air=data.get("air_pressure_bar", "?"), air_norm=lim.get("air_norm", "?"),
        air_min=lim.get("air_min", "?"), air_max=lim.get("air_max", "?"),
        water=data.get("water_pressure_bar", "?"), water_norm=lim.get("water_norm", "?"),
        water_min=lim.get("water_min", "?"), water_max=lim.get("water_max", "?"),
        temp=data.get("water_temp_c", "?"), temp_norm=lim.get("temp_norm", "?"),
        temp_min=lim.get("temp_min", "?"), temp_max=lim.get("temp_max", "?"),
        air_sensor=t(lang, "esp32_sensor_ok") if data.get("air_sensor_ok") else t(lang, "esp32_sensor_bad"),
        water_sensor=t(lang, "esp32_sensor_ok") if data.get("water_sensor_ok") else t(lang, "esp32_sensor_bad"),
        temp_sensor=t(lang, "esp32_sensor_ok") if data.get("temp_sensor_ok") else t(lang, "esp32_sensor_bad"),
        wifi=data.get("wifi_ssid", "?"), uptime=uptime,
    )


def build_esp32_control_keyboard(lang: str = "uz", is_admin_user: bool = False):
    rows = [
        [InlineKeyboardButton("🔄 " + t(lang, "esp_refresh"), callback_data="esp:refresh")],
    ]
    if is_admin_user:
        rows.append([
            InlineKeyboardButton("🚀 " + t(lang, "esp_start"), callback_data="esp:start"),
            InlineKeyboardButton("⏸️ " + t(lang, "esp_stop"), callback_data="esp:stop"),
        ])
        rows.append([
            InlineKeyboardButton("🌐 " + t(lang, "esp_mode1"), callback_data="esp:mode1"),
            InlineKeyboardButton("💨 " + t(lang, "esp_mode2"), callback_data="esp:mode2"),
        ])
        rows.append([
            InlineKeyboardButton("🔕 " + t(lang, "esp_reset_alarm"), callback_data="esp:reset_alarm"),
            InlineKeyboardButton("⚙️ " + t(lang, "esp_limits"), callback_data="esp:limits"),
        ])
    return InlineKeyboardMarkup(rows)


async def handle_esp32_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    uid = update.effective_user.id
    if not is_authorized(uid):
        await update.message.reply_text(t(lang, "access_denied_detail"), parse_mode="Markdown", reply_markup=unauthorized_keyboard(lang))
        return

    if not ESP32_STATUS_URL:
        await update.message.reply_text(t(lang, "esp32_not_configured"), reply_markup=kb_for(update))
        return

    msg_status = await update.message.reply_text(t(lang, "esp32_fetching"))
    data = await fetch_esp32_status()
    kb = build_esp32_control_keyboard(lang, is_admin(uid))
    if data is None:
        offline_text = (
            f"{t(lang, 'esp32_unreachable')}\n\n"
            f"📡 Manzil: `{ESP32_STATUS_URL}`\n"
            "🔍 Tekshiring:\n"
            "1. ESP32 elektr tarmog'iga ulanganmi?\n"
            "2. Zavod Wi-Fi tarmog'i faolmi?\n"
            "3. IP manzil to'g'riligini tekshiring."
        )
        refresh_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 " + t(lang, "esp_refresh"), callback_data="esp:refresh")]])
        try:
            await msg_status.edit_text(offline_text, parse_mode="Markdown", reply_markup=refresh_kb)
        except Exception:
            await update.message.reply_text(offline_text, parse_mode="Markdown", reply_markup=refresh_kb)
        return

    try:
        await msg_status.edit_text(
            format_esp32_status(data, lang), parse_mode="Markdown", reply_markup=kb
        )
    except Exception:
        await update.message.reply_text(
            format_esp32_status(data, lang), parse_mode="Markdown", reply_markup=kb
        )


async def handle_esp32_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """ESP32 boshqaruv inline tugmalari."""
    query = update.callback_query
    lang = get_lang(context)
    uid = update.effective_user.id

    if not is_authorized(uid):
        await query.answer(text=t(lang, "security_blocked"), show_alert=True)
        return

    action = data.split(":", 1)[1] if ":" in data else ""

    if action == "refresh":
        await query.answer("Yangilanmoqda...")
        status_data = await fetch_esp32_status()
        kb = build_esp32_control_keyboard(lang, is_admin(uid))
        if status_data:
            text = format_esp32_status(status_data, lang) + f"\n\n⏱ _Oxirgi yangilanish: {datetime.now().strftime('%H:%M:%S')}_"
            try:
                await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                pass
        else:
            await query.answer(t(lang, "esp32_unreachable"), show_alert=True)
        return

    # Qolgan boshqaruv buyruqlari faqat admin uchun
    if not is_admin(uid):
        await query.answer(t(lang, "admin_only"), show_alert=True)
        return

    cmd_map = {
        "start": "🚀 Start",
        "stop": "⏸️ Stop",
        "mode1": "🌐 1-Rejim",
        "mode2": "💨 2-Rejim",
        "reset_alarm": "/reset_alarm",
    }

    if action in cmd_map:
        await query.answer("Buyruq yuborilmoqda...")
        res = await esp32_relay("message", update.effective_chat.id, cmd_map[action], from_name=(update.effective_user.full_name or "")[:64])
        if res:
            await query.answer(t(lang, "esp_cmd_sent"), show_alert=False)
        else:
            await query.answer(t(lang, "esp_cmd_fail"), show_alert=True)

        await asyncio.sleep(0.5)
        status_data = await fetch_esp32_status()
        if status_data:
            kb = build_esp32_control_keyboard(lang, is_admin(uid))
            text = format_esp32_status(status_data, lang) + f"\n\n⏱ _Oxirgi yangilanish: {datetime.now().strftime('%H:%M:%S')}_"
            try:
                await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                pass
        return

    if action == "limits":
        status_data = await fetch_esp32_status()
        if status_data and "limits" in status_data:
            lim = status_data["limits"]
            msg = (
                f"⚙️ *ESP32 Chegaralari:*\n"
                f"• Havo: {lim.get('air_min')} — {lim.get('air_norm')} — {lim.get('air_max')} bar\n"
                f"• Suv: {lim.get('water_min')} — {lim.get('water_norm')} — {lim.get('water_max')} bar\n"
                f"• Harorat: {lim.get('temp_min')} — {lim.get('temp_norm')} — {lim.get('temp_max')} °C"
            )
            await query.answer(msg, show_alert=True)
        else:
            await query.answer("Limitlarni olib bo'lmadi.", show_alert=True)
        return


# ---------------------------------------------------------------------------
# Telegram handlerlar
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # BITTA BOT: /start orqali ESP32 monitoring tizimiga ham ro'yxatdan o'tish
    # so'rovi yuboriladi (foydalanuvchi noma'lum bo'lsa, ESP32 admin tasdiqlaydi).
    if ESP32_CMD_URL:
        try:
            await esp32_relay(
                "start", update.effective_chat.id, "/start",
                from_name=(update.effective_user.full_name or "")[:64],
            )
        except Exception:
            pass

    # Avval til (ro'yxatdan o'tmaganlar ham til tanlay oladi)
    if not context.user_data.get("lang"):
        await update.message.reply_text(TEXT["uz"]["choose_lang"], reply_markup=language_keyboard())
        return

    lang = get_lang(context)

    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(
            t(lang, "access_denied_detail"),
            parse_mode="Markdown",
            reply_markup=unauthorized_keyboard(lang),
        )
        return

    ln = get_selected_line(context)
    if ln is not None:
        await update.message.reply_text(
            t(lang, "returning_greeting", machine=ln.label),
            parse_mode="Markdown",
            reply_markup=kb_for(update),
        )
        return
    await update.message.reply_text(
        t(lang, "greeting_after_lang", ai=AI_CHAT_LABEL),
        reply_markup=kb_for(update),
    )


async def choose_machine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(lang, "access_denied_detail"), parse_mode="Markdown")
        return
    await update.message.reply_text(t(lang, "choose_machine_prompt"), reply_markup=kb_for(update))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(lang, "access_denied_detail"), parse_mode="Markdown")
        return
    await update.message.reply_text(t(lang, "help_text"), parse_mode="Markdown", reply_markup=kb_for(update))


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(lang, "access_denied"))
        return
    lines = [t(lang, "status_header")]
    for name, _ in AI_PROVIDERS:
        if _provider_ready(name):
            lines.append(t(lang, "status_ready", name=name))
        else:
            wait = int(_provider_cooldown_until[name] - time.time())
            lines.append(t(lang, "status_cooldown", name=name, sec=wait))
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _looks_like_phone(token: str) -> bool:
    cleaned = token.replace(" ", "").replace("-", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    return cleaned.isdigit() and len(cleaned) >= 7


# ---------------------------------------------------------------------------
# Xodimlarni osonroq ro'yxatdan o'tkazish: /register orqali o'zi so'rov
# yuboradi, admin bitta tugma bilan tasdiqlaydi yoki rad etadi.
# ---------------------------------------------------------------------------

try:
    with open(PENDING_REG_PATH, "r", encoding="utf-8") as f:
        PENDING_REGISTRATIONS = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    PENDING_REGISTRATIONS = {}


def _save_pending_registrations():
    try:
        atomic_json_write(PENDING_REG_PATH, PENDING_REGISTRATIONS)
    except Exception as e:
        logger.warning("pending_registrations saqlashda xatolik: %s", e)


async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    uid = update.effective_user.id

    if is_authorized(uid):
        await update.message.reply_text(t(lang, "already_registered"))
        return

    if not check_reg_rate_limit(uid):
        await update.message.reply_text(t(lang, "reg_rate_limited"))
        return

    if len(context.args) < 2:
        await update.message.reply_text(t(lang, "register_usage"))
        return

    phone = context.args[-1]
    name = " ".join(context.args[:-1]).strip()
    if not _looks_like_phone(phone) or not name:
        await update.message.reply_text(t(lang, "register_usage"))
        return

    reg_id = uuid.uuid4().hex[:10]
    PENDING_REGISTRATIONS[reg_id] = {
        "uid": uid, "name": name, "phone": phone,
        "requested_at": datetime.now().isoformat(),
    }
    _save_pending_registrations()

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅", callback_data=f"reg:approve:{reg_id}"),
        InlineKeyboardButton("❌", callback_data=f"reg:reject:{reg_id}"),
    ]])
    for admin_uid in ADMIN_USER_IDS:
        admin_lang = employee_lang(context, admin_uid)
        try:
            await context.bot.send_message(
                chat_id=admin_uid,
                text=t(admin_lang, "register_admin_notice", name=name, phone=phone, uid=uid),
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Adminga registratsiya so'rovini yuborishda xatolik: %s", e)

    await update.message.reply_text(t(lang, "register_sent"))


async def handle_registration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, reg_id: str):
    query = update.callback_query
    lang = get_lang(context)

    # XAVFSIZLIK: ikkinchi marta tekshirish (bu funksiya boshqa joydan
    # chaqirilib qolsa ham himoyalangan bo'lsin).
    if not is_admin(update.effective_user.id):
        await query.answer(text=t(lang, "admin_only"), show_alert=True)
        return

    reg = PENDING_REGISTRATIONS.pop(reg_id, None)
    _save_pending_registrations()
    if not reg:
        await query.edit_message_text(t(lang, "register_already_handled"))
        return

    applicant_uid = reg["uid"]
    applicant_lang = employee_lang(context, applicant_uid)

    if action == "approve":
        ALLOWED_USERS[applicant_uid] = {
            "name": reg["name"], "phone": reg["phone"], "added_at": datetime.now().isoformat(),
        }
        _save_allowed_users()
        await query.edit_message_text(t(lang, "register_approved_admin", name=reg["name"]))
        try:
            await context.bot.send_message(chat_id=applicant_uid, text=t(applicant_lang, "register_approved_user"))
        except Exception:
            pass
    else:
        await query.edit_message_text(t(lang, "register_rejected_admin", name=reg["name"]))
        try:
            await context.bot.send_message(chat_id=applicant_uid, text=t(applicant_lang, "register_rejected_user"))
        except Exception:
            pass


async def adduser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(t(lang, "adduser_usage"))
        return
    uid = int(context.args[0])
    rest = context.args[1:]
    phone = ""
    if rest and _looks_like_phone(rest[-1]):
        phone = rest[-1]
        rest = rest[:-1]
    name = " ".join(rest).strip() or str(uid)
    ALLOWED_USERS[uid] = {"name": name, "phone": phone, "added_at": datetime.now().isoformat()}
    _save_allowed_users()
    label = f"{name} ({uid})" + (f", {phone}" if phone else "")
    await update.message.reply_text(t(lang, "user_added", uid=label))


async def setphone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(t(lang, "setphone_usage"))
        return
    uid = int(context.args[0])
    phone = context.args[1]
    if uid not in ALLOWED_USERS:
        await update.message.reply_text(t(lang, "user_not_found", uid=uid))
        return
    ALLOWED_USERS[uid]["phone"] = phone
    _save_allowed_users()
    await update.message.reply_text(t(lang, "phone_updated", uid=uid, phone=phone))


async def removeuser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(t(lang, "adduser_usage"))
        return
    uid = int(context.args[0])
    ALLOWED_USERS.pop(uid, None)
    _save_allowed_users()
    await update.message.reply_text(t(lang, "user_removed", uid=uid))


async def listusers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    admins = ", ".join(str(x) for x in sorted(ADMIN_USER_IDS)) or "—"
    users = "\n".join(
        f"• {info['name']} — ID: {uid}" + (f" — 📞 {info.get('phone')}" if info.get("phone") else "")
        for uid, info in ALLOWED_USERS.items()
    ) or "—"
    await update.message.reply_text(f"👑 Adminlar: {admins}\n\n👤 Xodimlar:\n{users}")


# ---------------------------------------------------------------------------
# Mukammal Admin Paneli (Boshqaruv markazi):
# - Barcha ro'yxatdan o'tgan xodimlarni ko'rish, tekshirish va o'chirish
# - Kutilayotgan arizalarni bir zumda tasdiqlash / rad etish
# - AI tizimlar holatini kuzatish
# - ESP32 boshqaruvi va bot statistikasi
# ---------------------------------------------------------------------------
USERS_PAGE_SIZE = 6


def _users_panel_text(lang: str) -> str:
    admins = ", ".join(str(x) for x in sorted(ADMIN_USER_IDS)) or "—"
    lines = [
        t(lang, "users_panel_title", count=len(ALLOWED_USERS)),
        t(lang, "users_panel_admins", admins=admins),
        t(lang, "users_panel_pending", count=len(PENDING_REGISTRATIONS)),
    ]
    if not ALLOWED_USERS:
        lines.append("")
        lines.append(t(lang, "users_panel_empty"))
    return "\n".join(lines)


def _users_panel_keyboard(page: int, lang: str):
    items = list(ALLOWED_USERS.items())
    total = len(items)
    pages = max(1, (total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * USERS_PAGE_SIZE
    rows = []
    for uid, info in items[start:start + USERS_PAGE_SIZE]:
        nm = (info.get("name") or str(uid))
        rows.append([InlineKeyboardButton(
            f"👤 {nm[:20]} · {uid}", callback_data=safe_callback_data("adm:uview", uid)
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm:upg:{page - 1}"))
    nav.append(InlineKeyboardButton(
        t(lang, "users_page", page=page + 1, total=pages), callback_data="adm:noop"
    ))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"adm:upg:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(t(lang, "admin_btn_adduser"), callback_data="adm:adduser"),
        InlineKeyboardButton("🔙 Bosh menyu", callback_data="adm:menu"),
    ])
    return InlineKeyboardMarkup(rows)


async def show_admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adminning asosiy interaktiv boshqaruv paneli."""
    lang = get_lang(context)
    uid = update.effective_user.id if update and update.effective_user else None
    if not is_admin(uid):
        if update.callback_query:
            await update.callback_query.answer(t(lang, "admin_only"), show_alert=True)
        else:
            await update.message.reply_text(t(lang, "admin_only"))
        return

    admins_str = ", ".join(str(x) for x in sorted(ADMIN_USER_IDS)) or "—"
    esp_status_str = "Sozlangan ✅" if ESP32_STATUS_URL else "Sozlanmagan ⚠️"
    text = t(
        lang, "admin_panel_title",
        admins=admins_str,
        users=len(ALLOWED_USERS),
        pending=len(PENDING_REGISTRATIONS),
        docs=len(LIBRARY_DOCS),
        esp32=esp_status_str,
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{t(lang, 'admin_btn_users')} ({len(ALLOWED_USERS)})", callback_data="adm:users:0")],
        [InlineKeyboardButton(f"{t(lang, 'admin_btn_pending')} ({len(PENDING_REGISTRATIONS)})", callback_data="adm:pending")],
        [
            InlineKeyboardButton(t(lang, "admin_btn_ai"), callback_data="adm:ai"),
            InlineKeyboardButton(t(lang, "admin_btn_esp32"), callback_data="adm:esp32"),
        ],
        [
            InlineKeyboardButton(t(lang, "admin_btn_stats"), callback_data="adm:stats"),
            InlineKeyboardButton("📚 Kutubxona", callback_data="adm:lib"),
        ],
        [InlineKeyboardButton("✖️ Yopish", callback_data="adm:close")],
    ])

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def show_user_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchilar ro'yxati (komanda yoki tugma orqali)."""
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    kb = _users_panel_keyboard(0, lang)
    await update.message.reply_text(_users_panel_text(lang), reply_markup=kb)


async def handle_admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Admin markazining barcha inline tugmalarini boshqarish."""
    query = update.callback_query
    lang = get_lang(context)
    uid = update.effective_user.id

    if not is_admin(uid):
        await query.answer(text=t(lang, "security_blocked"), show_alert=True)
        return

    await query.answer()

    if data in ("adm:close", "usrclose"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if data in ("adm:noop", "usrnoop"):
        return

    if data == "adm:menu":
        await show_admin_dashboard(update, context)
        return

    if data.startswith("adm:users:") or data.startswith("adm:upg:") or data.startswith("usrpg:"):
        try:
            page = int(data.split(":", 2)[-1])
        except (ValueError, IndexError):
            page = 0
        try:
            await query.edit_message_text(
                _users_panel_text(lang), reply_markup=_users_panel_keyboard(page, lang)
            )
        except Exception:
            pass
        return

    if data.startswith("adm:uview:"):
        target_uid_str = data.split(":", 2)[2]
        try:
            target_uid = int(target_uid_str)
            info = ALLOWED_USERS.get(target_uid, {})
        except ValueError:
            target_uid = target_uid_str
            info = {}

        name = info.get("name") or "Noma'lum"
        phone = info.get("phone") or "—"
        added = info.get("added_at", "—")[:19]
        text = t(lang, "admin_uview_text", name=name, uid=target_uid, phone=phone, added=added)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "admin_udel_btn"), callback_data=safe_callback_data("adm:udel", target_uid))],
            [InlineKeyboardButton(t(lang, "admin_back_btn"), callback_data="adm:users:0")],
        ])
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        return

    if data.startswith("adm:udel:") or data.startswith("usrask:"):
        target_uid_str = data.split(":", 2)[-1]
        try:
            target_uid = int(target_uid_str)
            info = ALLOWED_USERS.get(target_uid, {})
        except ValueError:
            info = {}
        name = info.get("name") or target_uid_str
        text = t(lang, "admin_udel_confirm", name=name, uid=target_uid_str)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "admin_udel_yes"), callback_data=safe_callback_data("adm:udelyes", target_uid_str))],
            [InlineKeyboardButton(t(lang, "admin_udel_no"), callback_data=safe_callback_data("adm:uview", target_uid_str))],
        ])
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        return

    if data.startswith("adm:udelyes:") or data.startswith("usryes:"):
        target_uid_str = data.split(":", 2)[-1]
        try:
            ALLOWED_USERS.pop(int(target_uid_str), None)
            _save_allowed_users()
        except ValueError:
            pass
        text = f"🗑 *Xodim o'chirildi:* `{target_uid_str}`\n\nFoydalanuvchi endi botdan foydalana olmaydi."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "admin_btn_users"), callback_data="adm:users:0")],
            [InlineKeyboardButton("🔙 Boshqaruv paneli", callback_data="adm:menu")],
        ])
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        return

    if data == "adm:pending":
        if not PENDING_REGISTRATIONS:
            text = f"⏳ *Kutilayotgan arizalar:*\n\n{t(lang, 'admin_pending_empty')}"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Boshqaruv paneli", callback_data="adm:menu")]])
            try:
                await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                pass
            return

        text = f"⏳ *Kutilayotgan arizalar soni: {len(PENDING_REGISTRATIONS)} ta*\nTasdiqlash yoki rad etish uchun tanlang:"
        rows = []
        for reg_id, reg in list(PENDING_REGISTRATIONS.items())[:10]:
            nm = reg.get("name") or "Noma'lum"
            ph = reg.get("phone") or ""
            rows.append([
                InlineKeyboardButton(f"👤 {nm[:16]} ({ph})", callback_data=f"adm:pview:{reg_id}"),
                InlineKeyboardButton("✅", callback_data=f"reg:approve:{reg_id}"),
                InlineKeyboardButton("❌", callback_data=f"reg:reject:{reg_id}"),
            ])
        rows.append([[InlineKeyboardButton("🔙 Boshqaruv paneli", callback_data="adm:menu")]][0])
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        except Exception:
            pass
        return

    if data.startswith("adm:pview:"):
        reg_id = data.split(":", 2)[2]
        reg = PENDING_REGISTRATIONS.get(reg_id)
        if not reg:
            await query.answer("Bu ariza allaqachon ko'rib chiqilgan.", show_alert=True)
            await show_admin_dashboard(update, context)
            return
        text = t(
            lang, "admin_pending_item",
            name=reg.get("name", "Noma'lum"),
            phone=reg.get("phone", "—"),
            uid=reg.get("uid", "—"),
            date=reg.get("requested_at", "—")[:19],
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Qabul qilish", callback_data=f"reg:approve:{reg_id}"),
                InlineKeyboardButton("❌ Rad etish", callback_data=f"reg:reject:{reg_id}"),
            ],
            [InlineKeyboardButton("⬅️ Arizalar ro'yxati", callback_data="adm:pending")],
        ])
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        return

    if data == "adm:ai":
        lines = ["🤖 *AI Provayderlar holati va kaskadi:*\n━━━━━━━━━━━━━━━━━━━━"]
        for name, _ in AI_PROVIDERS:
            if _provider_ready(name):
                lines.append(f"✅ *{name}* — Faol va tayyor")
            else:
                wait_sec = int(_provider_cooldown_until.get(name, 0) - time.time())
                lines.append(f"⏳ *{name}* — Dam olmoqda (~{wait_sec}s qoldi)")
        lines.append("━━━━━━━━━━━━━━━━━━━━\n_Barcha kalitlar .env orqali boshqariladi._")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Boshqaruv paneli", callback_data="adm:menu")]])
        try:
            await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        return

    if data == "adm:esp32":
        await handle_esp32_status(update, context)
        return

    if data == "adm:stats":
        stats_text = (
            "📊 *Bot statistikasi:*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Ruxsatli xodimlar: *{len(ALLOWED_USERS)}* ta\n"
            f"👑 Administratorlar: *{len(ADMIN_USER_IDS)}* ta\n"
            f"🤖 Faol AI tizimlar: *{len(AI_PROVIDERS)}* ta\n"
            f"📚 Kutubxona fayllari: *{len(LIBRARY_DOCS)}* ta\n"
            f"⏳ Kutilayotgan arizalar: *{len(PENDING_REGISTRATIONS)}* ta\n"
            f"💾 Keshdagi javoblar: *{len(ANSWER_CACHE)}* ta\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Boshqaruv paneli", callback_data="adm:menu")]])
        try:
            await query.edit_message_text(stats_text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        return

    if data == "adm:adduser":
        msg = (
            "➕ *Yangi xodimni ro'yxatga qo'shish:*\n\n"
            "Quyidagi buyruqni yuboring:\n"
            "`/adduser <telegram_id> <Ism Familiya>`\n\n"
            "Masalan:\n`/adduser 123456789 Jasur Aliyev`"
        )
        await query.message.reply_text(msg, parse_mode="Markdown")
        return

    if data == "adm:lib":
        await show_user_library(update, context)
        return


async def nomatches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    try:
        with open(NO_MATCH_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-15:]
    except FileNotFoundError:
        lines = []
    if not lines:
        await update.message.reply_text("Hozircha 'topilmadi' holatlari yo'q.")
        return
    out = []
    for ln_raw in lines:
        try:
            d = json.loads(ln_raw)
            out.append(f"[{d['line']}] {d['text']}")
        except Exception:
            continue
    await update.message.reply_text("*So'nggi mos kelmagan so'rovlar:*\n" + "\n".join(out), parse_mode="Markdown")


async def addcomment_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin ma'lumot bo'shlig'ini to'g'ridan-to'g'ri bot orqali to'ldiradi:
    /addcomment <uskuna_id> <manzil> <izoh matni>
    Bu Excel'ni qayta ochish va prepare_tags.py'ni qayta ishga tushirishni
    talab qilmaydi — o'zgarish darhol kuchga kiradi va faylga saqlanadi."""
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    if len(context.args) < 3:
        await update.message.reply_text(t(lang, "addcomment_usage"))
        return

    line_id, address = context.args[0], context.args[1]
    comment_text = " ".join(context.args[2:])

    ln = LINES.get(line_id)
    if ln is None:
        await update.message.reply_text(t(lang, "addcomment_bad_machine", ids=", ".join(LINES.keys())))
        return

    tg = ln.find_tag(address)
    if tg is None:
        await update.message.reply_text(t(lang, "tag_not_found", addr=address, machine=ln.label))
        return

    tg["comment"] = comment_text
    try:
        atomic_json_write(ln.kb_file, ln.tags, indent=1)
    except Exception as e:
        logger.warning("tags_kb faylini saqlashda xatolik (%s): %s", ln.id, e)
        await update.message.reply_text(t(lang, "addcomment_save_error"))
        return

    await update.message.reply_text(t(lang, "addcomment_done", addr=tg["address"], machine=ln.label, comment=comment_text))


# ---------------------------------------------------------------------------
# Eng ko'p nosozlik chiqaradigan uskuna/manzillar statistikasi
# ---------------------------------------------------------------------------

async def topfaults_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return

    days = 30
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])
    cutoff = datetime.now() - timedelta(days=days)

    by_line = Counter()
    by_addr = Counter()
    try:
        with open(QUERY_LOG_PATH, "r", encoding="utf-8") as f:
            for raw in f:
                try:
                    d = json.loads(raw)
                    ts = datetime.fromisoformat(d["time"])
                    if ts < cutoff:
                        continue
                    by_line[d.get("line", "?")] += 1
                    for m in ADDR_SEARCH_RE.finditer(d.get("question", "")):
                        by_addr[m.group(0).upper().lstrip("%")] += 1
                except Exception:
                    continue
    except FileNotFoundError:
        pass

    if not by_line:
        await update.message.reply_text(t(lang, "topfaults_empty", days=days))
        return

    lines = [t(lang, "topfaults_header", days=days)]
    lines.append("\n" + t(lang, "topfaults_by_machine"))
    for line_id, cnt in by_line.most_common(10):
        label = LINES[line_id].label if line_id in LINES else line_id
        lines.append(f"• {label}: {cnt}")

    if by_addr:
        lines.append("\n" + t(lang, "topfaults_by_address"))
        for addr, cnt in by_addr.most_common(10):
            lines.append(f"• {addr}: {cnt}")

    await update.message.reply_text("\n".join(lines))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        if should_answer_denied(update.effective_user.id):
            await update.message.reply_text(t(get_lang(context), "access_denied"))
        return
    if not context.user_data.get("lang"):
        await update.message.reply_text(TEXT["uz"]["choose_lang"], reply_markup=language_keyboard())
        return

    lang = get_lang(context)

    # /libadd rejimida admin rasm yuborsa — diagnostika emas, kutubxonaga tushadi
    if is_admin(update.effective_user.id) and context.user_data.get("mode") == "lib_admin_upload":
        await handle_library_photo_upload(update, context)
        return

    # Rasm tahlili ham AI sarflaydi (Gemini vision) — limit tekshiruvi shart
    if not check_rate_limit(update.effective_user.id):
        await update.message.reply_text(t(lang, "rate_limited"), reply_markup=kb_for(update))
        return

    await update.message.reply_text(t(lang, "photo_processing"))

    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())

    text_val, img_keywords = await analyze_image(image_bytes)
    if text_val:
        await update.message.reply_text(t(lang, "photo_extracted", text=text_val))

    mode = context.user_data.get("mode")
    if mode == "general":
        query_text = text_val or "Rasmda nima ko'rinyapti, tushuntirib ber."
        await handle_general_ai(update, context, query_text, limit_checked=True)
        return

    ln = get_selected_line(context)
    if ln is None:
        await update.message.reply_text(
            t(lang, "no_machine_selected", ai=AI_CHAT_LABEL), reply_markup=kb_for(update)
        )
        return

    # Tegishli taglarni topish: rasmdan olingan kalit so'zlar + (bo'lsa) matn.
    keywords = set(img_keywords)
    if text_val:
        keywords |= local_keywords(text_val)
    candidates = local_search(ln.tags, keywords) if keywords else []
    if len(candidates) < MIN_LOCAL_CANDIDATES and text_val:
        ai_keywords_raw = await ask_ai(KEYWORD_SYSTEM_PROMPT, text_val)
        if ai_keywords_raw:
            keywords |= {w.strip().lower() for w in ai_keywords_raw.replace("\n", ",").split(",") if w.strip()}
            candidates = local_search(ln.tags, keywords)

    if not candidates:
        log_no_match(ln.id, lang, text_val or "[rasm/image]")
        await update.message.reply_text(t(lang, "no_match"), reply_markup=kb_for(update))
        return

    tag_block = build_tag_block(candidates)
    system_prompt = build_diagnosis_prompt(ln.label, tag_block, False, lang)

    # Avval rasmning O'ZI asosida (Gemini vision) diagnostika qilishga
    # harakat qilamiz — bu OCR matn transkriptiga qaraganda ancha aniqroq
    # (indikator rangi, ekran tuzilishi kabi tafsilotlarni ham ko'radi).
    answer = await ask_ai_vision(system_prompt, image_bytes, lang)
    if not answer and text_val:
        # Gemini vision band bo'lsa, matn asosida (Groq/OpenRouter orqali
        # ham ishlaydigan) oddiy tahlilga o'tamiz.
        answer = await ask_ai(system_prompt, text_val, lang=lang)

    query_label = text_val or "[rasm]"

    if not answer:
        answer = format_raw_tags(candidates, ln.label, lang)
        await update.message.reply_text(answer, reply_markup=kb_for(update))
        answer_id = register_answer(ln.id, lang, query_label, answer)
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=t(lang, "feedback_prompt"),
            reply_markup=build_feedback_keyboard(answer_id, lang),
        )
        return

    answer_id = register_answer(ln.id, lang, query_label, answer)
    await safe_reply_text(update, answer)
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=t(lang, "feedback_prompt"),
        reply_markup=build_feedback_keyboard(answer_id, lang),
    )
    log_query(update, ln.id, query_label, answer)
    cache_set(ln.id, lang, query_label, answer)


# ---------------------------------------------------------------------------
# Ovozli xabar orqali murojaat (Groq Whisper orqali matnga aylantiriladi)
# ---------------------------------------------------------------------------

def _transcribe_voice_sync(audio_bytes: bytes, lang_hint: str) -> str:
    if not groq_client:
        raise RuntimeError("Groq sozlanmagan — ovozni matnga aylantirish uchun GROQ_API_KEY kerak")
    lang_map = {"uz": "uz", "en": "en", "zh": "zh"}
    resp = groq_client.audio.transcriptions.create(
        model=GROQ_WHISPER_MODEL,
        file=("audio.ogg", audio_bytes),
        language=lang_map.get(lang_hint, "uz"),
        response_format="text",
    )
    return str(resp).strip()


async def transcribe_voice(audio_bytes: bytes, lang_hint: str):
    try:
        return await asyncio.to_thread(_transcribe_voice_sync, audio_bytes, lang_hint)
    except Exception as e:
        logger.warning("Ovozni matnga aylantirishda xatolik: %s", e)
        return None


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        if should_answer_denied(update.effective_user.id):
            await update.message.reply_text(t(get_lang(context), "access_denied"))
        return
    if not context.user_data.get("lang"):
        await update.message.reply_text(TEXT["uz"]["choose_lang"], reply_markup=language_keyboard())
        return

    lang = get_lang(context)

    # Transkripsiya (Whisper) ham pullik resurs — avval limit tekshiruvi
    if not check_rate_limit(update.effective_user.id):
        await update.message.reply_text(t(lang, "rate_limited"), reply_markup=kb_for(update))
        return

    # Juda uzun ovozli xabarlar transkripsiya qilinmaydi (resurs tejash)
    if update.message.voice and update.message.voice.duration and update.message.voice.duration > 120:
        await update.message.reply_text(t(lang, "voice_too_long"), reply_markup=kb_for(update))
        return

    await update.message.reply_text(t(lang, "voice_processing"))

    voice = update.message.voice
    file = await voice.get_file()
    audio_bytes = bytes(await file.download_as_bytearray())

    text_val = await transcribe_voice(audio_bytes, lang)
    if not text_val:
        await update.message.reply_text(t(lang, "voice_failed"), reply_markup=kb_for(update))
        return

    await update.message.reply_text(t(lang, "voice_transcribed", text=text_val))

    # Endi xuddi yozma xabar kelgandek, mavjud yo'nalishlar bo'yicha davom etamiz.
    mode = context.user_data.get("mode")
    if mode == "general":
        await handle_general_ai(update, context, text_val, limit_checked=True)
        return
    ln = get_selected_line(context)
    if ln is None:
        await update.message.reply_text(
            t(lang, "no_machine_selected", ai=AI_CHAT_LABEL), reply_markup=kb_for(update)
        )
        return
    await handle_machine_query(update, context, ln, text_val, limit_checked=True)


async def tag_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(lang, "access_denied"))
        return
    ln = get_selected_line(context)
    if ln is None:
        await update.message.reply_text(t(lang, "choose_machine_first_tag"), reply_markup=kb_for(update))
        return
    if not context.args:
        await update.message.reply_text(t(lang, "tag_usage"))
        return
    address = context.args[0]
    tg = ln.find_tag(address)
    if not tg:
        await update.message.reply_text(t(lang, "tag_not_found", addr=address, machine=ln.label))
        return
    scope = t(lang, "station_n", n=tg["station"]) if tg.get("station") else t(lang, "general_scope")
    kind_labels = KIND_LABELS.get(lang, KIND_LABELS["uz"])
    await update.message.reply_text(
        t(lang, "tag_details",
          addr=tg["address"], dtype=tg["data_type"], machine=ln.label,
          kind=kind_labels.get(tg["kind"], tg["kind"]), scope=scope,
          group=tg.get("group") or "—", name=tg.get("name") or "—",
          comment=tg.get("comment") or "—"),
        parse_mode="Markdown",
    )
    await send_schematic_pages(update, context, ln, [tg["address"]], lang)


MAX_FIND_RESULTS = int(os.getenv("MAX_FIND_RESULTS", "15"))


async def find_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tezkor, AI'siz kalit-so'z qidiruvi — mahalliy lug'at orqali."""
    lang = get_lang(context)
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(lang, "access_denied"))
        return
    ln = get_selected_line(context)
    if ln is None:
        await update.message.reply_text(t(lang, "choose_machine_first_tag"), reply_markup=kb_for(update))
        return
    if not context.args:
        await update.message.reply_text(t(lang, "find_usage"))
        return

    query_text = " ".join(context.args)
    keywords = local_keywords(query_text)
    results = local_search(ln.tags, keywords, limit=MAX_FIND_RESULTS)
    if not results:
        await update.message.reply_text(t(lang, "find_no_results", query=query_text))
        return

    lines = [t(lang, "find_results_header", query=query_text, count=len(results))]
    for tg in results:
        lines.append(
            f"📍 `{tg['address']}` — {tg.get('name') or ''} {tg.get('comment') or ''}".strip()
        )
    await safe_reply_text(update, "\n".join(lines))


async def handle_general_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, limit_checked: bool = False):
    lang = get_lang(context)

    cached = cache_get("general", lang, user_text)
    if cached:
        await safe_reply_text(update, cached, reply_markup=kb_for(update))
        return

    if not limit_checked and not check_rate_limit(update.effective_user.id):
        await update.message.reply_text(t(lang, "rate_limited"), reply_markup=kb_for(update))
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    answer = await ask_ai(general_ai_prompt(lang), user_text, lang=lang)
    if not answer:
        await update.message.reply_text(t(lang, "ai_busy_general"), reply_markup=kb_for(update))
        return
    answer_id = register_answer("general", lang, user_text, answer)
    await safe_reply_text(update, answer)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t(lang, "feedback_prompt"),
        reply_markup=build_feedback_keyboard(answer_id, lang),
    )
    cache_set("general", lang, user_text, answer)


async def handle_machine_query(update: Update, context: ContextTypes.DEFAULT_TYPE, ln: MachineLine, user_text: str, limit_checked: bool = False):
    lang = get_lang(context)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    cached = cache_get(ln.id, lang, user_text)
    if cached:
        await safe_reply_text(update, cached, reply_markup=kb_for(update))
        return

    if not limit_checked and not check_rate_limit(update.effective_user.id):
        await update.message.reply_text(t(lang, "rate_limited"), reply_markup=kb_for(update))
        return

    if looks_like_address(user_text):
        exact = ln.find_tag(user_text)
        if exact:
            candidates, found_all = [exact], False
        else:
            log_no_match(ln.id, lang, user_text)
            await update.message.reply_text(
                t(lang, "addr_not_found", addr=user_text, machine=ln.label),
                reply_markup=kb_for(update),
            )
            return
    else:
        # Avval faqat mahalliy lug'at/so'z moslashuvi orqali qidiramiz —
        # agar yetarlicha natija topilsa, AI'dan kalit so'z so'rashning
        # hojati yo'q (bitta AI so'rovini tejaydi).
        keywords = local_keywords(user_text)
        candidates = local_search(ln.tags, keywords)
        if len(candidates) < MIN_LOCAL_CANDIDATES:
            ai_keywords_raw = await ask_ai(KEYWORD_SYSTEM_PROMPT, user_text)
            if ai_keywords_raw:
                keywords |= {w.strip().lower() for w in ai_keywords_raw.replace("\n", ",").split(",") if w.strip()}
                candidates = local_search(ln.tags, keywords)
        found_all = False
        if not candidates:
            log_no_match(ln.id, lang, user_text)
            await update.message.reply_text(t(lang, "no_match"), reply_markup=kb_for(update))
            return

    # Qo'llanma bo'lsa, mavzuga tegishli sahifalarni topib, AI javobini
    # chuqurlashtirish uchun kontekstga qo'shamiz (tashxis aniqroq bo'ladi).
    manual_excerpt = ""
    manual_pages_used = []
    if ln.manual_pdf:
        m_results = ln.search_manual(local_keywords(user_text), limit=2)
        if m_results:
            manual_pages_used = [p for p, _ in m_results]
            manual_excerpt = "\n\n".join(f"[page {p}]\n{txt[:600]}" for p, txt in m_results)

    tag_block = build_tag_block(candidates)
    system_prompt = build_diagnosis_prompt(ln.label, tag_block, found_all, lang, manual_excerpt)
    answer = await ask_ai(system_prompt, user_text, lang=lang)

    if not answer:
        answer = format_raw_tags(candidates, ln.label, lang)
        await update.message.reply_text(answer, reply_markup=kb_for(update))
        answer_id = register_answer(ln.id, lang, user_text, answer)
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=t(lang, "feedback_prompt"), reply_markup=build_feedback_keyboard(answer_id, lang)
        )
        await send_schematic_pages(update, context, ln, [c["address"] for c in candidates], lang)
        await send_manual_pages(update, context, ln, manual_pages_used, lang)
        return

    answer_id = register_answer(ln.id, lang, user_text, answer)
    await safe_reply_text(update, answer)
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=t(lang, "feedback_prompt"), reply_markup=build_feedback_keyboard(answer_id, lang)
    )
    await send_schematic_pages(update, context, ln, [c["address"] for c in candidates], lang)
    await send_manual_pages(update, context, ln, manual_pages_used, lang)
    log_query(update, ln.id, user_text, answer)
    cache_set(ln.id, lang, user_text, answer)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    if not user_text:
        return

    if not is_authorized(update.effective_user.id):
        user_text_check = (update.message.text or "").strip()
        tokens = user_text_check.split()
        if user_text_check in LANG_BUTTON_TO_CODE:
            pass  # til tanlashga ruxsat
        elif user_text_check.startswith("/register"):
            pass  # buyruq o'tadi
        elif len(tokens) >= 2 and _looks_like_phone(tokens[-1]):
            # Operator /register yozishni unutgan bo'lsa ham: "Ism Familiya +99890..."
            context.args = tokens
            await register_cmd(update, context)
            return
        else:
            if should_answer_denied(update.effective_user.id):
                await update.message.reply_text(
                    t(get_lang(context), "access_denied_detail"),
                    parse_mode="Markdown",
                    reply_markup=unauthorized_keyboard(get_lang(context)),
                )
            return

    # Til tanlash tugmasi bosilganmi?
    if user_text in LANG_BUTTON_TO_CODE:
        lang = LANG_BUTTON_TO_CODE[user_text]
        context.user_data["lang"] = lang
        await update.message.reply_text(
            t(lang, "lang_selected", label=user_text), parse_mode="Markdown"
        )
        if not is_authorized(update.effective_user.id):
            await update.message.reply_text(
                t(lang, "access_denied_detail"),
                parse_mode="Markdown",
                reply_markup=unauthorized_keyboard(lang),
            )
        else:
            await update.message.reply_text(
                t(lang, "greeting_after_lang", ai=AI_CHAT_LABEL),
                reply_markup=kb_for(update),
            )
        return

    if user_text == LANG_CHANGE_LABEL:
        await update.message.reply_text(TEXT["uz"]["choose_lang"], reply_markup=language_keyboard())
        return

    if not context.user_data.get("lang"):
        await update.message.reply_text(TEXT["uz"]["choose_lang"], reply_markup=language_keyboard())
        return

    lang = get_lang(context)

    # --- Admin paneli tugmasi ---
    if user_text in (ADMIN_PANEL_LABEL, "/admin"):
        if is_admin(update.effective_user.id):
            await show_admin_dashboard(update, context)
            return

    # --- AI Ingliz tili murabbiyi (English Tutor) rejimi ---
    if context.user_data.get("mode") == "eng_tutor":
        exit_triggers = [
            MACHINE_MENU_LABEL, AI_CHAT_LABEL, LIBRARY_MENU_LABEL,
            ESP32_MENU_LABEL, ENG_COURSE_LABEL, LANG_CHANGE_LABEL,
            ADMIN_PANEL_LABEL, ADMIN_LIBRARY_LABEL, HELP_BTN_LABEL,
            "/exit", "/stop", "/menu",
        ]
        if user_text in exit_triggers or user_text in LABEL_TO_ID:
            context.user_data["mode"] = None
            await update.message.reply_text(t(lang, "eng_tutor_exit"), reply_markup=kb_for(update))
            if user_text in ("/exit", "/stop", "/menu"):
                return
        else:
            if not check_rate_limit(update.effective_user.id):
                await update.message.reply_text(t(lang, "rate_limited"), reply_markup=kb_for(update))
                return
            await update.message.reply_chat_action("typing")
            answer = await ask_ai(ENGLISH_TUTOR_PROMPT, user_text, lang)
            if not answer:
                answer = "I'm having trouble with the connection right now. Please try again in a moment!"
            await safe_reply_text(update, answer)
            return

    # --- ESP32 monitoring relay (bitta bot rejimi) ---
    # Faqat ruxsatli foydalanuvchi. Yoki ESP32 buyrug'i, yoki davom etayotgan
    # kiritish sessiyasi (Wi-Fi parol, kalibrlash qiymati) bo'lsa — ESP32'ga
    # yetkazamiz va uning javobini kutamiz (Python AI oqimiga o'tmaymiz).
    if ESP32_CMD_URL and is_authorized(update.effective_user.id):
        in_session = bool(context.user_data.get("esp32_session"))
        if in_session or user_text in ESP32_TEXT_COMMANDS:
            res = await esp32_relay(
                "message", update.effective_chat.id, user_text,
                from_name=(update.effective_user.full_name or "")[:64],
            )
            if res:
                context.user_data["esp32_session"] = bool(res.get("session_open"))
                return
            context.user_data["esp32_session"] = False
            await update.message.reply_text(t(lang, "esp32_unreachable"), reply_markup=kb_for(update))
            return

    if user_text in LABEL_TO_ID:
        context.user_data["line_id"] = LABEL_TO_ID[user_text]
        context.user_data["mode"] = "line"
        ln = LINES[LABEL_TO_ID[user_text]]
        await update.message.reply_text(
            t(lang, "machine_selected", machine=ln.label),
            parse_mode="Markdown",
            reply_markup=kb_for(update),
        )
        return

    if user_text == AI_CHAT_LABEL:
        context.user_data["mode"] = "general"
        await update.message.reply_text(t(lang, "ai_mode_intro"), reply_markup=kb_for(update))
        return

    if user_text == ESP32_MENU_LABEL:
        await handle_esp32_status(update, context)
        return

    if user_text == LIBRARY_MENU_LABEL:
        context.user_data["mode"] = None
        await show_user_library(update, context)
        return

    if user_text == ENG_COURSE_LABEL:
        context.user_data["mode"] = None
        await show_eng_menu(update, context)
        return

    if user_text == ADMIN_LIBRARY_LABEL:
        await show_admin_library_menu(update, context)
        return

    if user_text == MACHINE_MENU_LABEL:
        await choose_machine(update, context)
        return

    if user_text == HELP_BTN_LABEL:
        await help_cmd(update, context)
        return

    mode = context.user_data.get("mode")

    if mode == "library_wait":
        await handle_library_query(update, context, user_text)
        return

    if mode == "general":
        await handle_general_ai(update, context, user_text)
        return

    ln = get_selected_line(context)
    if ln is None:
        await update.message.reply_text(
            t(lang, "no_machine_selected", ai=AI_CHAT_LABEL),
            reply_markup=kb_for(update),
        )
        return

    await handle_machine_query(update, context, ln, user_text)


async def error_handler(update, context):
    logger.error("Kutilmagan xatolik: %s\n%s", context.error, "".join(
        traceback.format_exception(type(context.error), context.error, context.error.__traceback__)
    ))
    try:
        if isinstance(update, Update) and update.effective_message:
            lang = "uz"
            if hasattr(context, "user_data") and context.user_data:
                lang = context.user_data.get("lang", "uz")
            await update.effective_message.reply_text(t(lang, "unexpected_error"))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rejalashtirilgan vazifalar: haftalik statistika, bot salomatligi (healthcheck)
# ---------------------------------------------------------------------------

async def weekly_stats_job(context: ContextTypes.DEFAULT_TYPE):
    if not STATS_CHAT_ID:
        return
    if datetime.now().weekday() != 0:  # faqat dushanba kuni
        return
    cutoff = datetime.now() - timedelta(days=7)
    counts = Counter()
    questions = Counter()
    try:
        with open(QUERY_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    ts = datetime.fromisoformat(d["time"])
                    if ts >= cutoff:
                        counts[d.get("line", "?")] += 1
                        questions[(d.get("line", "?"), d.get("question", "").strip().lower())] += 1
                except Exception:
                    continue
    except FileNotFoundError:
        return

    if not counts:
        return

    lines = ["📊 *Haftalik hisobot (so'nggi 7 kun)*\n"]
    for line_id, cnt in counts.most_common():
        label = LINES[line_id].label if line_id in LINES else line_id
        lines.append(f"• {label}: {cnt} ta so'rov")
    lines.append("\n*Eng ko'p takrorlangan savollar:*")
    for (line_id, q), cnt in questions.most_common(5):
        if cnt < 2:
            continue
        label = LINES[line_id].label if line_id in LINES else line_id
        lines.append(f"• [{label}] \"{q}\" — {cnt} marta")

    try:
        await context.bot.send_message(chat_id=STATS_CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.warning("Haftalik hisobotni yuborishda xatolik: %s", e)


def _ping_healthcheck_sync():
    try:
        urllib.request.urlopen(HEALTHCHECK_PING_URL, timeout=10)
    except Exception as e:
        logger.warning("Healthcheck ping xatosi: %s", e)


async def healthcheck_job(context: ContextTypes.DEFAULT_TYPE):
    if not HEALTHCHECK_PING_URL:
        return
    await asyncio.to_thread(_ping_healthcheck_sync)


# ---------------------------------------------------------------------------
# Log arxivlash va zaxira nusxa (backup)
# ---------------------------------------------------------------------------

LOG_FILES_TO_ROTATE = [QUERY_LOG_PATH, NO_MATCH_LOG_PATH, "feedback.log"]
BACKUP_FILES = [ALLOWED_USERS_PATH, PENDING_REG_PATH]


def _rotate_logs_sync():
    """Har bir log faylini oy boshida (fayl shu oyga tegishli bo'lmasa)
    '<nom>-YYYY-MM.log' deb arxivlab, yangisini boshlaydi."""
    this_month = datetime.now().strftime("%Y-%m")
    for path in LOG_FILES_TO_ROTATE:
        if not os.path.exists(path):
            continue
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m")
            if mtime != this_month:
                base, ext = os.path.splitext(path)
                archive_path = f"{base}-{mtime}{ext}"
                if not os.path.exists(archive_path):
                    os.rename(path, archive_path)
                    logger.info("Log arxivlandi: %s -> %s", path, archive_path)
        except Exception as e:
            logger.warning("Log arxivlashda xatolik (%s): %s", path, e)


def _backup_sync():
    """tasks.json, allowed_users.json, recurring_tasks.json fayllaridan
    kunlik zaxira nusxa oladi, eng so'nggi BACKUP_KEEP tasi saqlanadi."""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
    except Exception as e:
        logger.warning("Backup papkasini yaratishda xatolik: %s", e)
        return

    today = datetime.now().strftime("%Y-%m-%d")
    for path in BACKUP_FILES:
        if not os.path.exists(path):
            continue
        try:
            base = os.path.basename(path)
            dest = os.path.join(BACKUP_DIR, f"{today}-{base}")
            if not os.path.exists(dest):
                with open(path, "rb") as src_f, open(dest, "wb") as dst_f:
                    dst_f.write(src_f.read())
        except Exception as e:
            logger.warning("Zaxira nusxa olishda xatolik (%s): %s", path, e)

    # Eskirgan zaxiralarni tozalash
    try:
        files = sorted(os.listdir(BACKUP_DIR))
        by_base = {}
        for fn in files:
            for base in (os.path.basename(p) for p in BACKUP_FILES):
                if fn.endswith(base):
                    by_base.setdefault(base, []).append(fn)
        for base, fnames in by_base.items():
            fnames.sort()
            for old in fnames[:-BACKUP_KEEP]:
                try:
                    os.remove(os.path.join(BACKUP_DIR, old))
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Eski zaxiralarni tozalashda xatolik: %s", e)


async def log_rotation_job(context: ContextTypes.DEFAULT_TYPE):
    await asyncio.to_thread(_rotate_logs_sync)
    await asyncio.to_thread(_backup_sync)


# ---------------------------------------------------------------------------
# Ishga tushirish
# ---------------------------------------------------------------------------

# YANGI: bitta nusxa qulfi (single-instance lock) — bot ikki marta (masalan
# Spyder'da ham, CMD'da ham) tasodifan ishga tushirilsa, Telegramda bitta
# tokenga ikkita getUpdates so'rovi to'qnashib, "Conflict: terminated by
# other getUpdates request" xatosi bilan botning javob berishi to'xtab
# qolishining oldini oladi. Ikkinchi nusxa aniq xabar bilan darhol to'xtaydi,
# birinchisiga tegmaydi.
LOCK_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.lock")


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        try:
            out = os.popen(f'tasklist /FI "PID eq {pid}"').read()
            return str(pid) in out
        except Exception:
            return True  # aniqlay olmasak, xavfsizroq tomonni tanlaymiz
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            return True


def _ensure_single_instance():
    try:
        if os.path.exists(LOCK_FILE_PATH):
            with open(LOCK_FILE_PATH, "r") as f:
                old_pid_str = f.read().strip()
            old_pid = int(old_pid_str) if old_pid_str.isdigit() else None
            if old_pid and old_pid != os.getpid() and _pid_is_running(old_pid):
                print(
                    f"\n❌ Bot allaqachon ishlab turibdi (PID {old_pid}).\n"
                    f"   Avval o'sha jarayonni to'xtating:  taskkill /F /PID {old_pid}\n"
                    f"   so'ngra botni qayta ishga tushiring.\n"
                )
                sys.exit(1)
        with open(LOCK_FILE_PATH, "w") as f:
            f.write(str(os.getpid()))

        def _release_lock():
            try:
                if os.path.exists(LOCK_FILE_PATH):
                    with open(LOCK_FILE_PATH, "r") as f:
                        if f.read().strip() == str(os.getpid()):
                            os.remove(LOCK_FILE_PATH)
            except Exception:
                pass

        atexit.register(_release_lock)
    except SystemExit:
        raise
    except Exception as e:
        logger.warning("Bitta-nusxa qulfini tekshirishda xatolik (davom etiladi): %s", e)


def main():
    _ensure_single_instance()
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    persistence = PicklePersistence(filepath=PERSISTENCE_PATH)
    # Timeout qiymatlari oshirildi — sekin/notekis tarmoqda TimedOut xatosini
    # kamaytirish uchun (default odatda 5–10 s bo'ladi).
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .get_updates_connect_timeout(30.0)
        .get_updates_read_timeout(30.0)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("machine", choose_machine))
    app.add_handler(CommandHandler("tag", tag_lookup))
    app.add_handler(CommandHandler("find", find_cmd))
    app.add_handler(CommandHandler("addcomment", addcomment_cmd))
    app.add_handler(CommandHandler("topfaults", topfaults_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("aistatus", status_cmd))
    app.add_handler(CommandHandler("esp32", handle_esp32_status))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("adduser", adduser_cmd))
    app.add_handler(CommandHandler("setphone", setphone_cmd))
    app.add_handler(CommandHandler("removeuser", removeuser_cmd))
    app.add_handler(CommandHandler("listusers", listusers_cmd))
    app.add_handler(CommandHandler("users", show_user_panel))
    app.add_handler(CommandHandler("admin", show_admin_dashboard))
    app.add_handler(CommandHandler("course", show_eng_menu))
    app.add_handler(CommandHandler("nomatches", nomatches_cmd))
    app.add_handler(CommandHandler("register", register_cmd))
    # Kutubxona
    app.add_handler(CommandHandler("libadd", libadd_cmd))
    app.add_handler(CommandHandler("liblist", liblist_cmd))
    app.add_handler(CommandHandler("libdel", libdel_cmd))
    app.add_handler(CommandHandler("libget", libget_cmd))
    app.add_handler(CommandHandler("library", show_user_library))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_library_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_feedback_callback))
    app.add_error_handler(error_handler)

    if app.job_queue is not None:
        if STATS_CHAT_ID:
            app.job_queue.run_daily(weekly_stats_job, time=datetime.strptime("08:00", "%H:%M").time())
        if HEALTHCHECK_PING_URL:
            app.job_queue.run_repeating(healthcheck_job, interval=HEALTHCHECK_INTERVAL_MIN * 60, first=10)
        app.job_queue.run_repeating(log_rotation_job, interval=6 * 3600, first=30)

    logger.info(
        "Bot ishga tushdi... (%d ta uskuna, AI provayderlar: %s, kirish nazorati: %s)",
        len(LINES), ", ".join(name for name, _ in AI_PROVIDERS),
        "MAJBURIY (admin ruxsati shart)",
    )
    # bootstrap_retries: ishga tushishda getMe() timeout bo'lsa qayta urinadi
    # drop_pending_updates: eski navbatdagi update'larni tashlab yuboradi
    app.run_polling(
        bootstrap_retries=10,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
