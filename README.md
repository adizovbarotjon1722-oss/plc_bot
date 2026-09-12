# PLC Xatolik Diagnostika Boti — to'liq versiya

TIA Portal'dan eksport qilingan PLC Tag jadvallari asosida, Telegram orqali
xodimlar yozgan (yoki rasm sifatida yuborgan) muammoni AI yordamida tahlil
qilib, aynan qaysi signal/qism bilan bog'liqligini va nima qilish kerakligini
tushuntirib beruvchi bot.

## Asosiy xususiyatlar

- **3 tilni qo'llab-quvvatlaydi:** O'zbek, ingliz, xitoy.
- **5 ta uskuna/liniya:** GEM welding, SA3 welding, Water cooling,
  Water cooling robot, Deflashing.
- **Aqlli qidiruv + mahalliy lug'at:** AI'ga butun tag ro'yxati emas, faqat
  mos taglar yuboriladi; o'zbek/rus so'zlar avtomatik inglizcha texnik
  atamalarga moslashtiriladi (AI so'rovisiz).
- **Javoblarni keshlash:** bir xil savol qayta so'ralsa, AI'siz darhol
  javob beriladi.
- **3 ta bepul AI provayder, avtomatik almashinuv:** Gemini → Groq →
  OpenRouter, biri limitga uchrasa vaqtincha "dam oladi", boshqasi ishlaydi.
- **Hech qachon butunlay to'xtamaydi:** barcha AI'lar band bo'lsa, bazadan
  topilgan xom ma'lumot ko'rsatiladi.
- **🤖 Sun'iy intellekt bo'limi:** PLC bilan bog'liq bo'lmagan savollarga
  ham javob beradi.
- **📷 Rasm orqali murojaat:** HMI ekrani yoki indikatorning suratini
  yuborsa, bot undan matnni o'qib, xuddi yozma savoldek javob beradi.
- **👍/👎 fikr-mulohaza va ✅/❌ "Hal bo'ldimi?" tugmalari:** har bir javobdan
  keyin chiqadi. "Hal bo'lmadi" bosilsa, muammo avtomatik ravishda
  eskalatsiya chatiga (smena boshlig'i/muhandis) yuboriladi.
- **Haftalik statistik hisobot:** har dushanba, so'nggi 7 kunlik eng ko'p
  uchragan muammolar haqida qisqa hisobot belgilangan chatga yuboriladi.
- **Ma'lumot sifatini kuzatish:** "topilmadi" holatlari alohida logga
  yoziladi — bu orqali qaysi taglarga Excel'da izoh qo'shish kerakligini
  bilib olish mumkin (`/nomatches` buyrug'i orqali ko'rish mumkin).
- **Kirishni cheklash (ixtiyoriy):** yoqilsa, faqat ro'yxatdagi xodimlar
  botdan foydalana oladi.
- **Bot salomatligini kuzatish (ixtiyoriy):** healthchecks.io kabi xizmat
  orqali, bot to'xtab qolsa sizga xabar keladi.
- **Xatoliklarga chidamli:** kutilmagan xatolik bot jarayonini to'xtatmaydi.

## O'rnatish

```bash
cd plc_bot
pip install -r requirements.txt
```

## Sozlash

`.env.example` faylini `.env` deb nusxalang va kerakli qismlarni to'ldiring.
**Majburiy:** `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`. Qolganlari ixtiyoriy,
lekin tavsiya etiladi.

### Kirishni cheklash (ixtiyoriy)

1. @userinfobot'ga yozib, o'zingizning Telegram ID'ingizni bilib oling.
2. `.env`da `ADMIN_USER_IDS=sizning_id` deb yozing (bir nechtasi bo'lsa
   vergul bilan ajrating).
3. Botni ishga tushiring. Endi faqat siz (va siz qo'shgan foydalanuvchilar)
   foydalana oladi.
4. Yangi xodimni qo'shish: botga `/adduser <uning_telegram_id>` yozing.
5. Ro'yxatni ko'rish: `/listusers`. O'chirish: `/removeuser <id>`.

`ADMIN_USER_IDS` bo'sh qoldirilsa, bot hammaga ochiq bo'lib qoladi
(standart holat, hech narsa o'zgartirish shart emas).

### Eskalatsiya (ixtiyoriy)

`ESCALATION_CHAT_IDS`ga smena boshlig'i/muhandisning Telegram chat ID'sini
yozing (bir nechtasi bo'lsa vergul bilan). "❌ Hal bo'lmadi" bosilganda
ularga to'liq ma'lumot (uskuna, savol, bot javobi) yuboriladi.

### Haftalik hisobot (ixtiyoriy)

`STATS_CHAT_ID`ga hisobot yuborilishi kerak bo'lgan chat ID'ni yozing.

### Bot salomatligini kuzatish (ixtiyoriy)

1. https://healthchecks.io saytida (bepul) ro'yxatdan o'ting.
2. Yangi "check" yarating, ping URL'ni oling.
3. `.env`da `HEALTHCHECK_PING_URL=<url>` deb yozing.
4. Agar bot biror sababdan to'xtab qolsa (kompyuter o'chsa, internet
   uzilsa), healthchecks.io sizga email/xabar yuboradi.

## YANGI USKUNA QO'SHISH (kod o'zgartirish shart emas!)

1. `python prepare_tags.py YangiUskuna.xlsx tags_kb_yangiuskuna.json`
2. `lines.json`ga qo'shing: `{"id": "yangiuskuna", "label": "6. Yangi Uskuna", "kb_file": "tags_kb_yangiuskuna.json"}`
3. Botni qayta ishga tushiring.

## Ishga tushirish

```bash
python bot.py
```

## Buyruqlar ro'yxati

| Buyruq | Kim uchun | Vazifasi |
|---|---|---|
| `/start` | hammaga | tilni tanlash, botni boshlash |
| `/machine` | hammaga | uskuna tanlash menyusi |
| `/tag %I0.5` | hammaga | tezkor, AI'siz lug'aviy qidiruv |
| `/status` | hammaga | AI provayderlar holati |
| `/adduser <id>` | faqat admin | foydalanuvchiga ruxsat berish |
| `/removeuser <id>` | faqat admin | ruxsatni olib tashlash |
| `/listusers` | faqat admin | adminlar/ruxsat berilganlar ro'yxati |
| `/nomatches` | faqat admin | so'nggi "topilmadi" so'rovlar |

## Fayllar tuzilishi

```
plc_bot/
├── prepare_tags.py            # Excel -> JSON konvertor
├── lines.json                  # Uskunalar ro'yxati
├── tags_kb_*.json               # Har bir uskuna bilim bazasi
├── bot.py                       # Telegram bot
├── requirements.txt
├── .env.example
├── allowed_users.json           # (avtomatik yaratiladi) ruxsat berilganlar
├── answer_cache.json            # (avtomatik yaratiladi) javoblar keshi
├── queries.log                  # (avtomatik yaratiladi) barcha so'rovlar
├── no_match.log                 # (avtomatik yaratiladi) topilmagan so'rovlar
├── feedback.log                 # (avtomatik yaratiladi) 👍/👎 va hal bo'lish holati
└── README.md
```
