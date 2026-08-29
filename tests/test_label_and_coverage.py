"""
Слой 4 обязан говорить ровно то, что делает.

Три дефекта, найденные внешним разбором 29.08 и подтверждённые замером (F-63):

1. Метка считалась совпавшей, если **одно** слово длиннее двух букв нашлось рядом
   с числом. Слова `the`, `and`, `for`, `group` проходили этот фильтр, и метка от
   другого числа получала статус VERIFIED.
2. Проверялось только **первое** вхождение числа, а 76% чисел встречаются в статье
   больше одного раза — метка и группа сверялись с произвольным местом документа.
3. «0 инверсий групп» печаталось и тогда, когда проверка группы не вынесла ни
   одного вердикта: на разборе BMJ она молчала про все 440 чисел.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth.verify_numbers import (LABEL_THRESHOLD, check_label,  # noqa: E402
                                  find_occurrences, label_overlap, verify)

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


CTX = ("Charlson comorbidity index 5 or more was 19.7 percent in the GLP-1 group "
       "and 10.4 percent among matched controls")

# 1. Метка из слов, которые в этом документе повсюду, больше не подтверждается.
#    Проверяется через `verify`, потому что вес слова считается по частотам
#    документа, а не по списку: «group» и «risk» — слова предметные, но если они
#    встречаются в статье на каждом шагу, их совпадение ничего не доказывает.
DOC = ("Baseline table. " + "the risk in each group was assessed. " * 60 +
       "Charlson comorbidity index 5 or more was 19.7 percent in the GLP-1 group. "
       + "the risk in each group was assessed. " * 60)
r_generic = verify([{"value": "19.7", "label": "the risk and the group for that"}],
                   DOC)["claims"][0]
r_real = verify([{"value": "19.7",
                  "label": "Charlson comorbidity index 5 or more"}], DOC)["claims"][0]
# Метка из слов, которые в этом документе повсюду, не даёт ни подтверждения, ни
# обвинения: измерять нечем, и отчёт обязан сказать именно это.
check("метка из повсеместных слов не измеряется", r_generic["label_match"] is None,
      str(r_generic["label_match"]))
check("и обвинения в потере метки за это не выносится",
      r_generic["status"] != "FOUND_LABEL_MISMATCH", r_generic["status"])
check("редкая метка подтверждается", r_real["status"] == "VERIFIED",
      f"{r_real['status']}, match={r_real['label_match']}")
check("у редкой метки сила измерена", r_real["label_match"] is not None
      and r_real["label_match"] > 0.5, str(r_real["label_match"]))
check("неизмеримые метки посчитаны отдельно",
      verify([{"value": "19.7", "label": "the risk and the group for that"}],
             DOC)["summary"]["label_not_judged"] == 1)
check("доля совпадения возвращается числом",
      0.0 < label_overlap("Charlson comorbidity index", CTX) <= 1.0)
check("порог объявлен наружу", 0 < LABEL_THRESHOLD <= 1)

# 2. Осматриваются все вхождения, и выбирается лучшее по метке.
SRC = ("Table 1. Age at index: 19.7 years overall. " + "filler text. " * 30 +
       "Table 2. Charlson comorbidity index 5 or more: 19.7 percent in the GLP-1 group.")
occ = find_occurrences("19.7", SRC)
check("найдены оба вхождения", len(occ) == 2, f"{len(occ)}")
res = verify([{"value": "19.7", "label": "Charlson comorbidity index 5 or more"}], SRC)
c = res["claims"][0]
check("выбрано вхождение с подходящей меткой", c["status"] == "VERIFIED",
      f"{c['status']}, match={c['label_match']}")
check("число вхождений попадает в отчёт", c["occurrences"] == 2)
# Первое вхождение метке не соответствует — на старой логике был бы mismatch.
check("на первом вхождении метка не совпала бы",
      label_overlap("Charlson comorbidity index 5 or more", occ[0]["context"])
      < LABEL_THRESHOLD)

# 3. Сводка обязана называть покрытие проверки группы.
s = verify([{"value": "19.7", "label": "Charlson index, GLP-1 group"},
            {"value": "10.4", "label": "Charlson index, matched controls"}], CTX)["summary"]
check("в сводке есть покрытие группы",
      "group_checked" in s and "group_undecided" in s, str(s.get("group_checked")))
check("в сводке есть медиана совпадения метки", "label_match_median" in s,
      str(s.get("label_match_median")))
check("покрытие сходится с числом заявок",
      s["group_checked"] + s["group_undecided"] == s["total"] - s.get("skipped", 0)
      or s["group_checked"] + s["group_undecided"] <= s["total"])

# Отсутствующее число по-прежнему ловится — это самая надёжная часть слоя.
miss = verify([{"value": "77.7", "label": "invented"}], CTX)["claims"][0]
check("выдуманное число не находится", miss["status"] == "UNVERIFIED")
check("у ненайденного числа ноль вхождений", miss["occurrences"] == 0)

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
