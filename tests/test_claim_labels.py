#!/usr/bin/env python3
"""
Регрессионный тест: метка числа — самодостаточное описание, а не путь в JSON.

Зачем (F-51). `verify_findings` собирала метку из пути по структуре ответа модели:
«domains · direction_justification», «subagents · baseline_comparability ·
characteristics · exposed». Таких слов в научной статье нет никогда, поэтому
`check_label` почти не находил метку рядом с числом: статуса VERIFIED
удостаивались 3-8 чисел из четырёхсот.

Хуже второе: имя поля схемы `exposed` принималось `check_group` за заявленную
группу, и система объявляла инверсию групп там, где модель аккуратно положила
значение в нужное поле — ложное обвинение в ошибке класса F-12, 20 штук из 191
на живой статье.

Тест не ходит в сеть и не вызывает модель: и разбор, и источник заданы здесь.

  python3 tests/test_claim_labels.py
"""
import pathlib
import statistics
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from truth.pipeline import verify_findings                    # noqa: E402
from truth.verify_numbers import verify                       # noqa: E402

# Разбор в том виде, в каком его отдаёт продовый пайплайн: домены ROBINS-E плюс
# суб-агент сопоставимости. Числа настоящие — McDonald, Appendix Table A2.
FINDINGS = {
    "domains": [{
        "id": 1,
        "name": "Confounding",
        "direction": "away_from_null",
        "direction_justification":
            "Matched controls had a prior breast cancer prevalence of 7.8% versus "
            "5.9% in the GLP-1 arm.",
        "findings": [{
            "title": "Imbalance in prior history of breast cancer",
            "mechanism": "Prior breast cancer raises the risk of a later diagnosis.",
            "evidence": ["Table A2: History of breast cancer was 1,197 (7.8%) in the "
                         "No GLP-1 group versus 907 (5.9%) in the GLP-1 group"],
        }],
    }],
    "subagents": {
        "baseline_comparability": {
            "characteristics": [
                # ключевой случай: значения лежат в полях `exposed` / `unexposed`,
                # и раньше именно эти имена уходили в метку и в проверку группы
                {"name": "History of breast cancer",
                 "exposed": "907 (5.9%)", "unexposed": "1,197 (7.8%)",
                 "table": "Appendix Table A2"},
                {"name": "Charlson comorbidity 5 or higher",
                 "exposed": "3,008 (19.7%)", "unexposed": "10,005 (10.4%)",
                 "table": "Table 1"},
            ],
        },
    },
}

SOURCE = (
    # текст добит до реалистичной длины: окно вокруг числа (380
    # знаков) обязано быть заметно уже документа, иначе «рядом с числом»
    # означает «где угодно» и сила совпадения метки не измеряется (F-63)
    "Methods. Patients were identified in the claims database between January "
    "2019 and December 2024 and followed until the first qualifying event, "
    "death, disenrolment or the end of the study window. Covariates were "
    "measured in the year before the index date and included age, sex, "
    "region, smoking status, body mass index, prior imaging and the number "
    "of outpatient visits. Propensity scores were estimated by logistic "
    "regression and used for one-to-one nearest neighbour matching without "
    "replacement inside a calliper of 0.2 standard deviations. Analyses were "
    "repeated after excluding the first six months of follow-up and after "
    "restricting the cohort to patients with at least two recorded visits. "

    "Appendix Table A2. History of breast cancer: No GLP-1 1,197 (7.8), "
    "GLP-1 907 (5.9). "
    "Table 1. Charlson comorbidity 5+ very high: GLP-1 3,008 (19.7), "
    "No GLP-1 10,005 (10.4). "
    "Matched cohort of 15,264 women in each arm."
)

SCHEMA_WORDS = ("subagents", "domains", "characteristics", "direction_justification",
                "baseline_comparability")

# Второй случай — тот, на котором ложные инверсии и всплыли (PMC13318673).
# Здесь группы названы «Intervention» и «Control», а не «GLP-1» / «No GLP-1»,
# поэтому маркеры не перекрываются подстрокой и `check_group` доходит до
# вердикта. Со старой меткой имя поля `exposed` принимается за заявленную
# группу, ближайшим маркером к числу оказывается «Control group» — и система
# объявляет инверсию, которой нет.
FINDINGS_TWO_ARMS = {
    "subagents": {
        "baseline_comparability": {
            "characteristics": [
                {"name": "Baseline upper limb muscle tone",
                 "exposed": "1.14", "unexposed": "1.29", "table": "Table 2"},
            ],
        },
    },
}

SOURCE_TWO_ARMS = (
    # текст добит до реалистичной длины: окно вокруг числа (380
    # знаков) обязано быть заметно уже документа, иначе «рядом с числом»
    # означает «где угодно» и сила совпадения метки не измеряется (F-63)
    "Methods. Patients were identified in the claims database between January "
    "2019 and December 2024 and followed until the first qualifying event, "
    "death, disenrolment or the end of the study window. Covariates were "
    "measured in the year before the index date and included age, sex, "
    "region, smoking status, body mass index, prior imaging and the number "
    "of outpatient visits. Propensity scores were estimated by logistic "
    "regression and used for one-to-one nearest neighbour matching without "
    "replacement inside a calliper of 0.2 standard deviations. Analyses were "
    "repeated after excluding the first six months of follow-up and after "
    "restricting the cohort to patients with at least two recorded visits. "

    "Table 2. Baseline characteristics by allocation. "
    "Baseline upper limb muscle tone: Control group 1.29, Intervention group 1.14. "
    "Standardized mean difference 0.37 before matching."
)


def old_way(findings, source_text):
    """Как метка собиралась до F-51 — путь по структуре. Для проверки возвратом бага."""
    claims = []

    def walk(node, label="", root=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if not root and k in {"computed", "arithmetic"}:
                    continue
                walk(v, k if not label else f"{label} · {k}", root or k)
        elif isinstance(node, list):
            for v in node:
                walk(v, label, root)
        elif isinstance(node, str):
            for raw in re.findall(
                    r"\d{1,3}(?:[  ,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+", node):
                claims.append({"value": raw.replace(",", "").replace(" ", ""),
                               "label": label or node[:60]})
    walk(findings or {})
    seen, uniq = set(), []
    for c in claims:
        k = (c["value"], c["label"])
        if k not in seen:
            seen.add(k); uniq.append(c)
    return verify(uniq, source_text)


def checks(res, name):
    """Четыре проверки. Возвращает список провалов."""
    s, claims = res["summary"], res["claims"]
    fails = []
    total = s["total"] or 1

    # 1. Метка не состоит из имён полей нашей же схемы
    schema_labels = [c for c in claims
                     if any(w in (c["label"] or "").lower() for w in SCHEMA_WORDS)]
    if schema_labels:
        fails.append(f"метка из имён полей схемы у {len(schema_labels)} чисел, "
                     f"например {schema_labels[0]['label'][:60]!r}")

    # 2. Число из поля `exposed` подписано названием характеристики
    tagged = [c for c in claims if c["value"] == "5.9"]
    if not tagged:
        fails.append("число 5.9 не попало в проверку вовсе")
    elif not any("breast cancer" in (c["label"] or "").lower() for c in tagged):
        fails.append(f"5.9 подписано не характеристикой, а {tagged[0]['label'][:60]!r}")

    # 3. Ложных обвинений в инверсии групп нет: в источнике всё стоит верно
    if s["group_mismatch"]:
        bad = [c for c in claims if c["status"] == "GROUP_MISMATCH"][0]
        fails.append(f"ложная инверсия групп ({s['group_mismatch']} шт.), "
                     f"например {bad['value']} — {bad['label'][:50]!r}")

    # 4. Метка реально описывает своё число.
    #
    # Раньше здесь стояла доля статуса VERIFIED, но после F-63 этот статус зависит
    # от порога, а порог — от того, насколько слова метки редки в конкретном
    # документе. Инвариант, который проверка обязана держать, от порога не зависит:
    # **сила совпадения у самодостаточной метки должна быть заметно выше, чем у
    # метки из имён полей схемы**. Её и меряем.
    scored = [c["label_match"] for c in claims if c.get("label_match") is not None]
    med = statistics.median(scored) if scored else None
    if med is None:
        fails.append("силу совпадения метки не удалось измерить ни на одном числе")
    elif med < 0.3:
        fails.append(f"медианная сила совпадения метки {med:.2f} — метки не "
                     f"описывают свои числа")

    print(f"  {name}: медиана совпадения метки "
          f"{'—' if med is None else f'{med:.2f}'} · VERIFIED {s['verified']}/"
          f"{s['total']} · метка не совпала {s['found_but_label_mismatch']} · "
          f"инверсий {s['group_mismatch']}")
    return fails


def false_inversions(res):
    """Сколько чисел объявлено инверсией групп. В обоих источниках всё верно,
    поэтому любое срабатывание здесь — ложное обвинение (класс F-12)."""
    return res["summary"]["group_mismatch"]


def main():
    print("текущая сборка меток…")
    fails = checks(verify_findings(FINDINGS, SOURCE), "сейчас")
    for f in fails:
        print(f"    ❌ {f}")

    print("\nвозврат дефекта (метка = путь в JSON) — тест обязан упасть…")
    old_fails = checks(old_way(FINDINGS, SOURCE), "до F-51")
    for f in old_fails:
        print(f"    ↳ поймано: {f}")

    # Вторая половина дефекта: имя поля схемы, принятое за название группы.
    print("\nисточник с группами «Intervention / Control» — ложные инверсии…")
    now_inv = false_inversions(verify_findings(FINDINGS_TWO_ARMS, SOURCE_TWO_ARMS))
    old_inv = false_inversions(old_way(FINDINGS_TWO_ARMS, SOURCE_TWO_ARMS))
    print(f"  сейчас: {now_inv} · до F-51: {old_inv}")
    if now_inv:
        fails.append(f"ложных инверсий на двухрукавном источнике: {now_inv}")
        print(f"    ❌ ложных инверсий {now_inv}")
    if not old_inv:
        print("    ⚠️  на возвращённом дефекте инверсий тоже нет — "
              "эта проверка дефект не ловит")
    else:
        print(f"    ↳ поймано: до F-51 система объявляла {old_inv} инверсий на "
              f"верных данных")

    print()
    if fails:
        print("❌ проверки не проходят на текущем коде")
        return 1
    if not old_fails or not old_inv:
        print("❌ тест недостаточен: возвращённый дефект ловится не полностью")
        return 1
    print(f"✅ метки самодостаточны, ложных инверсий нет; на возвращённом дефекте "
          f"тест падает {len(old_fails)} проверками из 4 и ловит {old_inv} "
          f"ложных инверсий")
    return 0


if __name__ == "__main__":
    sys.exit(main())
