"""
Проверка группы: обвинять в инверсии только при доказательстве.

Дефект (F-56): маркером контрольной группы служила подстрока «control», из-за чего
«glycemic control» в метке превращало число в принадлежащее контрольной руке. Все
13 ложных инверсий, поднятых из сохранённых прогонов Cheng, имели ровно эту
причину — и ни в одном из них в контексте не было ни единого упоминания
контрольной группы. Тест держит обе стороны: ложное обвинение не выносится, а
настоящая инверсия по-прежнему видна.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth.verify_numbers import check_group      # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


# Настоящая инверсия: число заявлено за контрольной рукой, а в источнике оно стоит
# вплотную к группе экспозиции, и контрольная рука тоже упомянута рядом.
real = {"value": "19.7", "label": "Charlson 5+, control group"}
ctx_real = ("baseline characteristics: glp-1 group 19.7 percent versus "
            "control group 10.4 percent across the matched cohort")
check("настоящая инверсия ловится",
      check_group(real, ctx_real, "19.7") == "mismatch",
      check_group(real, ctx_real, "19.7"))

# Та же строка, но число взято у своей руки — обвинения быть не должно.
check("верное отнесение не трогается",
      check_group({"value": "10.4", "label": "Charlson 5+, control group"},
                  ctx_real, "10.4") == "ok")

# Реальный случай из прогона: «glycemic control» в метке, контрольной руки в
# контексте нет вовсе.
glyc = {"value": "90.52",
        "label": "Unmeasured glycemic control and metabolic severity"}
ctx_glyc = ("the missing rates of the hba1c were 90.52 percent, 91.79 percent and "
            "90.45 percent among glp-1 ras, insulin only and metformin groups")
check("«glycemic control» больше не считается группой",
      check_group(glyc, ctx_glyc, "90.52") == "unknown",
      check_group(glyc, ctx_glyc, "90.52"))

check("«blood pressure control» тоже не группа",
      check_group({"value": "7.1", "label": "poor blood pressure control at baseline"},
                  "mean value 7.1 in the glp-1 arm", "7.1") == "unknown")

# Множественное число — законное имя руки.
check("«matched controls» распознаётся как группа",
      check_group({"value": "7.8", "label": "prior breast cancer, matched controls"},
                  "glp-1 users 5.9 percent while matched controls reached 7.8 percent",
                  "7.8") == "ok")

check("«control arm» распознаётся как группа",
      check_group({"value": "5.0", "label": "events in the control arm"},
                  "the control arm recorded 5.0 events per hundred", "5.0") == "ok")

# Маркера заявленной группы рядом нет — это отсутствие сведений, а не улика.
check("отсутствие своей группы в контексте не даёт обвинения",
      check_group({"value": "3.3", "label": "control group value"},
                  "the glp-1 users reached 3.3 percent overall", "3.3") == "unknown")

# Метка вообще не про группы.
check("метка без групп даёт unknown",
      check_group({"value": "12", "label": "median follow-up months"},
                  "median follow-up was 12 months in the control group", "12") == "unknown")

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
