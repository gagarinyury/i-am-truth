#!/usr/bin/env python3
"""
Eval-харнес проекта «Я Правда».

Задача: воспроизводимо померить, насколько модель самостоятельно воспроизводит
экспертный методологический разбор, и как на это влияет объём поданных данных
(только абстракт против абстракта с таблицами приложения).

Одна команда = один прогон = один файл результата. Результаты не перезаписываются.

Примеры:
  python3 eval/harness.py run   --case mcdonald-2026 --inputs abstract,with_appendix \
                                --models gemini-3.7-flash,gemini-2.5-pro
  python3 eval/harness.py score --run <run_id>
  python3 eval/harness.py report

Зависимости: google-genai, pyyaml (обе уже стоят в системе — см. F-09).
Харнес — исследовательский инструмент, не часть продукта; в подачу не идёт.
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time

import yaml
from google import genai
from google.genai import types

ROOT = pathlib.Path(__file__).resolve().parent
GT_DIR = ROOT / "ground_truth"
IN_DIR = ROOT / "inputs"
PROMPT_DIR = ROOT / "prompts"
RES_DIR = ROOT / "results"

PROJECT = "merci-prod"
LOCATION = "global"          # F-01: линейка >=3.5 живёт здесь и в us-central1
DEFAULT_MODELS = ["gemini-3.7-flash"]
DEFAULT_JUDGE = "gemini-3.7-flash"
MAX_OUTPUT_TOKENS = 32000    # F-02: thinking-токены считаются сюда же, скупиться нельзя
PAUSE_SEC = 6                # F-11: без паузы Vertex отдаёт 429 на серии вызовов


def client() -> genai.Client:
    return genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def strip_html_comments(text: str) -> str:
    """Служебные комментарии во входных файлах модели не показываем."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def load_case(case_id: str) -> dict:
    path = GT_DIR / f"{case_id}.yaml"
    if not path.exists():
        sys.exit(f"нет эталона: {path}")
    return yaml.safe_load(path.read_text())


def load_input(case_id: str, variant: str) -> str:
    path = IN_DIR / f"{case_id}.{variant}.md"
    if not path.exists():
        sys.exit(f"нет входа: {path}")
    return strip_html_comments(path.read_text())


def load_prompt(version: str) -> str:
    path = PROMPT_DIR / f"critic_{version}.md"
    if not path.exists():
        sys.exit(f"нет промпта: {path}")
    return strip_html_comments(path.read_text())


def parse_json_answer(raw: str):
    """Модель просили отдать голый JSON, но иногда приходит в ```-заборе."""
    if raw is None:
        return None, "пустой ответ (возможно, весь бюджет ушёл в thinking)"
    t = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, flags=re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t), None
    except json.JSONDecodeError as e:
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(t[start:end + 1]), None
            except json.JSONDecodeError:
                pass
        return None, f"не JSON: {e}"


# ---------------------------------------------------------------- прогон

def call_model(cli, model: str, system: str, user: str, attempts: int = 5) -> dict:
    """Вызов с backoff: Vertex отдаёт 429 RESOURCE_EXHAUSTED при частых запросах
    (поймано вживую 2026-08-27 — судья упал на четвёртом подряд вызове)."""
    delay = 8
    for i in range(attempts):
        try:
            resp = cli.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    temperature=0,
                ),
            )
            break
        except Exception as e:                          # noqa: BLE001
            transient = any(s in str(e) for s in ("429", "RESOURCE_EXHAUSTED",
                                                  "503", "UNAVAILABLE", "500"))
            if not transient or i == attempts - 1:
                raise
            print(f"    …{type(e).__name__} — пауза {delay}с "
                  f"(попытка {i + 2}/{attempts})", flush=True)
            time.sleep(delay)
            delay *= 2
    else:                                               # pragma: no cover
        raise RuntimeError("исчерпаны попытки")
    u = resp.usage_metadata
    return {
        "text": resp.text,
        "usage": {
            "prompt": getattr(u, "prompt_token_count", None),
            "candidates": getattr(u, "candidates_token_count", None),
            "thoughts": getattr(u, "thoughts_token_count", None),
            "total": getattr(u, "total_token_count", None),
        },
    }


def cmd_run(args):
    cli = client()
    case = load_case(args.case)
    prompt = load_prompt(args.prompt)
    run_id = f"{now_stamp()}-{args.case}"
    out = {
        "run_id": run_id,
        "case": args.case,
        "prompt_version": args.prompt,
        "location": LOCATION,
        "project": PROJECT,
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "results": [],
    }

    for model in args.models.split(","):
        for variant in args.inputs.split(","):
            model, variant = model.strip(), variant.strip()
            print(f"→ {model} × {variant} …", flush=True)
            try:
                r = call_model(cli, model, prompt, load_input(args.case, variant))
            except Exception as e:                      # noqa: BLE001
                print(f"  ОШИБКА: {e}")
                out["results"].append(
                    {"model": model, "input": variant, "error": str(e)}
                )
                continue
            time.sleep(PAUSE_SEC)
            parsed, err = parse_json_answer(r["text"])
            n = len(parsed.get("findings", [])) if parsed else 0
            print(f"  ok · findings={n} · thoughts={r['usage']['thoughts']}"
                  + (f" · ⚠ {err}" if err else ""))
            out["results"].append({
                "model": model,
                "input": variant,
                "raw": r["text"],
                "parsed": parsed,
                "parse_error": err,
                "usage": r["usage"],
            })

    RES_DIR.mkdir(exist_ok=True)
    path = RES_DIR / f"{run_id}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nсохранено: {path}")
    if not args.no_score:
        score_run(path, args.judge)


# ---------------------------------------------------------------- скоринг

JUDGE_SYSTEM = """You are a strict grader. You compare an AI auditor's findings against
a reference expert critique of the same study.

For each reference point, decide how well the auditor's answer covers it:
  1.0 — covered: the auditor identified this problem AND its direction/mechanism matches
        the reference. Wording may differ; substance must match.
  0.5 — partially covered: the auditor gestured at the problem but missed the mechanism,
        the direction, or the supporting numbers.
  0.0 — not covered.

Grade strictly. Two hard rules:
  - If the reference point has a `trap` field and the auditor asserted that trap, the
    score is 0.0, not 0.5 — asserting a plausible but wrong direction is worse than
    silence. Say so in the reason.
  - Superficially naming a textbook bias without connecting it to the study's own data
    is at most 0.5.

Also list any substantive finding the auditor made that is NOT in the reference, and say
whether it is valid given the input data.

Return ONLY a JSON object:
{
  "points": [
    {"id": "P1", "score": 1.0, "reason": "...", "quote": "the auditor's own words you graded"}
  ],
  "extra_findings": [
    {"title": "...", "valid": true, "why": "..."}
  ],
  "classification_correct": true,
  "notes": "..."
}"""


def build_judge_user(case: dict, answer: dict) -> str:
    ref = []
    for p in case["expert_points"]:
        block = [f"### {p['id']} — {p['short']}", p["full"].strip()]
        if p.get("key_numbers"):
            block.append(f"Key numbers: {', '.join(p['key_numbers'])}")
        if p.get("trap"):
            block.append(f"TRAP (wrong answer to catch): {p['trap']}")
        ref.append("\n".join(block))
    return (
        "## REFERENCE EXPERT CRITIQUE\n\n" + "\n\n".join(ref)
        + f"\n\nExpected classification: {case.get('classification_notes','').strip()}"
        + "\n\n## AUDITOR'S ANSWER (JSON)\n\n"
        + json.dumps(answer, ensure_ascii=False, indent=2)
    )


def score_run(path: pathlib.Path, judge_model: str):
    cli = client()
    data = json.loads(path.read_text())
    case = load_case(data["case"])
    total_points = case["expert_points_total"]

    for res in data["results"]:
        if not res.get("parsed"):
            res["score"] = None
            continue
        if res.get("score"):                     # уже оценён — не тратим квоту заново
            continue
        print(f"⚖  сужу {res['model']} × {res['input']} …", flush=True)
        try:
            r = call_model(cli, judge_model, JUDGE_SYSTEM,
                           build_judge_user(case, res["parsed"]))
        except Exception as e:                          # noqa: BLE001
            res["score"] = None
            res["judge_error"] = str(e)[:200]
            print(f"  ⚠ судья упал: {str(e)[:120]}")
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            continue
        time.sleep(PAUSE_SEC)
        verdict, err = parse_json_answer(r["text"])
        if not verdict:
            res["score"] = None
            res["judge_error"] = err
            print(f"  ⚠ судья не отдал JSON: {err}")
            continue
        got = sum(float(p.get("score", 0)) for p in verdict.get("points", []))
        res["judge_model"] = judge_model
        res["verdict"] = verdict
        res["score"] = {"got": got, "of": total_points,
                        "ratio": round(got / total_points, 3)}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"  {got}/{total_points}")

    data["scored"] = dt.datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print_table(data)


def cmd_score(args):
    path = RES_DIR / f"{args.run}.json"
    if not path.exists():
        sys.exit(f"нет прогона: {path}")
    score_run(path, args.judge)


# ---------------------------------------------------------------- отчёт

def print_table(data: dict):
    print(f"\n=== {data['run_id']} · промпт {data['prompt_version']} ===")
    print(f"{'модель':<22} {'вход':<16} {'счёт':>10}  {'думал':>8}")
    print("-" * 60)
    for r in data["results"]:
        s = r.get("score")
        cell = f"{s['got']}/{s['of']}" if s else (r.get("error") and "ОШИБКА" or "—")
        th = (r.get("usage") or {}).get("thoughts") or "—"
        print(f"{r['model']:<22} {r['input']:<16} {cell:>10}  {th:>8}")


def cmd_report(args):
    runs = sorted(RES_DIR.glob("*.json"))
    if not runs:
        sys.exit("прогонов ещё нет")
    for p in runs:
        try:
            print_table(json.loads(p.read_text()))
        except json.JSONDecodeError:
            print(f"битый файл: {p}")


def main():
    ap = argparse.ArgumentParser(description="eval-харнес «Я Правда»")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="прогнать модели по входам и оценить")
    r.add_argument("--case", default="mcdonald-2026")
    r.add_argument("--inputs", default="abstract,with_appendix")
    r.add_argument("--models", default=",".join(DEFAULT_MODELS))
    r.add_argument("--prompt", default="v1")
    r.add_argument("--judge", default=DEFAULT_JUDGE)
    r.add_argument("--no-score", action="store_true")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("score", help="оценить уже сохранённый прогон")
    s.add_argument("--run", required=True)
    s.add_argument("--judge", default=DEFAULT_JUDGE)
    s.set_defaults(func=cmd_score)

    rep = sub.add_parser("report", help="таблица по всем прогонам")
    rep.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
