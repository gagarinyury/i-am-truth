"""
Свод направлений: счёт по доменам обязан ловить расхождение с общим выводом.

Смысл проверки — не в арифметике, а в том, что общее направление модели больше не
принимается на слово: если все домены тянут в одну сторону, а итог назван другой,
это видно.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth.direction import summarise      # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


def f(dirs, overall):
    return {"domains": [{"id": i + 1, "name": f"D{i+1}", "direction": d}
                        for i, d in enumerate(dirs)],
            "overall": {"direction": overall}}


r = summarise(f(["away_from_null"] * 3 + ["towards_null", "unpredictable",
                "no_information", "no_information"], "away_from_null"))
check("счёт по доменам верен", r["counts"]["away_from_null"] == 3
      and r["counts"]["towards_null"] == 1 and r["counts"]["no_information"] == 2)
check("преобладание определено", r["dominant_by_count"] == "away_from_null")
check("итог согласуется с доменами", r["agreement"] == "consistent")
check("домены названы поимённо",
      r["domains_by_direction"]["away_from_null"] == ["D1", "D2", "D3"])

contra = summarise(f(["away_from_null"] * 4 + ["unpredictable"], "towards_null"))
check("противоречие итога доменам поймано", contra["agreement"] == "contradicts",
      contra["agreement"])

tie = summarise(f(["away_from_null", "towards_null"], "away_from_null"))
check("при равенстве итог объявлен необеспеченным", tie["agreement"] == "unsupported")

none_dir = summarise(f(["no_information"] * 3, "away_from_null"))
check("без направленных доменов итог тоже необеспечен",
      none_dir["agreement"] == "unsupported")

silent = summarise(f(["away_from_null"], None))
check("молчание модели — отдельный случай, не согласие",
      silent["agreement"] == "not_stated")

check("мусорное направление считается отдельно",
      summarise(f(["вверх", "away_from_null"], "away_from_null"))["unclassified"] == 1)
check("без доменов свод не выдумывается", summarise({"overall": {}}) is None)

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
