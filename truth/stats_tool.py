#!/usr/bin/env python3
"""
Stats Tool — арифметика эпидемиологических мер.

Правило проекта: LLM не считает числа. Числа считает эта функция, модель их только
интерпретирует (D-14 и правило про function-call из STATE.md).

Формулы взяты из первоисточников, не выведены заново:
  E-value — VanderWeele TJ, Ding P. «Sensitivity Analysis in Observational Research:
  Introducing the E-Value». Ann Intern Med 2017;167(4):268-274.
      E = RR + sqrt(RR * (RR - 1)),  для RR < 1 сначала берётся 1/RR
  Реализация сверена с известным результатом проекта: RR 0.700 -> E 2.21,
  граница CI 0.822 -> E 1.73 (наш QBA, посчитанный вручную ранее).
"""
import math
from dataclasses import dataclass, asdict


def e_value(rr: float) -> float:
    """E-value для отношения рисков. VanderWeele & Ding 2017."""
    if rr <= 0:
        raise ValueError("RR должен быть положительным")
    r = rr if rr >= 1 else 1.0 / rr
    return r + math.sqrt(r * (r - 1.0))


@dataclass
class TwoByTwo:
    """Контингентная таблица: события и размеры групп."""
    exposed_events: int
    exposed_total: int
    control_events: int
    control_total: int

    def risks(self):
        return (self.exposed_events / self.exposed_total,
                self.control_events / self.control_total)

    def rr(self) -> float:
        re, rc = self.risks()
        return re / rc

    def odds_ratio(self) -> float:
        a, b = self.exposed_events, self.exposed_total - self.exposed_events
        c, d = self.control_events, self.control_total - self.control_events
        return (a * d) / (b * c)

    def arr(self) -> float:
        """Абсолютное снижение риска, в долях (не в процентах)."""
        re, rc = self.risks()
        return rc - re

    def nnt(self) -> float | None:
        """`None`, если разницы рисков нет.

        Раньше здесь стояло `inf`, и оно доходило до FastAPI, который на
        сериализации падал с 500 — после трёх вызовов Vertex и полного разбора.
        В хранилище при этом ложился литерал `Infinity`, невалидный JSON для всех,
        кроме Python. «Лечить бессмысленно» — это результат, и выражается он
        отсутствием числа, а не бесконечностью."""
        a = self.arr()
        return None if a == 0 else 1.0 / a

    def rr_ci(self, level: float = 0.95):
        """ДИ для RR методом Katz (лог-нормальное приближение)."""
        a, n1 = self.exposed_events, self.exposed_total
        c, n0 = self.control_events, self.control_total
        if a == 0 or c == 0:
            return (float("nan"), float("nan"))
        se = math.sqrt(1 / a - 1 / n1 + 1 / c - 1 / n0)
        z = 1.959963985 if abs(level - 0.95) < 1e-9 else _z(level)
        lr = math.log(self.rr())
        return (math.exp(lr - z * se), math.exp(lr + z * se))

    def report(self) -> dict:
        re, rc = self.risks()
        lo, hi = self.rr_ci()
        return {
            "risk_exposed": round(re, 6),
            "risk_control": round(rc, 6),
            "rr": round(self.rr(), 4),
            "rr_ci95": [round(lo, 4), round(hi, 4)],
            "odds_ratio": round(self.odds_ratio(), 4),
            "arr_pp": round(self.arr() * 100, 4),
            "nnt": None if self.nnt() is None else round(self.nnt(), 1),
            "e_value_point": round(e_value(self.rr()), 3),
            # ДИ, накрывающий единицу, означает, что эффект вообще не отделён от
            # нуля: по VanderWeele & Ding E-value для такого интервала равен 1 —
            # достаточно сколь угодно слабого конфаундера. Подставлять границу в
            # формулу нельзя, она вернёт >1 и заявит устойчивость, которой нет.
            "e_value_ci": (1.0 if lo <= 1.0 <= hi else
                           round(e_value(hi if self.rr() < 1 else lo), 3)),
        }


def _z(level: float) -> float:
    """Обратная функция нормального распределения (Acklam), для нестандартных уровней."""
    p = 1 - (1 - level) / 2
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q, r = p - 0.5, (p - 0.5) ** 2
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


if __name__ == "__main__":
    print("=== сверка с известными результатами проекта ===\n")
    # 1. E-value: наш QBA дал 2.21 (точка) и 1.73 (граница CI) при RR 0.700 (0.596-0.822)
    for rr, expected, label in ((0.700, 2.21, "точка"), (0.822, 1.73, "граница CI")):
        got = e_value(rr)
        ok = abs(got - expected) < 0.005
        print(f"{'✅' if ok else '❌'} E-value RR={rr} ({label}): {got:.3f}, ожидалось {expected}")
    # 2. VanderWeele & Ding приводят пример: RR 3.9 -> E 7.26
    got = e_value(3.9)
    print(f"{'✅' if abs(got-7.26)<0.005 else '❌'} E-value RR=3.9 (пример из статьи): {got:.3f}, ожидалось 7.26")

    print("\n=== McDonald et al., matched-когорта ===")
    t = TwoByTwo(exposed_events=247, exposed_total=15264,
                 control_events=353, control_total=15264)
    for k, v in t.report().items():
        print(f"  {k:<16} {v}")
    print("\n  эталон проекта: ARR 0.69 п.п., NNT ~145, OR 0.695 (0.590-0.819)")
