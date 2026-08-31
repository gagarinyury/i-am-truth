#!/usr/bin/env python3
"""
Вес улики: «число найдено» — величина, а не факт.

Зачем этот тест существует. Слой 4 печатал «566 чисел найдено, 0 не найдено» и
подавал это как достижение. Замер 29.08 на разборе `10.1136/jitc-2025-014726`
(341 тыс. знаков) показал, чего такая строка стоит: ВЫДУМАННОЕ число вида `x.y`
находится в этом документе в 33% случаев, целое из двух-трёх цифр — в 46%. На
абстракте (1.7 тыс. знаков) — в 1%. То есть сила проверки падала ровно там, где
retrieval работал лучше всего, а retrieval — главный тезис проекта.

Плюс две ошибки счёта, которые тест держит закрытыми:
  * в знаменатель шли числа из списка `TRIVIAL`, которые никогда не искали:
    отчёт объявлял найденными 566 из 566, тогда как искали 474;
  * граница числа не исключала точку, и «247» подтверждалось строкой «247.83».

Модель нуля проверяется здесь же против подстановки выдуманных чисел — она
обязана воспроизводить сэмплинг, иначе это не измерение, а красивая формула.
"""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import verify_numbers as V                        # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


print("1. мощность формы")
check("однозначные: 10", V.shape_space((1, 0)) == 10)
check("двузначные: 90", V.shape_space((2, 0)) == 90)
check("вида x.y: 900", V.shape_space((2, 1)) == 900)
check("вида xx.yy: 9000", V.shape_space((2, 2)) == 9000)

print("\n2. документ сам задаёт нуль")
# Документ, в котором заняты все однозначные числа и одно пятизначное.
doc = " ".join(str(i) for i in range(10)) + " and a cohort of 15264 patients"
sh = V.document_shapes(doc)
check("однозначное весит 0 бит — нашлось бы само",
      V.evidence_bits("7", sh) == 0.0, str(V.evidence_bits("7", sh)))
check("пятизначное весит много", V.evidence_bits("15264", sh) > 10,
      str(V.evidence_bits("15264", sh)))
check("список тривиальных чисел не нужен: их обнуляет измерение",
      not hasattr(V, "TRIVIAL"))

print("\n3. модель нуля обязана сходиться с подстановкой выдуманных чисел")
random.seed(11)
# Синтетический документ с заданной плотностью формы (2,1): занято 300 из 900.
vals = random.sample([f"{a}.{b}" for a in range(10, 100) for b in range(10)], 300)
doc2 = "text " + " text ".join(vals) + " text"
sh2 = V.document_shapes(doc2)
model = V.chance_rate("42.7", sh2)
probe = [f"{random.randint(10, 99)}.{random.randint(0, 9)}" for _ in range(600)]
sampled = sum(1 for x in set(probe) if V.find_occurrences(x, doc2, limit=1)) / len(set(probe))
check("модель и сэмплинг совпали", abs(model - sampled) < 0.06,
      f"модель {model:.0%}, сэмплинг {sampled:.0%}")

print("\n4. граница числа: целое не подтверждается чужой дробью")
src = "The hazard ratio was 247.83 and 15264 patients were matched."
check("«247» НЕ находится внутри «247.83»",
      not V.find_occurrences("247", src))
check("«247.83» находится", bool(V.find_occurrences("247.83", src)))
check("«15264» находится", bool(V.find_occurrences("15264", src)))

print("\n5. счёт сходится и вес доходит до сводки")
claims = [{"value": "247.83", "label": "hazard ratio"},
          {"value": "15264", "label": "matched patients"},
          {"value": "7", "label": "some small count"},
          {"value": "99.91", "label": "invented"}]
r = V.verify(claims, src)
s = r["summary"]
check("найдено + не найдено = искали", s["found"] + s["unverified"] == s["total"],
      f"{s['found']}+{s['unverified']} vs {s['total']}")
# Ненайденных двое, и оба по делу: выдуманное «99.91» и одиночное «7», которое
# в тексте встречается только внутри «247.83» и «15264» — то есть как отдельное
# число отсутствует. Ровно то, что должна давать починенная граница.
check("не найдены выдуманное и мнимое одиночное", s["unverified"] == 2,
      str([c["value"] for c in r["claims"] if c["status"] == "UNVERIFIED"]))
check("у сильных чисел вес выше порога",
      all(c["evidence_bits"] >= V.STRONG_BITS
          for c in r["claims"] if c["value"] in ("247.83", "15264")),
      str([(c["value"], c["evidence_bits"]) for c in r["claims"]]))
check("сводка несёт число сильных улик", s["strong"] >= 2, str(s["strong"]))
check("медиана веса посчитана", s["evidence_bits_median"] is not None)

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
