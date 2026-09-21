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
import re
import json
import time
import uuid
import asyncio
import logging
import traceback
import urllib.request
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

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# ---------------------------------------------------------------------------
# Sozlamalar
# ---------------------------------------------------------------------------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
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

# --- Bot salomatligini kuzatish (ixtiyoriy, masalan healthchecks.io) ---
HEALTHCHECK_PING_URL = os.getenv("HEALTHCHECK_PING_URL", "").strip()
HEALTHCHECK_INTERVAL_MIN = int(os.getenv("HEALTHCHECK_INTERVAL_MIN", "5"))

# --- ESP32 zavod monitoring integratsiyasi (ixtiyoriy) ---
# ESP32'dagi /status endpoint manzili, masalan: http://192.168.1.50/status
ESP32_STATUS_URL = os.getenv("ESP32_STATUS_URL", "").strip()
ESP32_TIMEOUT_SEC = int(os.getenv("ESP32_TIMEOUT_SEC", "5"))

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN topilmadi. .env faylni tekshiring.")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY topilmadi. .env faylni tekshiring.")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("plc-fault-bot")

QUERY_LOG_PATH = os.getenv("QUERY_LOG_PATH", "queries.log")

genai_client = genai.Client(api_key=GEMINI_API_KEY)

groq_client = Groq(api_key=GROQ_API_KEY) if (GROQ_API_KEY and Groq) else None
if GROQ_API_KEY and not Groq:
    logger.warning("GROQ_API_KEY berilgan, lekin 'groq' kutubxonasi o'rnatilmagan.")

openrouter_client = (
    OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")
    if (OPENROUTER_API_KEY and OpenAI)
    else None
)
if OPENROUTER_API_KEY and not OpenAI:
    logger.warning("OPENROUTER_API_KEY berilgan, lekin 'openai' kutubxonasi o'rnatilmagan.")

# ---------------------------------------------------------------------------
# Kirishni cheklash: agar ADMIN_USER_IDS bo'sh bo'lsa, bot hammaga ochiq
# (orqaga moslik uchun standart holat). ADMIN_USER_IDS to'ldirilsa, faqat
# adminlar va ular ruxsat bergan foydalanuvchilar botdan foydalana oladi.
# ---------------------------------------------------------------------------

ACCESS_CONTROL_ENABLED = bool(ADMIN_USER_IDS)

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
        with open(ALLOWED_USERS_PATH, "w", encoding="utf-8") as f:
            json.dump({str(uid): info for uid, info in ALLOWED_USERS.items()}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("allowed_users saqlashda xatolik: %s", e)


def is_authorized(user_id: int) -> bool:
    if not ACCESS_CONTROL_ENABLED:
        return True
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
        "access_denied": "Kechirasiz, bu botdan foydalanish uchun ruxsatingiz yo'q. Administratorga murojaat qiling.",
        "user_added": "✅ Foydalanuvchi {uid} ro'yxatga qo'shildi.",
        "user_removed": "✅ Foydalanuvchi {uid} ro'yxatdan o'chirildi.",
        "admin_only": "Bu buyruq faqat administrator uchun.",
        "adduser_usage": "Foydalanish: /adduser <telegram_id> <ism (ixtiyoriy)>",
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
            "2️⃣ Muammoni yozing (masalan \"konveyer ishlamayapti\") yoki shunchaki "
            "manzilni yozing (masalan I0.1).\n"
            "📷 Rasm ham yuborishingiz mumkin — HMI ekrani yoki indikator surati.\n"
            "🎤 Ovozli xabar ham qabul qilinadi.\n\n"
            "🤖 *Sun'iy intellekt* — PLC bilan bog'liq bo'lmagan savollar uchun.\n"
            "🏭 *Zavod monitoring* — kompressor/chiller holatini ko'rish (agar sozlangan bo'lsa).\n"
            "👥 *Xodimlar* — profilingiz va sizga berilgan topshiriqlar.\n"
            "🔑 *Admin* — (faqat adminlar) topshiriq berish, hisobotlar.\n"
            "🌐 *Til* — istalgan vaqtda tilni almashtirish.\n\n"
            "Ro'yxatdan o'tmagan bo'lsangiz: /register <ism> <telefon>"
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
        "voice_transcribed": "🎤 Eshitdim: \"{text}\"",
        "schematic_page_caption": "🔌 Elektr sxemasi — {machine}, {page}-sahifa",
        "library_select_machine_first": "Avval uskunani tanlang, so'ng qayta \"📚 Qo'llanma\" tugmasini bosing.",
        "library_no_manual_for_machine": "\"{machine}\" uchun qo'llanma hali yuklanmagan.",
        "library_none_available": "Hozircha hech qanday qo'llanma yuklanmagan.",
        "library_ask_topic": "📚 *{machine} qo'llanmasi*\nQaysi mavzuni qidiryapsiz? (masalan: \"moylash\", \"xavfsizlik to'ri sozlash\")",
        "library_no_results": "Qo'llanmadan bu mavzu bo'yicha hech narsa topa olmadim. Boshqacha so'z bilan yozib ko'ring.",
        "library_found": "📖 {count} ta tegishli sahifa topildi:",
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
            "2️⃣ Describe the problem (e.g. \"conveyor not moving\") or just type "
            "an address (e.g. I0.1).\n"
            "📷 You can also send a photo — of an HMI screen or indicator.\n"
            "🎤 Voice messages are supported too.\n\n"
            "🤖 *AI Assistant* — for questions unrelated to PLC.\n"
            "🏭 *Factory monitoring* — view compressor/chiller status (if set up).\n"
            "👥 *Employees* — your profile and assigned tasks.\n"
            "🔑 *Admin* — (admins only) assign tasks, view reports.\n"
            "🌐 *Language* — switch language anytime.\n\n"
            "Not registered yet? /register <name> <phone>"
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
        "voice_transcribed": "🎤 Heard: \"{text}\"",
        "schematic_page_caption": "🔌 Electrical schematic — {machine}, page {page}",
        "library_select_machine_first": "Please select a machine first, then tap \"📚 Manual\" again.",
        "library_no_manual_for_machine": "No manual has been loaded for \"{machine}\" yet.",
        "library_none_available": "No manual is loaded yet.",
        "library_ask_topic": "📚 *{machine} manual*\nWhat topic are you looking for? (e.g. \"lubrication\", \"light curtain setup\")",
        "library_no_results": "I couldn't find anything on that topic in the manual. Try different wording.",
        "library_found": "📖 Found {count} relevant page(s):",
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
            "2️⃣ 描述问题（例如\"输送带不动\"），或直接输入地址（例如 I0.1）。\n"
            "📷 也可以发送照片——HMI屏幕或指示灯。\n"
            "🎤 也支持语音消息。\n\n"
            "🤖 *人工智能* — 用于与PLC无关的问题。\n"
            "🏭 *工厂监控* — 查看压缩机/冷水机状态（如已配置）。\n"
            "👥 *员工* — 您的资料和分配的任务。\n"
            "🔑 *管理员* — （仅限管理员）分配任务、查看报表。\n"
            "🌐 *语言* — 随时切换语言。\n\n"
            "还未注册？发送 /register <姓名> <电话>"
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
        "voice_transcribed": "🎤 听到：\"{text}\"",
        "schematic_page_caption": "🔌 电气原理图 — {machine}，第{page}页",
        "library_select_machine_first": "请先选择设备，然后再次点击\"📚 手册\"。",
        "library_no_manual_for_machine": "\"{machine}\"尚未上传手册。",
        "library_none_available": "目前还没有上传任何手册。",
        "library_ask_topic": "📚 *{machine}手册*\n您要查找什么主题？（例如：\"润滑\"、\"光幕设置\"）",
        "library_no_results": "未能在手册中找到该主题的相关内容。请尝试其他措辞。",
        "library_found": "📖 找到{count}个相关页面：",
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
LIBRARY_MENU_LABEL = "📚 Qo'llanma / Manual / 手册"

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
        lines.append(
            f"{tag['address']}\t{tag['kind']}\t{st}\t{tag.get('group','')}\t{tag['data_type']}\t"
            f"{tag.get('name','')}\t{tag.get('comment','')}"
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
RELEVANT MANUAL/DOCUMENTATION EXCERPT (from the equipment's real manual —
use it to ground and deepen your answer, e.g. official procedure steps,
specified tolerances, or manufacturer-recommended checks; do not contradict
it, and prefer it over generic guesses):
---
{manual_excerpt}
---
"""
    return f"""You are a senior PLC diagnostics engineer for the "{machine_label}" equipment.
{scope_note} Each row: address TAB kind TAB station number (if known) TAB
group/location TAB data type TAB tag name TAB comment. Names/comments may be
in Chinese or English — understand them naturally regardless of language.

An employee (often new, inexperienced) describes a problem they see on the
equipment, in Uzbek, English, Chinese, or a mix. Give the most precise and
COMPLETE analysis you can — do not give a superficial one-line guess.

Your task:
1. Find the matching PLC tag(s) from the list and state the exact address(es).
2. Explain in simple terms what this signal physically represents and where
   it sits in the equipment's logic (e.g. what it's interlocked with, what
   depends on it).
3. Give a THOROUGH list of possible root causes, ordered from most to least
   likely, covering electrical (cable break, loose terminal, blown fuse,
   sticking relay/contactor), mechanical (misalignment, obstruction, worn
   part), and sensor-specific (dirty/misaligned sensor, wrong sensitivity,
   wrong wiring polarity) causes as relevant to this signal's type.
4. Give concrete, step-by-step troubleshooting instructions an inexperienced
   technician could follow directly (what to check first, what tool/meter to
   use, what a good vs. bad reading looks like, in what order).
5. If multiple tags could match, list all of them and indicate which is most
   likely and why.
6. If nothing in the list matches, say so clearly and ask for more detail
   (which station/robot, which indicator is lit, etc.) rather than guessing.
{manual_block}
LANGUAGE RULE (important): Your default reply language is {LANG_NAME.get(lang, "o'zbek")}.
However, if the employee's message is clearly written in one of the other
two supported languages (Uzbek, English, or Chinese), reply in THAT language
instead, matching what they used. Never mix languages within one reply.
Never produce a reply in any language other than these three.

Format your reply exactly like this (translate the bold labels into the
reply's language; use these labels for {LANG_NAME.get(lang, "o'zbek")}):

🔧 **{hl['address']}:** <address(es)>
📍 **{hl['what']}:** <explanation>
✅ **{hl['action']}:** <practical steps>

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
        "You are a helpful, accurate, concise AI assistant used inside a factory "
        "Telegram bot. The employee's question may or may not be related to PLC "
        "or manufacturing equipment. Your default reply language is "
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
        address = address.strip()
        if not address.startswith("%"):
            address = "%" + address
        for tg in self.tags:
            if tg["address"].lower() == address.lower():
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
    rows = [[line.label] for line in LINES.values()]
    if ESP32_STATUS_URL:
        rows.append([ESP32_MENU_LABEL])
    if any(ln.manual_pdf for ln in LINES.values()):
        rows.append([LIBRARY_MENU_LABEL])
    rows.append([AI_CHAT_LABEL])
    rows.append([LANG_CHANGE_LABEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def kb_for(update: Update) -> ReplyKeyboardMarkup:
    uid = update.effective_user.id if update and update.effective_user else None
    return machine_keyboard(uid)


def get_selected_line(context: ContextTypes.DEFAULT_TYPE):
    line_id = context.user_data.get("line_id")
    return LINES.get(line_id) if line_id else None


# ---------------------------------------------------------------------------
# Tag qidiruv (kerakli taglarni topish — butun ro'yxatni yubormaslik uchun)
# ---------------------------------------------------------------------------

ADDR_RE = re.compile(r"^%?[A-Za-z]{1,4}\d+(\.\d+)?$")
ADDR_SEARCH_RE = re.compile(r"%?\b[IQM]\d+\.\d+\b", re.IGNORECASE)
WORD_RE = re.compile(r"[a-zA-Z\u4e00-\u9fff]+")

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


async def show_library_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    ln = get_selected_line(context)

    if ln is None:
        await update.message.reply_text(t(lang, "library_select_machine_first"), reply_markup=kb_for(update))
        return

    if not ln.manual_pdf:
        await update.message.reply_text(
            t(lang, "library_no_manual_for_machine", machine=ln.label), reply_markup=kb_for(update)
        )
        return

    context.user_data["mode"] = "library_wait"
    await update.message.reply_text(t(lang, "library_ask_topic", machine=ln.label), reply_markup=kb_for(update))


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
        config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
    )
    return (resp.text or "").strip()


def _groq_generate(system_prompt: str, user_text: str) -> str:
    if not groq_client:
        raise RuntimeError("Groq sozlanmagan")
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _openrouter_generate(system_prompt: str, user_text: str) -> str:
    if not openrouter_client:
        raise RuntimeError("OpenRouter sozlanmagan")
    resp = openrouter_client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


AI_PROVIDERS = [("Gemini", _gemini_generate)]
if groq_client:
    AI_PROVIDERS.append(("Groq", _groq_generate))
if openrouter_client:
    AI_PROVIDERS.append(("OpenRouter", _openrouter_generate))


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


async def ask_ai(system_prompt: str, user_text: str, lang: str = None):
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
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(ANSWER_CACHE, f, ensure_ascii=False)
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


async def handle_feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    # XAVFSIZLIK: har qanday tugma bosilishidan oldin ruxsatni tekshiramiz —
    # aks holda begona/ruxsatsiz shaxs (masalan bot tokeni sizib chiqqan bo'lsa)
    # o'zi uchun soxta tugma yaratib, ma'lumotlarni o'zgartira olishi mumkin edi.
    if not is_authorized(update.effective_user.id):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if data.startswith("reg:"):
        _, action, reg_id = data.split(":", 2)
        # XAVFSIZLIK: faqat admin ro'yxatdan o'tishni tasdiqlay/rad eta oladi.
        if not is_admin(update.effective_user.id):
            return
        await handle_registration_callback(update, context, action, reg_id)
        return

    parts = data.split(":", 2)
    if len(parts) != 3:
        return
    kind, value, answer_id = parts
    lang = context.user_data.get("lang", "uz")

    if kind == "fb":
        log_feedback(answer_id, "feedback", value)
        msg = t(lang, "feedback_thanks_up") if value == "up" else t(lang, "feedback_thanks_down")
        await query.answer(text=msg, show_alert=False)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass


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


async def handle_esp32_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not ESP32_STATUS_URL:
        await update.message.reply_text(t(lang, "esp32_not_configured"), reply_markup=kb_for(update))
        return
    await update.message.reply_text(t(lang, "esp32_fetching"))
    data = await fetch_esp32_status()
    if data is None:
        await update.message.reply_text(t(lang, "esp32_unreachable"), reply_markup=kb_for(update))
        return
    await update.message.reply_text(
        format_esp32_status(data, lang), parse_mode="Markdown", reply_markup=kb_for(update)
    )


# ---------------------------------------------------------------------------
# Telegram handlerlar
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(get_lang(context), "access_denied"))
        return

    if not context.user_data.get("lang"):
        await update.message.reply_text(TEXT["uz"]["choose_lang"], reply_markup=language_keyboard())
        return

    lang = get_lang(context)
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
        await update.message.reply_text(t(lang, "access_denied"))
        return
    await update.message.reply_text(t(lang, "choose_machine_prompt"), reply_markup=kb_for(update))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(lang, "access_denied"))
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
        with open(PENDING_REG_PATH, "w", encoding="utf-8") as f:
            json.dump(PENDING_REGISTRATIONS, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("pending_registrations saqlashda xatolik: %s", e)


async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    uid = update.effective_user.id

    if is_authorized(uid):
        await update.message.reply_text(t(lang, "already_registered"))
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
        with open(ln.kb_file, "w", encoding="utf-8") as f:
            json.dump(ln.tags, f, ensure_ascii=False, indent=1)
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
        await update.message.reply_text(t(get_lang(context), "access_denied"))
        return
    if not context.user_data.get("lang"):
        await update.message.reply_text(TEXT["uz"]["choose_lang"], reply_markup=language_keyboard())
        return

    lang = get_lang(context)

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
        await handle_general_ai(update, context, query_text)
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
    await update.message.reply_text(answer, parse_mode="Markdown")
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
        await update.message.reply_text(t(get_lang(context), "access_denied"))
        return
    if not context.user_data.get("lang"):
        await update.message.reply_text(TEXT["uz"]["choose_lang"], reply_markup=language_keyboard())
        return

    lang = get_lang(context)
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
        await handle_general_ai(update, context, text_val)
        return
    ln = get_selected_line(context)
    if ln is None:
        await update.message.reply_text(
            t(lang, "no_machine_selected", ai=AI_CHAT_LABEL), reply_markup=kb_for(update)
        )
        return
    await handle_machine_query(update, context, ln, text_val)


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

    kind_labels = KIND_LABELS.get(lang, KIND_LABELS["uz"])
    lines = [t(lang, "find_results_header", query=query_text, count=len(results))]
    for tg in results:
        lines.append(
            f"📍 `{tg['address']}` — {tg.get('name') or ''} {tg.get('comment') or ''}".strip()
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_general_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    lang = get_lang(context)

    cached = cache_get("general", lang, user_text)
    if cached:
        await update.message.reply_text(cached, parse_mode="Markdown", reply_markup=kb_for(update))
        return

    if not check_rate_limit(update.effective_user.id):
        await update.message.reply_text(t(lang, "rate_limited"), reply_markup=kb_for(update))
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    answer = await ask_ai(general_ai_prompt(lang), user_text, lang=lang)
    if not answer:
        await update.message.reply_text(t(lang, "ai_busy_general"), reply_markup=kb_for(update))
        return
    answer_id = register_answer("general", lang, user_text, answer)
    await update.message.reply_text(answer, parse_mode="Markdown")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t(lang, "feedback_prompt"),
        reply_markup=build_feedback_keyboard(answer_id, lang),
    )
    cache_set("general", lang, user_text, answer)


async def handle_machine_query(update: Update, context: ContextTypes.DEFAULT_TYPE, ln: MachineLine, user_text: str):
    lang = get_lang(context)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    cached = cache_get(ln.id, lang, user_text)
    if cached:
        await update.message.reply_text(cached, parse_mode="Markdown", reply_markup=kb_for(update))
        return

    if not check_rate_limit(update.effective_user.id):
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
            manual_excerpt = "\n\n".join(f"[page {p}]\n{txt[:1200]}" for p, txt in m_results)

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
    await update.message.reply_text(answer, parse_mode="Markdown")
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
        await update.message.reply_text(t(get_lang(context), "access_denied"))
        return

    # Til tanlash tugmasi bosilganmi?
    if user_text in LANG_BUTTON_TO_CODE:
        lang = LANG_BUTTON_TO_CODE[user_text]
        context.user_data["lang"] = lang
        await update.message.reply_text(
            t(lang, "lang_selected", label=user_text), parse_mode="Markdown"
        )
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
        await show_library_menu(update, context)
        return

    if user_text == MACHINE_MENU_LABEL:
        await choose_machine(update, context)
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

def main():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    persistence = PicklePersistence(filepath=PERSISTENCE_PATH)
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("machine", choose_machine))
    app.add_handler(CommandHandler("tag", tag_lookup))
    app.add_handler(CommandHandler("find", find_cmd))
    app.add_handler(CommandHandler("addcomment", addcomment_cmd))
    app.add_handler(CommandHandler("topfaults", topfaults_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("adduser", adduser_cmd))
    app.add_handler(CommandHandler("setphone", setphone_cmd))
    app.add_handler(CommandHandler("removeuser", removeuser_cmd))
    app.add_handler(CommandHandler("listusers", listusers_cmd))
    app.add_handler(CommandHandler("nomatches", nomatches_cmd))
    app.add_handler(CommandHandler("register", register_cmd))
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
        "yoqilgan" if ACCESS_CONTROL_ENABLED else "o'chirilgan",
    )
    app.run_polling()


if __name__ == "__main__":
    main()
