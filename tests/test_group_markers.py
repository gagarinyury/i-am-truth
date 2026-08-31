#!/usr/bin/env python3
"""
Маркеры групп: своя область задаётся файлом, имена рук берутся из документа.

Что было. Список маркеров содержал `\\bglp[-\\s]?1\\b` — название класса
препаратов, вшитое в продукт, объявленный для биомедицины вообще.

Что показал замер 31.08 (`eval/probes/rescore_offline.py`, ноль вызовов модели).
Убрать эти слова — покрытие проверки падает с 81 числа из 457 до 4 (McDonald) и с
60 из 474 до 0 (Cheng). То есть «0 инверсий» на обоих эталонах обеспечено не общим
механизмом, а тем, что обе статьи про GLP-1. Слова оставлены, но вынесены в
`DOMAIN_MARKERS` и заменяются файлом `TRUTH_GROUP_MARKERS`.

Второй заход — имена рук из заголовков колонок: «Unvaccinated» рядом с
«Vaccinated». **Замерено: на обоих эталонах даёт ноль.** У Cheng руки называются
«GLP-1 RA», «Insulin», «Metformin» — какая из них сравнение, не выводится ни из
какого правила языка, а угадать значило бы выносить обвинение без основания.
Механизм оставлен: он строго ограничен (отрицание принимается, только если основа
стоит колонкой в той же таблице) и молчит там, где не уверен. Тест держит и то,
что он ловит настоящую пару, и то, что он не ловит «Under 65 / Over 65».
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import verify_numbers as V                           # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


print("имена рук из заголовков…")
pair = [{"columns": ["Characteristic", "Vaccinated", "Unvaccinated", "SMD"],
         "rows": [["Age", "62.1", "61.8", "0.01"]]}]
m = V.markers_from_tables(pair)
check("пара «слово и его отрицание» распознана",
      any("vaccinated" in x for x in m["exposed"]) and
      any("unvaccinated" in x for x in m["control"]), str(m))
check("составной заголовок берётся по последнему уровню",
      V.markers_from_tables([{"columns": ["Before PSM · GLP-1", "Before PSM · Non-GLP-1"],
                              "rows": [["1", "2"]]}])["control"] != [])
check("хвост «(n = 15 264)» не мешает",
      V.markers_from_tables([{"columns": ["Users (n = 15 264)", "Non-users (n = 15 264)"],
                              "rows": [["1", "2"]]}])["control"] != [])

print("\nи не выдумывает там, где пары нет…")
check("«Under 65 / Over 65» руками не объявляются",
      V.markers_from_tables([{"columns": ["Under 65", "Over 65"],
                              "rows": [["1", "2"]]}]) == {"exposed": [], "control": []})
check("одиночное отрицание без основы отвергается",
      V.markers_from_tables([{"columns": ["Characteristic", "Non-smokers"],
                              "rows": [["1", "2"]]}])["control"] == [])
check("три руки по названиям лекарств — молчание (замер на Cheng)",
      V.markers_from_tables([{"columns": ["GLP-1 RA", "Insulin", "Metformin"],
                              "rows": [["1", "2", "3"]]}]) ==
      {"exposed": [], "control": []})
check("пустой вход не роняет", V.markers_from_tables([]) == {"exposed": [], "control": []})

print("\nсловарь не портится добавками…")
before = {g: list(v) for g, v in V.DEFAULT_GROUPS.items()}
merged = V.groups_with(m)
check("общий словарь не изменён", V.DEFAULT_GROUPS == before)
check("в копии маркеров больше", len(merged["control"]) > len(before["control"]))
check("без добавок возвращается тот же объект",
      V.groups_with({"exposed": [], "control": []}) is V.DEFAULT_GROUPS)

print("\nвердикт с именами из документа…")
src = ("Baseline characteristics. Among Vaccinated patients the rate was 19.7 percent "
       "over the whole period of follow-up, which lasted thirty six months in that arm "
       "and covered every eligible individual in the linked registry without exception. "
       "Among Unvaccinated patients the rate was 10.4 percent.")
claim = {"value": "10.4", "label": "rate among Vaccinated patients"}
check("без имён из документа проверка молчит",
      V.verify([claim], src)["claims"][0]["group_check"] == "unknown")
res = V.verify([claim], src, V.groups_with(m))["claims"][0]
check("с ними инверсия видна", res["status"] == "GROUP_MISMATCH", res["status"])
right = V.verify([{"value": "19.7", "label": "rate among Vaccinated patients"}],
                 src, V.groups_with(m))["claims"][0]
check("верное число не обвиняется", right["status"] != "GROUP_MISMATCH",
      right["status"])

print("\nпредметный словарь задаётся файлом…")
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                 encoding="utf-8") as fh:
    json.dump({"exposed": [r"\bmetformin\b"], "control": [r"\bplacebo\b"]}, fh)
    path = fh.name
os.environ["TRUTH_GROUP_MARKERS"] = path
extra = V._extra_markers()
os.environ.pop("TRUTH_GROUP_MARKERS")
os.unlink(path)
check("файл прочитан", extra["exposed"] == [r"\bmetformin\b"], str(extra))
os.environ["TRUTH_GROUP_MARKERS"] = "/нет/такого/файла.json"
check("нечитаемый файл не роняет разбор", V._extra_markers() == {})
os.environ.pop("TRUTH_GROUP_MARKERS")
check("предметные маркеры вынесены отдельно и подмешаны",
      any("glp" in x for x in V.DOMAIN_MARKERS["exposed"]) and
      any("glp" in x for x in V.DEFAULT_GROUPS["exposed"]))

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
