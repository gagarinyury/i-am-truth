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

# Предел на РАСПАКОВАННЫЙ размер члена архива.
#
# Зачем. Сервис ограничивал загрузку 25 МБ — но 25 МБ сжатого .docx это
# произвольно много распакованного: `word/document.xml` состоит из повторяющейся
# разметки и жмётся в сотни раз. Проверка стояла на том размере, который виден
# снаружи, а в память читалось то, что внутри: `z.read("word/document.xml")` без
# всякого предела, тремя разными местами (здесь, `pipeline._docx_text` и
# `pipeline._supplementary_text`). Контейнер с двумя слотами разбора кладётся
# одним запросом.
#
# Откуда 64 МБ. Замерены `word/document.xml` всех .docx-приложений корпуса проекта
# (31.08, пять файлов, добыты через `retrieval.fetch_supplementary`):
#
#   2.92 МБ  10.1136/jitc-2025-014726   jitc-14-6-s001.docx   ← самый тяжёлый
#   1.37 МБ  10.1111/1753-0407.70013    JDB-16-e70013-s001.docx
#   0.16 МБ  10.1016/j.pmedr.2026.103548
#   0.08 МБ / 0.05 МБ  10.3389/fonc.2026.1742210
#
# 64 МБ — примерно двадцатидвухкратный запас над наибольшим виденным. ⚠️ Сам предел
# при этом остаётся допущением, и названо оно так: документированной верхней границы
# размера приложения не существует, а измерены пять файлов, не популяция. Здесь
# сначала стояло «3.4 МБ» — число, которого никто не мерил, подставленное «по
# мотивам». Оно ничего не ломало и потому продержалось бы долго: ровно так
# необеспеченные значения и живут в чужих статьях, которые этот продукт разбирает.
#
# Проверяются ОБА размера — объявленный в оглавлении и фактически прочитанный.
# Объявленный дешёв, но он всего лишь число в заголовке архива и может лгать;
# читать без предела, доверившись ему, значит не проверять вовсе.
MAX_UNCOMPRESSED = 64 * 1024 * 1024


class TooLarge(RuntimeError):
    """Член архива распаковывается во что-то, чего мы не станем держать в памяти."""


def read_limited(z, name: str, limit: int = MAX_UNCOMPRESSED) -> bytes:
    """Член zip-архива с пределом на распакованный размер."""
    try:
        declared = z.getinfo(name).file_size
    except KeyError:
        declared = 0
    if declared > limit:
        raise TooLarge(f"{name}: {declared} байт после распаковки, предел {limit}")
    with z.open(name) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise TooLarge(f"{name}: больше {limit} байт после распаковки — "
                       f"оглавление архива объявляло {declared}")
    return data


def cell_text(tc) -> str:
    """Текст ячейки — без текста вложенных в неё таблиц.

    `ElementTree.iter()` рекурсивен, и это здесь ловушка. Прежний код собирал
    `tc.iter(w:t)`, поэтому таблица внутри ячейки втягивалась в текст самой
    ячейки: `OUTER-1` превращалось в `OUTER-1INNER-AINNER-B`. Дальше из этой
    строки `cells.build_index` делал подпись строки, то есть **адрес числа** —
    а адрес объявлен решением, а не шкалой. Испорченный адрес хуже
    отсутствующего: он выглядит проверкой.

    Поэтому текст берётся только из абзацев, лежащих в ячейке напрямую.
    """
    return "".join(t.text or "" for p in tc.findall(f"{W}p")
                   for t in p.iter(f"{W}t")).strip()


def _rows_of(tbl) -> list:
    """Строки таблицы — только её собственные.

    `tbl.iter(w:tr)` тоже рекурсивен и возвращал вдобавок строки вложенных
    таблиц, а `tr.iter(w:tc)` — их ячейки, и они приписывались внешней строке.
    Одна таблица с одной вложенной давала четыре колонки вместо двух и лишнюю
    строку-дубль. Прямые дети такой ошибки не делают.
    """
    return [[cell_text(tc) for tc in tr.findall(f"{W}tc")]
            for tr in tbl.findall(f"{W}tr")]


def _nested(tbl):
    """Таблицы, вложенные в ячейки этой. Выдаются отдельными, а не теряются:
    в приложениях так свёрстаны подтаблицы по подгруппам, и числа в них
    настоящие."""
    for tr in tbl.findall(f"{W}tr"):
        for tc in tr.findall(f"{W}tc"):
            for inner in tc.findall(f"{W}tbl"):
                yield inner
                yield from _nested(inner)


def _table(tbl, caption: str) -> dict | None:
    rows = [r for r in _rows_of(tbl) if any(c for c in r)]
    if not rows:
        return None
    return {"caption": caption, "rows": rows,
            "n_rows": len(rows), "n_cols": max(len(r) for r in rows)}


def extract(path: str) -> list:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(read_limited(z, "word/document.xml"))
    body = root.find(f"{W}body")
    tables, caption = [], ""
    for el in body:
        if el.tag == f"{W}p":
            txt = "".join(t.text or "" for t in el.iter(f"{W}t")).strip()
            if re.match(r"^(e?Table|Supplementary Table|Appendix Table)\s*[Ss]?\d+", txt):
                caption = txt
        elif el.tag == f"{W}tbl":
            t = _table(el, caption)
            if t:
                tables.append(t)
                for inner in _nested(el):
                    nt = _table(inner, f"{caption} (nested)".strip())
                    if nt:
                        tables.append(nt)
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
