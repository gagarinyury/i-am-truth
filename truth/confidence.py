"""
Статус доверия каждому выводу — по правилу, а не по слову.

## Зачем модуль появился

В отчёте с самого начала стояло поле `max_confidence`: `CONFIRMED` на L1,
`PLAUSIBLE-UNVERIFIED` на L2 и L3. Оно рождалось в `retrieval.LEVELS`,
протаскивалось через сборку, печаталось в интерфейсе строкой «ceiling
CONFIRMED» и в брифе строкой «Ceiling of confidence». README, `/levels` и шапка
`pipeline` утверждали: ниже L1 статус `CONFIRMED` недостижим.

Утверждение было верным тавтологически. **Ни один вывод нигде не получал
никакого статуса.** Промпт такого поля не просил, пайплайн его не проставлял,
`brief.CEILING` был объявлен и не использован ни разу, а `batch.summary`
считал полем `confirmable` статьи, у которых потолок равен `CONFIRMED`, то есть
просто пересчитывал L1 под другим именем. Потолок ограничивал шкалу, значений
на которой не было.

Это тот же дефект, что F-55, где раздел «посчитано функцией» полтора дня
показывал арифметику модели: заголовок обещал механизм, механизма не было. Для
продукта, который ищет в чужих статьях утверждения без обеспечения, держать
такое на видном месте — не мелочь.

## Правило

Статус выводится из того, что уже измерено, и не спрашивает модель ни о чём.
Три входа, все три существуют независимо от этого модуля:

  1. **Потолок уровня** — что вообще удалось достать (`retrieval.assess_level`).
     Это именно потолок: он не повышает статус, только ограничивает.
  2. **Обеспеченность вывода** — `grounding.owners`: сколько чисел вывод
     приводит, сколько из них нашлось в документе и сколько несёт вес улики
     не ниже `STRONG_BITS`.
  3. **Адрес** — стоит ли хоть одно из этих чисел в ячейке, чьи подпись строки и
     заголовок колонки согласуются с меткой модели (`located == "cell"`).

Лестница:

  `CONFIRMED`   — есть число с весом улики ≥ 6 бит И у него есть адрес ячейки.
                  Сильнейшее, что этот инструмент умеет: значение не могло
                  попасться в документе случайно, и оно стоит там, где сказано.
  `SUPPORTED`   — есть число с весом ≥ 6 бит, но адреса нет: оно из прозы или
                  метка относится к целому предложению.
  `INDICATIVE`  — числа есть и нашлись, но все они такой формы, что документ
                  этого размера содержит их и без этой статьи (< 6 бит).
  `UNVERIFIED`  — вывод чисел не приводит вовсе, либо ни одно из приведённых в
                  документе не найдено.

## Чего этот статус НЕ означает

Он не говорит «вывод верен». Он говорит, **чем вывод обеспечен**, и ровно в тех
терминах, которые измерены. `UNVERIFIED` на домене «Missing data» — обычное
дело и не упрёк: вывод об отсутствующих данных законно опирается на свойства
дизайна, а не на цифры. Ошибкой было бы обратное — печатать `CONFIRMED` рядом с
выводом, под которым нет ни одного числа из статьи.

И он не проверяет смысл. Число с весом 12 бит, стоящее в своей ячейке,
доказывает, что оно взято из этой статьи и оттуда, откуда сказано. Что вывод,
построенный на нём, правилен, не доказывает ничто в этом файле.
"""
from .grounding import CONTESTED
from .verify_numbers import STRONG_BITS

# Порядок от сильного к слабому. Индекс в списке — сила статуса.
LADDER = ("CONFIRMED", "SUPPORTED", "INDICATIVE", "UNVERIFIED")

WHY = {
    "CONFIRMED": ("rests on a value distinctive enough that a document this size "
                  "would not hold it by chance, standing in a table cell whose row "
                  "and column agree with what the audit says it is"),
    "SUPPORTED": ("rests on a value distinctive enough that a document this size "
                  "would not hold it by chance, but that value sits in running text "
                  "rather than in an identifiable cell"),
    "INDICATIVE": ("cites numbers from the paper, but every one of them is of a shape "
                   "this document would contain anyway, so they do not tell this paper "
                   "apart from another"),
    "UNVERIFIED": ("cites no number from the paper, or none of the numbers it cites "
                   "could be found there — the conclusion rests on general properties "
                   "of the design, which is a different kind of claim, not necessarily "
                   "a weaker conclusion"),
}

# Потолок по уровню добытых данных. Ключ — уровень, значение — самый сильный
# статус, достижимый на нём. Числа здесь нет: потолок берётся из
# `retrieval.LEVELS[...]["max_confidence"]`, а это отображение только приводит
# формулировки уровня к лестнице статусов.
CEILING = {
    "CONFIRMED": "CONFIRMED",
    "PLAUSIBLE-UNVERIFIED": "SUPPORTED",
    "NONE": "UNVERIFIED",
}


def _cap(status: str, ceiling: str) -> str:
    """Не выше потолка. Потолок только опускает — поднять он не может ничего."""
    if ceiling not in LADDER:
        return status
    return status if LADDER.index(status) >= LADDER.index(ceiling) else ceiling


def _addressed(numbers: set, claims: list) -> bool:
    """Есть ли среди чисел вывода хоть одно с адресом ячейки и весом улики."""
    for c in claims or []:
        # Оспоренное число адресом не спасается: ячейка у него есть, но значение
        # приписано другой группе или взято с обратным знаком, и `CONFIRMED`
        # рядом с таким выводом означал бы, что адрес важнее направления.
        if c.get("status") in CONTESTED:
            continue
        if c.get("located") != "cell":
            continue
        if (c.get("evidence_bits") or 0.0) < STRONG_BITS:
            continue
        if str(c.get("value", "")) in numbers:
            return True
    return False


def assign(grounding: dict, numbers: dict, claims: list, level: dict) -> dict:
    """Статус каждому выводу разбора. Ключи те же, что у `grounding.owners`.

    `grounding` уже посчитал, сколько чисел вывод приводит и сколько из них несут
    вес улики; `numbers` (из `grounding.numbers_by_owner`) даёт сами значения,
    чтобы спросить про адрес именно их. Здесь не считается заново ничего —
    иначе две части отчёта разошлись бы в цифрах.
    """
    if not grounding:
        return {}
    ceiling = CEILING.get((level or {}).get("max_confidence"), "CONFIRMED")
    out = {}
    for key, part in grounding.items():
        nums = set((numbers or {}).get(key) or ())
        if not part.get("numbers"):
            status = "UNVERIFIED"
        elif not part.get("found"):
            status = "UNVERIFIED"
        elif not part.get("strong"):
            status = "INDICATIVE"
        elif _addressed(nums, claims):
            status = "CONFIRMED"
        else:
            status = "SUPPORTED"
        capped = _cap(status, ceiling)
        out[key] = {
            "title": part.get("title", key),
            "status": capped,
            "why": WHY[capped],
        }
        if capped != status:
            # Понижение потолком — отдельное сведение, а не то же самое, что
            # слабая опора. Читатель должен видеть, что вывод обеспечен сильнее,
            # чем ему позволено заявить, и почему именно.
            out[key]["capped_from"] = status
            out[key]["capped_because"] = (
                f"the evidence under it would support {status}, but only "
                f"{(level or {}).get('level')} of the paper was retrieved "
                f"({(level or {}).get('name')}), and the ceiling at that level is "
                f"{ceiling}")
    return out


def summarise(statuses: dict) -> dict | None:
    """Сколько выводов на каждой ступени. Ведущая строка отчёта.

    Одно число «уровень L1» говорит о добыче; это говорит о разборе.
    """
    if not statuses:
        return None
    counts = {s: 0 for s in LADDER}
    for x in statuses.values():
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    capped = sum(1 for x in statuses.values() if x.get("capped_from"))
    return {"counts": counts, "total": len(statuses), "capped_by_level": capped,
            "note": ("A status says what a conclusion rests on, not whether it is "
                     "right. UNVERIFIED is not an accusation: a conclusion drawn from "
                     "the design of a study legitimately cites no numbers.")}
