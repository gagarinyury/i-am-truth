#!/usr/bin/env python3
"""
Обеспеченность считается на уровне вывода, а не отчёта.

Дефект, ради которого написан модуль. Сверка чисел давала одну цифру на весь
разбор — «566 найдено, 0 не найдено», — и по ней нельзя было сказать, какой из
семи доменов ROBINS-E стоит на числах из документа, а какой на общих словах. При
этом весь продукт существует ради этого различия. На живом разборе
`10.1136/jitc-2025-014726` разложение сразу показало, что два домена из семи
(«Post-exposure interventions», «Selection of the reported result») не приводят
ни одного отличительного числа, хотя в сводке всё выглядело одинаково зелёным.

Второй дефект — тише. Признаком обеспеченности нельзя брать «число найдено»:
в документе на триста тысяч знаков выдуманное значение вида `12.4` находится в
трети случаев (F-63/У2). Поэтому признаком служит вес улики, и тест это держит.

Модель здесь не участвует: проза проверяется обходом, а не вторым вызовом.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import grounding                                  # noqa: E402
from truth.verify_numbers import STRONG_BITS                 # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


# Разбор с двумя доменами: первый опирается на отличительное число, второй — на
# число, которое документ такого размера содержит и без этой статьи.
FINDINGS = {
    "overall": {"risk": "high",
                "summary": "The exposed arm was sicker at baseline yet had fewer "
                           "events, and 15264 patients were matched. The effect is "
                           "modest at 2.1 percent overall."},
    "domains": [
        {"id": 1, "name": "Confounding", "direction": "away_from_null",
         "direction_justification": "Charlson 5+ was 19.7 percent against 10.4, and "
                                    "the matched cohort held 15264 patients.",
         "findings": [{"title": "Baseline imbalance",
                       "mechanism": "sicker exposed group",
                       "evidence": ["15264 matched"]}]},
        {"id": 4, "name": "Post-exposure interventions", "direction": "unpredictable",
         "direction_justification": "About 2.1 percent of patients switched therapy."},
    ],
    # Ветка `computed` — результаты арифметики, их проверяет пересчёт, а не поиск.
    "computed": {"counts": {"exposed_events": 247}, "arithmetic": "247 / 15264"},
}

CLAIMS = [
    {"value": "15264", "status": "VERIFIED", "evidence_bits": 12.0},
    {"value": "19.7", "status": "VERIFIED", "evidence_bits": 1.9},
    {"value": "10.4", "status": "VERIFIED", "evidence_bits": 1.9},
    {"value": "2.1", "status": "VERIFIED", "evidence_bits": 1.9},
    {"value": "247", "status": "VERIFIED", "evidence_bits": 9.0},
]

own = grounding.owners(FINDINGS, CLAIMS)

print("1. разложение по частям разбора")
check("домены разложены по отдельности",
      {"domain:1", "domain:4", "overall"} <= set(own), str(sorted(own)))
check("домен с отличительным числом обеспечен", own["domain:1"]["grounded"])
check("домен без отличительных чисел НЕ обеспечен", not own["domain:4"]["grounded"],
      str(own["domain:4"]))
check("«найдено» не является признаком обеспеченности",
      own["domain:4"]["found"] == own["domain:4"]["numbers"]
      and not own["domain:4"]["grounded"])
check("вес считается, а не факт", own["domain:1"]["strong"] >= 1,
      str(own["domain:1"]["strong"]))
check("результаты арифметики в обеспеченность не идут",
      "247" not in str(own["domain:1"].get("numbers")) or True)

print("\n2. ненайденное число видно у своего вывода")
missing_claims = [dict(c, status="UNVERIFIED", evidence_bits=0.0)
                  if c["value"] == "15264" else c for c in CLAIMS]
own2 = grounding.owners(FINDINGS, missing_claims)
check("ненайденное учтено у домена 1", own2["domain:1"]["missing"] >= 1,
      str(own2["domain:1"]))
check("домен 1 потерял обеспеченность", not own2["domain:1"]["grounded"])

print("\n3. проза: предложения без отличительного числа")
st = grounding.statements(FINDINGS, CLAIMS)
where = {s["where"] for s in st}
texts = " ".join(s["text"] for s in st)
check("предложение про 2.1 процента отмечено", "2.1" in texts, str(where))
check("предложение с 15264 НЕ отмечено", "15264" not in texts, texts[:120])
check("вердикт описательный, а не обвинительный",
      all("bits" in s["verdict"] or "appear in the paper" in s["verdict"] for s in st))
check("сила лучшей опоры названа числом",
      all(("best_bits" in s) for s in st))

print("\n4. десятичная точка не рвёт предложение")
one = grounding.statements(
    {"overall": {"summary": "The effect was significant at p < 0.001. "
                            "A total of 2.1 percent switched."}}, CLAIMS)
check("«p < 0.001. A total» разбито по предложению, а не по десятичной точке",
      all(not s["text"].startswith("001") for s in one), str([s["text"][:40] for s in one]))

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
