# -*- coding: utf-8 -*-
"""
prepare_tags.py
----------------
TIA Portal'dan eksport qilingan PLC Tag jadvalini (Excel) o'qib,
Telegram bot ishlatadigan tags_kb.json bilim bazasiga aylantiradi.

Ishlatilishi:
    python prepare_tags.py PLCTags.xlsx tags_kb.json
"""

import sys
import re
import json
import openpyxl

# Xitoycha "N-工位" (N-stansiya) so'zlarini stansiya raqamiga aylantirish uchun lug'at
CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

STATION_RE_CN = re.compile(r"([一二三四五六七八九十])工位")
STATION_RE_ST = re.compile(r"\bST0*([0-9]+)\b", re.IGNORECASE)


STATION_RE_NUM = re.compile(r"(?:station|stansiya|участок|станция)\s*#?\s*0*([0-9]+)", re.IGNORECASE)
STATION_RE_CN2 = re.compile(r"工位\s*0*([0-9]+)")


def extract_station(text: str):
    """Matndan stansiya raqamini ajratib olishga harakat qiladi.
    Formatlar: '三工位', '工位3', 'ST3', 'Station 3', 'станция 2'."""
    if not text:
        return None
    m = STATION_RE_CN.search(text)
    if m:
        return CN_NUM.get(m.group(1))
    m = STATION_RE_CN2.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    m = STATION_RE_ST.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    m = STATION_RE_NUM.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def io_kind(address: str):
    """Manzil turini aniqlaydi: kiruvchi (I), chiquvchi (Q) yoki ichki/marker (M, DB va h.k.)."""
    if not address:
        return "unknown"
    a = address.lstrip("%").upper()
    if a.startswith("I") or a.startswith("PI") or a.startswith("AI"):
        return "input"
    if a.startswith("Q") or a.startswith("PQ") or a.startswith("AQ"):
        return "output"
    if a.startswith("M"):
        return "memory"
    if a.startswith("DB"):
        return "db"
    if a.startswith("T"):
        return "timer"
    if a.startswith("C"):
        return "counter"
    return "other"


def main():
    if len(sys.argv) < 3:
        print("Ishlatilishi: python prepare_tags.py <input.xlsx> <output.json>")
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]

    wb = openpyxl.load_workbook(in_path, data_only=True)
    ws = wb.active  # birinchi/faol sheet ("PLC Tags")

    rows = list(ws.iter_rows(min_row=2, values_only=True))

    tags = []
    for r in rows:
        # Kutilgan ustunlar: Name, Path, Data Type, Logical Address, Comment, ...
        if len(r) < 5:
            continue
        name, path, dtype, address, comment = r[0], r[1], r[2], r[3], r[4]

        if not address:
            continue  # manzili yo'q qatorlarni tashlab ketamiz

        name = (name or "").strip()
        comment = (comment or "").strip()

        tag = {
            "address": str(address),
            "data_type": dtype or "",
            "name": name,
            "comment": comment,
            "group": (path or "").strip(),  # masalan: "ST1(主电柜)_IO", "Robot1->PLC(DO)"
            "kind": io_kind(str(address)),
            "station": extract_station(f"{name} {comment} {path or ''}"),
        }
        tags.append(tag)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False, indent=1)

    print(f"Tayyor: {len(tags)} ta tag '{out_path}' fayliga yozildi.")

    # Statistikani ko'rsatish
    kinds = {}
    for t in tags:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    print("Turlari bo'yicha taqsimot:", kinds)


if __name__ == "__main__":
    main()
