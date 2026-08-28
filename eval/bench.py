#!/usr/bin/env python3
"""
Сквозной бенчмарк: пайплайн целиком против эталонов, с сохранением результата.

Чем отличается от `harness.py`. Харнес меряет **модель на подготовленном входе**:
берёт файл из `eval/inputs/`, отдаёт его модели, судит ответ. Это правильный
инструмент, чтобы сравнивать промпты и уровни данных, но он не трогает добычу,
разбор таблиц, суб-агентов и сверку чисел.

Bench меряет **продукт**: DOI (или файл) на входе, отчёт на выходе, всё как у
пользователя. Именно этими числами описан проект в README и заявке, поэтому они
обязаны лежать в репозитории, а не в терминале.

  python3 eval/bench.py run                      # оба эталона, 3 прогона каждый
  python3 eval/bench.py run --case cheng-2024 --runs 1
  python3 eval/bench.py run --engine adk
  python3 eval/bench.py report                   # сводка по сохранённому

Результаты: `eval/results/bench-<стамп>-<кейс>.json` — полный отчёт пайплайна
плюс оценка судьи по каждому пункту эталона.
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import harness as H                                          # noqa: E402
from truth import pipeline                                   # noqa: E402

RESULTS = ROOT / "eval" / "results"
PROMPT = (ROOT / "truth" / "prompt_robins_e.md").read_text()

# Как каждый эталон подаётся продукту. McDonald закрыт у издателя (bronze OA за
# Cloudflare), поэтому идёт путём B — файлом; Cheng открыт в Europe PMC и идёт
# путём A по одному DOI. Разные пути здесь не недостаток замера, а его смысл:
# меряется то, как статья реально попадает в систему.
CASES = {
    "mcdonald-2026": {
        "doi": "10.1200/OP-26-00485",
        "pdf": "mcdonald.pdf",
        "note": "путь B — PDF от пользователя; статья за Cloudflare",
    },
    "cheng-2024": {
        "doi": "10.1111/1753-0407.70013",
        "pdf": None,
        "note": "путь A — Europe PMC, full-text и приложение",
    },
}


def find_pdf(name: str) -> bytes:
    """PDF статей в репозиторий не кладутся — копирайт издателя. Ищем рядом."""
    for d in (ROOT / "eval" / "pdf", pathlib.Path.home() / "Downloads",
              pathlib.Path("/private/tmp")):
        p = d / name
        if p.exists():
            return p.read_bytes()
    raise SystemExit(
        f"нужен {name}. Положи его в eval/pdf/ или ~/Downloads/.\n"
        "В репозиторий он не коммитится: статья под копирайтом издателя.")


def judge(case: dict, findings: dict) -> dict:
    cli = H.client()
    raw = H.call_model(cli, H.DEFAULT_JUDGE, H.JUDGE_SYSTEM,
                       H.build_judge_user(case, findings))["text"]
    v, err = H.parse_json_answer(raw)
    if err:
        return {"error": err, "raw": raw[:1000]}
    pts = {p["id"]: p.get("score", 0) for p in v.get("points", [])}
    return {
        "points": pts,
        "score": sum(pts.values()),
        "of": case["expert_points_total"],
        "ratio": round(sum(pts.values()) / case["expert_points_total"], 3),
        "detail": v.get("points"),
        "extra_findings": v.get("extra_findings"),
        "traps": v.get("traps"),
    }


def one_run(case_id: str, engine: str) -> dict:
    case = H.load_case(case_id)
    cfg = CASES[case_id]
    uploads = [(cfg["pdf"], find_pdf(cfg["pdf"]))] if cfg["pdf"] else None

    t0 = time.time()
    r = pipeline.run(doi=cfg["doi"], prompt=PROMPT, uploads=uploads, engine=engine)
    seconds = round(time.time() - t0, 1)

    scored = judge(case, r.get("findings") or {})
    return {
        "case": case_id,
        "engine": engine,
        "ground_truth_version": case.get("version"),
        "seconds": seconds,
        "level": r["level"]["level"],
        "tables": r["tables"],
        "agents": list((r.get("agents") or {}).keys()) or None,
        "tool_calls": r.get("tool_calls"),
        "engine_note": r.get("engine_note"),
        "verification": r["verification"],
        "unverified_numbers": r.get("unverified_numbers"),
        "judge": scored,
        "report": r,
    }


def cmd_run(args):
    RESULTS.mkdir(parents=True, exist_ok=True)
    cases = [args.case] if args.case else list(CASES)
    for case_id in cases:
        for i in range(args.runs):
            out = one_run(case_id, args.engine)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = RESULTS / f"bench-{stamp}-{case_id}.json"
            path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
            j = out["judge"]
            print(f"{case_id:<15} {args.engine:<7} прогон {i+1}/{args.runs}: "
                  f"{j.get('score')}/{j.get('of')} "
                  f"({j.get('ratio')}) · {out['level']} · {out['seconds']} c · "
                  f"невалидных {out['verification']['unverified'] if out['verification'] else '—'} "
                  f"→ {path.name}")


def cmd_report(args):
    rows = []
    for p in sorted(RESULTS.glob("bench-*.json")):
        d = json.loads(p.read_text())
        j = d.get("judge") or {}
        rows.append((d["case"], d.get("engine", "direct"), j.get("score"),
                     j.get("of"), j.get("ratio"), d.get("level"),
                     d.get("seconds"), len(d.get("agents") or []), p.name))
    if not rows:
        print("прогонов bench не найдено — сначала `python3 eval/bench.py run`")
        return

    print(f"{'кейс':<15} {'движок':<7} {'балл':>8} {'доля':>6} {'ур':>3} "
          f"{'сек':>6} {'аг':>3}  файл")
    print("-" * 88)
    for r in rows:
        print(f"{r[0]:<15} {r[1]:<7} {str(r[2])+'/'+str(r[3]):>8} {r[4]:>6} "
              f"{r[5] or '—':>3} {r[6] or '—':>6} {r[7]:>3}  {r[8]}")

    # медиана по кейсу и движку — именно она стоит в README
    print("\nмедианы:")
    import statistics as st
    from collections import defaultdict
    g = defaultdict(list)
    for r in rows:
        if r[2] is not None:
            g[(r[0], r[1])].append(r[2])
    for (case_id, engine), vals in sorted(g.items()):
        of = next(r[3] for r in rows if r[0] == case_id)
        med = st.median(vals)
        print(f"  {case_id:<15} {engine:<7} {med}/{of} "
              f"({round(med/of*100)}%)  n={len(vals)}  {sorted(vals)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--case", choices=list(CASES))
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--engine", default="direct", choices=["direct", "adk"])
    r.set_defaults(func=cmd_run)
    s = sub.add_parser("report")
    s.set_defaults(func=cmd_report)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
