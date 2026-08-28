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

from .stats_tool import TwoByTwo

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


def counts_from(computed: dict) -> tuple[dict, str] | tuple[None, None]:
    """Четыре числа таблицы 2×2 и основание, на котором они получены."""
    c = (computed or {}).get("counts") or {}
    keys = ("exposed_events", "exposed_total", "control_events", "control_total")
    vals = {k: _f(c.get(k)) for k in keys}
    if all(v is not None and v > 0 for v in vals.values()):
        return {k: int(v) for k, v in vals.items()}, "reported"

    # Запасной путь: вытащить дроби из строки арифметики. Правильность разбора
    # проверяется тем, что из восстановленных чисел получается тот же ARR, который
    # модель заявила, — иначе это чужие числа и брать их нельзя.
    fr = _FRACTION.findall(str((computed or {}).get("arithmetic") or ""))
    if len(fr) >= 2:
        a, n1 = _f(fr[0][0]), _f(fr[0][1])
        c2, n0 = _f(fr[1][0]), _f(fr[1][1])
        if all(v and v > 0 for v in (a, n1, c2, n0)) and a <= n1 and c2 <= n0:
            stated = _first_number((computed or {}).get("absolute_risk_difference"))
            got = (a / n1 - c2 / n0) * 100
            if stated is None or abs(abs(got) - abs(stated)) <= TOL_PP:
                return ({"exposed_events": int(a), "exposed_total": int(n1),
                         "control_events": int(c2), "control_total": int(n0)}, "parsed")
    return None, None


def recompute(computed: dict) -> dict | None:
    """Пересчитанные значения и сравнение с тем, что заявила модель.

    Возвращает `None`, если пересчитывать нечего: раздела нет или таблица 2×2 не
    собирается. Отсутствие пересчёта — тоже результат, и оно должно быть видно.
    """
    if not computed:
        return None
    counts, basis = counts_from(computed)
    if not counts:
        return {"basis": "none", "note": "the 2×2 table could not be assembled from "
                                         "the model's output, so nothing was recomputed"}
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
        if stated is None:
            return "not_stated"
        return "match" if abs(abs(stated) - abs(fn)) <= tol else "mismatch"

    return {
        "basis": basis,
        "counts": counts,
        "absolute_risk_difference_pp": round(fn_ard_pp, 4),
        "nnt": rep["nnt"],
        "risk_exposed": rep["risk_exposed"],
        "risk_control": rep["risk_control"],
        "rr": rep["rr"],
        "rr_ci95": rep["rr_ci95"],
        "odds_ratio": rep["odds_ratio"],
        "e_value_point": rep["e_value_point"],
        "e_value_ci": rep["e_value_ci"],
        "model_said": {"absolute_risk_difference": stated_ard, "nnt": stated_nnt},
        "agreement": {
            "absolute_risk_difference": verdict(stated_ard, fn_ard_pp, TOL_PP),
            "nnt": verdict(stated_nnt, fn_nnt, TOL_NNT),
        },
        "independent": basis == "reported",
    }
