#!/usr/bin/env python3
"""
Верификатор чисел — реализация решения D-14.

Ни одно число не попадает в отчёт без обратной сверки с первоисточником, кем бы оно ни
было извлечено: структурным парсером, PDF-конвертером или мультимодальной моделью.

Основание — три независимых сигнала за 27.08.2026 (F-27, F-29, prior-art п.4):
любой источник уверенно врёт именно на плотных числах.

  python3 eval/verify_numbers.py --claims claims.json --source статья.xml
"""
import argparse
import json
import re
import sys
import unicodedata

CONTEXT_BEFORE = 260   # заголовок метки бывает далеко до числа
CONTEXT_AFTER = 120

# Числа, которые вообще не стоит проверять как «данные статьи»
TRIVIAL = {"0", "1", "2", "5", "10", "100", "95"}


def normalise(text: str) -> str:
    """Приводит текст к виду, в котором числа сравнимы.

    Единый минус, неразрывные и тонкие пробелы, разделители разрядов.
    Разметка (JATS/HTML) снимается: иначе теги встают между числом и его меткой и
    проверка метки даёт лавину ложных срабатываний — поймано 27.08 на PMC13311639,
    282 ложных из 342.

    Тег обязан начинаться с буквы (или «/»): в научном тексте «<» и «>» — это ещё
    и знаки сравнения, «p < 0.001 … reliability coefficients > 0.8». Паттерн
    `<[^>]+>` считал всё между ними тегом и вырезал вместе с числами: на одной
    статье в PDF так терялась треть текста, 24 488 символов из 72 468, и честно
    процитированные числа получали статус UNVERIFIED (F-41).
    """
    t = unicodedata.normalize("NFKC", text)
    if "<" in t and ">" in t:
        t = re.sub(r"</?[A-Za-z][^<>]*>", " ", t)
    t = t.replace("−", "-").replace("–", "-").replace("—", "-")
    t = re.sub(r"[    ]", " ", t)
    return t


def number_variants(num: str) -> list:
    """Все написания одного числа, встречающиеся в статьях.

    1234.5 -> {1234.5, 1,234.5, 1 234.5, 1234,5}
    """
    num = num.strip()
    neg = num.startswith("-")
    core = num.lstrip("-")
    if "." in core:
        whole, frac = core.split(".", 1)
    else:
        whole, frac = core, None

    groups = []
    if len(whole) > 3:
        rev = whole[::-1]
        groups = [",".join(rev[i:i + 3] for i in range(0, len(rev), 3))[::-1],
                  " ".join(rev[i:i + 3] for i in range(0, len(rev), 3))[::-1]]
    wholes = [whole] + groups

    out = set()
    for w in wholes:
        out.add(w if frac is None else f"{w}.{frac}")
        if frac is not None:
            out.add(f"{w},{frac}")          # десятичная запятая
    return sorted(("-" if neg else "") + v for v in out)


def find_number(num: str, source: str) -> dict:
    """Ищет число в источнике дословно, во всех допустимых написаниях."""
    for v in number_variants(num):
        # границы: число не должно быть частью более длинного числа
        pat = re.compile(r"(?<![\d.,])" + re.escape(v) + r"(?![\d])")
        m = pat.search(source)
        if m:
            # окно широкое: заголовок строки таблицы или подраздела может стоять
            # заметно раньше самого числа (поймано на реальном файле 27.08)
            s = max(0, m.start() - CONTEXT_BEFORE)
            return {"found": True, "as": v,
                    "context": " ".join(source[s:m.end() + CONTEXT_AFTER].split())}
    return {"found": False, "as": None, "context": None}


# Группы сравнения и их написания. Настраивается под предметную область.
DEFAULT_GROUPS = {
    "exposed": ["glp-1", "glp1", "exposed", "экспон", "группа glp"],
    "control": ["контрол", "control", "comparator", "сравнени", "без glp"],
}


def check_label(label: str, context: str, min_words: int = 1) -> bool:
    """Проверяет, что рядом с числом стоит его метка (строка/столбец таблицы).

    D-14 п.3: «19.7» само по себе ничего не значит, значение имеет
    «Charlson >=5, группа GLP-1, 19.7%». Сверяется пара, а не голое значение.
    """
    if not label or not context:
        return False
    words = [w for w in re.findall(r"\w+", label.lower()) if len(w) > 2]
    if not words:
        return False
    ctx = context.lower()
    return sum(1 for w in words if w in ctx) >= min_words


def check_group(claim: dict, context: str, matched: str, groups=None) -> str:
    """Направленная сверка: то ли это число, но у ТОЙ ЛИ группы.

    Защита от ошибки класса F-12 — инверсии направления. В проекте такая ошибка уже
    случалась: «контроль 9.2% против GLP-1 5.9%» было записано наоборот, и испорченный
    эталон снижал оценку модели за ВЕРНЫЙ ответ. Проверка наличия числа её не ловит:
    число-то в источнике есть, просто относится к другой группе.

    Возвращает: "ok" | "mismatch" | "unknown" (группа не заявлена или не распознана).
    """
    groups = groups or DEFAULT_GROUPS
    want = (claim.get("group") or "").lower().strip()
    if not want:
        # группа не задана явно — пробуем вытащить из метки
        lab = (claim.get("label") or "").lower()
        hits = [g for g, al in groups.items() if any(a in lab for a in al)]
        if len(hits) != 1:
            return "unknown"
        want = hits[0]
    if want not in groups:
        return "unknown"

    ctx = context.lower()
    pos = ctx.find(matched.lower())
    if pos < 0:
        return "unknown"

    # ближайший к числу маркер группы решает, чьё это число
    nearest, best = None, 10 ** 9
    for g, aliases in groups.items():
        for a in aliases:
            start = 0
            while (i := ctx.find(a, start)) != -1:
                d = abs(i - pos)
                if d < best:
                    best, nearest = d, g
                start = i + 1
    if nearest is None:
        return "unknown"
    return "ok" if nearest == want else "mismatch"


def verify(claims: list, source_text: str) -> dict:
    src = normalise(source_text)
    results, verified, unverified, unlabelled, inverted = [], 0, 0, 0, 0
    for c in claims:
        num = str(c.get("value", "")).strip()
        label = c.get("label", "")
        if not num:
            continue
        if num in TRIVIAL:
            results.append({**c, "status": "SKIPPED_TRIVIAL"})
            continue
        hit = find_number(num, src)
        grp = "unknown"
        if not hit["found"]:
            status = "UNVERIFIED"      # D-14 п.2: в расчёты не идёт
            unverified += 1
        else:
            grp = check_group(c, hit["context"], hit["as"])
            if grp == "mismatch":
                status = "GROUP_MISMATCH"   # число есть, но у другой группы — инверсия
                inverted += 1
            elif label and not check_label(label, hit["context"]):
                status = "FOUND_LABEL_MISMATCH"
                unlabelled += 1
            else:
                status = "VERIFIED"
                verified += 1
        results.append({**c, "status": status, "group_check": grp,
                        "matched_as": hit["as"], "context": hit["context"]})
    return {
        "summary": {"total": len(results), "verified": verified,
                    "unverified": unverified,
                    "group_mismatch": inverted,
                    "found_but_label_mismatch": unlabelled},
        "claims": results,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", required=True,
                    help='JSON: [{"value":"19.7","label":"Charlson GLP-1"}, ...]')
    ap.add_argument("--source", required=True, help="файл первоисточника (xml/txt/md)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    claims = json.load(open(a.claims))
    source = open(a.source, encoding="utf-8", errors="replace").read()
    res = verify(claims, source)

    if a.json:
        json.dump(res, sys.stdout, ensure_ascii=False, indent=2)
        return
    s = res["summary"]
    print(f"проверено: {s['total']} · подтверждено: {s['verified']} · "
          f"НЕ НАЙДЕНО: {s['unverified']} · ИНВЕРСИЯ ГРУППЫ: {s['group_mismatch']} · "
          f"метка не совпала: {s['found_but_label_mismatch']}\n")
    for c in res["claims"]:
        mark = {"VERIFIED": "✅", "UNVERIFIED": "❌", "GROUP_MISMATCH": "🔄",
                "FOUND_LABEL_MISMATCH": "⚠️ ", "SKIPPED_TRIVIAL": "·"}[c["status"]]
        print(f"{mark} {str(c.get('value')):>10}  {c.get('label','')[:40]:<42} {c['status']}")
        if c["status"] in ("FOUND_LABEL_MISMATCH", "GROUP_MISMATCH"):
            print(f"     контекст: …{(c['context'] or '')[:110]}…")
    if s["unverified"]:
        print(f"\n⚠️  {s['unverified']} чисел не найдено в первоисточнике — "
              f"в отчёт и расчёты они не идут (D-14).")
    if s["group_mismatch"]:
        print(f"🔄 {s['group_mismatch']} чисел относятся к ДРУГОЙ группе — "
              f"инверсия направления, ошибка класса F-12.")


if __name__ == "__main__":
    main()
