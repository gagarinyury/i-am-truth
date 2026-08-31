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

def _pct(part, whole):
    return f"{part / whole * 100:.0f}%" if whole else "—"


def _num(x):
    """Число или прочерк. `None` в таблице — это «величина не определена для этой
    таблицы», и «—» говорит это, тогда как напечатанное `None` выглядит поломкой."""
    return "—" if x is None else str(x)


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
             f"No conclusion here may be rated above **{report.get('max_confidence', '?')}**: "
             f"the ceiling is applied to every part of the audit below, and where it "
             f"actually bites, the table says so.")
    L.append(f"- Tables parsed: {tb.get('main', 0)} in the paper, "
             f"{tb.get('appendix', 0)} in the appendix.")
    if v:
        total_n = v.get("total", 0)
        missing = v.get("unverified", 0)
        found = v.get("found", total_n - missing)
        L.append(f"- Numbers taken from the report and searched for in the paper: "
                 f"**{total_n}** — {found} found, **{missing} not found at all**.")
        # Ведущее число — не «сколько найдено», а «сколько найденного что-то
        # значит». В документе на триста тысяч знаков выдуманное число вида «12.4»
        # находится примерно в трети случаев, поэтому «найдено всё» сообщает о
        # размере документа, а не о статье.
        if v.get("strong") is not None:
            L.append(f"- Of those found, **{v['strong']}** could not have turned up by "
                     f"chance: a value of that shape would be present in a document "
                     f"this size less than once in "
                     f"{2 ** int(v.get('strong_bits_threshold', 6)):.0f} times. "
                     f"The median find is worth {v.get('evidence_bits_median')} bits. "
                     f"That weight establishes only that the value came from this paper "
                     f"rather than from the shape of any paper — whether it means what "
                     f"the audit says it means is a separate question, answered by the "
                     f"cell address and the group check, which cover far less.")
        if v.get("in_cell") is not None:
            L.append(f"- **{v['in_cell']}** of them were located in a specific table "
                     f"cell whose row and column agree with what the model said the "
                     f"number was — an address, not a resemblance. "
                     f"{v.get('in_table_address_unmatched', 0)} more sit somewhere in a "
                     f"parsed table but could not be pinned to one cell, because the "
                     f"model's label for them is a whole sentence quoting several "
                     f"numbers at once.")
        L.append(f"- Of those found, {v.get('verified', 0)} also had at least "
                 f"{int((v.get('label_threshold') or 0.5) * 100)}% of the words of "
                 f"their description standing beside them"
                 + (f" (median agreement {v['label_match_median']})"
                    if v.get("label_match_median") is not None else "")
                 + ". Wording is not identity: this is a strength, not a verdict.")
        gd, gu = v.get("group_checked"), v.get("group_undecided")
        if gd is not None:
            L.append(f"- The group check ruled on {gd} of {gd + (gu or 0)} numbers"
                     + ("; it stayed silent on the rest rather than guessing, so the "
                        "count of inversions below says nothing about them."
                        if gu else ".")
                     + f" Inversions found: {v.get('group_mismatch', 0)}.")
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
        WORDS = {
            "consistent": "which matches where its own domains point",
            "contradicts": "which is the opposite of where its own domains point",
            "unsupported": "and the domains favour neither way",
            "not_comparable": "which is not a direction, so there is nothing to compare",
            "not_stated": "— it gave none",
        }
        L.append(f"The model's overall direction is "
                 f"**{ds.get('model_overall') or 'not stated'}**, "
                 f"{WORDS.get(ds.get('agreement'), '')}. {ds.get('note', '')}")
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
        L.append(f"| absolute risk difference | {_num(rc.get('absolute_risk_difference_pp'))} pp | "
                 f"{_num(ms.get('absolute_risk_difference'))} | "
                 f"{ag.get('absolute_risk_difference')} |")
        L.append(f"| {rc.get('nnt_kind', 'number needed to treat')} | "
                 f"{_num(rc.get('nnt_abs', rc.get('nnt')))} | {_num(ms.get('nnt'))} | "
                 f"{ag.get('nnt')} |")
        ci = rc.get("rr_ci95") or []
        L.append(f"| risk ratio | {_num(rc.get('rr'))}"
                 + (f" ({', '.join(str(x) for x in ci)})" if ci else "") + " | — | — |")
        ev = rc.get("e_value") or {}
        if ev.get("basis") == "adjusted":
            L.append(f"| E-value (on the paper's adjusted {ev.get('measure')}"
                     f" {ev.get('reported')}) | {_num(ev.get('point'))} "
                     f"(CI {_num(ev.get('ci'))}) | — | — |")
        else:
            L.append(f"| E-value | {_num(rc.get('e_value_point'))} "
                     f"(CI {_num(rc.get('e_value_ci'))}) | — | — |")
        L.append("")
        if rc.get("undefined"):
            L.append(f"*Not defined for this table: {', '.join(rc['undefined'])} — "
                     f"{rc.get('undefined_note', '')}.*")
            L.append("")
        if rc.get("note"):
            L.append(f"*{rc['note']}.*")
            L.append("")
        if ev.get("note"):
            # На каком числе стоит E-value — не деталь. От сырой оценки он
            # отвечает не на тот вопрос, ради которого его приводят.
            L.append(f"*E-value: {ev['note']}"
                     + (f"; the crude 2×2 would have given {rc.get('e_value_point')} "
                        f"instead" if ev.get("basis") == "adjusted"
                        and rc.get("e_value_point") else "") + ".*")
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

    # 4b. Чем обеспечен каждый вывод по отдельности
    gr = report.get("grounding") or {}
    if gr:
        backed = sum(1 for x in gr.values() if x.get("grounded"))
        L.append("## What each conclusion rests on")
        L.append("")
        L.append(f"{backed} of {len(gr)} parts of this audit cite at least one number "
                 f"distinctive enough that a document this size would not hold it by "
                 f"chance. The rest may still be right — they rest on general properties "
                 f"of the design, and that is a different kind of claim.")
        L.append("")
        conf = report.get("confidence") or {}
        cs = report.get("confidence_summary") or {}
        if cs:
            c = cs.get("counts") or {}
            L.append(f"Status, by the rule in `truth/confidence.py`: "
                     + ", ".join(f"**{c.get(k, 0)} {k.lower()}**"
                                 for k in ("CONFIRMED", "SUPPORTED", "INDICATIVE",
                                           "UNVERIFIED"))
                     + (f". {cs['capped_by_level']} of them are held below what their "
                        f"evidence would support, because only "
                        f"{lv.get('level')} of the paper was retrieved."
                        if cs.get("capped_by_level") else ".")
                     + f" {cs.get('note', '')}")
            L.append("")
        L.append("| part | status | numbers | found | not found | carrying weight |")
        L.append("|---|---|---|---|---|---|")
        for k, x in gr.items():
            st = (conf.get(k) or {}).get("status", "—")
            if (conf.get(k) or {}).get("capped_from"):
                st += f" (capped from {conf[k]['capped_from']})"
            L.append(f"| {x.get('title', k)} | {st} | {x.get('numbers', 0)} | "
                     f"{x.get('found', 0)} | {x.get('missing', 0)} | "
                     f"{x.get('strong', 0)} |")
        L.append("")

    weak = report.get("weakly_grounded_statements") or []
    if weak:
        L.append("## Sentences whose numbers do not tell this paper apart")
        L.append("")
        L.append("Found by walking the audit's own prose and looking up every number in "
                 "it — no second model was asked. A pointer, not a verdict.")
        L.append("")
        for w in weak[:6]:
            L.append(f"- *{w.get('text', '')[:220]}*")
            L.append(f"  — {w.get('where', '')}: {w.get('verdict', '')}")
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
