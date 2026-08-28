"""
Слой 4b — attribution через готовый API Google, а не свой.

`checkGrounding` (Vertex AI Search / Agent Builder, Discovery Engine API) принимает
текст-кандидат и набор фактов и возвращает, какие утверждения какими фактами
подтверждены: `supportScore` 0-1, границы каждого claim в байтах, индексы
процитированных фактов, при `enableClaimLevelScore` — балл на каждый claim.

**Роль слоя ограничена замером, а не общими соображениями.** Проба 28.08 на
приложенной таблице McDonald, порог по умолчанию 0.6:

| Кандидат | supportScore | вывод |
|---|---|---|
| верное утверждение с двумя числами | 0.979 | подтверждает |
| выдуманные числа (9.9 / 3.1) | 0.203 | **ловит** |
| инверсия групп: 7.8 и 5.9 переставлены | 0.662 | пропускает |
| инверсия групп: 19.7 и 10.4 переставлены | 0.854 | **пропускает** |
| верные числа + вывод «значит, группа больнее» | 0.157 | **ложно обвиняет** |

Отсюда разделение труда, и оно не подлежит упрощению:

* **выдуманное число** — ловит `checkGrounding`, и делает это лучше поиска по
  строке, потому что видит смысл, а не подстроку;
* **инверсия групп** (класс F-12, ради которого слой вообще заведён) — остаётся
  за `verify_numbers.check_group`. Семантическое следование её не ловит:
  переставленные значения по-прежнему «в целом соответствуют» таблице;
* **интерпретация рядом с числом** («so the arm was sicker») роняет балл всего
  утверждения, поэтому низкий `supportScore` сам по себе не улика.

Поэтому результат этого слоя идёт в отчёт **рядом** с нашей сверкой, а не вместо
неё, и ни одно число не отвергается по одному только `supportScore`.

Документация: https://docs.cloud.google.com/generative-ai-app-builder/docs/check-grounding
Лимиты оттуда же: answer_candidate ≤ 4096 токенов, до 200 фактов, каждый ≤ 10 000
символов.
"""
import json
import os
import urllib.error
import urllib.request

import google.auth
import google.auth.transport.requests

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "merci-prod")
ENDPOINT = ("https://discoveryengine.googleapis.com/v1/projects/{p}/locations/global"
            "/groundingConfigs/default_grounding_config:check")

MAX_FACTS = 200            # лимит API
MAX_FACT_CHARS = 10_000    # лимит API
MAX_CANDIDATE_CHARS = 12_000   # ~4096 токенов с запасом


def _token() -> str:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def row_to_fact(caption: str, columns: list, row: list) -> str:
    """Строка таблицы → самодостаточное предложение с заголовками столбцов.

    **Замерено, а не предположено.** Один и тот же claim («SMD базового тонуса
    до матчинга = 0.37») против одного и того же значения:

    | Факт | supportScore | цитата |
    |---|---|---|
    | `Baseline upper limb tone \\| 0.37 \\| 0.07` | 0.022 | нет |
    | `Table 2… Characteristic: Baseline upper limb tone. SMD before matching: 0.37…` | **0.982** | есть |

    Голая строка таблицы не говорит, ЧЕМ является 0.37 — шапка потеряна при
    склейке. Тот же принцип самодостаточности, что и у claim (SAFE): проверять
    можно только то, что понятно вне своего контекста.
    """
    head = [c.strip() for c in (columns or [])]
    cells = [str(c).strip() for c in (row or [])]
    if not cells:
        return ""
    label = cells[0]
    pairs = []
    for i, val in enumerate(cells[1:], start=1):
        if not val:
            continue
        col = head[i] if i < len(head) else f"column {i}"
        pairs.append(f"{col}: {val}")
    body = ". ".join(pairs) if pairs else " | ".join(cells)
    return f"{caption or 'Table'}. {label}. {body}."[:MAX_FACT_CHARS]


def facts_from_tables(gathered: dict) -> list:
    """Разобранные таблицы → самодостаточные факты, по одной строке на факт."""
    facts = []
    for t in (gathered.get("appendix_tables") or []) + (gathered.get("jats_tables") or []):
        cap = (t.get("caption") or t.get("label") or "Table").strip()
        cols = t.get("columns") or []
        for row in (t.get("rows") or []):
            if not any(ch.isdigit() for ch in " ".join(map(str, row))):
                continue
            f = row_to_fact(cap, cols, row)
            if f:
                facts.append(f)
    return facts


def facts_from(source_text: str, tables_text: str = "") -> list:
    """Источник → факты. Таблицы идут первыми и по одной строке на факт.

    Дробление осознанное: документация прямо советует «breaking large facts into
    smaller facts», а строка таблицы — естественная единица, у которой есть своя
    подпись. Сплошной текст режется по абзацам до лимита в 10 000 символов.

    Для таблиц предпочтительнее `facts_from_tables` — она сохраняет заголовки
    столбцов, без которых строка не проверяема (см. `row_to_fact`).
    """
    facts = []
    for line in (tables_text or "").split("\n"):
        line = line.strip()
        if line and any(ch.isdigit() for ch in line):
            facts.append(line[:MAX_FACT_CHARS])
    chunk = []
    size = 0
    for para in (source_text or "").split("\n"):
        para = para.strip()
        if not para:
            continue
        if size + len(para) > MAX_FACT_CHARS and chunk:
            facts.append("\n".join(chunk)[:MAX_FACT_CHARS])
            chunk, size = [], 0
        chunk.append(para)
        size += len(para)
    if chunk:
        facts.append("\n".join(chunk)[:MAX_FACT_CHARS])
    return facts[:MAX_FACTS]


def check(answer_candidate: str, facts: list, threshold: float = 0.6) -> dict:
    """Один вызов API. Ошибка сети не должна ронять аудит — она возвращается полем."""
    if not answer_candidate or not facts:
        return {"available": False, "error": "нет кандидата или фактов"}
    body = json.dumps({
        "answerCandidate": answer_candidate[:MAX_CANDIDATE_CHARS],
        "facts": [{"factText": f} for f in facts[:MAX_FACTS]],
        "groundingSpec": {"citationThreshold": threshold,
                          "enableClaimLevelScore": True},
    }).encode()
    req = urllib.request.Request(
        ENDPOINT.format(p=PROJECT), data=body,
        headers={"Authorization": f"Bearer {_token()}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:                      # noqa: BLE001
        return {"available": False, "error": f"HTTP {e.code}: {e.read()[:200]!r}"}
    except Exception as e:                                   # noqa: BLE001
        return {"available": False, "error": f"{type(e).__name__}: {e}"}

    claims = d.get("claims") or []
    unsupported = [
        {"claim": c.get("claimText", "")[:200], "score": c.get("score")}
        for c in claims
        if c.get("groundingCheckRequired") and not c.get("citationIndices")
    ]
    return {
        "available": True,
        "support_score": d.get("supportScore"),
        "claims_total": len(claims),
        "claims_cited": sum(1 for c in claims if c.get("citationIndices")),
        "unsupported": unsupported[:20],
        "facts_sent": min(len(facts), MAX_FACTS),
        # Напоминание в самом ответе: балл не является приговором — см. докстроку.
        "note": ("supportScore не улика сам по себе: инверсия групп его почти не "
                 "снижает, а вывод рядом с числом снижает сильно. Инверсию ловит "
                 "check_group, а не этот слой."),
    }


def check_findings(findings: dict, source_text: str, tables_text: str = "") -> dict:
    """Проверяет обоснования из отчёта: доказательства и текстовые формулировки."""
    parts = []

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and any(ch.isdigit() for ch in node):
            # берём только то, что претендует на цитату из статьи
            if key in ("evidence", "direction_justification", "statement",
                       "supporting_characteristics", "reported_result"):
                parts.append(node.strip())

    walk(findings or {})
    if not parts:
        return {"available": False, "error": "в отчёте нет цитируемых обоснований"}
    return check("\n".join(parts), facts_from(source_text, tables_text))
