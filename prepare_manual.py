# -*- coding: utf-8 -*-
"""
prepare_manual.py
------------------
Uskuna qo'llanmasi (foydalanuvchi/texnik xizmat ko'rsatish qo'llanmasi) PDF
faylini skanerlab, har bir sahifaning to'liq matnini JSON fayliga yozadi.
Bu orqali:
  1) Bot "Kutubxona" bo'limida qo'llanma ichidan kalit so'z bo'yicha
     qidiruv qila oladi va aynan tegishli sahifani rasm qilib yuboradi.
  2) PLC xatolik tashxisi chiqarilganda, agar qo'llanmada shu mavzuga
     tegishli matn topilsa, AI javobini yanada chuqurroq va aniqroq
     qilish uchun shu matn ham kontekstga qo'shiladi.

MUHIM: Bu skript ham hech narsani "o'zidan yozmaydi" — faqat sizning
haqiqiy PDF qo'llanmangizdagi matnni indekslaydi.

Ishlatilishi:
    python prepare_manual.py Qollanma.pdf manual_index_gem.json
"""

import sys
import json

import pymupdf


def main():
    if len(sys.argv) < 3:
        print("Ishlatilishi: python prepare_manual.py <Qollanma.pdf> <output_index.json>")
        sys.exit(1)

    pdf_path, out_path = sys.argv[1], sys.argv[2]
    doc = pymupdf.open(pdf_path)
    page_count = len(doc)

    pages = {}  # "1" -> "sahifa matni", "2" -> "..." (1-based)
    for page_num in range(page_count):
        text = doc[page_num].get_text().strip()
        if text:
            pages[str(page_num + 1)] = text

    doc.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "pdf_path": pdf_path,
            "page_count": page_count,
            "pages": pages,
        }, f, ensure_ascii=False, indent=1)

    total_chars = sum(len(v) for v in pages.values())
    print(f"Tayyor: {len(pages)}/{page_count} sahifa matni indekslandi -> '{out_path}'")
    print(f"Jami matn hajmi: ~{total_chars} belgi")


if __name__ == "__main__":
    main()
