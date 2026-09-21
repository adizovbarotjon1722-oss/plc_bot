# PLC Diagnostika Bot — v2 (xavfsizlik + kutubxona)

## Yangi imkoniyatlar

### 1. Majburiy kirish nazorati
- `ADMIN_USER_IDS` bo'sh bo'lsa bot **ishga tushmaydi**
- Har bir yangi foydalanuvchi `/register Ism +998...` orqali so'rov yuboradi
- Admin ✅/❌ tugma bilan tasdiqlaydi
- Tasdiqlanmaganlar hech qanday ma'lumot / AI / kutubxonani ko'rmaydi

### 2. Kutubxona (Library)
**Foydalanuvchi (ruxsatli):**
- Menyuda `📚 Kutubxona / Library`
- `/library` yoki `/libget <ID>` — hujjatni olish

**Admin:**
- Menyuda `🔐 Admin: Kutubxona boshqaruvi`
- `/libadd <sarlavha>` → keyin PDF/DOC/DOCX/TXT/JPG/PNG yuborish
- `/liblist` — barcha hujjatlar
- `/libdel <ID>` — o'chirish

Fayllar `library_files/` da, meta `library_docs.json` da.

### 3. Xavfsizlik
- Access control doimo yoqilgan
- Registratsiya spam limiti (soatiga 3)
- Fayl turi va hajm cheklovi
- Path traversal himoyasi (`sanitize_filename`, abs path check)
- Callback/admin amallarida `is_admin` qayta tekshiruv
- Markdown fallback (`safe_reply_text`)
- Atomic JSON yozish

### 4. Dizayn
- Aniqroq menyu qatorlari
- Admin uchun alohida kutubxona tugmasi
- Kirish rad etilganda batafsil ko'rsatma

## O'rnatish
1. `.env` da `ADMIN_USER_IDS` ni to'ldiring (majburiy)
2. `bot.py` ni almashtiring
3. `mkdir -p library_files`
4. `python bot.py`
