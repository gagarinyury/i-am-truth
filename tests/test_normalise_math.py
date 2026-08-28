#!/usr/bin/env python3
"""
Регрессия на F-41: знаки сравнения в научном тексте — не разметка.

Баг. `normalise` снимала теги паттерном `<[^>]+>`. В статьях «<» и «>» сплошь и
рядом стоят как знаки сравнения («p < 0.001», «coefficients > 0.8»), и всё между
ними вырезалось как один огромный тег. На реальном PDF так терялась треть текста
— 24 488 символов из 72 468, — после чего честно процитированные моделью числа
получали статус UNVERIFIED. Обвинить в галлюцинации того, кто не галлюцинировал,
для этого проекта — худший из возможных отказов.

Тест проверяет обе стороны: числа между знаками сравнения выживают, а настоящая
разметка по-прежнему снимается.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth.verify_numbers import normalise, verify   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"{name:<52} {'✅' if cond else '❌ ' + detail}")
    if not cond:
        FAILS.append(name)


# 1. Текст со знаками сравнения — числа между ними обязаны уцелеть
science = ("Reliability was acceptable, p < 0.001 for all scales and "
           "coefficients > 0.80. A total of 267 consecutive patients were "
           "screened; 199 participants (97.1%) had complete data.")
n = normalise(science)
# Главная проверка: число стоит МЕЖДУ «<» и следующим «>» — именно этот кусок
# старый паттерн вырезал целиком. Число после закрывающего знака уцелевало и с
# багом, поэтому проверять надо здесь.
check("число МЕЖДУ знаками сравнения сохранено", "0.001" in n,
      f"вырезано вместе с мнимым тегом: {n[:90]!r}")
check("число после знака сравнения сохранено", "267" in n, f"не найдено в {n[:80]!r}")
check("число в скобках сохранено", "97.1" in n)
check("текст не укоротился больше чем на 5%",
      len(n) >= len(science) * 0.95, f"{len(science)} → {len(n)}")

# 2. Настоящая разметка по-прежнему снимается
markup = "<p>Group <bold>A</bold> had <italic>1234</italic> events</p>"
m = normalise(markup)
check("теги сняты", "<" not in m and ">" not in m, repr(m))
check("число из-под тегов сохранено", "1234" in m)

# 3. Сквозная проверка: число, стоящее за знаком сравнения, верифицируется
res = verify([{"value": "0.001", "label": "p value"}], science)
st = res["claims"][0]["status"]
check("verify находит число между знаками сравнения", st != "UNVERIFIED", st)

# 4. И обратное: выдуманного числа в тексте нет — статус обязан быть UNVERIFIED
res2 = verify([{"value": "9999", "label": "invented"}], science)
check("выдуманное число ловится", res2["claims"][0]["status"] == "UNVERIFIED",
      res2["claims"][0]["status"])

print()
if FAILS:
    print(f"провалено: {len(FAILS)} — {', '.join(FAILS)}")
    sys.exit(1)
print("все проверки пройдены")
