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
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
LINES_CONFIG_PATH = os.getenv("LINES_CONFIG_PATH", "lines.json")
PERSISTENCE_PATH = os.getenv("PERSISTENCE_PATH", "bot_state.pickle")
MAX_CANDIDATE_TAGS = int(os.getenv("MAX_CANDIDATE_TAGS", "25"))
CACHE_PATH = os.getenv("CACHE_PATH", "answer_cache.json")
CACHE_TTL_HOURS = float(os.getenv("CACHE_TTL_HOURS", "72"))
MIN_LOCAL_CANDIDATES = int(os.getenv("MIN_LOCAL_CANDIDATES", "3"))

# --- Kirishni cheklash (ixtiyoriy) ---
ADMIN_USER_IDS = {int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()}
ALLOWED_USERS_PATH = os.getenv("ALLOWED_USERS_PATH", "allowed_users.json")

# --- Eskalatsiya (hal bo'lmagan muammolar) ---
ESCALATION_CHAT_IDS = [int(x) for x in os.getenv("ESCALATION_CHAT_IDS", "").split(",") if x.strip().lstrip("-").isdigit()]

# --- Haftalik statistika ---
STATS_CHAT_ID = os.getenv("STATS_CHAT_ID", "").strip()
STATS_CHAT_ID = int(STATS_CHAT_ID) if STATS_CHAT_ID.lstrip("-").isdigit() else None

# --- Ma'lumot sifati (mos kelmagan so'rovlar) ---
NO_MATCH_LOG_PATH = os.getenv("NO_MATCH_LOG_PATH", "no_match.log")

# --- Bot salomatligini kuzatish (ixtiyoriy, masalan healthchecks.io) ---
HEALTHCHECK_PING_URL = os.getenv("HEALTHCHECK_PING_URL", "").strip()
HEALTHCHECK_INTERVAL_MIN = int(os.getenv("HEALTHCHECK_INTERVAL_MIN", "5"))

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

try:
    with open(ALLOWED_USERS_PATH, "r", encoding="utf-8") as f:
        ALLOWED_USERS = set(json.load(f))
except (FileNotFoundError, json.JSONDecodeError):
    ALLOWED_USERS = set()


def _save_allowed_users():
    try:
        with open(ALLOWED_USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(ALLOWED_USERS), f)
    except Exception as e:
        logger.warning("allowed_users saqlashda xatolik: %s", e)


def is_authorized(user_id: int) -> bool:
    if not ACCESS_CONTROL_ENABLED:
        return True
    return user_id in ADMIN_USER_IDS or user_id in ALLOWED_USERS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

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
        "adduser_usage": "Foydalanish: /adduser <telegram_id>",
        "feedback_thanks_up": "Rahmat! ✅",
        "feedback_thanks_down": "Xabar uchun rahmat, buni yaxshilashga harakat qilamiz. 🙏",
        "resolved_thanks": "Ajoyib! Yopildi. ✅",
        "escalated": "Xabar smenaga/muhandisga yuborildi. Tez orada bog'lanishadi. 📨",
        "escalation_message": (
            "⚠️ *Hal qilinmagan muammo*\n"
            "Uskuna: {machine}\n"
            "Foydalanuvchi: @{username} (ID: {uid})\n"
            "Savol: {question}\n\n"
            "Bot javobi:\n{answer}"
        ),
        "photo_processing": "🖼 Rasmni o'qiyapman...",
        "photo_no_text": "Rasmda o'qiladigan xatolik matni topa olmadim. Iltimos, matnni qo'lda yozing.",
        "photo_extracted": "📷 Rasmdan o'qildi: \"{text}\"",
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
        "adduser_usage": "Usage: /adduser <telegram_id>",
        "feedback_thanks_up": "Thanks! ✅",
        "feedback_thanks_down": "Thanks for the feedback, we'll try to improve. 🙏",
        "resolved_thanks": "Great, closed. ✅",
        "escalated": "The issue was sent to the shift lead/engineer. They'll follow up soon. 📨",
        "escalation_message": (
            "⚠️ *Unresolved issue*\n"
            "Machine: {machine}\n"
            "User: @{username} (ID: {uid})\n"
            "Question: {question}\n\n"
            "Bot's answer:\n{answer}"
        ),
        "photo_processing": "🖼 Reading the photo...",
        "photo_no_text": "I couldn't find readable error text in the photo. Please type the message instead.",
        "photo_extracted": "📷 Read from photo: \"{text}\"",
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
        "adduser_usage": "用法：/adduser <telegram_id>",
        "feedback_thanks_up": "谢谢！✅",
        "feedback_thanks_down": "感谢反馈，我们会努力改进。🙏",
        "resolved_thanks": "太好了，已关闭。✅",
        "escalated": "问题已发送给班组长/工程师，他们会尽快跟进。📨",
        "escalation_message": (
            "⚠️ *未解决的问题*\n"
            "设备：{machine}\n"
            "用户：@{username}（ID：{uid}）\n"
            "问题：{question}\n\n"
            "机器人的回答：\n{answer}"
        ),
        "photo_processing": "🖼 正在读取图片...",
        "photo_no_text": "未能在图片中找到可读的错误文本。请改为输入文字。",
        "photo_extracted": "📷 从图片中读取：\"{text}\"",
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


def build_diagnosis_prompt(machine_label: str, tag_block: str, found_all: bool, lang: str) -> str:
    scope_note = (
        "The FULL tag list is given below."
        if found_all
        else "Below is only the SUBSET of tags judged most relevant to the "
             "employee's message (not the full list)."
    )
    hl = HEADER_LABELS.get(lang, HEADER_LABELS["uz"])
    return f"""You are a PLC diagnostics assistant for the "{machine_label}" equipment.
{scope_note} Each row: address TAB kind TAB station number (if known) TAB
group/location TAB data type TAB tag name TAB comment. Names/comments may be
in Chinese or English — understand them naturally regardless of language.

An employee (often new, inexperienced) describes a problem they see on the
equipment, in Uzbek, English, Chinese, or a mix.

Your task:
1. Find the matching PLC tag(s) from the list and state the exact address.
2. Explain in simple terms what this signal physically represents.
3. List common causes of problems with this signal (cable break, dirty
   sensor, mechanical obstruction, wrong wiring, sticking relay, etc.).
4. Give concrete, practical troubleshooting steps.
5. If multiple tags could match, list them and indicate which is most likely.
6. If nothing in the list matches, say so clearly and ask for more detail
   (which station/robot, which indicator is lit, etc.).

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
        with open(cfg["kb_file"], "r", encoding="utf-8") as f:
            self.tags = json.load(f)
        logger.info("Yuklandi: '%s' -> %d ta tag", self.label, len(self.tags))

    def find_tag(self, address: str):
        address = address.strip()
        if not address.startswith("%"):
            address = "%" + address
        for tg in self.tags:
            if tg["address"].lower() == address.lower():
                return tg
        return None


LINES = {}
LABEL_TO_ID = {}
for cfg in LINES_CONFIG:
    line = MachineLine(cfg)
    LINES[line.id] = line
    LABEL_TO_ID[line.label] = line.id


def language_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[LANG_BUTTON["uz"]], [LANG_BUTTON["en"]], [LANG_BUTTON["zh"]]], resize_keyboard=True)


def machine_keyboard() -> ReplyKeyboardMarkup:
    rows = [[line.label] for line in LINES.values()]
    rows.append([AI_CHAT_LABEL])
    rows.append([LANG_CHANGE_LABEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_selected_line(context: ContextTypes.DEFAULT_TYPE):
    line_id = context.user_data.get("line_id")
    return LINES.get(line_id) if line_id else None


# ---------------------------------------------------------------------------
# Tag qidiruv (kerakli taglarni topish — butun ro'yxatni yubormaslik uchun)
# ---------------------------------------------------------------------------

ADDR_RE = re.compile(r"^%?[A-Za-z]{1,4}\d+(\.\d+)?$")
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


async def ask_ai(system_prompt: str, user_text: str):
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


def build_feedback_keyboard(answer_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👍", callback_data=f"fb:up:{answer_id}"),
         InlineKeyboardButton("👎", callback_data=f"fb:down:{answer_id}")],
        [InlineKeyboardButton("✅ Hal bo'ldi", callback_data=f"res:yes:{answer_id}"),
         InlineKeyboardButton("❌ Hal bo'lmadi", callback_data=f"res:no:{answer_id}")],
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
    parts = data.split(":", 2)
    if len(parts) != 3:
        return
    kind, value, answer_id = parts
    lang = context.user_data.get("lang", "uz")
    info = PENDING_ANSWERS.get(answer_id, {})

    if kind == "fb":
        log_feedback(answer_id, "feedback", value)
        msg = t(lang, "feedback_thanks_up") if value == "up" else t(lang, "feedback_thanks_down")
        await query.answer(text=msg, show_alert=False)
        return

    if kind == "res":
        log_feedback(answer_id, "resolution", value)
        if value == "yes":
            await query.answer(text=t(lang, "resolved_thanks"), show_alert=False)
        else:
            ln = LINES.get(info.get("line_id"))
            machine_label = ln.label if ln else info.get("line_id", "?")
            user = update.effective_user
            esc_text = t(lang, "escalation_message",
                         machine=machine_label, username=user.username or user.id,
                         uid=user.id, question=info.get("question", "?"),
                         answer=info.get("answer", "?"))
            for chat_id in ESCALATION_CHAT_IDS:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=esc_text, parse_mode="Markdown")
                except Exception as e:
                    logger.warning("Eskalatsiya xabarini yuborishda xatolik (%s): %s", chat_id, e)
            if ESCALATION_CHAT_IDS:
                await query.answer(text=t(lang, "escalated"), show_alert=True)
            else:
                await query.answer(text=t(lang, "resolved_thanks"), show_alert=False)


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

IMAGE_OCR_PROMPT = (
    "This is a photo of an industrial HMI screen, control panel, or status "
    "indicator. Extract any visible error code, alarm text, or fault message. "
    "Reply with ONLY the extracted text (short), nothing else. If there is no "
    "readable error/alarm text, reply with exactly: NONE"
)


def _extract_text_from_image_sync(image_bytes: bytes) -> str:
    image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    resp = genai_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[image_part, IMAGE_OCR_PROMPT],
    )
    return (resp.text or "").strip()


async def extract_text_from_image(image_bytes: bytes):
    try:
        text = await asyncio.to_thread(_extract_text_from_image_sync, image_bytes)
        if not text or text.upper() == "NONE":
            return None
        return text
    except Exception as e:
        logger.warning("Rasmni o'qishda xatolik: %s", e)
        return None


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
            reply_markup=machine_keyboard(),
        )
        return
    await update.message.reply_text(
        t(lang, "greeting_after_lang", ai=AI_CHAT_LABEL),
        reply_markup=machine_keyboard(),
    )


async def choose_machine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    await update.message.reply_text(t(lang, "choose_machine_prompt"), reply_markup=machine_keyboard())


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


async def adduser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(t(lang, "adduser_usage"))
        return
    uid = int(context.args[0])
    ALLOWED_USERS.add(uid)
    _save_allowed_users()
    await update.message.reply_text(t(lang, "user_added", uid=uid))


async def removeuser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(t(lang, "adduser_usage"))
        return
    uid = int(context.args[0])
    ALLOWED_USERS.discard(uid)
    _save_allowed_users()
    await update.message.reply_text(t(lang, "user_removed", uid=uid))


async def listusers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "admin_only"))
        return
    admins = ", ".join(str(x) for x in sorted(ADMIN_USER_IDS)) or "—"
    users = ", ".join(str(x) for x in sorted(ALLOWED_USERS)) or "—"
    await update.message.reply_text(f"👑 Adminlar: {admins}\n👤 Ruxsat berilganlar: {users}")


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

    extracted = await extract_text_from_image(image_bytes)
    if not extracted:
        await update.message.reply_text(t(lang, "photo_no_text"), reply_markup=machine_keyboard())
        return

    await update.message.reply_text(t(lang, "photo_extracted", text=extracted))

    mode = context.user_data.get("mode")
    if mode == "general":
        await handle_general_ai(update, context, extracted)
        return
    ln = get_selected_line(context)
    if ln is None:
        await update.message.reply_text(
            t(lang, "no_machine_selected", ai=AI_CHAT_LABEL), reply_markup=machine_keyboard()
        )
        return
    await handle_machine_query(update, context, ln, extracted)


async def tag_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(t(lang, "access_denied"))
        return
    ln = get_selected_line(context)
    if ln is None:
        await update.message.reply_text(t(lang, "choose_machine_first_tag"), reply_markup=machine_keyboard())
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


async def handle_general_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    lang = get_lang(context)

    cached = cache_get("general", lang, user_text)
    if cached:
        await update.message.reply_text(cached, parse_mode="Markdown", reply_markup=machine_keyboard())
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    answer = await ask_ai(general_ai_prompt(lang), user_text)
    if not answer:
        await update.message.reply_text(t(lang, "ai_busy_general"), reply_markup=machine_keyboard())
        return
    answer_id = register_answer("general", lang, user_text, answer)
    await update.message.reply_text(answer, parse_mode="Markdown")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="—",
        reply_markup=build_feedback_keyboard(answer_id),
    )
    cache_set("general", lang, user_text, answer)


async def handle_machine_query(update: Update, context: ContextTypes.DEFAULT_TYPE, ln: MachineLine, user_text: str):
    lang = get_lang(context)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    cached = cache_get(ln.id, lang, user_text)
    if cached:
        await update.message.reply_text(cached, parse_mode="Markdown", reply_markup=machine_keyboard())
        return

    if looks_like_address(user_text):
        exact = ln.find_tag(user_text)
        if exact:
            candidates, found_all = [exact], False
        else:
            log_no_match(ln.id, lang, user_text)
            await update.message.reply_text(
                t(lang, "addr_not_found", addr=user_text, machine=ln.label),
                reply_markup=machine_keyboard(),
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
            await update.message.reply_text(t(lang, "no_match"), reply_markup=machine_keyboard())
            return

    tag_block = build_tag_block(candidates)
    system_prompt = build_diagnosis_prompt(ln.label, tag_block, found_all, lang)
    answer = await ask_ai(system_prompt, user_text)

    if not answer:
        answer = format_raw_tags(candidates, ln.label, lang)
        await update.message.reply_text(answer, reply_markup=machine_keyboard())
        answer_id = register_answer(ln.id, lang, user_text, answer)
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="—", reply_markup=build_feedback_keyboard(answer_id)
        )
        return

    answer_id = register_answer(ln.id, lang, user_text, answer)
    await update.message.reply_text(answer, parse_mode="Markdown")
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="—", reply_markup=build_feedback_keyboard(answer_id)
    )
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
            reply_markup=machine_keyboard(),
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
            reply_markup=machine_keyboard(),
        )
        return

    if user_text == AI_CHAT_LABEL:
        context.user_data["mode"] = "general"
        await update.message.reply_text(t(lang, "ai_mode_intro"), reply_markup=machine_keyboard())
        return

    if user_text == MACHINE_MENU_LABEL:
        await choose_machine(update, context)
        return

    mode = context.user_data.get("mode")
    if mode == "general":
        await handle_general_ai(update, context, user_text)
        return

    ln = get_selected_line(context)
    if ln is None:
        await update.message.reply_text(
            t(lang, "no_machine_selected", ai=AI_CHAT_LABEL),
            reply_markup=machine_keyboard(),
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
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("adduser", adduser_cmd))
    app.add_handler(CommandHandler("removeuser", removeuser_cmd))
    app.add_handler(CommandHandler("listusers", listusers_cmd))
    app.add_handler(CommandHandler("nomatches", nomatches_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_feedback_callback))
    app.add_error_handler(error_handler)

    if app.job_queue is not None:
        if STATS_CHAT_ID:
            app.job_queue.run_daily(weekly_stats_job, time=datetime.strptime("08:00", "%H:%M").time())
        if HEALTHCHECK_PING_URL:
            app.job_queue.run_repeating(healthcheck_job, interval=HEALTHCHECK_INTERVAL_MIN * 60, first=10)

    logger.info(
        "Bot ishga tushdi... (%d ta uskuna, AI provayderlar: %s, kirish nazorati: %s)",
        len(LINES), ", ".join(name for name, _ in AI_PROVIDERS),
        "yoqilgan" if ACCESS_CONTROL_ENABLED else "o'chirilgan",
    )
    app.run_polling()


if __name__ == "__main__":
    main()
