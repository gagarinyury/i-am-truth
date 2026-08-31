#!/usr/bin/env python3
"""
Знак числа: направление нельзя подтвердить цифрами без знака.

Дефект, ради которого написан тест. Шаблон числа во всех слоях начинался с `\\d`,
поэтому знак в заявку не попадал, а поиск шёл по модулю:

    verify([{"value": "0.69", "label": "absolute risk difference"}],
           "The absolute risk difference was -0.69 percentage points") → VERIFIED

То есть разность рисков, записанная с противоположным направлением,
подтверждалась первоисточником. Это тот же класс, ради которого написана
`check_group` — инверсия направления (F-12), — только этажом ниже: там ловилась
подмена группы, здесь беспрепятственно проходила подмена знака.

Вторая половина теста важнее первой. Наивное решение («минус перед числом —
значит знак») ломает каждую вторую границу доверительного интервала: в «1.05-1.42»
дефис — разделитель диапазона, и объявить верхнюю границу «не найденной» значит
выдвинуть ложное обвинение, а оно в этом проекте дороже пропуска. Поэтому минус
читается как знак только там, где перед ним не стоит цифра.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import confidence, grounding, pipeline                 # noqa: E402
from truth.verify_numbers import NUM, verify                      # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


def status(value, source, label="absolute risk difference between the arms"):
    return verify([{"value": value, "label": label}], source)["claims"][0]["status"]


print("знак ловится…")
check("положительное против отрицательного — не подтверждение",
      status("0.69", "The absolute risk difference was -0.69 percentage points")
      == "SIGN_MISMATCH")
check("свой знак подтверждается",
      status("-0.69", "The absolute risk difference was -0.69 percentage points")
      == "VERIFIED")
check("отрицательное против положительного — тоже",
      status("-0.12", "the standardised difference was 0.12 across arms")
      == "SIGN_MISMATCH")
check("числа нет вовсе — прежний UNVERIFIED, а не подмена знака",
      status("7.77", "nothing of the sort here") == "UNVERIFIED")

print("\nложных обвинений нет — дефис не всегда минус…")
for src, why in (("adjusted HR 1.22 (95% CI 1.05-1.42)", "дефис-диапазон"),
                 ("adjusted HR 1.22 (95% CI 1.05–1.42)", "тире-диапазон"),
                 ("follow-up ran 2020-2024", "годы"),
                 ("the ratio was 1.05 - 1.42 across strata", "диапазон с пробелами")):
    st = status("1.42" if "1.42" in src else "2024", src)
    check(f"{why}: верхняя граница остаётся найденной", st != "SIGN_MISMATCH", st)
check("маркер списка не делает число отрицательным",
      status("5", "- 5 patients were excluded") == "VERIFIED")

print("\nшаблон числа общий для слоёв и видит знак…")
check("шаблон один и тот же объект", grounding.NUM is NUM)
check("минус входит в число", "-0.69" in NUM.findall("difference of -0.69 pp"))
check("граница ДИ остаётся положительной",
      NUM.findall("(95% CI 1.05-1.42)") == ["95", "1.05", "1.42"],
      str(NUM.findall("(95% CI 1.05-1.42)")))
check("неразрывный пробел в разрядах не рвёт число",
      NUM.findall("n = 15 264") == ["15 264"])

print("\nсквозь весь слой 4…")
findings = {"domains": [{"id": 1, "name": "Confounding",
                         "direction_justification":
                             "the difference was 0.69 percentage points"}]}
res = pipeline.verify_findings(
    findings, "The absolute risk difference was -0.69 percentage points.")
check("сводка называет число с чужим знаком", res["summary"]["sign_mismatch"] == 1)
check("оно не попало в «подтверждено»", res["summary"]["verified"] == 0)

print("\nоспоренное число не обеспечивает вывод…")
flipped = [{"value": "0.69", "status": "SIGN_MISMATCH", "evidence_bits": 9.0,
            "located": "cell"}]
gr = grounding.owners(findings, flipped)
key = "domain:1"
check("в обеспеченности оно не считается найденным", gr[key]["found"] == 0,
      str(gr[key]))
check("вывод не объявлен обеспеченным", gr[key]["grounded"] is False)
st = confidence.assign(gr, grounding.numbers_by_owner(findings), flipped,
                       {"level": "L1", "max_confidence": "CONFIRMED"})
check("адрес ячейки не даёт CONFIRMED числу с чужим знаком",
      st[key]["status"] == "UNVERIFIED", st[key]["status"])

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
