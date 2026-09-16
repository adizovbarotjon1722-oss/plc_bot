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

Bot orqali kunlik topshiriq/vazifa berish mumkin — siz (muhandis)
mexaniklarga vazifa yozasiz, ish vaqtini belgilaysiz, ular avtomatik
ravishda **shaxsiy Telegram chatiga** yuboriladi, shu bilan birga botning
"Xodimlar" bo'limida ham saqlanadi. Kun oxirida esa barcha natijalar
avtomatik ravishda sizga (adminga) hisobot qilib yuboriladi.

### Sozlash

1. Avval `ADMIN_USER_IDS`ni to'ldiring (yuqoridagi "Kirishni cheklash"
   bo'limiga qarang) — siz shu orqali admin bo'lasiz.
2. Har bir mexanikni ism va telefon raqami bilan qo'shing (ikkalasi ham
   ixtiyoriy, lekin tavsiya etiladi — vazifa albatta to'g'ri odamga
   borishini ta'minlaydi):
   ```
   /adduser 123456789 +998901234567 Aziz Karimov
   /adduser 987654321 Botir
   ```
   (Telefon raqami avtomatik aniqlanadi — uni istalgan joyga yozsangiz ham
   bo'ladi, faqat "+" yoki raqamdan boshlansa yetarli.)
3. Ixtiyoriy: `DAILY_REPORT_TIME` orqali kunlik hisobot soatini sozlang
   (standart: 18:00).

### Foydalanish (admin/muhandis sifatida)

1. Pastdagi **"🔑 Admin bo'limi"** tugmasini bosing (bu tugma faqat
   adminlarga ko'rinadi).
2. **"📋 Yangi topshiriq"** ni tanlang.
3. Kimga berilishini tanlang — muayyan xodim yoki **"🌐 Hammaga"**.
4. Topshiriq matnini yozing (masalan: "3-stansiyadagi konveyer motorini
   moylang").
5. Ish vaqtini kiriting (masalan: "09:00–13:00") yoki vaqt belgilamaslikni
   tanlang.
6. Bosilgan zahoti, tanlangan xodim(lar)ning shaxsiy Telegram chatiga
   to'liq ma'lumot (vazifa + vaqt + kim yuborgani) bilan xabar boradi.
7. **"📊 Hisobot"** tugmasi orqali istalgan payt shu kunning barcha
   topshiriqlari va ularning holatini jadval ko'rinishida ko'rishingiz
   mumkin.

### Xodim tomonidan

Har bir xodim pastdagi **"👥 Xodimlar"** tugmasini bosib:
- O'z profilini (ism, ID, telefon) ko'radi.
- So'nggi 5 ta o'ziga tegishli topshiriqni (matn, vaqt, holat bilan) ko'radi.
- Har bir topshiriq ostida **3 ta aniq holat** tugmasi bor:
  - **▶️ Boshladim** — jarayonda ekanini bildiradi
  - **✅ Bajardim** — tugallanganini bildiradi
  - **❌ Bajarolmadim** — bajarilmaganini bildiradi
- Qaysi tugma bosilishidan qat'iy nazar, **darhol adminga** (vazifani
  bergan kishiga) xodim ismi, vazifa matni va tanlangan holat bilan xabar
  boradi.

### Kunlik avtomatik hisobot

Har kuni `DAILY_REPORT_TIME`da (standart 18:00) bot o'zi barcha
adminlarga o'sha kunning **to'liq jadvalini** (xodim, vazifa, vaqt, holat)
yuboradi — buni so'rashning hojati yo'q, avtomatik keladi.

## 📷 Rasm orqali murojaat (kuchaytirilgan)

Xodim uskunadagi xatolik/indikatorning suratini yuborsa, bot endi:
1. Rasmni Gemini (rasmni tushunadigan AI) orqali tahlil qiladi — ekrandagi
   matnni o'qiydi VA qidiruv uchun kalit so'zlarni ajratadi (hatto ekranda
   umuman matn bo'lmasa, faqat yonib turgan lampa bo'lsa ham).
2. Shu kalit so'zlar orqali tegishli PLC taglarni topadi.
3. **Yakuniy tashxisni rasmning O'ZIDAN** chiqaradi (nafaqat o'qilgan
   matndan) — bu indikator rangi, ekran tuzilishi kabi oddiy matn
   o'qishda yo'qolib ketadigan tafsilotlarni ham hisobga oladi, natijada
   ancha aniqroq javob beradi.
4. Agar Gemini band bo'lsa, avtomatik ravishda o'qilgan matn asosida
   oddiy (Groq/OpenRouter orqali ham ishlaydigan) tahlilga o'tadi.

## Yangi qo'shilgan 7 ta imkoniyat

### 1. Muddati o'tgan topshiriqlar uchun eslatma
Vazifa berayotganda vaqtni "14:00 dan 18:00 gacha" kabi yozsangiz, bot oxirgi
vaqtni (18:00) muddat sifatida tushunadi. Shu vaqt o'tib ketsa-yu xodim hali
"Bajardim/Bajarilmadi" bosmagan bo'lsa — har 15 daqiqada (`TASK_REMINDER_CHECK_MIN`)
tekshirilib, xodimga eslatma, sizga (adminga) ogohlantirish yuboriladi. Har bir
xodimga bitta topshiriq uchun faqat bir marta eslatiladi.

### 2. Doimiy/takrorlanuvchi kunlik topshiriqlar
Admin bo'limida **"🔁 Doimiy topshiriq"** tugmasi orqali: kimga → matn → soat
nechada (masalan `08:00`) — shu uch qadamdan so'ng, bot **har kuni** shu vaqtda
avtomatik ravishda o'sha topshiriqni yaratib, tegishli xodim(lar)ga yuboraveradi.
Botni qayta ishga tushirsangiz ham, barcha doimiy topshiriqlar avtomatik qayta
faollashadi.

### 3. Xodimlarni osonroq ro'yxatdan o'tkazish
Endi xodim o'zi botga yozadi:
```
/register Aziz Karimov +998901234567
```
Sizga (barcha adminlarga) ✅/❌ tugmali so'rov keladi — bitta bosish bilan
qabul qilasiz yoki rad etasiz. Qabul qilinsa, xodim avtomatik ravishda
`/adduser` bilan qo'lda qo'shgandek ro'yxatga kiradi (ism va telefon bilan).

### 4. Ovozli xabar orqali murojaat
Xodim yozish o'rniga ovozli xabar yuborsa, bot uni (Groq Whisper orqali,
tanlangan tilda) matnga aylantirib, xuddi yozma savoldek javob beradi.

### 5. Ish bajarilgach rasm-dalil
"✅ Bajardim" yoki "❌ Bajarilmadi" bosilgandan keyin, bot ixtiyoriy ravishda
rasm so'raydi (10 daqiqa ichida). Xodim rasm yuborsa, u sizga (barcha
adminlarga) tegishli topshiriq matni bilan birga forward qilinadi.

### 6. `/help` buyrug'i
Botning barcha imkoniyatlarini joriy tilda tushuntirib beradi — yangi xodim
uchun juda foydali.

### 7. Log arxivlash va zaxira nusxa
- `queries.log`, `no_match.log`, `feedback.log` har oy avtomatik arxivlanadi
  (masalan `queries-2026-09.log` deb saqlanib, yangisi boshlanadi).
- `tasks.json`, `allowed_users.json`, `recurring_tasks.json` fayllaridan
  har kuni `backups/` papkasiga zaxira nusxa olinadi (oxirgi `BACKUP_KEEP`
  tasi — standart 14 kunlik — saqlanadi).

## Elektr sxemasi bilan integratsiya (ixtiyoriy)

Agar uskunangiz uchun elektr sxemasi PDF fayl sifatida mavjud bo'lsa (masalan
EPLAN yoki TIA Portal'dan eksport qilingan), bot mos signal so'ralganda
**aynan o'sha PDF'ning tegishli sahifasini** rasm qilib yuborishi mumkin.

**Muhim:** Bot hech qachon sxemani o'zi "chizmaydi" yoki taxmin qilmaydi —
faqat sizning haqiqiy PDF faylingizning haqiqiy sahifasini topib ko'rsatadi.
Bu PDF'dagi matnni skanerlab, qaysi manzil (%I0.1 kabi) qaysi sahifada
yozilganini indekslash orqali ishlaydi.

### Sozlash

1. Har bir uskuna uchun sxema PDF faylini indekslang:
   ```bash
   python prepare_schematic.py GEM_Sxema.pdf schematic_index_gem.json
   ```
   Bu buyruq PDF'dagi barcha sahifalarni skanerlab, qaysi PLC manzili qaysi
   sahifada yozilganini topadi va shuni JSON faylga yozadi.

2. `lines.json` faylida tegishli uskunaga `schematic_index` maydonini qo'shing:
   ```json
   {"id": "gem", "label": "1. GEM welding", "kb_file": "tags_kb_gem.json",
    "schematic_index": "schematic_index_gem.json"}
   ```

3. Botni qayta ishga tushiring. Endi `I0.1` kabi manzil so'ralganda yoki
   `/tag %I0.1` yozilganda, agar sxemada shu manzil topilsa, bot javobdan
   keyin tegishli sahifa(lar)ni rasm qilib yuboradi.

Sxema hozircha faqat aniq manzil (masalan I0.1) so'ralganda yoki AI aynan
shu manzilni tanlab, javob bergan taglar orasida bo'lsa ko'rsatiladi — erkin
tavsif orqali topilgan barcha nomzod-taglar uchun ham tekshiriladi.

## Fayllar tuzilishi

```
plc_bot/
├── prepare_tags.py            # Excel -> JSON konvertor
├── prepare_schematic.py         # Sxema PDF -> manzil/sahifa indeksi konvertor
├── lines.json                  # Uskunalar ro'yxati
├── tags_kb_*.json               # Har bir uskuna bilim bazasi
├── schematic_index_*.json       # (ixtiyoriy) sxema manzil/sahifa indekslari
├── bot.py                       # Telegram bot
├── requirements.txt
├── .env.example
├── allowed_users.json           # (avtomatik yaratiladi) ruxsat berilganlar
├── tasks.json                    # (avtomatik yaratiladi) barcha topshiriqlar
├── recurring_tasks.json          # (avtomatik yaratiladi) doimiy topshiriq shablonlari
├── pending_registrations.json    # (avtomatik yaratiladi) /register so'rovlari
├── answer_cache.json            # (avtomatik yaratiladi) javoblar keshi
├── queries.log                  # (avtomatik yaratiladi) barcha so'rovlar
├── no_match.log                 # (avtomatik yaratiladi) topilmagan so'rovlar
├── feedback.log                 # (avtomatik yaratiladi) 👍/👎 va hal bo'lish holati
├── backups/                      # (avtomatik yaratiladi) kunlik zaxira nusxalar
└── README.md
```
