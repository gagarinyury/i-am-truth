"""
Фоновый батч-разбор корпуса статей.

Это ядро того, что просит хакатон: агент, работающий в фоне над тяжёлым набором
данных, а не чат-цикл. Запускается как Cloud Run Job (решение D-10: своя очередь
не пишется, а `background=True` из Interactions API нашему сценарию не подходит —
он принимает только managed-агентов, F-20).

Состояние и результаты складываются в GCS: каждая статья — отдельный объект,
поэтому упавшая статья не теряет остальные и прогон можно продолжить.

  python3 -m truth.batch --query '(GLP-1) AND cancer AND cohort' --limit 20
  python3 -m truth.batch --dois 10.1136/jitc-2025-014726,10.3389/fonc.2026.1742210
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import pathlib
import sys
import traceback
import urllib.parse
import urllib.request

from . import pipeline, retrieval

BUCKET = os.environ.get("TRUTH_BUCKET", "i-am-truth-runs-merci-prod")
PROMPT_PATH = pathlib.Path(__file__).resolve().parent / "prompt_robins_e.md"
# Vertex отдаёт 429 уже на пятом запросе подряд (F-11) — параллелизм скромный,
# у каждого вызова свой backoff внутри critic.call
WORKERS = int(os.environ.get("TRUTH_WORKERS", "3"))


def _storage():
    """Ленивый импорт: локальный прогон возможен и без облака."""
    from google.cloud import storage
    return storage.Client()


# Отказы записи в хранилище копятся здесь и обязаны попасть в сводку:
# в Cloud Run Job локальная файловая система умирает вместе с задачей, и
# «успешный» прогон без единого сохранённого результата выглядел бы как успешный.
STORAGE_FAILURES = []


def save(path: str, obj: dict) -> bool:
    """Возвращает True, если запись реально ушла в облако.

    `allow_nan=False` — то же правило, что в `store.put`, и по той же причине:
    с `inf` или `nan` в отчёте в бакет лёг бы литерал `Infinity`, невалидный
    JSON для всех потребителей, кроме Python. Раньше проверка стояла в одном из
    двух мест записи, то есть закрывала половину риска.
    """
    data = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)
    try:
        _storage().bucket(BUCKET).blob(path).upload_from_string(
            data, content_type="application/json")
        return True
    except Exception as e:                                   # noqa: BLE001
        out = pathlib.Path("batch_out") / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(data)
        STORAGE_FAILURES.append({"path": path, "error": f"{type(e).__name__}: {e}"[:200]})
        print(f"  ⚠️  GCS недоступен ({type(e).__name__}), записано во временную "
              f"файловую систему {out} — в Cloud Run Job она исчезнет вместе с задачей",
              file=sys.stderr)
        return False


def search_dois(query: str, limit: int) -> list:
    """Список DOI по запросу Europe PMC.

    Идёт через `retrieval._get`, а не своим `urlopen`: темп и повтор при 503
    написаны там, и обходить их в единственном месте, откуда стартует
    трёхпоточный батч, — значит держать правило ровно там, где оно не нужно, и
    нарушать там, где нужно.
    """
    url = (f"{retrieval.EPMC}/search?query={urllib.parse.quote(query)}"
           f"&format=json&resultType=core&pageSize={min(limit, 100)}")
    d = retrieval._get(url, attempts=3)
    return [x["doi"] for x in (d.get("resultList") or {}).get("result", [])
            if x.get("doi")][:limit]


def analyse_one(doi: str, prompt: str, run_id: str) -> dict:
    try:
        res = pipeline.run(doi=doi, prompt=prompt)
        row = {
            "doi": doi, "ok": True,
            "title": (res["meta"].get("title") or "")[:200],
            "level": res["level"]["level"],
            "max_confidence": res["max_confidence"],
            "tables": res["tables"],
            "overall": (res["findings"] or {}).get("overall"),
            "classification": (res["findings"] or {}).get("classification"),
            "domains": len((res["findings"] or {}).get("domains", [])),
            "verification": res["verification"],
            "confidence": ((res.get("confidence_summary") or {}).get("counts") or {}),
            "unverified": len(res.get("unverified_numbers") or []),
        }
        save(f"{run_id}/papers/{doi.replace('/', '_')}.json", res)
    except Exception as e:                                   # noqa: BLE001
        row = {"doi": doi, "ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        traceback.print_exc(limit=1)
    print(f"  {'✓' if row['ok'] else '✗'} {doi} "
          f"{row.get('level', '')} {row.get('error', '')}", flush=True)
    return row


def run_batch(dois: list, run_id: str = None) -> dict:
    run_id = run_id or dt.datetime.now().strftime("run-%Y%m%d-%H%M%S")
    prompt = PROMPT_PATH.read_text()
    print(f"батч {run_id}: {len(dois)} статей, воркеров {WORKERS}", flush=True)

    rows = []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(analyse_one, d, prompt, run_id): d for d in dois}
        for f in cf.as_completed(futs):
            rows.append(f.result())

    ok = [r for r in rows if r["ok"]]
    by_level = {}
    for r in ok:
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
    summary = {
        "run_id": run_id,
        "finished": dt.datetime.now().isoformat(timespec="seconds"),
        "total": len(dois), "succeeded": len(ok), "failed": len(rows) - len(ok),
        "by_level": by_level,
        # Раньше здесь стояло `max_confidence == "CONFIRMED"` под именем
        # `confirmable` — то есть считались статьи, которым потолок ПОЗВОЛЯЛ бы
        # такой статус, а это ровно те же статьи, что и `by_level["L1"]`, только
        # под другим именем. Теперь считается, сколько выводов статус реально
        # получили: потолок и достижение — разные вещи, и путать их в сводке
        # батча значит отчитываться удвоенной строкой.
        "ceiling_confirmed": sum(1 for r in ok if r["max_confidence"] == "CONFIRMED"),
        "conclusions_confirmed": sum((r.get("confidence") or {}).get("CONFIRMED", 0)
                                     for r in ok),
        "conclusions_total": sum(sum((r.get("confidence") or {}).values()) for r in ok),
        "numbers_checked": sum((r["verification"] or {}).get("total", 0) for r in ok),
        "numbers_unverified": sum((r["verification"] or {}).get("unverified", 0)
                                  for r in ok),
        "storage_ok": not STORAGE_FAILURES,
        "storage_failures": STORAGE_FAILURES[:20],
        "papers": rows,
    }
    if STORAGE_FAILURES:
        summary["warning"] = (
            f"{len(STORAGE_FAILURES)} результатов не сохранились в облако — "
            f"прогон нельзя считать состоявшимся, данные потеряны вместе с задачей")
        print("\n" + summary["warning"], file=sys.stderr)
    save(f"{run_id}/summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "papers"},
                     ensure_ascii=False, indent=2))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", help="поисковый запрос Europe PMC")
    ap.add_argument("--dois", help="список DOI через запятую")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--run-id")
    a = ap.parse_args()

    if a.dois:
        dois = [d.strip() for d in a.dois.split(",") if d.strip()]
    elif a.query:
        dois = search_dois(a.query, a.limit)
    else:
        # запрос по умолчанию — класс статей, на котором строился проект
        dois = search_dois('(retrospective cohort) AND cancer AND '
                           '("propensity score" OR confounding) AND PUB_YEAR:2026',
                           a.limit)
    if not dois:
        sys.exit("не найдено ни одного DOI")
    run_batch(dois, a.run_id)


if __name__ == "__main__":
    main()
