#!/usr/bin/env python3
"""
Второй судья другой модели — замер self-preference, а не его декларация.

Зачем. `eval/README.md` честно перечисляет, чего харнес не доказывает, и первым
пунктом стоит: «судья той же семьи, что и подсудимый. Оценивает Gemini работу
Gemini — возможен self-preference». Пункт стоял там с 27.08 и всё это время
оставался словами: все числа продукта — 5.0/6 и 3.5/4, медианы десяти прогонов —
получены одним судьёй `gemini-3.7-flash`, то есть той же моделью, что писала
разбор. Возражение очевидное, справедливое и до сих пор неизмеренное.

Здесь оно измеряется. Уже сохранённые прогоны пересуживаются **другой** моделью,
пайплайн не запускается заново: судья видит только текст разбора (`findings`),
поэтому пересчёт корректен и стоит одного вызова на прогон.

Что считается. Совпадение по каждому пункту эталона, а не только по сумме: две
модели могут сойтись в итоге, разойдясь во всех слагаемых, и такой итог ничего
не подтверждает.

  python3 eval/second_judge.py --judge gemini-2.5-pro --limit 6
"""
import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import harness as H                                          # noqa: E402

RESULTS = ROOT / "eval" / "results"


def rejudge(path: pathlib.Path, judge: str) -> dict | None:
    d = json.loads(path.read_text())
    findings = (d.get("report") or {}).get("findings")
    first = d.get("judge") or {}
    if not findings or not first.get("points"):
        return None
    case = H.load_case(d["case"])
    raw = H.call_model(H.client(), judge, H.JUDGE_SYSTEM,
                       H.build_judge_user(case, findings))["text"]
    v, err = H.parse_json_answer(raw)
    if not v:
        return {"file": path.name, "error": err}
    second = {p["id"]: float(p.get("score", 0)) for p in v.get("points", [])}
    shared = sorted(set(first["points"]) & set(second))
    return {
        "file": path.name, "case": d["case"], "judge": judge,
        "first_total": round(sum(first["points"].values()), 2),
        "second_total": round(sum(second.get(k, 0) for k in shared), 2),
        "of": first.get("of"),
        "points": {k: [first["points"][k], second[k]] for k in shared},
        "agreed": sum(1 for k in shared if first["points"][k] == second[k]),
        "shared": len(shared),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", default="gemini-2.5-pro",
                    help="модель второго судьи — обязана отличаться от первой")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--case")
    a = ap.parse_args()
    if a.judge == H.DEFAULT_JUDGE:
        sys.exit("второй судья обязан отличаться от первого, иначе мерить нечего")

    files = sorted(RESULTS.glob("bench-*.json"), reverse=True)
    if a.case:
        files = [f for f in files if a.case in f.name]
    rows, out = [], RESULTS / f"second-judge-{a.judge}.json"
    for p in files[:a.limit]:
        r = rejudge(p, a.judge)
        if not r:
            continue
        rows.append(r)
        print(f"{r['case']:<15} {r['first_total']}/{r['of']} → {r['second_total']}/{r['of']} "
              f"· совпало пунктов {r['agreed']}/{r['shared']}  {r['file']}")
    if not rows:
        sys.exit("нечего пересуживать")

    diffs = [r["second_total"] - r["first_total"] for r in rows if "error" not in r]
    per_point = [(a2, b2) for r in rows for a2, b2 in r.get("points", {}).values()]
    exact = sum(1 for x, y in per_point if x == y)
    print(f"\nпрогонов {len(rows)} · пунктов {len(per_point)}")
    print(f"совпадение по пунктам: {exact}/{len(per_point)} = {exact/len(per_point):.0%}")
    print(f"смещение суммы (второй минус первый): медиана {st.median(diffs)}, "
          f"диапазон {min(diffs)}…{max(diffs)}")
    print(f"средняя |разница| по пункту: "
          f"{sum(abs(x-y) for x, y in per_point)/len(per_point):.2f} балла")
    out.write_text(json.dumps({"judge": a.judge, "runs": rows,
                               "point_agreement": round(exact/len(per_point), 3),
                               "median_shift": st.median(diffs)},
                              ensure_ascii=False, indent=2))
    print(f"сохранено: {out.name}")


if __name__ == "__main__":
    main()
