"""
Проверка того, что арифметику действительно пересчитывает функция.

Дефект, ради которого написан тест (F-55): раздел отчёта назывался «Recomputed by
a function, not by the model», а на движке по умолчанию `stats_tool` не вызывался
ни разу — числа писала модель, и никто их не проверял. Тест держит три вещи:
пересчёт происходит, ложь модели ловится, а основание пересчёта названо честно.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth.recompute import recompute          # noqa: E402

# Живой прогон 29.08, DOI 10.1136/jitc-2025-014726
ARITH = ("ARD = (1,986 / 3,609) - (1,629 / 3,609) = 0.55029 - 0.45137 = 0.09892 "
         "(9.89 percentage points). NNH = 1 / 0.09892 = 10.11 patients.")
COUNTS = {"exposed_events": 1986, "exposed_total": 3609,
          "control_events": 1629, "control_total": 3609}

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


# 1. Счётчики выписаны моделью отдельным полем — пересчёт независимый.
r = recompute({"counts": COUNTS, "absolute_risk_difference": "9.89 percentage points",
               "nnt": "-10.1", "arithmetic": ARITH})
check("основание названо: числа выписаны, не разобраны", r["basis"] == "reported", r["basis"])
check("пересчёт объявлен независимым", r["independent"] is True)
check("ARR посчитан функцией", abs(r["absolute_risk_difference_pp"] - 9.8919) < 1e-3,
      str(r["absolute_risk_difference_pp"]))
check("NNT посчитан функцией", abs(r["nnt"] + 10.1) < 0.05, str(r["nnt"]))
check("модель и функция сошлись", r["agreement"] == {"absolute_risk_difference": "match",
                                                     "nnt": "match"})
# RR из тех же чисел обязан совпасть с HR статьи (1.22) — внешняя сверка.
check("RR совпал с опубликованным HR 1.22", abs(r["rr"] - 1.22) < 0.01, str(r["rr"]))
check("E-value посчитан", r["e_value_point"] > 1, str(r["e_value_point"]))

# 2. Модель заявила не то, что следует из её же чисел, — это обязано вскрыться.
bad = recompute({"counts": COUNTS, "absolute_risk_difference": "3.10 percentage points",
                 "nnt": "-32.3", "arithmetic": ARITH})
check("расхождение по ARR поймано",
      bad["agreement"]["absolute_risk_difference"] == "mismatch")
check("расхождение по NNT поймано", bad["agreement"]["nnt"] == "mismatch")

# 3. Поля counts нет — числа восстанавливаются из строки, и это честно помечено
#    как проверка на непротиворечивость, а не как независимое подтверждение.
p = recompute({"absolute_risk_difference": "9.89 percentage points", "nnt": "-10.1",
               "arithmetic": ARITH})
check("без counts числа восстановлены", p["counts"] == COUNTS, str(p["counts"]))
check("основание понижено до 'parsed'", p["basis"] == "parsed", p["basis"])
check("такой пересчёт не выдаётся за независимый", p["independent"] is False)

# 4. Разбор строки не должен хватать чужие дроби: если из них не получается
#    заявленный ARR, это не та таблица.
alien = recompute({"absolute_risk_difference": "9.89 percentage points",
                   "arithmetic": "Sensitivity: 12 / 500 versus 8 / 500 in a subgroup."})
check("чужие числа из строки не берутся", alien["basis"] == "none", alien["basis"])

# 5. Пересчитывать нечего — это результат, а не молчание.
check("пустой раздел даёт явное 'none'",
      recompute({"absolute_risk_difference": None})["basis"] == "none")
check("отсутствие раздела не роняет", recompute(None) is None)

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
