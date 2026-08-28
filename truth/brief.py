"""
Одностраничный бриф по разбору — Markdown.

Зачем. Отчёт целиком читается минутами, а решение по статье принимается по
нескольким строкам: на чём стоит разбор, что посчитала функция, сколько чисел
сошлось с документом. Судье, рецензенту и соавтору нужен именно такой лист, и он
должен быть файлом, который можно приложить к письму, а не экраном, который надо
прокручивать.

Порядок разделов повторяет порядок экрана и задан тем же правилом: сначала уровень
добытых данных и потолок доверия, потом счёт направлений, потом пересчёт функцией,
потом сверка чисел, и лишь затем сами формулировки. Вывод не должен читаться
раньше того, чем он обеспечен.
"""
import datetime as dt

CEILING = {"CONFIRMED": "confirmed", "SUPPORTED": "supported",
           "INDICATIVE": "indicative"}


def _pct(part, whole):
    return f"{part / whole * 100:.0f}%" if whole else "—"


def render(report: dict) -> str:
    m = report.get("meta") or {}
    lv = report.get("level") or {}
    f = report.get("findings") or {}
    ov = f.get("overall") or {}
    v = report.get("verification") or {}
    rc = report.get("recomputed") or {}
    ds = report.get("direction_summary") or {}
    tb = report.get("tables") or {}

    L = []
    L.append(f"# Methodology audit — {m.get('title') or 'untitled paper'}")
    L.append("")
    meta_line = " · ".join(x for x in (m.get("journal"), m.get("doi"), m.get("pmcid")) if x)
    if meta_line:
        L.append(f"*{meta_line}*")
        L.append("")

    # 1. На чём стоит разбор
    L.append("## What this audit stands on")
    L.append("")
    L.append(f"- **Evidence level {lv.get('level', '?')}** — {lv.get('name', '')}. "
             f"Ceiling of confidence: **{report.get('max_confidence', '?')}**.")
    L.append(f"- Tables parsed: {tb.get('main', 0)} in the paper, "
             f"{tb.get('appendix', 0)} in the appendix.")
    if v:
        L.append(f"- Numbers checked against the source: **{v.get('total', 0)}**, "
                 f"of which {v.get('verified', 0)} matched with their label "
                 f"({_pct(v.get('verified', 0), v.get('total', 0))}), "
                 f"{v.get('found_but_label_mismatch', 0)} were found without it, "
                 f"{v.get('unverified', 0)} were not found at all, "
                 f"{v.get('group_mismatch', 0)} appeared attached to the wrong group.")
    if report.get("caveat"):
        L.append(f"- ⚠️ {report['caveat']}")
    L.append("")

    # 2. Куда тянет смещение — счётом
    if ds:
        c = ds.get("counts") or {}
        L.append("## Where the bias pushes, by count of domains")
        L.append("")
        L.append(f"| away from null | towards null | unpredictable | no information |")
        L.append(f"|---|---|---|---|")
        L.append(f"| {c.get('away_from_null', 0)} | {c.get('towards_null', 0)} | "
                 f"{c.get('unpredictable', 0)} | {c.get('no_information', 0)} |")
        L.append("")
        L.append(f"The model's overall direction is **{ds.get('model_overall') or 'not stated'}**; "
                 f"against its own domains this is **{ds.get('agreement')}**. "
                 f"{ds.get('note', '')}")
        L.append("")

    # 3. Пересчёт функцией
    if rc and rc.get("basis") not in (None, "none"):
        ag = rc.get("agreement") or {}
        ms = rc.get("model_said") or {}
        cn = rc.get("counts") or {}
        L.append("## Recomputed by a function")
        L.append("")
        L.append(f"From {cn.get('exposed_events')}/{cn.get('exposed_total')} against "
                 f"{cn.get('control_events')}/{cn.get('control_total')}:")
        L.append("")
        L.append("| quantity | function | the model said | |")
        L.append("|---|---|---|---|")
        L.append(f"| absolute risk difference | {rc.get('absolute_risk_difference_pp')} pp | "
                 f"{ms.get('absolute_risk_difference')} | {ag.get('absolute_risk_difference')} |")
        L.append(f"| number needed to treat/harm | {rc.get('nnt')} | {ms.get('nnt')} | "
                 f"{ag.get('nnt')} |")
        L.append(f"| risk ratio | {rc.get('rr')} ({', '.join(str(x) for x in rc.get('rr_ci95', []))}) | — | — |")
        L.append(f"| E-value | {rc.get('e_value_point')} (CI {rc.get('e_value_ci')}) | — | — |")
        L.append("")
        L.append("Independent of the model's arithmetic." if rc.get("independent") else
                 "The 2×2 counts were recovered from the model's own arithmetic, so this "
                 "is a consistency check rather than an independent one.")
        L.append("")

    # 4. Вердикт
    if ov:
        L.append("## Verdict")
        L.append("")
        L.append(f"**Overall risk of bias: {ov.get('risk', '?')}.** "
                 f"Threatens the paper's conclusions: {ov.get('threatens_conclusions')}.")
        if ov.get("summary"):
            L.append("")
            L.append(ov["summary"])
        if f.get("classification"):
            L.append("")
            L.append(f"Classification: **{f['classification']}**.")
        L.append("")

    # 5. Домены
    doms = f.get("domains") or []
    if doms:
        L.append("## Seven ROBINS-E domains")
        L.append("")
        L.append("| # | domain | risk | direction |")
        L.append("|---|---|---|---|")
        for dm in doms:
            L.append(f"| {dm.get('id', '')} | {dm.get('name', '')} | "
                     f"{dm.get('risk', '')} | {dm.get('direction', '')} |")
        L.append("")

    unres = f.get("unresolvable_without_more_data") or []
    if unres:
        L.append("## Cannot be settled without more data")
        L.append("")
        for x in unres[:6]:
            L.append(f"- {x}")
        L.append("")

    L.append("---")
    L.append("")
    aid = report.get("audit_id")
    L.append(f"Produced by I Am Truth · engine `{report.get('engine', '?')}` · "
             f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"
             + (f" · full report: `/audits/{aid}`" if aid else ""))
    return "\n".join(L)
