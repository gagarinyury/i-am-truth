#!/usr/bin/env python3
"""
Извлечение таблиц из .docx — формата, в котором Europe PMC отдаёт приложения статей
(проверено: 9 из 9 текстовых приложений в выборке — .docx, PDF нет ни одного, F-22).

.docx — это zip с WordprocessingML, где таблицы размечены <w:tbl>/<w:tr>/<w:tc>.
Структура строка/столбец сохраняется полностью, поэтому ни Document AI, ни GROBID
для этого пути не нужны — хватает стандартной библиотеки.

  python3 scripts/docx_tables.py файл.docx [--table 3] [--json]
"""
import argparse
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def cell_text(tc) -> str:
    return "".join(t.text or "" for t in tc.iter(f"{W}t")).strip()


def extract(path: str) -> list:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W}body")
    tables, caption = [], ""
    for el in body:
        if el.tag == f"{W}p":
            txt = "".join(t.text or "" for t in el.iter(f"{W}t")).strip()
            if re.match(r"^(e?Table|Supplementary Table|Appendix Table)\s*[Ss]?\d+", txt):
                caption = txt
        elif el.tag == f"{W}tbl":
            rows = [[cell_text(tc) for tc in tr.iter(f"{W}tc")]
                    for tr in el.iter(f"{W}tr")]
            rows = [r for r in rows if any(c for c in r)]
            if rows:
                tables.append({"caption": caption, "rows": rows,
                               "n_rows": len(rows), "n_cols": max(len(r) for r in rows)})
                caption = ""
    return tables


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--table", type=int, help="показать одну таблицу целиком")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    tables = extract(a.path)
    if a.json:
        json.dump(tables, sys.stdout, ensure_ascii=False, indent=2)
        return

    if a.table is not None:
        t = tables[a.table]
        print(f"[{a.table}] {t['caption']}  ({t['n_rows']}×{t['n_cols']})")
        for r in t["rows"]:
            print("  | " + " | ".join(c[:26] for c in r))
        return

    print(f"таблиц найдено: {len(tables)}\n")
    for i, t in enumerate(tables):
        nums = sum(1 for r in t["rows"] for c in r if re.search(r"\d", c))
        print(f"[{i:>2}] {t['n_rows']:>3}×{t['n_cols']:<3} чисел:{nums:>4}  "
              f"{(t['caption'] or '(без заголовка)')[:72]}")


if __name__ == "__main__":
    main()
