"""
Суб-агенты: узкие задачи, которые проигрывают внутри одного большого промпта.

Почему их вообще завели — замер, а не мода на многоагентность. Основной критик
разбирает семь доменов ROBINS-E одним вызовом и делает это прилично, но один
пункт эталона не берёт **никогда**: «GLP-1-группа исходно больнее (Charlson 5+
19.7% против 10.4%), а рака у неё меньше». Ноль из трёх прогонов на живом PDF
(F-43), при том что на подготовленном входе, где эти два числа лежат рядом,
пункт берётся на 1.0 (F-26).

Дело не в способности рассуждать, а в конкуренции задач: одному вызову приходится
одновременно искать факты по семи доменам и складывать из далеко стоящих чисел
противоречие. Второе проигрывает первому — тот же эффект, что с регрессией P4 при
росте объёма входа.

Отсюда правило, по которому сюда попадает задача:
  1) она механическая и её можно описать по шагам;
  2) внутри общего промпта она измеримо проваливается;
  3) её результат проверяем — то есть состоит из чисел, которые верификатор
     потом сверит с документом.

`baseline_comparability` отвечает всем трём. Остальные шесть доменов — нет:
они не механические, и общий критик с ними справляется.
"""
import pathlib

from . import critic

HERE = pathlib.Path(__file__).resolve().parent
PROMPT_BASELINE = (HERE / "prompt_baseline_table.md").read_text()


def baseline_comparability(paper_text: str, pdfs: list = None,
                           model: str = None) -> dict:
    """Суб-агент сопоставимости групп: таблица базовых характеристик и парадокс.

    Возвращает разобранный JSON промпта или `{"error": ...}`. Ошибка суб-агента
    не должна ронять разбор: он усиливает основной, а не заменяет его.
    """
    try:
        r = critic.critique(paper_text, PROMPT_BASELINE,
                            **({"model": model} if model else {}), pdfs=pdfs)
    except Exception as e:                                   # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:200]}
    if r.get("parse_error"):
        return {"error": f"ответ не разобран: {r['parse_error']}"}
    out = r.get("findings") or {}
    out["_usage"] = r.get("usage")
    return out


def merge_into_confounding(findings: dict, baseline: dict) -> dict:
    """Вливает результат суб-агента в домен Confounding основного разбора.

    Два правила, оба про честность отчёта:

    - Находка суб-агента добавляется **к** находкам основного критика, а не
      вместо них: два независимых прохода по одному документу — это две улики,
      и терять одну ради красоты структуры нельзя.
    - Направление домена НЕ переписывается молча. Если суб-агент разошёлся с
      основным критиком, расхождение выносится отдельным полем `disagreement` —
      пусть читатель видит, что два прохода дали разное, вместо того чтобы
      получить одно значение неизвестного происхождения.
    """
    if not isinstance(findings, dict) or not isinstance(baseline, dict):
        return findings
    if baseline.get("error"):
        findings.setdefault("subagents", {})["baseline_comparability"] = baseline
        return findings

    findings.setdefault("subagents", {})["baseline_comparability"] = baseline

    doms = findings.get("domains") or []
    conf = next((d for d in doms if "onfound" in str(d.get("name", "")).lower()), None)
    if conf is None:
        return findings

    contra = baseline.get("contradiction") or {}
    if contra.get("present"):
        conf.setdefault("findings", []).append({
            "title": "Baseline paradox: worse starting position, better outcome",
            "mechanism": "; ".join(contra.get("candidate_mechanisms") or []) or
                         "unmeasured mechanism unaccounted for by the reported analysis",
            "evidence": ([contra.get("statement")] if contra.get("statement") else [])
                        + list(contra.get("supporting_characteristics") or []),
            "found_by": "subagent:baseline_comparability",
        })

    for u in (baseline.get("matching") or {}).get("not_matched_but_imbalanced") or []:
        conf.setdefault("findings", []).append({
            "title": f"Not matched on and imbalanced: {u.get('name')}",
            "mechanism": u.get("why") or "",
            "evidence": [f"{u.get('name')}: exposed {u.get('exposed')} vs "
                         f"unexposed {u.get('unexposed')}"],
            "found_by": "subagent:baseline_comparability",
        })

    sub_dir = baseline.get("direction_of_baseline_imbalance")
    if sub_dir and sub_dir != "no_information" and sub_dir != conf.get("direction"):
        conf["disagreement"] = {
            "main_critic": conf.get("direction"),
            "subagent_baseline": sub_dir,
            "note": "два независимых прохода дали разное направление; "
                    "не сведено намеренно — расхождение само по себе сигнал",
        }
    return findings
