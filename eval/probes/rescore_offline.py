#!/usr/bin/env python3
"""
Пересчёт слоя 4 на сохранённых разборах — без единого вызова модели.

Зачем. Правки в верификаторе меняют числа, которыми описан продукт, и проверять их
новым прогоном пайплайна значит платить за три вызова Vertex ради вопроса, который
модели не задаётся вовсе: «как повёл бы себя верификатор на том же разборе». Здесь
берётся `findings` из сохранённого прогона `eval/results/bench-*.json`, статья
добывается заново (Europe PMC или PDF с диска), и слой 4 гоняется как есть.

Стоимость — сеть, ноль токенов. Именно этим замерены знак числа и покрытие
проверки групп на 31.08.

  python3 eval/probes/rescore_offline.py                 # оба эталона
  python3 eval/probes/rescore_offline.py --case cheng-2024
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from truth import pipeline                                       # noqa: E402

CASES = {
    "mcdonald-2026": {"doi": "10.1200/OP-26-00485", "pdf": "mcdonald.pdf"},
    "cheng-2024": {"doi": "10.1111/1753-0407.70013", "pdf": None},
}


def latest_findings(case: str) -> dict:
    """Разбор из самого свежего сохранённого прогона этого эталона."""
    files = sorted((ROOT / "eval" / "results").glob(f"bench-*-{case}.json"))
    if not files:
        sys.exit(f"нет сохранённых прогонов для {case}")
    d = json.loads(files[-1].read_text())
    return files[-1].name, (d.get("report") or {}).get("findings") or {}


def find_pdf(name: str) -> bytes:
    for d in (ROOT / "eval" / "pdf", pathlib.Path.home() / "Downloads"):
        if (d / name).exists():
            return (d / name).read_bytes()
    sys.exit(f"нужен {name} в eval/pdf/ — в репозиторий он не кладётся (копирайт)")


def run(case: str) -> dict:
    cfg = CASES[case]
    src_name, findings = latest_findings(case)
    uploads = [(cfg["pdf"], find_pdf(cfg["pdf"]))] if cfg["pdf"] else None
    g = pipeline.gather(doi=cfg["doi"], uploads=uploads)
    tables = list(g["appendix_tables"]) + list(g["jats_tables"])
    src = g["source_text"] + "\n\n" + pipeline.tables_as_text(g, limit=None, rows=None)
    res = pipeline.verify_findings(findings, src, tables)
    s = res["summary"]
    print(f"\n{case}  (разбор из {src_name}, уровень {g['level']['level']})")
    for k in ("total", "found", "unverified", "sign_mismatch", "group_mismatch",
              "group_checked", "group_undecided", "strong", "in_cell",
              "in_table_address_unmatched"):
        print(f"  {k:<28} {s.get(k)}")
    flips = [c for c in res["claims"] if c["status"] == "SIGN_MISMATCH"]
    for c in flips[:8]:
        print(f"    знак: {c['value']:>8}  «{(c.get('label') or '')[:60]}»")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=list(CASES))
    a = ap.parse_args()
    for case in ([a.case] if a.case else list(CASES)):
        try:
            run(case)
        except SystemExit as e:
            print(f"{case}: пропущен — {e}")


if __name__ == "__main__":
    main()
