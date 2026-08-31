#!/usr/bin/env python3
"""
Сверка числа с ячейкой таблицы: адрес вместо окрестности.

У числа из таблицы есть подпись строки и заголовок колонки — это адрес, а не
описание, и сверка адреса даёт решение, а не шкалу. Прежняя проверка метки
сравнивала формулировку модели со словами в окне 380 знаков и отделяла своё от
подложного лишь втрое (F-63).

Тест держит и то, что в отчёт НЕ попало. Проверка инверсии по колонкам работает
на синтетической таблице в обе стороны, но на живом разборе дала 6 срабатываний,
из которых ложных 6 из 6 (разбор — в шапке `truth/cells.py`). Поэтому она не
переводит число в GROUP_MISMATCH, и тест это фиксирует: обвинение без измеренной
точности не выносится, как бы соблазнительно ни выглядела функция.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import cells, pipeline                            # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


JATS = {"label": "Table 1", "caption": "Baseline characteristics",
        "columns": ["Characteristic", "GLP-1", "Control", "SMD"],
        "rows": [["Charlson 5+", "19.7", "10.4", "0.21"],
                 ["Age, mean", "62.1", "61.8", "0.02"]]}
# .docx-приложение приходит без отдельных заголовков: первая строка и есть шапка
DOCX = {"caption": "eTable S2. Sensitivity analysis",
        "rows": [["Analysis", "Exposed", "Unexposed"],
                 ["Landmark 90 days", "3.04", "2.15"]]}

idx = cells.build_index([JATS, DOCX])

print("1. индекс собирается из таблиц любого происхождения")
check("число из JATS-таблицы попало", "19.7" in idx)
check("число из .docx-приложения попало", "3.04" in idx)
check("шапка .docx не принята за данные", "Exposed" not in idx)
check("у ячейки есть адрес",
      idx["19.7"][0]["row"] == "Charlson 5+" and idx["19.7"][0]["column"] == "GLP-1",
      str(idx["19.7"][0]))

print("\n2. адрес сверяется с меткой модели")
c = cells.locate("19.7", "Charlson 5+ in the GLP-1 group", idx)
check("нашлась нужная ячейка", c["column"] == "GLP-1" and c["row"] == "Charlson 5+")
check("согласие адреса выше порога", c["agreement"] >= cells.CELL_THRESHOLD,
      str(c["agreement"]))
c2 = cells.locate("3.04", "Landmark 90 days, exposed arm", idx)
check("работает и для .docx", c2["column"] == "Exposed", str(c2))
check("числа нет в таблицах — None", cells.locate("99.99", "anything", idx) is None)

print("\n3. проверка инверсии по колонкам: работает, но в вердикт не идёт")
inv = cells.column_conflict("Charlson 5+ among matched controls",
                            cells.locate("19.7", "Charlson 5+ among matched controls", idx))
check("инверсия распознаётся на синтетике", inv is not None and
      inv["claimed_column"] == "Control", str(inv))
check("названо, какое число стоит в заявленной колонке",
      inv.get("value_in_claimed_column") == "10.4", str(inv.get("value_in_claimed_column")))
check("верное отнесение инверсией не считается",
      cells.column_conflict("Charlson 5+ in the GLP-1 group",
                            cells.locate("19.7", "Charlson 5+ in the GLP-1 group", idx)) is None)
check("метка-предложение к суждению о колонке не допускается",
      cells.column_conflict(
          "Vaccinated patients had lower mortality over thirty six months of follow up "
          "in the matched cohort with several outcomes reported side by side",
          cells.locate("19.7", "x", idx)) is None)

print("\n4. три тира вместо булева «найдено»")
findings = {"domains": [{"id": 1, "name": "Confounding",
                         "findings": [{"title": "Charlson 5+", "mechanism": "sicker",
                                       "evidence": ["19.7"]}]}],
            "overall": {"summary": "A prose sentence mentioning 62.1 and 41.7 only."}}
src = ("Baseline table follows. Charlson 5+ 19.7 versus 10.4. Age, mean 62.1 and 61.8. "
       "The cohort ran for 41.7 months in prose only.")
res = pipeline.verify_findings(findings, src, [JATS, DOCX])
tiers = {c["value"]: c.get("located") for c in res["claims"]}
check("число с совпавшим адресом — тир cell", tiers.get("19.7") == "cell", str(tiers))
check("число из прозы — тир text", tiers.get("41.7") == "text", str(tiers))
check("сводка несёт счёт по ячейкам", res["summary"].get("in_cell", 0) >= 1,
      str(res["summary"].get("in_cell")))
check("ни одно число не объявлено инверсией по колонке",
      all(c.get("status") != "GROUP_MISMATCH" for c in res["claims"]))
check("поля cell_conflict в отчёте нет",
      all("cell_conflict" not in c for c in res["claims"]))

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
