# Tuzatilgan versiya — o'zgarishlar ro'yxati
# ============================================

## bot.py
1. WORD_RE — kirill (rus) harflar qo'shildi → "конвейер не работает" local search ishlaydi
2. GLOSSARY — ruscha texnik atamalar kengaytirildi (конвейер, датчик, давление, ...)
3. ADDR_RE — DB manzillari qo'llab-quvvatlanadi: DB1.DBX0.0, DB10.DBW2
4. find_tag() — % prefiks va case-insensitive normalizatsiya yaxshilandi
5. atomic_json_write() — cache / allowed_users / pending_reg / tags_kb race-safe yoziladi
6. safe_reply_text() — Markdown xato bo'lsa plain text; 4000+ belgili javoblar bo'linadi
7. GEMINI_MODEL default: gemini-2.0-flash (haqiqiy model nomi)

## prepare_tags.py
1. extract_station() — Station 3 / 工位3 / станция 2 formatlari
2. io_kind() — timer, counter, PI/PQ aniqlash

## .env.example
1. Haqiqiy API kalitlari olib tashlandi (placeholder)
2. GEMINI_MODEL=gemini-2.0-flash

## Muhim eslatma
- Eski .env dagi kalitlar ochiq qolgan bo'lishi mumkin — DARHOL yangilang!
- Schematic/manual indekslar lines.json ga hali bog'lanmagan (PDF bo'lsa prepare_*.py ishlating)
- Rate-limit va provider cooldown hali RAM da (restart da nol)
