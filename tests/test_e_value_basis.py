#!/usr/bin/env python3
"""
E-value обязан стоять на скорректированной оценке, а не на сырой таблице 2×2.

Дефект. E-value отвечает на один вопрос: насколько сильным должен быть НЕУЧТЁННЫЙ
конфаундер, чтобы объяснить связь. Вопрос осмыслен только про оценку, из которой
учтённые конфаундеры уже убраны. Проект же считал его от сырого RR из таблицы 2×2
и печатал рядом со скорректированным HR статьи: арифметика верная, вопрос не тот.
На разборе `10.1136/jitc-2025-014726` разница не косметическая — 1.736 против
1.559, то есть устойчивость завышалась примерно на одиннадцать процентов.

Формулы приведения взяты из первоисточников, не выведены заново:
  VanderWeele TJ. «On a square-root transformation of the odds ratio for a common
    outcome». Epidemiology 2017 — OR -> RR ≈ sqrt(OR).
  VanderWeele TJ. «Optimal approximate conversions of odds ratios and hazard ratios
    to risk ratios». Biometrics 2020 — HR при частом исходе:
        RR ≈ (1 - 0.5^sqrt(HR)) / (1 - 0.5^sqrt(1/HR)).
  Оба указаны источниками преобразований в мануале пакета `EValue` (CRAN, `toRR`).
  Порог редкого исхода 15% — оттуда же.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth.recompute import recompute                        # noqa: E402
from truth.stats_tool import e_value, rr_from                # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


print("1. приведение мер — сверка с первоисточниками")
check("RR не преобразуется", rr_from("RR", 1.5, rare=False) == 1.5)
check("редкий исход: HR берётся как RR", rr_from("HR", 1.22, rare=True) == 1.22)
check("частый исход, OR: квадратный корень (VanderWeele 2017)",
      abs(rr_from("OR", 3.0, rare=False) - 3.0 ** 0.5) < 1e-9,
      str(round(rr_from("OR", 3.0, rare=False), 4)))
check("частый исход, HR: формула VanderWeele 2020",
      abs(rr_from("HR", 1.22, rare=False) - 1.1477) < 1e-3,
      str(round(rr_from("HR", 1.22, rare=False), 4)))
check("HR = 1 не даёт деления на ноль", rr_from("HR", 1.0, rare=False) == 1.0)
# Известный результат самого проекта: RR 0.700 -> E 2.21 (сверка stats_tool)
check("шкала E-value не изменилась", abs(e_value(0.700) - 2.21) < 0.005)

print("\n2. живые числа разбора 10.1136/jitc-2025-014726")
C = {"exposed_events": 1986, "exposed_total": 3609,
     "control_events": 1629, "control_total": 3609}
ADJ = {"measure": "HR", "value": "1.22", "ci_low": "1.17", "ci_high": "1.28",
       "outcome": "composite irAE"}
r = recompute({"counts": C, "absolute_risk_difference": "9.89 percentage points",
               "nnt": "-10.1", "adjusted_effect": ADJ})
ev = r["e_value"]
check("основание — скорректированная оценка", ev["basis"] == "adjusted", ev["basis"])
check("исход опознан как частый (45% событий в контроле)",
      ev["rare_outcome_assumed"] is False)
check("E-value пересчитан по преобразованной шкале",
      abs(ev["point"] - 1.559) < 0.01, str(ev["point"]))
check("сырое значение сохранено для сравнения, а не выброшено",
      abs(r["e_value_point"] - 1.736) < 0.01, str(r["e_value_point"]))
check("сырое и скорректированное расходятся заметно",
      abs(r["e_value_point"] - ev["point"]) > 0.15,
      f"{r['e_value_point']} против {ev['point']}")
check("граница ДИ посчитана", ev["ci"] is not None and ev["ci"] < ev["point"],
      str(ev["ci"]))

print("\n3. отказы называются, а не подменяются")
r2 = recompute({"counts": C, "absolute_risk_difference": "9.89 percentage points"})
check("без скорректированной оценки основание названо сырым",
      (r2.get("e_value") or {}).get("basis") == "crude")
check("и сказано, что это не тот вопрос",
      "does not answer" in (r2.get("e_value") or {}).get("note", ""))

r3 = recompute({"absolute_risk_difference": None, "adjusted_effect": ADJ})
check("скорректированная оценка считается и без таблицы 2×2",
      (r3.get("e_value") or {}).get("basis") == "adjusted", str(r3.get("basis")))
check("без таблицы редкость исхода предполагается (в сторону занижения)",
      (r3.get("e_value") or {}).get("rare_outcome_assumed") is True)
# Направление допущения названо верно: без преобразования E-value ВЫШЕ, то есть
# допущение о редком исходе завышает устойчивость. Первая версия комментария в
# коде утверждала обратное — поймано этим же тестом.
check("допущение о редком исходе завышает E-value, и это сказано",
      r3["e_value"]["point"] > ev["point"]
      and "upper bound" in r3["e_value"]["note"],
      f"{r3['e_value']['point']} против {ev['point']}")

print("\n4. ДИ, накрывающий единицу")
r4 = recompute({"counts": C, "adjusted_effect": {"measure": "HR", "value": "1.05",
                                                 "ci_low": "0.95", "ci_high": "1.16"}})
check("E-value границы равен единице, а не выдуманному числу",
      r4["e_value"]["ci"] == 1.0, str(r4["e_value"]["ci"]))

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
