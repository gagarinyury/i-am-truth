#!/usr/bin/env python3
"""
Длинная таблица приложения не превращается в обвинение.

Дефект, который тест держит. `tables_as_text` резал каждую таблицу до сорока
строк, и этот же обрезанный текст шёл верификатору. Число из сорок пятой строки
eTable — честно процитированное моделью и стоящее в разобранной ячейке — в
источнике сверки отсутствовало, получало статус UNVERIFIED и печаталось в графе
«не найдено в статье вовсе». Хуже того, `_attach_cells` выносил вердикт
`located: "absent"`, ни разу не заглянув в индекс ячеек, который это число
содержал.

Базовые таблицы характеристик на сорок с лишним строк — норма, а L1-приложение
и есть то, ради чего построен весь продукт: обвинение било по самому частому
случаю на самом важном пути.

Две половины исправления проверяются раздельно, потому что чинят разное:
  1. верификатор получает нерезаные таблицы — устраняет причину;
  2. адрес ячейки снимает обвинение, даже если текстовый поиск промахнулся —
     страхует от расхождения между разбором таблицы и `normalise`.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import pipeline                                    # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1
    ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


# Таблица на пятьдесят строк; интересующее нас число стоит в сорок пятой,
# то есть за прежней границей в сорок.
ROWS = [[f"Characteristic {i}", f"{100 + i}.{i % 10}{i % 7}"] for i in range(50)]
TABLE = {"caption": "eTable 1. Baseline characteristics",
         "columns": ["Characteristic", "GLP-1"], "rows": ROWS}
DEEP = ROWS[44][1]          # число из сорок пятой строки
SHALLOW = ROWS[3][1]        # число из четвёртой — оно проходило и раньше
PROSE = "Some prose that does not contain either of those values."

FINDINGS = {"domains": [{"name": "Confounding", "findings": [
    {"title": "Charlson", "mechanism": f"the value was {DEEP} in the exposed arm",
     "evidence": [f"and {SHALLOW} among the unexposed"]}]}]}

gathered = {"appendix_tables": [TABLE], "jats_tables": [], "source_text": PROSE}

print("подача модели — усечена, у вызова есть окно и цена")
short = pipeline.tables_as_text(gathered)
check("сорок первая строка и дальше модели не идут", DEEP not in short)
check("первые сорок идут", SHALLOW in short)

print("\nподача верификатору — полная, окна у него нет")
full = pipeline.tables_as_text(gathered, limit=None, rows=None)
check("число из сорок пятой строки в источнике сверки есть", DEEP in full)

print("\nсверка по полному источнику")
res = pipeline.verify_findings(FINDINGS, f"{PROSE}\n{full}", [TABLE])
deep = next(c for c in res["claims"] if c["value"] == DEEP)
check("статус не UNVERIFIED", deep["status"] != "UNVERIFIED", deep["status"])
check("адрес ячейки найден", (deep.get("cell") or {}).get("row") == "Characteristic 44",
      str((deep.get("cell") or {}).get("row")))
check("вес улики посчитан", (deep.get("evidence_bits") or 0) > 0,
      f"{deep.get('evidence_bits')} бит")
check("в сводке ноль ненайденных", res["summary"]["unverified"] == 0)

print("\nвторой рубеж: таблицы в тексте источника нет вовсе")
res2 = pipeline.verify_findings(FINDINGS, PROSE, [TABLE])
d2 = next(c for c in res2["claims"] if c["value"] == DEEP)
check("обвинение снято адресом ячейки", d2["status"] == "VERIFIED", d2["status"])
check("основание названо", d2.get("found_via") == "table_cell", str(d2.get("found_via")))
check("вес улики пересчитан, а не оставлен нулём",
      (d2.get("evidence_bits") or 0) > 0, f"{d2.get('evidence_bits')} бит")
check("метка не выдаётся за проверенную", d2.get("label_match") in (None, 0.0),
      str(d2.get("label_match")))
check("группа не выдаётся за проверенную", d2.get("group_check") == "unknown")
s2 = res2["summary"]
check("сводка пересобрана: found + unverified == total",
      s2["found"] + s2["unverified"] == s2["total"],
      f"{s2['found']} + {s2['unverified']} == {s2['total']}")
check("расхождение инструментов видно в сводке",
      s2.get("found_only_in_table_cell") == 2, str(s2.get("found_only_in_table_cell")))

print("\nчисло, которого в статье действительно нет, обвинения не теряет")
res3 = pipeline.verify_findings(
    {"domains": [{"name": "Confounding", "findings": [
        {"title": "invented", "mechanism": "the value was 987.6543 in the exposed arm",
         "evidence": []}]}]},
    f"{PROSE}\n{full}", [TABLE])
inv = next(c for c in res3["claims"] if c["value"] == "987.6543")
check("выдуманное остаётся UNVERIFIED", inv["status"] == "UNVERIFIED", inv["status"])
check("и помечено как отсутствующее", inv.get("located") == "absent")

print(f"\n{ok}/{total}")
sys.exit(0 if ok == total else 1)
