#!/usr/bin/env python3
"""
Пересчёт функцией отказывается от невозможных чисел и не падает на вырожденных.

Два дефекта, которые тест держит.

**Строгая проверка охраняла не тот вход.** Путь `parsed` — слабый, где числа
восстановлены из строки, написанной моделью, — требовал `events <= total`. Путь
`reported` — сильный, тот самый, чей результат отчёт подписывает «independent of
the model's arithmetic», — требовал только `> 0`. Поэтому
`{"exposed_events": 2000, "exposed_total": 100}` проходило насквозь и печаталось
в разделе «Recomputed by a function, not by the model»: риск 2000%, отношение
шансов −1.05, разность рисков 1950 процентных пунктов. Функция, объявленная
авторитетом над моделью, брала у модели что угодно.

**Вырожденная таблица роняла весь запрос.** Если исход наступил у всех в руке,
знаменатель шансов равен нулю, и `ZeroDivisionError` уходил наружу: `recompute`
его не ловил, и `POST /analyze` отвечал 500 — после трёх оплаченных вызовов
Vertex и полного разбора. Тот же класс, что `inf` в `nnt`, который проект уже
чинил однажды: величина, которой нет, выражается её отсутствием.

Граница между двумя случаями — содержательная, а не техническая. «Событий
больше, чем людей» невозможно, и такие числа отвергаются целиком. «Исход у
всех» возможно: RR, ARR и NNT для такой таблицы существуют, не существует
только отношения шансов, и оно приходит как `None` с названной причиной.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import brief                                       # noqa: E402
from truth.recompute import recompute                         # noqa: E402
from truth.stats_tool import TwoByTwo                          # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1
    ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


def run(**counts):
    """Пересчёт, который обязан не бросать ни при каком входе."""
    try:
        return recompute({"counts": counts})
    except Exception as e:                                    # noqa: BLE001
        return {"basis": "RAISED", "note": f"{type(e).__name__}: {e}"}


print("эталон проекта не сдвинулся")
good = run(exposed_events=247, exposed_total=15264,
           control_events=353, control_total=15264)
check("basis reported", good["basis"] == "reported", good["basis"])
check("RR 0.6997", good["rr"] == 0.6997, str(good["rr"]))
check("OR 0.6948", good["odds_ratio"] == 0.6948, str(good["odds_ratio"]))
check("NNT 144.0", good["nnt"] == 144.0, str(good["nnt"]))
check("ничего не осталось неопределённым", not good.get("undefined"))

print("\nневозможные числа отвергаются, и отказ объяснён словами")
bad = run(exposed_events=2000, exposed_total=100,
          control_events=50, control_total=100)
check("пересчёта нет", bad["basis"] == "none", bad["basis"])
check("причина названа", "more events than participants" in bad["note"], bad["note"][:70])
check("сами числа в причине есть", "2000/100" in bad["note"])
for name, c in (("пустая рука", dict(exposed_events=10, exposed_total=0,
                                     control_events=5, control_total=100)),
                ("отрицательные события", dict(exposed_events=-5, exposed_total=100,
                                               control_events=5, control_total=100)),
                ("событий нет ни у кого", dict(exposed_events=0, exposed_total=100,
                                               control_events=0, control_total=100))):
    r = run(**c)
    check(f"{name}: отказ, а не число", r["basis"] == "none", r.get("note", "")[:60])

print("\nвырожденная таблица считается, а не роняет запрос")
allx = run(exposed_events=100, exposed_total=100,
           control_events=50, control_total=100)
check("исключения нет", allx["basis"] != "RAISED", allx.get("note", "")[:60])
check("RR посчитан", allx["rr"] == 2.0, str(allx["rr"]))
check("NNT посчитан", allx["nnt"] is not None, str(allx["nnt"]))
check("шансов нет", allx["odds_ratio"] is None)
check("и об этом сказано", allx.get("undefined") == ["odds_ratio"],
      str(allx.get("undefined")))
check("с причиной", "no odds" in (allx.get("undefined_note") or ""))

none_ctrl = run(exposed_events=10, exposed_total=100,
                control_events=0, control_total=100)
check("нет событий в контроле: исключения нет", none_ctrl["basis"] != "RAISED")
check("RR не определён", none_ctrl["rr"] is None)
check("ДИ не определён", none_ctrl["rr_ci95"] is None)
check("E-value не определён", none_ctrl["e_value_point"] is None)

print("\nотчёт остаётся валидным JSON — ни nan, ни inf")
for r in (good, allx, none_ctrl, bad):
    try:
        json.dumps(r, allow_nan=False)
        good_json = True
    except ValueError:
        good_json = False
    check(f"basis={r['basis']} сериализуется без nan/inf", good_json)

print("\nбриф печатает прочерк, а не None")
md = brief.render({"recomputed": none_ctrl, "meta": {}, "level": {}, "findings": {}})
check("прочерк вместо None", "| — |" in md or "| risk ratio | — |" in md,
      next((l for l in md.splitlines() if "risk ratio" in l), ""))
check("слова None в брифе нет", "None" not in md)

print("\nдве меры, которых не бывает, не выдумываются нулём")
t = TwoByTwo(100, 100, 50, 100)
check("TwoByTwo.odds_ratio → None", t.odds_ratio() is None)
check("TwoByTwo.report не бросает", isinstance(t.report(), dict))

print(f"\n{ok}/{total}")
sys.exit(0 if ok == total else 1)
