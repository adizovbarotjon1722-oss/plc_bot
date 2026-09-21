# PLC Xatolik Diagnostika Boti

TIA Portal'dan eksport qilingan PLC Tag jadvallari asosida, Telegram orqali
xodimlar yozgan (yozma, ovozli yoki rasm sifatida yuborgan) muammoni AI
yordamida chuqur tahlil qilib, aynan qaysi signal/qism bilan bog'liqligini,
kerak bo'lsa elektr sxemasi va qo'llanma sahifalarini ko'rsatib, nima qilish
kerakligini tushuntirib beruvchi bot.

## Asosiy xususiyatlar

- **3 tilni qo'llab-quvvatlaydi:** O'zbek, ingliz, xitoy — har bir AI
  so'roviga til-eslatmasi biriktirilib, tanlangan/aniqlangan tilga barqaror
  rioya qilinishi ta'minlangan.
- **5 ta uskuna/liniya** (kengaytiriladigan): GEM welding, SA3 welding,
  Water cooling, Water cooling robot, Deflashing.
- **Aqlli, chuqur tashxis:** AI'ga butun tag ro'yxati emas, faqat mos
  taglar yuboriladi (token tejaladi); o'zbek/rus so'zlar mahalliy lug'at
  orqali inglizcha texnik atamalarga moslashtiriladi; javob elektr
  sxemasi va qo'llanma matnidan olingan haqiqiy ma'lumot bilan
  boyitiladi (pastga qarang).
- **📷 Rasm orqali murojaat:** HMI ekrani yoki indikator suratini
  Gemini vision orqali bevosita tahlil qiladi (nafaqat matn o'qish).
- **🎤 Ovozli xabar:** Groq Whisper orqali matnga aylantiriladi.
- **🔌 Elektr sxemasi:** haqiqiy PDF sxemangizdan tegishli sahifani
  avtomatik topib, rasm qilib yuboradi (AI hech narsa "chizmaydi").
- **📚 Kutubxona/Qo'llanma:** uskuna qo'llanmasi PDF'idan mavzu bo'yicha
  qidiruv, va tashxis javobini qo'llanma matni bilan chuqurlashtirish.
- **3 ta bepul AI provayder, avtomatik almashinuv:** Gemini → Groq →
  OpenRouter, biri limitga uchrasa vaqtincha "dam oladi".
- **Hech qachon butunlay to'xtamaydi:** barcha AI'lar band bo'lsa, bazadan
  topilgan xom ma'lumot ko'rsatiladi.
- **🤖 Sun'iy intellekt bo'limi:** PLC bilan bog'liq bo'lmagan savollarga
  ham javob beradi.
- **Javoblarni keshlash:** bir xil savol qayta so'ralsa, AI'siz darhol
  javob beriladi.
- **👍/👎 fikr-mulohaza** har bir javobdan keyin.
- **Kirishni cheklash (ixtiyoriy):** faqat ro'yxatdagi xodimlar botdan
  foydalana oladi; `/register` orqali o'zi so'rov yuboradi, admin bitta
  tugma bilan tasdiqlaydi.
- **Foydalanuvchi boshiga so'rov chegarasi:** bitta xodim kunlik AI
  limitini yakka o'zi tugatib qo'ymasligi uchun.
- **Ma'lumot sifati asboblari:** `/find` (tezkor qidiruv), `/addcomment`
  (izoh qo'shish), `/nomatches`, `/topfaults`.
- **🏭 Zavod monitoring (ixtiyoriy):** ESP32 asosidagi kompressor/chiller
  tizimining holatini (faqat o'qish) ko'rsatadi.
- **Bot salomatligini kuzatish, log arxivlash, zaxira nusxa** — barchasi
  avtomatik.
- **Xatoliklarga chidamli:** kutilmagan xatolik bot jarayonini to'xtatmaydi.

## O'rnatish

```bash
cd plc_bot
pip install -r requirements.txt
```

## Sozlash

`.env.example` faylini `.env` deb nusxalang va to'ldiring:

- `TELEGRAM_BOT_TOKEN` — @BotFather'dan
- `GEMINI_API_KEY` — https://aistudio.google.com/apikey (bepul, majburiy)
- `GROQ_API_KEY` — https://console.groq.com/keys (bepul, tavsiya etiladi —
  zaxira AI VA ovozli xabarlarni matnga aylantirish uchun ham kerak)
- `OPENROUTER_API_KEY` — https://openrouter.ai/keys (bepul, tavsiya etiladi)

### Kirishni cheklash (ixtiyoriy)

1. @userinfobot'ga yozib, o'zingizning Telegram ID'ingizni bilib oling.
2. `.env`da `ADMIN_USER_IDS=sizning_id` deb yozing.
3. Botni ishga tushiring. Endi faqat siz (va siz qo'shgan/tasdiqlagan
   foydalanuvchilar) foydalana oladi.
4. Yangi xodim qo'shish: `/adduser <telegram_id> <ism> <telefon>` — yoki
   xodimning o'zi botga `/register <ism> <telefon>` yozadi, sizga
   tasdiqlash so'rovi keladi (✅/❌ tugma bilan).
5. Ro'yxatni ko'rish: `/listusers`. O'chirish: `/removeuser <id>`.

`ADMIN_USER_IDS` bo'sh qoldirilsa, bot hammaga ochiq bo'lib qoladi.

## YANGI USKUNA QO'SHISH (kod o'zgartirish shart emas!)

1. `python prepare_tags.py YangiUskuna.xlsx tags_kb_yangiuskuna.json`
2. `lines.json`ga qo'shing:
   ```json
   {"id": "yangiuskuna", "label": "6. Yangi Uskuna", "kb_file": "tags_kb_yangiuskuna.json"}
   ```
3. Botni qayta ishga tushiring.

## Elektr sxemasi bilan integratsiya (ixtiyoriy)

Bot hech qachon sxemani o'zi "chizmaydi" — faqat sizning haqiqiy PDF
faylingizning haqiqiy sahifasini topib ko'rsatadi (PDF matnini skanerlab,
qaysi manzil qaysi sahifada yozilganini indekslash orqali).

1. `python prepare_schematic.py Sxema.pdf schematic_index_gem.json`
2. `lines.json`da tegishli uskunaga qo'shing:
   ```json
   {"id": "gem", ..., "schematic_index": "schematic_index_gem.json"}
   ```
3. Endi `I0.1` kabi manzil so'ralganda, agar sxemada shu manzil topilsa,
   bot javobdan keyin tegishli sahifa(lar)ni rasm qilib yuboradi.

## 📚 Kutubxona/Qo'llanma bilan integratsiya (ixtiyoriy)

Uskunangiz uchun foydalanuvchi/texnik xizmat qo'llanmasi PDF sifatida
mavjud bo'lsa, bot undan ikki xil foydalanadi:

1. **To'g'ridan-to'g'ri qidiruv:** "📚 Qo'llanma" tugmasi orqali xodim
   mavzu yozadi (masalan "moylash"), bot qo'llanmadan mos sahifani topib,
   rasm qilib yuboradi.
2. **Chuqurroq tashxis:** PLC muammosi tahlil qilinayotganda, agar
   qo'llanmada shu mavzuga oid matn topilsa, u AI'ning javobini
   chuqurlashtirish uchun (rasmiy protseduralar, ishlab chiqaruvchi
   tavsiyalari) avtomatik ravishda kontekstga qo'shiladi.

### Sozlash

1. `python prepare_manual.py Qollanma.pdf manual_index_gem.json`
2. `lines.json`da tegishli uskunaga qo'shing:
   ```json
   {"id": "gem", ..., "manual_index": "manual_index_gem.json"}
   ```
3. Botni qayta ishga tushiring — endi "📚 Qo'llanma" tugmasi menyuda
   ko'rinadi (kamida bitta uskunada qo'llanma sozlangan bo'lsa).

## Ishga tushirish

```bash
python bot.py
```

Telegram'da: tilni tanlang → uskunani tanlang → muammoni yozing/ayting/
rasmga oling → javobni oling (kerak bo'lsa sxema/qo'llanma sahifasi bilan
birga). `🤖 Sun'iy intellekt` — erkin savol. `/status` — AI provayderlar
holati. `/tag %I0.5` yoki `/find <so'z>` — tezkor, AI'siz qidiruv.

## Buyruqlar ro'yxati

| Buyruq | Kim uchun | Vazifasi |
|---|---|---|
| `/start` | hammaga | tilni tanlash, botni boshlash |
| `/help` | hammaga | botning imkoniyatlari haqida qisqa qo'llanma |
| `/machine` | hammaga | uskuna tanlash menyusi |
| `/tag %I0.5` | hammaga | aniq manzil bo'yicha tezkor, AI'siz qidiruv |
| `/find <so'z>` | hammaga | kalit so'z bo'yicha tezkor tag qidiruv |
| `/status` | hammaga | AI provayderlar holati |
| `/register <ism> <tel>` | hammaga | botdan foydalanish uchun so'rov yuborish |
| `/adduser <id> <ism> <tel>` | faqat admin | foydalanuvchiga ruxsat berish |
| `/setphone <id> <tel>` | faqat admin | xodim telefon raqamini yangilash |
| `/removeuser <id>` | faqat admin | ruxsatni olib tashlash |
| `/listusers` | faqat admin | adminlar/ruxsat berilganlar ro'yxati |
| `/addcomment <uskuna> <manzil> <matn>` | faqat admin | tag izohini to'g'ridan-to'g'ri to'ldirish |
| `/nomatches` | faqat admin | so'nggi "topilmadi" so'rovlar |
| `/topfaults [kun]` | faqat admin | eng ko'p nosozlik chiqargan uskuna/manzillar |

## ESP32 zavod monitoring integratsiyasi (ixtiyoriy)

Agar sizda kompressor/chiller monitoring ESP32 tizimi bo'lsa, uni shu
Python botga **qo'shimcha, faqat ko'rish** rejimida bog'lash mumkin:

1. ESP32 kodingizga `/status` JSON endpoint qo'shing (mavjud Telegram bot,
   rele, sirena mantig'iga tegmasdan — buni oldin birga tayyorlagan edik).
2. `.env`da `ESP32_STATUS_URL=http://<ESP32_IP>/status` deb yozing.
3. Botni qayta ishga tushiring — "🏭 Zavod monitoring" tugmasi paydo bo'ladi.

**Muhim cheklov (ataylab):** bu integratsiya faqat o'qish uchun — boshqarish
faqat ESP32'ning o'z Telegram boti va jismoniy tugmalari orqali qoladi.

## Muhim eslatmalar

- **Bepul AI limitlari:** Gemini/Groq/OpenRouter'ning har birining o'z
  kunlik/daqiqalik bepul limiti bor. `/status` orqali holatini tekshiring.
- **Foydalanuvchi chegarasi:** standart holatda bitta xodim daqiqasiga
  3 tadan, kuniga 30 tadan ortiq AI so'rovi yubora olmaydi (adminlarga
  chegara yo'q) — `.env`da `RATE_LIMIT_PER_MIN`/`RATE_LIMIT_PER_DAY`
  orqali sozlanadi.
- **Loglar:** `queries.log`, `no_match.log`, `feedback.log` har oy
  avtomatik arxivlanadi; `allowed_users.json` va
  `pending_registrations.json`dan har kuni zaxira nusxa olinadi
  (`backups/` papkasida, oxirgi `BACKUP_KEEP` tasi saqlanadi).
- **Doimiy ishlashi uchun:** kompyuterni 24/7 ishlaydigan qilib sozlash
  (Power sozlamalari, BIOS, Task Scheduler) alohida yo'riqnoma sifatida
  berilgan edi.
- Ma'lumotlar AI xizmatlariga (Google, Groq, OpenRouter) yuboriladi —
  jadvalda maxfiy/tijorat sirlari bo'lsa, buni hisobga oling.

## Fayllar tuzilishi

```
plc_bot/
├── prepare_tags.py             # Excel -> JSON konvertor
├── prepare_schematic.py         # Sxema PDF -> manzil/sahifa indeksi
├── prepare_manual.py            # Qo'llanma PDF -> sahifa matni indeksi
├── lines.json                    # Uskunalar ro'yxati
├── tags_kb_*.json                 # Har bir uskuna bilim bazasi
├── schematic_index_*.json         # (ixtiyoriy) sxema indekslari
├── manual_index_*.json            # (ixtiyoriy) qo'llanma indekslari
├── bot.py                         # Telegram bot
├── requirements.txt
├── .env.example
├── allowed_users.json             # (avtomatik) ruxsat berilganlar
├── pending_registrations.json     # (avtomatik) /register so'rovlari
├── answer_cache.json              # (avtomatik) javoblar keshi
├── queries.log                    # (avtomatik) barcha so'rovlar
├── no_match.log                   # (avtomatik) topilmagan so'rovlar
├── feedback.log                   # (avtomatik) 👍/👎 holati
├── backups/                        # (avtomatik) kunlik zaxira nusxalar
└── README.md
```
