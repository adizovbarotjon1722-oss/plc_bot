# PLC Xatolik Diagnostika Boti (ko'p uskunali, ko'p AI provayderli, aqlli qidiruv)

TIA Portal'dan eksport qilingan PLC Tag jadvallari asosida, Telegram orqali
xodimlar yozgan muammoni AI yordamida tahlil qilib, aynan qaysi signal/qism
bilan bog'liqligini va nima qilish kerakligini tushuntirib beruvchi bot.

## Asosiy xususiyatlar

- **3 tilni qo'llab-quvvatlaydi:** O'zbek, ingliz, xitoy. Xodim `/start`
  bosganda avval tilni tanlaydi — butun interfeys shu tilda ko'rsatiladi.
  AI javobi ham, agar xodim savolni boshqa (qo'llab-quvvatlanadigan) tilda
  yozsa, o'sha tilda javob berishga harakat qiladi. Tilni istalgan vaqtda
  "🌐 Til / Language / 语言" tugmasi orqali almashtirish mumkin.
- **5 ta uskuna/liniya** bitta bot orqali: GEM welding, SA3 welding,
  Water cooling, Water cooling robot, Deflashing.
- **Aqlli qidiruv:** Bot AI'ga BUTUN tag ro'yxatini emas, faqat so'rovga mos
  keladigan bir necha o'nlab tagni yuboradi. Bu tokenlarni keskin
  kamaytiradi, javobni tezlashtiradi va aniqlikni oshiradi.
- **3 ta bepul AI provayder, avtomatik almashinuv:** Gemini → Groq →
  OpenRouter. Biror provayder limitga uchrasa, vaqtincha "dam oladi" va
  bot avtomatik navbatdagisiga o'tadi; muddat tugagach, o'zi qayta sinaydi.
- **Hech qachon butunlay to'xtamaydi:** Agar barcha AI'lar band bo'lsa ham,
  bot bazadan topilgan xom (AI'siz) ma'lumotni ko'rsatadi.
- **Xatoliklarga chidamli:** Kutilmagan xatolik yuz bersa, bot butunlay
  to'xtab qolmaydi — xatolikni logga yozib, foydalanuvchiga tushunarli
  xabar beradi va ishlashda davom etadi.
- **🤖 Sun'iy intellekt bo'limi:** PLC bilan bog'liq bo'lmagan har qanday
  savolga ham javob beradi.
- **`/status` buyrug'i:** qaysi AI provayder hozir band, qaysi tayyorligini
  ko'rsatadi.

## Qanday ishlaydi

1. `prepare_tags.py` — har bir uskunaning TIA Portal Excel faylini o'qib,
   `tags_kb_*.json` bilim bazasini yaratadi.
2. `lines.json` — qaysi uskunalar mavjudligi va bilim bazalari ro'yxati.
3. `bot.py` — Telegram bot. Xodim uskunani tanlaydi, muammoni yozadi:
   - Agar shunchaki manzil yozsa (masalan `I0.1`) — AI'siz, to'g'ridan-to'g'ri
     topib, faqat o'sha bitta tag haqida AI'dan tushuntirish so'raydi.
   - Agar erkin so'z bilan tasvirlasa — avval mahalliy kalit so'z qidiruvi
     (va kerak bo'lsa AI yordamida kalit so'z ajratish) orqali eng mos
     ~40 ta tagni topadi, so'ng FAQAT o'shalarni AI'ga yuborib tushuntirish
     so'raydi.

## O'rnatish

```bash
cd plc_bot
pip install -r requirements.txt
```

## Sozlash

`.env.example` faylini `.env` deb nusxalang va to'ldiring:

- `TELEGRAM_BOT_TOKEN` — @BotFather'dan
- `GEMINI_API_KEY` — https://aistudio.google.com/apikey (bepul, majburiy)
- `GROQ_API_KEY` — https://console.groq.com/keys (bepul, tavsiya etiladi)
- `OPENROUTER_API_KEY` — https://openrouter.ai/keys (bepul, tavsiya etiladi)

Uchtasi ham qo'shilsa, tizim eng barqaror ishlaydi.

## YANGI USKUNA QO'SHISH (kod o'zgartirish shart emas!)

1. `python prepare_tags.py YangiUskuna.xlsx tags_kb_yangiuskuna.json`
2. `lines.json`ga qo'shing: `{"id": "yangiuskuna", "label": "6. Yangi Uskuna", "kb_file": "tags_kb_yangiuskuna.json"}`
3. Botni qayta ishga tushiring.

## Ishga tushirish

```bash
python bot.py
```

Telegram'da: uskunani tanlang → muammoni yozing (yoki manzilni yozing) →
javobni oling. `🤖 Sun'iy intellekt` tugmasi orqali erkin savol berish
mumkin. `/status` — AI provayderlar holatini ko'rsatadi. `/tag %I0.5` —
tezkor, AI'siz lug'aviy qidiruv.

## Muhim eslatmalar

- **Token tejash:** Endi har bir so'rov uchun AI'ga o'rtacha bir necha yuz —
  bir necha ming token yuboriladi (avval har doim 20,000-180,000 token
  yuborilardi). Bu Gemini/Groq/OpenRouter'ning bepul limitlariga ancha
  yaxshi sig'adi.
- **Avtomatik "dam olish":** Bir provayder xato bersa, xatoning turiga
  qarab (kunlik limit / daqiqalik limit / boshqa) turlicha muddatga
  "dam oladi" va shu muddat davomida qayta urinilmaydi — bu vaqtni va
  keraksiz so'rovlarni tejaydi. Muddat tugagach avtomatik qayta faollashadi.
- **Doimiy ishlashi uchun:** Kompyuterni 24/7 ishlaydigan qilib sozlash
  bo'yicha alohida yo'riqnoma berilgan edi (Power sozlamalari, BIOS,
  Task Scheduler orqali avtomatik ishga tushirish).
- Ma'lumotlar AI xizmatlariga (Google, Groq, OpenRouter) yuboriladi —
  jadvalda maxfiy/tijorat sirlari bo'lsa, buni hisobga oling.

## Fayllar tuzilishi

```
plc_bot/
├── prepare_tags.py            # Excel -> JSON konvertor
├── lines.json                  # Uskunalar ro'yxati
├── tags_kb_*.json               # Har bir uskuna bilim bazasi
├── bot.py                       # Telegram bot
├── requirements.txt
├── .env.example
└── README.md
```
