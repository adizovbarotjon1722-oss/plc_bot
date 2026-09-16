# -*- coding: utf-8 -*-
"""
prepare_schematic.py
---------------------
Elektr sxemasi PDF faylini skanerlab, har bir PLC manzili (%I0.1, %Q0.5 va
h.k.) qaysi sahifa(lar)da uchrashini topadi va shu ma'lumotni JSON fayliga
yozadi. Bot shu JSON orqali "aynan qaysi sahifada shu signal chizilgan"ni
bilib, o'sha sahifani rasmga aylantirib yuboradi.

MUHIM: Bu skript sxemani "chizmaydi" yoki o'zgartirmaydi — faqat PDF'dagi
matnni o'qib, manzillar qaysi sahifada yozilganini indekslaydi. Ko'rsatiladigan
rasm har doim sizning haqiqiy, asl PDF faylingizning bir sahifasi bo'ladi.

Ishlatilishi:
    python prepare_schematic.py Sxema.pdf schematic_index_gem.json
"""

import sys
import re
import json

import pymupdf

ADDR_RE = re.compile(r"%?\b[IQM]\d+\.\d+\b", re.IGNORECASE)


def main():
    if len(sys.argv) < 3:
        print("Ishlatilishi: python prepare_schematic.py <Sxema.pdf> <output_index.json>")
        sys.exit(1)

    pdf_path, out_path = sys.argv[1], sys.argv[2]
    doc = pymupdf.open(pdf_path)
    page_count = len(doc)

    index = {}  # "I0.1" -> [page_num, page_num, ...] (1-based sahifa raqami)
    for page_num in range(page_count):
        text = doc[page_num].get_text()
        for m in ADDR_RE.finditer(text):
            addr = m.group(0).upper().lstrip("%")
            index.setdefault(addr, [])
            if (page_num + 1) not in index[addr]:
                index[addr].append(page_num + 1)

    doc.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "pdf_path": pdf_path,
            "page_count": page_count,
            "addresses": index,
        }, f, ensure_ascii=False, indent=1)

    print(f"Tayyor: {len(index)} ta manzil indekslandi -> '{out_path}'")
    sample = list(index.items())[:5]
    for addr, pages in sample:
        print(f"  {addr}: {pages}-sahifa(lar)")


if __name__ == "__main__":
    main()
