"""
Пересчёт заявленной арифметики функцией.

Зачем модуль появился. В отчёте есть раздел, который в интерфейсе называется
«Recomputed by a function, not by the model», и до 29.08 это было неправдой на
движке по умолчанию: `stats_tool` импортировался в `pipeline.py` и не вызывался ни
разу, а показанные ARR и NNT писала сама модель. Функция работала только внутри
ADK-графа, где она — инструмент агента. То есть продукт, который ищет в чужих
статьях утверждения без обеспечения, сам держал ровно такое утверждение на видном
месте. Найдено сверкой ревью с кодом, а не тестом — тест на это теперь есть.

Как устроена честность здесь. Пересчёт стоит ровно столько, сколько стоит его
основание, поэтому основание всегда указывается:

  `reported`  — четыре числа таблицы 2×2 модель выписала отдельным полем `counts`.
                Эти числа есть в документе, их проверяет сверка чисел наравне с
                остальными, и пересчёт из них — независимая проверка вывода.
  `parsed`    — поля `counts` нет, числа восстановлены из строки `arithmetic`,
                которую написала та же модель. Это **проверка на внутреннюю
                непротиворечивость**, а не независимое подтверждение: если модель
                придумала и числа, и результат согласованно, пересчёт этого не
                поймает. Так и написано в отчёте — вместо того чтобы выдавать
                проверку за более сильную, чем она есть.

Формулы не дублируются: считает `stats_tool`, здесь только сбор входа и сравнение.
"""
import re

from .stats_tool import RARE_OUTCOME, TwoByTwo, e_value, rr_from

# Число в тексте модели: «1,986», «3609», «0.09892».
_NUM = r"\d[\d,\s]*(?:\.\d+)?"
_FRACTION = re.compile(rf"({_NUM})\s*/\s*({_NUM})")
# Допуск сравнения. ARR модель округляет до сотых процента, NNT — до десятых,
# поэтому расхождение в пределах округления расхождением не считается.
TOL_PP = 0.02       # процентных пункта
TOL_NNT = 0.15      # человек


def _f(x) -> float | None:
    try:
        return float(str(x).replace(",", "").replace(" ", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _first_number(s) -> float | None:
    """Первое число из строки вида «9.89 percentage points» или «-10.1»."""
    if s is None:
        return None
    m = re.search(rf"-?{_NUM}", str(s))
    return _f(m.group(0)) if m else None


def _impossible(a, n1, c, n0) -> str | None:
    """Почему из этих четырёх чисел таблицы 2×2 не выходит. `None` — выходит.

    Существует потому, что проверка стояла не на том пути. Путь `parsed` —
    слабый, восстановленный из строки, которую написала модель, — требовал
    `a <= n1 and c <= n0`. Путь `reported` — сильный, тот, чей результат отчёт
    называет независимым от арифметики модели, — требовал только `> 0`. То есть
    строгая проверка охраняла запасной вход, а парадный стоял открытым.

    Чего это стоило. `{"exposed_events": 2000, "exposed_total": 100}` проходило
    насквозь и печаталось в разделе «Recomputed by a function, not by the model»:
    риск 2000%, отношение шансов −1.05, разность рисков 1950 процентных пунктов,
    и подпись «independent of the model's arithmetic». Функция, объявленная
    авторитетом над моделью, брала у модели что угодно.

    Отказ — это результат, а не молчание: причина возвращается словами и уходит
    в отчёт, потому что «событий больше, чем людей» — это находка о разборе,
    и прятать её незачем.
    """
    if None in (a, n1, c, n0):
        return "the model did not report all four counts of the 2×2 table"
    if min(a, c) < 0 or min(n1, n0) <= 0:
        return ("the counts are not a table: an arm cannot be empty and events "
                "cannot be negative")
    if a > n1 or c > n0:
        return (f"more events than participants — exposed {a:g}/{n1:g}, "
                f"control {c:g}/{n0:g}, which cannot both be true")
    if a == 0 and c == 0:
        return "no events in either arm — there is no risk to compare"
    return None


def counts_from(computed: dict) -> tuple[dict, str] | tuple[None, str]:
    """Четыре числа таблицы 2×2 и основание, на котором они получены.

    При отказе вторым элементом идёт причина, а не `None`: она попадает в отчёт.
    """
    c = (computed or {}).get("counts") or {}
    keys = ("exposed_events", "exposed_total", "control_events", "control_total")
    vals = {k: _f(c.get(k)) for k in keys}
    reported_given = any(v is not None for v in vals.values())
    why = _impossible(vals["exposed_events"], vals["exposed_total"],
                      vals["control_events"], vals["control_total"])
    if why is None:
        return {k: int(v) for k, v in vals.items()}, "reported"

    # Запасной путь: вытащить дроби из строки арифметики. Правильность разбора
    # проверяется тем, что из восстановленных чисел получается тот же ARR, который
    # модель заявила, — иначе это чужие числа и брать их нельзя.
    fr = _FRACTION.findall(str((computed or {}).get("arithmetic") or ""))
    if len(fr) >= 2:
        a, n1 = _f(fr[0][0]), _f(fr[0][1])
        c2, n0 = _f(fr[1][0]), _f(fr[1][1])
        if _impossible(a, n1, c2, n0) is None:
            stated = _first_number((computed or {}).get("absolute_risk_difference"))
            got = (a / n1 - c2 / n0) * 100
            if stated is None or abs(abs(got) - abs(stated)) <= TOL_PP:
                return ({"exposed_events": int(a), "exposed_total": int(n1),
                         "control_events": int(c2), "control_total": int(n0)}, "parsed")
    # Числа были, но противоречили друг другу — это сильнее, чем «их не было»,
    # и говорится отдельно.
    return None, (why if reported_given else
                  "the 2×2 table could not be assembled from the model's output")


def adjusted_e_value(computed: dict, control_risk: float = None) -> dict | None:
    """E-value от **скорректированной** оценки статьи, а не от сырой таблицы 2×2.

    E-value спрашивает: насколько сильным должен быть неучтённый конфаундер, чтобы
    объяснить наблюдаемую связь. Вопрос осмыслен только про оценку, из которой уже
    убрали учтённые конфаундеры: у сырой оценки убирать ничего не пробовали, и
    говорить об «остатке» нечего. До 29.08 проект считал E-value от сырого RR и
    печатал его рядом со скорректированным HR статьи — арифметика верная, вопрос
    не тот.

    Числа берутся из поля `adjusted_effect`, которое модель выписывает отдельно и
    которое проходит ту же сверку с источником, что и все прочие (D-14).
    """
    a = (computed or {}).get("adjusted_effect") or {}
    val = _first_number(a.get("value"))
    if not val or val <= 0:
        return None
    measure = (a.get("measure") or "HR").strip()
    lo, hi = _first_number(a.get("ci_low")), _first_number(a.get("ci_high"))
    rare = True if control_risk is None else control_risk < RARE_OUTCOME
    try:
        rr = rr_from(measure, val, rare)
        point = round(e_value(rr), 3)
    except (ValueError, ZeroDivisionError):
        return None
    ci = None
    if lo and hi and lo > 0 and hi > 0:
        if lo <= 1.0 <= hi:
            # ДИ накрывает единицу: эффект не отделён от нуля, и E-value границы,
            # ближайшей к нулю, равен единице. Та же логика, что в `stats_tool`.
            ci = 1.0
        else:
            near = hi if val < 1 else lo      # граница, ближняя к единице
            try:
                ci = round(e_value(rr_from(measure, near, rare)), 3)
            except (ValueError, ZeroDivisionError):
                ci = None
    return {
        "basis": "adjusted",
        "measure": measure,
        "reported": val,
        "reported_ci": [lo, hi] if (lo and hi) else None,
        "rare_outcome_assumed": rare,
        "rr_used": round(rr, 4),
        "point": point,
        "ci": ci,
        "note": ("the paper's own adjusted estimate, converted to the risk-ratio scale "
                 + ("directly (the outcome is rare enough that the two coincide)"
                    if rare and control_risk is not None else
                    "by the approximation of VanderWeele 2017/2020 for a common outcome"
                    if not rare else
                    "directly, ASSUMING the outcome is rare — the event rate is not "
                    "known here, and this assumption inflates the E-value rather than "
                    "shrinking it, so treat the figure as an upper bound")),
    }


def recompute(computed: dict) -> dict | None:
    """Пересчитанные значения и сравнение с тем, что заявила модель.

    Возвращает `None`, если пересчитывать нечего: раздела нет или таблица 2×2 не
    собирается. Отсутствие пересчёта — тоже результат, и оно должно быть видно.
    """
    if not computed:
        return None
    counts, basis = counts_from(computed)
    if not counts:
        # Причина приходит из `counts_from` словами. «Таблица не собралась» и
        # «в таблице событий больше, чем людей» — разные сведения о разборе, и
        # второе читателю нужнее: это отказ от заведомо неверных чисел, а не
        # отсутствие данных.
        out = {"basis": "none", "note": f"{basis}, so nothing was recomputed"}
        # Скорректированная оценка может быть выписана и без таблицы 2×2 — тогда
        # доля событий неизвестна и редкость исхода предполагается. Это допущение
        # завышает E-value (см. `stats_tool.RARE_OUTCOME`), поэтому оно помечено
        # флагом и названо словами в примечании, а не спрятано.
        ev = adjusted_e_value(computed)
        if ev:
            out["e_value"] = ev
        return out
    t = TwoByTwo(counts["exposed_events"], counts["exposed_total"],
                 counts["control_events"], counts["control_total"])
    rep = t.report()
    # Знак: `stats_tool.arr` — снижение риска (контроль минус экспозиция), поэтому
    # вред даёт отрицательные ARR и NNT. Разница рисков в отчётах статей пишется в
    # обратную сторону, и именно её сравниваем с заявленным числом по модулю.
    fn_ard_pp = -rep["arr_pp"]
    fn_nnt = rep["nnt"]
    stated_ard = _first_number(computed.get("absolute_risk_difference"))
    stated_nnt = _first_number(computed.get("nnt"))

    def verdict(stated, fn, tol):
        if stated is None or fn is None:
            return "not_stated"
        return "match" if abs(abs(stated) - abs(fn)) <= tol else "mismatch"

    # Знаки ARD и NNT по построению противоположны: разность рисков пишется
    # «экспозиция минус контроль», а NNT — от снижения риска. Читателю выдаётся
    # модуль и слово по знаку, иначе на одной строке стоят −1.02 и +98.1, и надо
    # держать в голове две противоположные конвенции.
    harm = fn_ard_pp > 0
    nnt = rep["nnt"]
    out = {
        "basis": basis,
        "counts": counts,
        "absolute_risk_difference_pp": round(fn_ard_pp, 4),
        "nnt": nnt,
        "nnt_abs": None if nnt is None else abs(nnt),
        "nnt_kind": ("no difference in risk" if nnt is None else
                     "number needed to harm" if harm else "number needed to treat"),
        "adjusted": False,
        "risk_exposed": rep["risk_exposed"],
        "risk_control": rep["risk_control"],
        "rr": rep["rr"],
        "rr_ci95": rep["rr_ci95"],
        "odds_ratio": rep["odds_ratio"],
        # Сырые E-value остаются под прежними именами — их читают сохранённые
        # отчёты, — но ведущим стал `e_value`, посчитанный от скорректированной
        # оценки, когда она есть.
        "e_value_point": rep["e_value_point"],
        "e_value_ci": rep["e_value_ci"],
        "e_value": (adjusted_e_value(computed, rep["risk_control"])
                    or {"basis": "crude", "point": rep["e_value_point"],
                        "ci": rep["e_value_ci"],
                        "note": "computed from the raw 2×2 counts, because the paper's "
                                "adjusted estimate was not reported in a usable form. "
                                "An E-value on an unadjusted estimate does not answer "
                                "the question it is normally asked"}),
        # Оценка сырая: она сложена из четырёх чисел таблицы 2×2 и никаких поправок
        # не знает. Без этой пометки читатель сравнит её с опубликованным
        # скорректированным эффектом и решит, что мы его подтвердили.
        "note": ("crude, unadjusted — computed from the raw 2×2 counts only; the "
                 "paper's own adjusted estimate is a different quantity"),
        "model_said": {"absolute_risk_difference": stated_ard, "nnt": stated_nnt},
        "agreement": {
            "absolute_risk_difference": verdict(stated_ard, fn_ard_pp, TOL_PP),
            "nnt": verdict(stated_nnt, fn_nnt, TOL_NNT),
        },
        "independent": basis == "reported",
    }
    if rep.get("undefined"):
        # Меры, которых для этой таблицы не существует, и почему. Пустая клетка
        # без объяснения читается как сбой инструмента, а это свойство данных.
        out["undefined"] = rep["undefined"]
        out["undefined_note"] = rep["undefined_note"]
    return out
