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
import collections
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


# Сколько вхождений одного числа осматривать. Числа вроде «2.1» встречаются в
# статье десятками раз; проверять все — дорого и бессмысленно, но проверять одно
# первое — неверно (замер 29.08: 76% найденных чисел встречаются больше одного
# раза, медиана 2, максимум 118, F-63).
MAX_OCCURRENCES = 40


def find_occurrences(num: str, source: str, limit: int = MAX_OCCURRENCES) -> list:
    """Все вхождения числа с их окружением.

    Раньше возвращалось первое, и на нём же строились проверка метки и проверка
    группы. Это значит, что число сверялось с произвольным местом документа: если
    «19.7» стоит в статье шесть раз, метка проверялась у первого вхождения, а
    принадлежало число, возможно, шестому. Отсюда шли и ложные `label mismatch`,
    и невозможность поймать настоящую инверсию.
    """
    out = []
    for v in number_variants(num):
        # границы: число не должно быть частью более длинного числа
        pat = re.compile(r"(?<![\d.,])" + re.escape(v) + r"(?![\d])")
        for m in pat.finditer(source):
            # окно широкое: заголовок строки таблицы или подраздела может стоять
            # заметно раньше самого числа (поймано на реальном файле 27.08)
            s = max(0, m.start() - CONTEXT_BEFORE)
            out.append({"as": v, "start": m.start(),
                        "context": " ".join(source[s:m.end() + CONTEXT_AFTER].split())})
            if len(out) >= limit:
                return out
        if out:
            # написание найдено — другие варианты того же числа не нужны
            break
    return out


def find_number(num: str, source: str) -> dict:
    """Первое вхождение. Оставлено для совместимости с зондами в `eval/`."""
    occ = find_occurrences(num, source, limit=1)
    return ({"found": True, "as": occ[0]["as"], "context": occ[0]["context"]}
            if occ else {"found": False, "as": None, "context": None})


# Группы сравнения и их написания. Настраивается под предметную область.
# Маркеры групп сравнения — регулярные выражения, а не подстроки.
#
# Подстрока «control» ловила «glycemic control», «blood pressure control», «disease
# control» и объявляла числа принадлежащими контрольной группе. Отсюда шли все
# ложные инверсии, какие удалось поднять: 13 случаев на трёх прогонах Cheng, и во
# всех тринадцати метка содержала «glycemic control», а в контексте не было ни
# одного упоминания контрольной группы вообще (замер 29.08, F-56). Записанное в
# F-51 объяснение про три группы оказалось неверным — механизм другой.
#
# Отсюда правило: слово «control» считается названием группы, только если рядом
# стоит слово, обозначающее руку исследования, либо оно употреблено во
# множественном числе («matched controls»). Это не подобранный порог, а свойство
# языка: одиночное «control» в медицинском тексте почти всегда про контроль
# показателя, а не про группу.
ARM = r"(?:group|arm|cohort|patients|subjects|participants|users)"
DEFAULT_GROUPS = {
    "exposed": [rf"\bglp[-\s]?1\b", rf"\bexposed\b", rf"\bexposure\s+{ARM}\b",
                rf"\btreated\s+{ARM}\b", rf"\bintervention\s+{ARM}\b",
                r"\bэкспон", r"\bгруппа\s+glp"],
    "control": [rf"\bcontrol\s+{ARM}\b", r"\bcontrols\b", r"\bcomparator\b",
                rf"\bcomparison\s+{ARM}\b", rf"\bunexposed\b",
                r"\bконтрольн", r"\bгруппа\s+сравнени"],
}
_COMPILED = {}


def _patterns(groups):
    """Компиляция с памятью: verify зовёт это на каждое число."""
    key = id(groups)
    if key not in _COMPILED:
        _COMPILED[key] = {g: [re.compile(a) for a in al] for g, al in groups.items()}
    return _COMPILED[key]


# Слова, которые не несут смысла метки. Список короткий и намеренно не
# «оптимизированный»: без него доля совпадения набивается служебными словами —
# `the`, `and`, `for` проходили прежний фильтр наравне с `Charlson`.
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "was", "were", "are",
    "not", "but", "all", "any", "its", "their", "there", "which", "who", "whom",
    "into", "than", "then", "over", "under", "between", "among", "per", "each",
    "both", "such", "may", "can", "has", "had", "have", "been", "being", "its",
    "after", "before", "during", "within", "without", "about", "also", "more",
    "most", "less", "least", "some", "other", "others", "used", "using", "use",
}


def label_words(label: str) -> list:
    """Значимые слова метки: длиннее двух букв и не служебные."""
    return [w for w in re.findall(r"\w+", (label or "").lower())
            if len(w) > 2 and w not in STOPWORDS]


def word_counts(source: str) -> "collections.Counter":
    """Частоты слов документа. Считаются один раз на разбор."""
    return collections.Counter(re.findall(r"\w+", source.lower()))


# Ширина окна вокруг числа — она же вероятностная мера «слово попалось случайно».
WINDOW = CONTEXT_BEFORE + CONTEXT_AFTER


def word_weight(word: str, counts, source_len: int) -> float:
    """Сколько значит совпадение этого слова: 1 - вероятность попасть случайно.

    Стоп-лист общих слов проблему не решает: замер показал, что метку
    подтверждали `risk` и `group` — слова предметные, но встречающиеся в статье
    сотни раз, так что рядом с любым числом они стоят почти наверняка. Значимость
    слова определяется не списком, а документом: если слово встречается n раз в
    тексте длиной L, вероятность застать его в окне шириной W около n·W/L.
    `Charlson` при пяти вхождениях в 300 000 знаков весит почти 1, `group` при
    двухстах — почти 0. Список остаётся только для служебных слов языка.
    """
    if not source_len:
        return 1.0
    n = counts.get(word, 0)
    if n <= 0:
        return 1.0
    # Вероятность, что хотя бы одно из n вхождений попадёт в окно шириной W:
    # 1 - (1 - W/L)^n. Прямое произведение n·W/L, стоявшее здесь сначала, — его
    # линейное приближение, годное только при W << L. На коротком источнике оно
    # даёт больше единицы и обнуляет вес у всех слов сразу, включая осмысленные:
    # на синтетическом тексте в полторы тысячи знаков измерять становилось нечего.
    q = 1.0 - min(1.0, WINDOW / source_len)
    return max(0.0, q ** n)


def label_overlap(label: str, context: str, counts=None,
                  source_len: int = 0) -> float:
    """Доля значимых слов метки, стоящих рядом с числом. 0.0-1.0.

    Прежняя проверка была булевой и требовала **одного** совпавшего слова длиннее
    двух букв. Замер 29.08 показал, чего она стоит: выдуманные метки вроде «Mean
    age at the index date in the unexposed group» получали статус VERIFIED на
    числе, к которому не имели отношения, а медианная доля реально совпавших слов
    у подтверждённых чисел равнялась 0.23 (F-63). Доля возвращается наружу и
    попадает в отчёт: «сверено» — это утверждение о силе совпадения, и сила должна
    быть видна, а не спрятана в булевом флаге.
    """
    words = label_words(label)
    if not words or not context:
        return 0.0
    ctx = context.lower()
    if counts is None:
        # Без частот документа — равные веса; так зовут пробы и тесты.
        return sum(1 for w in words if w in ctx) / len(words)
    if source_len and WINDOW >= source_len * MAX_WINDOW_SHARE:
        # Окно вокруг числа покрывает заметную часть документа: «рядом с числом»
        # здесь означает «где-то в тексте», и совпадение слов не сообщает ничего.
        # Так бывает на коротком входе `{"text": ...}`. Честный ответ — «мерить
        # нечем», а не ноль: ноль был бы обвинением без оснований.
        return None
    weights = {w: word_weight(w, counts, source_len) for w in set(words)}
    total_w = sum(weights.values())
    if total_w < MIN_INFORMATIVE_WEIGHT:
        # Ни одно слово метки не различает мест документа. Так бывает в двух
        # случаях: метка целиком из слов, которые в статье повсюду, — и документ
        # короче окна вокруг числа, когда «рядом» означает «где угодно». В обоих
        # мера бессмысленна, и правильный ответ — «не берусь судить», а не ноль.
        # Ноль здесь означал бы обвинение в потере метки, которого нет оснований
        # выдвигать (поймано на коротком синтетическом источнике 29.08).
        return None
    return sum(weights[w] for w in set(words) if w in ctx) / total_w


# Порог подтверждения метки — 0.5, и это не граница между «верно» и «неверно».
#
# Замер 29.08 на разборе `10.1136/bmj-2023-076990` (440 чисел; контроль — та же
# метка, приставленная к чужому числу того же отчёта):
#
#   порог   свои проходят   чужие проходят
#   0.3         72%              24%
#   0.4         37%              11%
#   0.5         26%               8%
#   0.7         12%               3%
#
# Медиана у своих 0.36, у чужих 0.12. Распределения **перекрываются**, отношение
# держится около 3× на всех порогах, и точки, где одно кончается и начинается
# другое, не существует. Причина видна из самих данных: метка — это описание,
# сформулированное моделью, а не строка из статьи, и требовать дословного
# совпадения половины её слов с текстом вокруг числа неправомерно.
#
# Отсюда вывод, который важнее самого порога: **совпадение слов не является
# проверкой метки**, оно измеряет силу согласия и не выносит вердикта. Поэтому
# доля совпадения возвращается наружу числом, медиана попадает в сводку, а порог
# 0.5 выбран в сторону строгости и назван честно — при нём мимо проходит 8%
# заведомо чужих меток (F-63).
LABEL_THRESHOLD = 0.5

# Ниже этой суммы весов метка ничего не различает — см. `label_overlap`.
MIN_INFORMATIVE_WEIGHT = 0.05

# Доля документа, начиная с которой окно вокруг числа перестаёт что-либо выделять.
MAX_WINDOW_SHARE = 0.5


def check_label(label: str, context: str, threshold: float = LABEL_THRESHOLD,
                counts=None, source_len: int = 0) -> bool:
    """Стоит ли рядом с числом его метка.

    D-14 п.3: «19.7» само по себе ничего не значит, значение имеет
    «Charlson >=5, группа GLP-1, 19.7%». Сверяется пара, а не голое значение.
    """
    m = label_overlap(label, context, counts, source_len)
    return m is not None and m >= threshold


def check_group(claim: dict, context: str, matched: str, groups=None) -> str:
    """Направленная сверка: то ли это число, но у ТОЙ ЛИ группы.

    Защита от ошибки класса F-12 — инверсии направления. В проекте такая ошибка уже
    случалась: «контроль 9.2% против GLP-1 5.9%» было записано наоборот, и испорченный
    эталон снижал оценку модели за ВЕРНЫЙ ответ. Проверка наличия числа её не ловит:
    число-то в источнике есть, просто относится к другой группе.

    Возвращает: "ok" | "mismatch" | "unknown" (группа не заявлена или не распознана).
    """
    groups = groups or DEFAULT_GROUPS
    pats = _patterns(groups)
    want = (claim.get("group") or "").lower().strip()
    if not want:
        # группа не задана явно — пробуем вытащить из метки
        lab = (claim.get("label") or "").lower()
        hits = [g for g, al in pats.items() if any(a.search(lab) for a in al)]
        if len(hits) != 1:
            return "unknown"
        want = hits[0]
    if want not in groups:
        return "unknown"

    ctx = context.lower()
    pos = ctx.find(matched.lower())
    if pos < 0:
        return "unknown"

    # Ближайший к числу маркер группы решает, чьё это число. Обвинение в инверсии
    # выносится только тогда, когда чужой маркер действительно ближе: если маркеров
    # заявленной группы в контексте нет вовсе, это не доказательство обратного —
    # это отсутствие сведений.
    dist = {}
    for g, aliases in pats.items():
        for a in aliases:
            for m in a.finditer(ctx):
                d = abs(m.start() - pos)
                if d < dist.get(g, 10 ** 9):
                    dist[g] = d
    if not dist:
        return "unknown"
    nearest = min(dist, key=dist.get)
    if nearest == want:
        return "ok"
    if want not in dist:
        # Маркера заявленной группы рядом нет. Спорить не с чем — молчим.
        return "unknown"
    return "mismatch"


def verify(claims: list, source_text: str) -> dict:
    src = normalise(source_text)
    counts, src_len = word_counts(src), len(src)
    results, verified, unverified, unlabelled, inverted = [], 0, 0, 0, 0
    unjudged = 0        # числа, у которых силу метки измерить нечем
    # Покрытие проверки группы. Без него «0 инверсий» читается как «проверили —
    # инверсий нет», а на деле означало «проверка отказалась отвечать»: на разборе
    # BMJ вердикт был вынесен для 0 чисел из 442, потому что маркеры групп заданы
    # под предметную область (F-63).
    group_seen = {"ok": 0, "mismatch": 0, "unknown": 0}
    for c in claims:
        num = str(c.get("value", "")).strip()
        label = c.get("label", "")
        if not num:
            continue
        if num in TRIVIAL:
            results.append({**c, "status": "SKIPPED_TRIVIAL"})
            continue
        occ = find_occurrences(num, src)
        grp = "unknown"
        if not occ:
            status = "UNVERIFIED"      # D-14 п.2: в расчёты не идёт
            unverified += 1
            results.append({**c, "status": status, "group_check": grp,
                            "matched_as": None, "context": None,
                            "label_match": 0.0, "occurrences": 0})
            continue

        # Из всех вхождений числа берётся то, рядом с которым метка совпадает
        # сильнее всего. Проверять первое попавшееся неверно: число живёт в статье
        # в среднем в двух местах, и «не та» позиция давала и ложные обвинения в
        # потере метки, и невозможность увидеть настоящую инверсию (F-63).
        best = max(occ, key=lambda o: (label_overlap(label, o["context"],
                                                     counts, src_len) or -1.0))
        match = label_overlap(label, best["context"], counts, src_len)
        grp = check_group(c, best["context"], best["as"])
        if grp == "mismatch":
            status = "GROUP_MISMATCH"   # число есть, но у другой группы — инверсия
            inverted += 1
        elif match is None:
            # Мера неприменима — число найдено, о метке ничего не сказано.
            status = "VERIFIED"
            unjudged += 1
            verified += 1
        elif label and match < LABEL_THRESHOLD:
            status = "FOUND_LABEL_MISMATCH"
            unlabelled += 1
        else:
            status = "VERIFIED"
            verified += 1
        if grp in group_seen:
            group_seen[grp] += 1
        results.append({**c, "status": status, "group_check": grp,
                        "matched_as": best["as"], "context": best["context"],
                        "label_match": None if match is None else round(match, 3),
                        "occurrences": len(occ)})
    checked = [r for r in results if r["status"] != "SKIPPED_TRIVIAL"]
    matches = sorted(r["label_match"] for r in checked
                     if r["status"] != "UNVERIFIED" and r.get("label_match") is not None)
    decided = group_seen["ok"] + group_seen["mismatch"]
    return {
        "summary": {
            "total": len(results), "verified": verified,
            "unverified": unverified,
            "group_mismatch": inverted,
            "found_but_label_mismatch": unlabelled,
            # Сила совпадения метки, а не только её факт.
            "label_match_median": (round(matches[len(matches) // 2], 3)
                                   if matches else None),
            "label_threshold": LABEL_THRESHOLD,
            # Числа, для которых силу метки измерить нечем: документ короче окна
            # или метка состоит из слов, встречающихся в нём повсюду.
            "label_not_judged": unjudged,
            # Сколько чисел проверка группы реально рассудила. Это число обязано
            # стоять рядом с `group_mismatch`, иначе ноль инверсий выглядит как
            # результат проверки, а не как её отказ.
            "group_checked": decided,
            "group_undecided": group_seen["unknown"],
        },
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
