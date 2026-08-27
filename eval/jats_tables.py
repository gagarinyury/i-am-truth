#!/usr/bin/env python3
"""
Разбор таблиц JATS с разворотом colspan/rowspan и многоуровневых заголовков.

Зачем, если есть pubmed_parser: он берёт на себя основную работу (подпись, метка,
строки), но **многоуровневые заголовки схлопывает неверно**. Проверено на реальном
файле PMC13311639 27.08.2026: вернул 9 «колонок» при 7 значениях в строке, потому что
групповые заголовки `Before propensity score matching` / `After propensity score
matching` (colspan=3) попали в один ряд с обычными. В результате нельзя понять, какая
колонка `Vaccinated` относится к «до матчинга», а какая к «после» — то есть число
теряет свою метку. Это ровно класс ошибки, против которого написано решение D-14.

Логика разворота воспроизводит подход R-пакета `tidypmc::pmc_table` (схлопывание
многострочных заголовков, разворот rowspan/colspan, вынос подзаголовков в колонку) —
он был найден в prior-art как референс, но это R, а мы на Python.

  python3 eval/jats_tables.py статья.xml [--table 0] [--json]
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split())


def expand_row(tr) -> list:
    """Строка → список ячеек с учётом colspan. rowspan возвращается отдельно."""
    out = []
    for tc in tr:
        if tc.tag not in ("td", "th"):
            continue
        txt = _text(tc)
        cs = int(tc.get("colspan", 1) or 1)
        rs = int(tc.get("rowspan", 1) or 1)
        for i in range(cs):
            # при colspan значение принадлежит группе: дублируем, но помечаем ширину
            out.append({"text": txt, "rowspan": rs, "colspan": cs, "span_index": i})
    return out


def build_header(head_rows: list) -> list:
    """Многоуровневый заголовок → плоские составные имена колонок.

    Два уровня «Before matching | After matching» над «Vaccinated | Unvaccinated | SMD»
    дают «Before matching · Vaccinated», «After matching · Vaccinated» — так число
    перестаёт быть безымянным.

    Ключевая тонкость: ячейка первого уровня с rowspan=2 (обычно «Characteristics»)
    во второй строке заголовка ОТСУТСТВУЕТ. Наивное выравнивание сдвигает всю вторую
    строку и приписывает числам чужие имена колонок — поймано на PMC13311639.
    Поэтому rowspan в заголовке разворачивается так же, как в теле таблицы.
    """
    if not head_rows:
        return []
    width = max(sum(1 for _ in r) for r in head_rows)

    grid, pending = [], {}
    for cells in head_rows:
        row, idx = [], 0
        for pos in range(width):
            if pos in pending and pending[pos]["left"] > 0:
                row.append(pending[pos]["text"])
                pending[pos]["left"] -= 1
                continue
            if idx < len(cells):
                c = cells[idx]; idx += 1
                row.append(c["text"])
                if c["rowspan"] > 1:
                    pending[pos] = {"text": c["text"], "left": c["rowspan"] - 1}
            else:
                row.append("")
        grid.append(row)

    cols = []
    for i in range(width):
        parts = []
        for row in grid:
            v = row[i].strip()
            if v and (not parts or parts[-1] != v):
                parts.append(v)
        cols.append(" · ".join(parts) if parts else f"col{i}")
    return cols


def parse_tables(path: str) -> list:
    root = ET.parse(path).getroot()
    out = []
    for tw in root.findall(".//table-wrap"):
        label = _text(tw.find("label")) if tw.find("label") is not None else ""
        cap_el = tw.find("caption")
        caption = _text(cap_el) if cap_el is not None else ""
        table = tw.find(".//table")
        if table is None:
            continue

        thead = table.find("thead")
        head_rows = [expand_row(tr) for tr in thead.findall("tr")] if thead is not None else []
        tbody = table.find("tbody")
        body_trs = tbody.findall("tr") if tbody is not None else table.findall(".//tr")

        columns = build_header(head_rows)

        rows, pending, section = [], {}, None
        for tr in body_trs:
            cells = expand_row(tr)
            # подставить значения, тянущиеся сверху по rowspan
            row, idx = [], 0
            for pos in range(len(columns) or len(cells)):
                if pos in pending and pending[pos]["left"] > 0:
                    row.append(pending[pos]["text"])
                    pending[pos]["left"] -= 1
                    continue
                if idx < len(cells):
                    c = cells[idx]; idx += 1
                    row.append(c["text"])
                    if c["rowspan"] > 1:
                        pending[pos] = {"text": c["text"], "left": c["rowspan"] - 1}
                else:
                    row.append("")
            if not any(v for v in row):
                continue
            # строка-разделитель: одно и то же значение во всех колонках
            # (tidypmc выносит такие подзаголовки в отдельную колонку — делаем так же)
            uniq = {v for v in row if v}
            if len(uniq) == 1 and len(row) > 1:
                section = row[0]
                continue
            rows.append([section] + row if section is not None else row)

        if rows and columns and len(rows[0]) == len(columns) + 1:
            columns = ["Section"] + columns
        n_num = sum(1 for r in rows for v in r if re.search(r"\d", v))
        out.append({"label": label, "caption": caption, "columns": columns,
                    "rows": rows, "n_rows": len(rows), "n_cols": len(columns),
                    "numeric_cells": n_num,
                    "consistent": all(len(r) == len(columns) for r in rows) if columns else None})
    return out


def to_claims(table: dict) -> list:
    """Каждое число таблицы → заявка на проверку верификатором (D-14).

    Метка собирается из имени колонки и первой ячейки строки: «19.7» становится
    «Charlson >=5 · GLP-1 · 19.7».
    """
    claims = []
    for r in table["rows"]:
        if not r:
            continue
        row_label = r[0]
        for i, v in enumerate(r[1:], start=1):
            for num in re.findall(r"-?\d+(?:[.,]\d+)?", v.replace(",", "")):
                col = table["columns"][i] if i < len(table["columns"]) else f"col{i}"
                claims.append({"value": num, "label": f"{row_label} · {col}",
                               "table": table["label"] or table["caption"][:40]})
    return claims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--table", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--claims", action="store_true", help="выдать заявки для verify_numbers")
    a = ap.parse_args()

    tabs = parse_tables(a.path)
    if a.claims:
        sel = [tabs[a.table]] if a.table is not None else tabs
        json.dump([c for t in sel for c in to_claims(t)], sys.stdout,
                  ensure_ascii=False, indent=2)
        return
    if a.json:
        json.dump(tabs if a.table is None else tabs[a.table], sys.stdout,
                  ensure_ascii=False, indent=2)
        return
    if a.table is not None:
        t = tabs[a.table]
        print(f"{t['label']} {t['caption'][:80]}")
        print(f"колонок {t['n_cols']} · строк {t['n_rows']} · согласовано: {t['consistent']}\n")
        print("  " + " | ".join(c[:22] for c in t["columns"]))
        for r in t["rows"][:12]:
            print("  " + " | ".join(v[:22] for v in r))
        return
    print(f"таблиц: {len(tabs)}\n")
    for i, t in enumerate(tabs):
        print(f"[{i}] {t['n_rows']:>3}×{t['n_cols']:<3} чисел:{t['numeric_cells']:>4} "
              f"согласовано:{str(t['consistent']):<5} {t['label']} {t['caption'][:48]}")


if __name__ == "__main__":
    main()
