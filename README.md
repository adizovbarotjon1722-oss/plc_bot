# PLC Xatolik Diagnostika Boti — to'liq versiya

TIA Portal'dan eksport qilingan PLC Tag jadvallari asosida, Telegram orqali
xodimlar yozgan (yoki rasm sifatida yuborgan) muammoni AI yordamida tahlil
qilib, aynan qaysi signal/qism bilan bog'liqligini va nima qilish kerakligini
tushuntirib beruvchi bot.

## Asosiy xususiyatlar

- **3 tilni qo'llab-quvvatlaydi (kuchaytirilgan):** O'zbek, ingliz, xitoy —
  har bir AI so'roviga qo'shimcha til-eslatmasi biriktirilib, bepul/zaifroq
  AI modellari ham tanlangan tilga barqaror rioya qilishi ta'minlangan.
- **👥 Xodimlar va 🔑 Admin bo'limi:** muhandis/admin mexaniklarga kunlik
  topshiriq beradi — xabar avtomatik ravishda har bir xodimning shaxsiy
  Telegram chatiga boradi va botda saqlanadi (pastga qarang).
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

## Xodimlar va Admin bo'limlari

Endi botda ikkita qo'shimcha bo'lim bor:

- **👥 Xodimlar** — hamma ko'radi. O'z profilini (ism, ID) va o'ziga
  berilgan so'nggi topshiriqlarni (holati bilan) ko'rsatadi. Har bir
  bajarilmagan topshiriq oldida "✅ Bajardim" tugmasi bo'ladi.
- **🔑 Admin bo'limi** — faqat `ADMIN_USER_IDS`da ko'rsatilgan
  foydalanuvchilarga ko'rinadi. Shu yerdan:
  - **📋 Kunlik topshiriq berish** — avval kimga (bitta xodim yoki
    "🌐 Hammaga") ekanini tanlaysiz, keyin matnni yozasiz. Xabar
    darhol har bir tegishli xodimning shaxsiy Telegram chatiga
    yuboriladi VA botda saqlanadi (xodim "Xodimlar" bo'limidan istalgan
    vaqt qayta ko'rishi mumkin).
  - **👥 Xodimlar ro'yxati** — barcha ro'yxatdagi xodimlar va adminlar.

**Xodimni ism bilan qo'shish:**
```
/adduser <telegram_id> <ism>
```
Masalan: `/adduser 123456789 Sardor mexanik`

Xodim topshiriqni "✅ Bajardim" tugmasi orqali belgilaganda, sizga (uni
bergan adminga) avtomatik xabar keladi.

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

## ESP32 zavod monitoring integratsiyasi (ixtiyoriy)

Agar sizda kompressor/chiller monitoring ESP32 tizimi bo'lsa (o'z Telegram
boti bilan), uni shu Python botga **qo'shimcha, faqat ko'rish** rejimida
bog'lash mumkin:

1. `zavod_monitoring_v08_status_api.ino` faylini (yoki o'zingizning ESP32
   kodingizga xuddi shunday qo'shilgan `/status` endpoint'ni) ESP32'ga
   yuklang. Bu ESP32'ning mavjud Telegram bot, rele, sirena mantig'iga
   **hech qanday ta'sir qilmaydi** — faqat mahalliy tarmoqda o'qish uchun
   kichik JSON sahifa qo'shadi.
2. ESP32 ishga tushgach, Serial Monitor'da shunday qator chiqadi:
   `📡 Status-server ishga tushdi: http://192.168.1.XX/status`
3. Shu manzilni nusxalab, `.env` fayliga qo'ying:
   ```
   ESP32_STATUS_URL=http://192.168.1.XX/status
   ```
4. Python botni qayta ishga tushiring. Endi menyuda **"🏭 Zavod monitoring"**
   tugmasi paydo bo'ladi — bosilganda joriy bosim, harorat, rejim va
   signalizatsiya holatini ko'rsatadi.

**Muhim cheklovlar (ataylab shunday qilingan, xavfsizlik uchun):**
- Bu integratsiya **faqat o'qish** uchun — Python bot orqali ESP32'ni
  boshqarish (start/stop/rejim/reset) mumkin emas. Boshqaruv faqat
  ESP32'ning o'z Telegram boti va jismoniy tugmalari orqali qoladi.
- Python bot va ESP32 **bitta mahalliy tarmoqda (Wi-Fi)** bo'lishi kerak —
  ESP32'ning IP-manziliga tashqaridan (masalan boshqa shahardan) kirib
  bo'lmaydi, faqat shu tarmoq ichida.
- ESP32'ning IP-manzili DHCP orqali vaqti-vaqti bilan o'zgarishi mumkin.
  Buni oldini olish uchun routeringizda ESP32'ga **statik IP** yoki
  "DHCP reservation" belgilashni tavsiya qilamiz — aks holda IP o'zgarsa,
  `.env`dagi `ESP32_STATUS_URL`ni qo'lda yangilashingiz kerak bo'ladi.

## 👥 Xodimlar va 🔑 Admin bo'limi (kunlik topshiriqlar)

Endi bot orqali kunlik topshiriq/vazifa berish mumkin — siz (muhandis)
mexaniklarga vazifa yozasiz, ular avtomatik ravishda **shaxsiy Telegram
chatiga** yuboriladi, shu bilan birga botning "Xodimlar" bo'limida ham
saqlanadi.

### Sozlash

1. Avval `ADMIN_USER_IDS`ni to'ldiring (yuqoridagi "Kirishni cheklash"
   bo'limiga qarang) — siz shu orqali admin bo'lasiz.
2. Har bir mexanikni ism bilan qo'shing:
   ```
   /adduser 123456789 Aziz
   /adduser 987654321 Botir
   ```
   (Ism yozmasangiz ham bo'ladi, lekin ism bilan ro'yxat chiroyliroq ko'rinadi.)

### Foydalanish (admin/muhandis sifatida)

1. Pastdagi **"🔑 Admin bo'limi"** tugmasini bosing (bu tugma faqat
   adminlarga ko'rinadi).
2. **"📋 Kunlik topshiriq berish"** ni tanlang.
3. Kimga berilishini tanlang — muayyan xodim yoki **"🌐 Hammaga"**.
4. Topshiriq matnini yozing (masalan: "3-stansiyadagi konveyer motorini
   moylang").
5. Bosilgan zahoti, tanlangan xodim(lar)ning shaxsiy Telegram chatiga
   xabar boradi, va bot buni saqlab qoladi.

### Xodim tomonidan

Har bir xodim pastdagi **"👥 Xodimlar"** tugmasini bosib:
- O'z profilini (ism, ID) ko'radi.
- So'nggi 5 ta o'ziga tegishli topshiriqni (holati bilan) ko'radi.
- Har bir bajarilmagan topshiriq ostida **"✅ Bajardim"** tugmasi bor —
  bosilsa, admin(engineer)ga avtomatik xabar boradi va topshiriq
  "Bajarildi" deb belgilanadi.

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
