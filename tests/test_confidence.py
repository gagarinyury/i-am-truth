#!/usr/bin/env python3
"""
Потолок доверия перестал быть словом.

Что было. Поле `max_confidence` рождалось в `retrieval.LEVELS`, протаскивалось
через отчёт, печаталось в интерфейсе строкой «ceiling CONFIRMED» и в брифе
строкой «Ceiling of confidence», а README и `/levels` объясняли, почему ниже L1
статус `CONFIRMED` недостижим. Недостижим он был тавтологически: **ни один вывод
нигде не получал никакого статуса**. Промпт такого поля не просил, пайплайн его
не проставлял, `brief.CEILING` был объявлен и не использован, а `batch` считал
полем `confirmable` статьи, у которых потолок РАВЕН `CONFIRMED`, то есть просто
пересчитывал L1 под другим именем. Потолок ограничивал шкалу без значений — тот
же дефект, что F-55, где раздел «посчитано функцией» показывал арифметику модели.

Тест держит два свойства правила, и оба содержательные.

**Статус выводится из измеренного, а не из мнения.** Вес улики и адрес ячейки
уже посчитаны слоем 4; здесь они только читаются. Ни одного обращения к модели.

**Потолок опускает и никогда не поднимает.** Вывод, под которым лежит число с
адресом, на L1 получает `CONFIRMED`, а на L3 — `SUPPORTED` с пометкой
`capped_from`, потому что на абстракте такого обеспечения быть не может. Обратно
это не работает ни при каких данных: слабый вывод не становится сильным оттого,
что статья открыта.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import confidence, grounding, retrieval                # noqa: E402
from truth.verify_numbers import STRONG_BITS                      # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1
    ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


def level(lvl):
    return {"level": lvl, **retrieval.LEVELS[lvl]}


L1, L3, L0 = level("L1"), level("L3"), level("L0")

# Один домен на каждый исход лестницы. Числа подобраны так, чтобы форма числа
# сама давала нужный вес: «15264.5» несёт много бит, «7» — ноль.
STRONG, WEAK = "15264.5", "7"

FINDINGS = {
    "domains": [
        {"id": 1, "name": "Confounding",
         "findings": [{"title": "x", "mechanism": f"the value was {STRONG} there"}]},
        {"id": 2, "name": "Measurement of the exposure",
         "findings": [{"title": "y", "mechanism": f"about {WEAK} of them"}]},
        {"id": 3, "name": "Missing data",
         "findings": [{"title": "z", "mechanism": "no numbers are cited at all here"}]},
    ]
}

CLAIMS_ADDRESSED = [
    {"value": STRONG, "status": "VERIFIED", "evidence_bits": 12.0, "located": "cell"},
    {"value": WEAK, "status": "VERIFIED", "evidence_bits": 0.0, "located": "text"},
]
CLAIMS_NO_ADDRESS = [
    {"value": STRONG, "status": "VERIFIED", "evidence_bits": 12.0, "located": "text"},
    {"value": WEAK, "status": "VERIFIED", "evidence_bits": 0.0, "located": "text"},
]
CLAIMS_MISSING = [
    {"value": STRONG, "status": "UNVERIFIED", "evidence_bits": 0.0, "located": "absent"},
    {"value": WEAK, "status": "UNVERIFIED", "evidence_bits": 0.0, "located": "absent"},
]


def statuses(claims, lvl):
    gr = grounding.owners(FINDINGS, claims)
    return {k: v["status"] for k, v in
            confidence.assign(gr, grounding.numbers_by_owner(FINDINGS),
                              claims, lvl).items()}, \
           confidence.assign(gr, grounding.numbers_by_owner(FINDINGS), claims, lvl)


print("лестница на L1: адрес решает")
st, full = statuses(CLAIMS_ADDRESSED, L1)
check("сильное число с адресом → CONFIRMED", st["domain:1"] == "CONFIRMED", st["domain:1"])
check("слабое число → INDICATIVE", st["domain:2"] == "INDICATIVE", st["domain:2"])
check("чисел нет вовсе → UNVERIFIED", st["domain:3"] == "UNVERIFIED", st["domain:3"])
check("у каждого статуса есть объяснение",
      all(full[k].get("why") for k in full))

print("\nто же без адреса — на ступень ниже, и не выше")
st2, _ = statuses(CLAIMS_NO_ADDRESS, L1)
check("сильное число без адреса → SUPPORTED", st2["domain:1"] == "SUPPORTED", st2["domain:1"])
check("слабое не поднялось", st2["domain:2"] == "INDICATIVE")

print("\nчисло приведено, но в статье не найдено")
st3, _ = statuses(CLAIMS_MISSING, L1)
check("→ UNVERIFIED", st3["domain:1"] == "UNVERIFIED", st3["domain:1"])

print("\nпотолок опускает")
st4, full4 = statuses(CLAIMS_ADDRESSED, L3)
check("на L3 CONFIRMED недостижим", st4["domain:1"] == "SUPPORTED", st4["domain:1"])
check("и понижение названо", full4["domain:1"].get("capped_from") == "CONFIRMED",
      str(full4["domain:1"].get("capped_from")))
check("с причиной, где сказан уровень",
      "L3" in (full4["domain:1"].get("capped_because") or ""))
st5, _ = statuses(CLAIMS_ADDRESSED, L0)
check("на L0 не выше UNVERIFIED", set(st5.values()) == {"UNVERIFIED"}, str(set(st5.values())))

print("\nпотолок не поднимает")
check("слабый вывод на L1 остаётся INDICATIVE", st["domain:2"] == "INDICATIVE")
check("пустой вывод на L1 остаётся UNVERIFIED", st["domain:3"] == "UNVERIFIED")
check("_cap не повышает", confidence._cap("UNVERIFIED", "CONFIRMED") == "UNVERIFIED")

print("\nсводка сходится с построчным")
s = confidence.summarise(full)
check("итог равен числу выводов", s["total"] == len(full), f"{s['total']} == {len(full)}")
check("сумма по ступеням равна итогу", sum(s["counts"].values()) == s["total"])
s4 = confidence.summarise(full4)
check("понижённые посчитаны", s4["capped_by_level"] == 1, str(s4["capped_by_level"]))

print("\nпорог статуса — тот же, что у веса улики, а не отдельный")
check("STRONG_BITS один на весь проект", STRONG_BITS == 6.0, str(STRONG_BITS))
just_under = [{"value": STRONG, "status": "VERIFIED",
               "evidence_bits": STRONG_BITS - 0.1, "located": "cell"}]
gr = grounding.owners(FINDINGS, just_under)
u = confidence.assign(gr, grounding.numbers_by_owner(FINDINGS), just_under, L1)
check("чуть ниже порога → не CONFIRMED", u["domain:1"]["status"] != "CONFIRMED",
      u["domain:1"]["status"])

print("\nни одного разбора без входа не падает")
check("пустой grounding", confidence.assign({}, {}, [], L1) == {})
check("пустая сводка", confidence.summarise({}) is None)

print(f"\n{ok}/{total}")
sys.exit(0 if ok == total else 1)
