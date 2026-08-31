"""
Обеспеченность на уровне вывода, а не отчёта.

Зачем модуль появился. Сверка чисел давала **одно число на весь разбор**:
«566 найдено, 0 не найдено». Читатель видел итог и не мог сказать, какой из семи
доменов держится на числах из документа, а какой — на общих словах. При этом вся
идея продукта именно в этом различии: он существует, чтобы отделять вывод,
обеспеченный данными, от вывода, который просто звучит убедительно.

Здесь ничего не считает модель. Берётся уже готовый результат слоя 4 и
раскладывается по владельцам: домен, общий вердикт, суб-агент. Плюс проза
проверяется на то же самое — предложение за предложением.

Что модуль **не** утверждает. Он не говорит «этот вывод неверен». Он говорит, чем
именно вывод обеспечен: сколько чисел он приводит, сколько из них нашлось в
документе и сколько несёт вес улики (см. `verify_numbers.evidence_bits` —
число, которое нашлось бы в документе такого размера само собой, весит около
нуля). Вывод без чисел — не ошибка; это вывод, стоящий на общих свойствах
дизайна, и читатель имеет право видеть, что это так.
"""
import re

from .verify_numbers import STRONG_BITS

# Тот же паттерн, что в `pipeline.verify_findings`: число вместе с разделителями
# разрядов, иначе «15 264» рвётся на куски и не сопоставляется с проверенным.
NUM = re.compile(r"\d{1,3}(?:[  ,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+")

# Ветки, где числа не являются цитатами из статьи: это результаты арифметики,
# их проверяет пересчёт функцией, а не поиск по тексту (D-14).
SKIP = {"computed", "arithmetic"}

# Поля, в которых модель пишет прозу. Именно её читает человек и именно за ней
# труднее всего заметить, что чисел под ней нет.
PROSE = ("summary", "direction_justification", "mechanism", "statement",
         "interpretation", "note", "why")


def _norm(raw: str) -> str:
    return raw.replace(",", "").replace(" ", "").replace(" ", "")


def index(claims: list) -> dict:
    """Значение → как оно себя показало. При повторах берётся лучший исход.

    Одно и то же число встречается в разборе под разными метками; для вопроса
    «обеспечено ли это утверждение» важен факт нахождения и вес, а не то, какая
    из меток совпала лучше.
    """
    out = {}
    for c in claims:
        v = _norm(str(c.get("value", "")))
        if not v:
            continue
        bits = c.get("evidence_bits") or 0.0
        found = c.get("status") != "UNVERIFIED"
        prev = out.get(v)
        if prev is None or (found, bits) > (prev["found"], prev["bits"]):
            out[v] = {"found": found, "bits": bits, "status": c.get("status")}
    return out


def _numbers_in(node, acc: list, root: str = ""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in SKIP:
                continue
            _numbers_in(v, acc, root or k)
    elif isinstance(node, list):
        for v in node:
            _numbers_in(v, acc, root)
    elif isinstance(node, str):
        acc.extend(_norm(x) for x in NUM.findall(node))


def _score(numbers: list, idx: dict) -> dict:
    seen = list(dict.fromkeys(numbers))
    known = [idx[n] for n in seen if n in idx]
    return {
        "numbers": len(seen),
        "found": sum(1 for k in known if k["found"]),
        "missing": sum(1 for k in known if not k["found"]),
        "strong": sum(1 for k in known if k["bits"] >= STRONG_BITS),
        # Утверждение обеспечено, когда под ним есть хотя бы одно число, которое
        # не могло попасться случайно. Один такой факт весит больше десяти
        # совпадений вида «12.4», и порог здесь именно поэтому — единица.
        "grounded": any(k["found"] and k["bits"] >= STRONG_BITS for k in known),
    }


def owners(findings: dict, claims: list) -> dict:
    """Разложение обеспеченности по частям разбора."""
    if not findings:
        return {}
    idx = index(claims or [])
    out = {}

    ov = findings.get("overall")
    if ov:
        acc = []; _numbers_in(ov, acc)
        out["overall"] = {"title": "Overall verdict", **_score(acc, idx)}

    for dm in findings.get("domains") or []:
        acc = []; _numbers_in(dm, acc)
        key = f"domain:{dm.get('id')}"
        out[key] = {"title": dm.get("name") or key, **_score(acc, idx)}

    for name, sub in (findings.get("subagents") or {}).items():
        if not isinstance(sub, dict) or sub.get("error"):
            continue
        acc = []; _numbers_in(sub, acc)
        out[f"subagent:{name}"] = {"title": name, **_score(acc, idx)}
    return out


def numbers_by_owner(findings: dict) -> dict:
    """Те же владельцы, но с самими числами, а не со счётчиками.

    Отдельной функцией, а не полем в `owners`, намеренно: числа нужны для одной
    задачи — спросить, есть ли у вывода число с адресом ячейки (`confidence`), —
    и складывать сотню значений в каждую строку отчёта ради этого не стоит.
    Ключи совпадают с `owners`, иначе две части отчёта разъедутся.
    """
    if not findings:
        return {}
    out = {}
    ov = findings.get("overall")
    if ov:
        acc = []; _numbers_in(ov, acc)
        out["overall"] = set(acc)
    for dm in findings.get("domains") or []:
        acc = []; _numbers_in(dm, acc)
        out[f"domain:{dm.get('id')}"] = set(acc)
    for name, sub in (findings.get("subagents") or {}).items():
        if not isinstance(sub, dict) or sub.get("error"):
            continue
        acc = []; _numbers_in(sub, acc)
        out[f"subagent:{name}"] = set(acc)
    return out


# Предложение отделяется от предыдущего только тогда, когда перед точкой стоит
# буква или скобка. В научном тексте «p < 0.001. The» и «Table 2. Baseline» иначе
# рвутся по десятичной точке и по номеру таблицы.
_SENT = re.compile(r"(?<=[a-zA-Z\)])\.\s+(?=[A-Z])")


def statements(findings: dict, claims: list, limit: int = 12) -> list:
    """Предложения прозы, под которыми нет ни одного отличительного числа.

    Это не обвинение, и формулировка в отчёте такая же: предложение, приводящее
    числа, ни одно из которых не нашлось, — повод посмотреть; предложение без
    чисел вовсе — вывод из общих свойств дизайна, что законно, но должно быть
    видно. Никакой второй модели здесь нет намеренно: продукт построен на том,
    что модель не проверяет модель, и проверять её прозу вызовом ещё одной было
    бы отрицанием собственного тезиса.
    """
    if not findings:
        return []
    idx = index(claims or [])
    out = []

    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in SKIP:
                    continue
                if isinstance(v, str) and k in PROSE and len(v) > 40:
                    for sent in _SENT.split(v):
                        nums = [_norm(x) for x in NUM.findall(sent)]
                        if not nums:
                            continue
                        known = [idx[n] for n in dict.fromkeys(nums) if n in idx]
                        if not known:
                            continue
                        best = max((k2["bits"] for k2 in known if k2["found"]),
                                   default=None)
                        if best is not None and best >= STRONG_BITS:
                            continue
                        out.append({
                            "where": f"{where} · {k}" if where else k,
                            "text": sent.strip()[:400],
                            "numbers": list(dict.fromkeys(nums))[:8],
                            "best_bits": best,
                            # Формулировка намеренно описательная. Сказать «не
                            # обосновано» было бы утверждением, которого у нас нет:
                            # HR 1.22 — настоящее число из статьи, просто такой
                            # формы, что документ этого размера содержит её и без
                            # него. Мы сообщаем силу опоры, а не выносим приговор.
                            "verdict": (
                                "none of its numbers appear in the paper at all"
                                if best is None else
                                f"its strongest number is worth {best} bits — a value "
                                f"of that shape sits in this document by chance about "
                                f"{2 ** -best:.0%} of the time, so it does not tell this "
                                f"paper apart from any other"),
                        })
                else:
                    walk(v, where)
        elif isinstance(node, list):
            for v in node:
                walk(v, where)

    ov = findings.get("overall")
    if ov:
        walk(ov, "overall")
    for dm in findings.get("domains") or []:
        walk(dm, dm.get("name") or f"domain {dm.get('id')}")
    return out[:limit]
