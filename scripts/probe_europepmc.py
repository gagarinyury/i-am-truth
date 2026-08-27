#!/usr/bin/env python3
"""
Зонд Europe PMC — ответ на вопрос Q-02.

Проверяет не «есть ли статья в базе», а то, что реально нужно продукту:
доступен ли ПОЛНЫЙ ТЕКСТ и есть ли в нём ТАБЛИЦЫ, из которых можно вытащить числа
уровня приложения. Наличие метаданных без таблиц для нас бесполезно.

  python3 scripts/probe_europepmc.py --dois 10.1200/OP-26-00485
  python3 scripts/probe_europepmc.py --query '(GLP-1 AND cancer AND cohort)' --limit 30
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UA = {"User-Agent": "i-am-truth-probe/0.1 (methodology audit research)"}


def get(url: str, as_json=True, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            return json.loads(raw) if as_json else raw.decode("utf-8", "replace")
        except Exception as e:                                   # noqa: BLE001
            if i == tries - 1:
                return {"__error__": str(e)[:200]} if as_json else ""
            time.sleep(2 * (i + 1))


def search(query: str, limit: int) -> list:
    out, cursor = [], "*"
    while len(out) < limit:
        url = (f"{BASE}/search?query={urllib.parse.quote(query)}"
               f"&format=json&resultType=core&pageSize=100&cursorMark={cursor}")
        d = get(url)
        if "__error__" in d:
            print("ошибка поиска:", d["__error__"], file=sys.stderr)
            break
        res = d.get("resultList", {}).get("result", [])
        if not res:
            break
        out.extend(res)
        nxt = d.get("nextCursorMark")
        if not nxt or nxt == cursor:
            break
        cursor = nxt
    return out[:limit]


def analyse_fulltext(pmcid: str) -> dict:
    """Тянет JATS XML и считает то, что важно: таблицы и их содержимое."""
    xml = get(f"{BASE}/{pmcid}/fullTextXML", as_json=False)
    if not xml or xml.lstrip().startswith("<error"):
        return {"fulltext": False}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        return {"fulltext": True, "parse_error": str(e)[:100]}

    tables = root.findall(".//table-wrap")
    # реальные ячейки с числами — то, ради чего всё затевается
    cells_with_numbers = 0
    for t in tables:
        for td in t.iter():
            if td.tag in ("td", "th") and td.text and re.search(r"\d", td.text):
                cells_with_numbers += 1
    # приложения: <app-group>, <app>, supplementary-material
    apps = root.findall(".//app") + root.findall(".//app-group")
    supp = root.findall(".//supplementary-material")
    # таблицы, помеченные как appendix/supplementary по заголовку
    appendix_tables = 0
    for t in tables:
        label = "".join(t.itertext())[:120].lower()
        if any(w in label for w in ("appendix", "supplement", "etable", "s1", "table s")):
            appendix_tables += 1
    return {
        "fulltext": True,
        "tables": len(tables),
        "appendix_tables": appendix_tables,
        "numeric_cells": cells_with_numbers,
        "app_sections": len(apps),
        "supplementary_material_tags": len(supp),
        "xml_bytes": len(xml),
    }


def check_supp_files(pmcid: str) -> int:
    """supplementaryFiles отдаёт zip; нам важен только факт наличия."""
    try:
        req = urllib.request.Request(f"{BASE}/{pmcid}/supplementaryFiles", headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            return len(r.read())
    except Exception:                                            # noqa: BLE001
        return 0


def row(rec: dict, deep: bool) -> dict:
    pmcid = rec.get("pmcid")
    out = {
        "pmid": rec.get("pmid"),
        "pmcid": pmcid,
        "doi": rec.get("doi"),
        "journal": (rec.get("journalInfo") or {}).get("journal", {}).get("title"),
        "year": (rec.get("journalInfo") or {}).get("yearOfPublication"),
        "open_access": rec.get("isOpenAccess") == "Y",
        "in_epmc": rec.get("inEPMC") == "Y",
        "has_suppl_flag": rec.get("hasSuppl") == "Y",
        "title": (rec.get("title") or "")[:90],
    }
    if deep and out["in_epmc"] and pmcid:
        out.update(analyse_fulltext(pmcid))
        out["supp_zip_bytes"] = check_supp_files(pmcid) if out["has_suppl_flag"] else 0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dois", help="список DOI через запятую")
    ap.add_argument("--query", help="поисковый запрос Europe PMC")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--out", help="куда сохранить json")
    ap.add_argument("--shallow", action="store_true", help="не тянуть full-text")
    a = ap.parse_args()

    records = []
    if a.dois:
        for doi in a.dois.split(","):
            doi = doi.strip()
            d = get(f'{BASE}/search?query=DOI:"{urllib.parse.quote(doi)}"'
                    f'&format=json&resultType=core')
            res = d.get("resultList", {}).get("result", []) if "__error__" not in d else []
            if not res:
                records.append({"doi": doi, "__notfound__": True})
            else:
                records.extend(res[:1])
    if a.query:
        records.extend(search(a.query, a.limit))

    rows = []
    for rec in records:
        if rec.get("__notfound__"):
            rows.append({"doi": rec["doi"], "not_in_europepmc": True})
            continue
        rows.append(row(rec, deep=not a.shallow))
        print(".", end="", flush=True)
    print()

    # сводка
    n = len(rows)
    have_ft = [r for r in rows if r.get("fulltext")]
    with_tables = [r for r in have_ft if (r.get("tables") or 0) > 0]
    with_appendix = [r for r in have_ft if (r.get("appendix_tables") or 0) > 0]
    with_numbers = [r for r in have_ft if (r.get("numeric_cells") or 0) >= 20]
    with_supp = [r for r in rows if r.get("has_suppl_flag")]

    print(f"\n{'='*74}")
    print(f"всего статей проверено:                    {n}")
    print(f"open access:                               {sum(1 for r in rows if r.get('open_access'))}")
    print(f"есть в Europe PMC (inEPMC=Y):              {sum(1 for r in rows if r.get('in_epmc'))}")
    print(f"full-text XML реально скачался:            {len(have_ft)}")
    print(f"  ...из них с таблицами:                   {len(with_tables)}")
    print(f"  ...из них с >=20 числовыми ячейками:     {len(with_numbers)}")
    print(f"  ...из них с таблицами приложения:        {len(with_appendix)}")
    print(f"помечены как имеющие supplementary:        {len(with_supp)}")
    if n:
        print(f"\nДОЛЯ ГОДНЫХ ДЛЯ НАШЕЙ ЗАДАЧИ (числа в таблицах): "
              f"{len(with_numbers)}/{n} = {100*len(with_numbers)//n}%")
    print("="*74)

    if a.out:
        with open(a.out, "w") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"подробности: {a.out}")
    return rows


if __name__ == "__main__":
    main()
