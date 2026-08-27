"""
Layer 0 — оркестратор. Сквозной путь от DOI или текста до отчёта.

Порядок и обоснования:
  1. retrieval  — что вообще удалось достать, отсюда уровень доказательности (D-13)
  2. extract    — таблицы из JATS и из .docx-приложений (D-11, D-12)
  3. critique   — разбор моделью, промпт один и тот же независимо от уровня
  4. verify     — обратная сверка каждого числа с первоисточником (D-14)
  5. stats      — пересчёт RR/ARR/NNT/E-value функцией, не моделью
  6. report     — сборка с явным указанием уровня и того, чего не хватило

Ключевое: уровень доказательности не прячется, а выносится в отчёт. На L2 и L3
находки не могут получить статус CONFIRMED — это следует из замера F-26, где
ошибка направления confounding держалась на обоих нижних уровнях.
"""
import io
import os
import re
import tempfile
import zipfile

from . import critic, retrieval
from .docx_tables import extract as extract_docx
from .jats_tables import parse_tables, to_claims
from .stats_tool import TwoByTwo
from .verify_numbers import verify


def _supplementary_text(blob: bytes) -> str:
    """Плоский текст всех .docx-приложений — нужен для сверки чисел.

    Без него верификатор обвиняет модель в выдумывании чисел, которые она честно
    процитировала из eTable. Поймано на первом сквозном прогоне 27.08: четыре
    значения HR были помечены UNVERIFIED, а на деле лежали в приложении.
    Обвинить в галлюцинации того, кто не галлюцинировал, — та же ошибка, которую
    продукт создан ловить.
    """
    import xml.etree.ElementTree as ET
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return ""
    for name in z.namelist():
        if not name.lower().endswith(".docx"):
            continue
        try:
            dz = zipfile.ZipFile(io.BytesIO(z.read(name)))
            xml = dz.read("word/document.xml").decode("utf-8", "replace")
            out.append(re.sub(r"<[^>]+>", " ", xml))
        except Exception:                                    # noqa: BLE001
            continue
    return "\n".join(out)


def _tables_from_supplementary(blob: bytes) -> list:
    """Приложения Europe PMC приходят zip'ом; текстовые внутри — .docx (F-22)."""
    tables = []
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return tables
    for name in z.namelist():
        if not name.lower().endswith(".docx"):
            continue
        # Файл обязан быть уникальным: батч гоняет статьи в несколько потоков,
        # а фиксированный путь означает, что таблицы одной статьи попадут в разбор
        # другой — молча, без ошибки. Это ровно тот класс подмены, который продукт
        # создан ловить у чужих работ, и нарушение D-14 внутри себя.
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as fh:
            fh.write(z.read(name))
            tmp_path = fh.name
        try:
            for t in extract_docx(tmp_path):
                t["source_file"] = name
                tables.append(t)
        except Exception:                                    # noqa: BLE001
            continue
        finally:
            os.unlink(tmp_path)
    return tables


def gather(doi: str = None, text: str = None) -> dict:
    """Шаги 1-2: достать что можно и определить уровень."""
    if text and not doi:
        return {"meta": {"found": False, "doi": None}, "source_text": text,
                "jats_tables": [], "appendix_tables": [],
                "level": {"level": "L3", **retrieval.LEVELS["L3"],
                          "note": "текст подан напрямую, уровень определить нельзя — "
                                  "считаем нижним, пока не доказано обратное"}}

    meta = retrieval.lookup(doi)
    source_text, jats, appendix = meta.get("abstract") or "", [], []

    if meta.get("in_epmc") and meta.get("pmcid"):
        xml = retrieval.fetch_fulltext(meta["pmcid"])
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as fh:
            fh.write(xml)
            ft_path = fh.name
        try:
            jats = parse_tables(ft_path)
        except Exception:                                    # noqa: BLE001
            jats = []
        finally:
            os.unlink(ft_path)
        source_text = re.sub(r"<[^>]+>", " ", xml.decode("utf-8", "replace"))
        if meta.get("has_supplementary"):
            try:
                blob = retrieval.fetch_supplementary(meta["pmcid"])
                appendix = _tables_from_supplementary(blob)
                # текст приложений идёт в источник для сверки чисел (D-14)
                source_text += "\n\n" + _supplementary_text(blob)
            except Exception:                                # noqa: BLE001
                appendix = []

    level = retrieval.assess_level(meta, bool(jats), bool(appendix))
    return {"meta": meta, "source_text": source_text, "jats_tables": jats,
            "appendix_tables": appendix, "level": level}


def tables_as_text(gathered: dict, limit: int = 40) -> str:
    """Таблицы в текст для подачи модели. Приложения идут первыми: замер показал,
    что именно они меняют направление вывода (F-26)."""
    out = []
    for t in gathered["appendix_tables"][:limit]:
        rows = "\n".join(" | ".join(r) for r in t["rows"][:40])
        out.append(f"### ПРИЛОЖЕНИЕ: {t.get('caption','')}\n{rows}")
    for t in gathered["jats_tables"][:limit]:
        head = " | ".join(t["columns"])
        rows = "\n".join(" | ".join(r) for r in t["rows"][:40])
        out.append(f"### {t.get('label','')} {t.get('caption','')}\n{head}\n{rows}")
    return "\n\n".join(out)


def verify_findings(findings: dict, source_text: str) -> dict:
    """Шаг 4 — D-14: каждое число из разбора сверяется с первоисточником."""
    claims = []

    # Ветка computed — это РЕЗУЛЬТАТЫ арифметики (ARR, NNT, проценты). Их в
    # первоисточнике нет по определению; их проверяет stats_tool пересчётом,
    # а не поиск по тексту. Поймано на первом же сквозном прогоне 27.08.
    SKIP_BRANCHES = {"computed", "arithmetic"}

    def walk(node, label="", root=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if not root and k in SKIP_BRANCHES:
                    continue
                walk(v, k if not label else f"{label} · {k}", root or k)
        elif isinstance(node, list):
            for v in node:
                walk(v, label, root)
        elif isinstance(node, str):
            # число целиком, вместе с разделителями разрядов: «3,609» и «151 691»
            # иначе regex рвёт их на куски и они не находятся в источнике
            for raw in re.findall(r"\d{1,3}(?:[  ,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", node):
                num = raw.replace(",", "").replace(" ", "").replace("\u00a0", "")
                claims.append({"value": num, "label": label or node[:60]})
    walk(findings or {})
    # дубликаты одного и того же числа с одной меткой не проверяем дважды
    seen, uniq = set(), []
    for c in claims:
        k = (c["value"], c["label"])
        if k not in seen:
            seen.add(k); uniq.append(c)
    return verify(uniq, source_text)


def run(doi: str = None, text: str = None, prompt: str = None) -> dict:
    gathered = gather(doi=doi, text=text)
    paper = gathered["source_text"]
    tbl = tables_as_text(gathered)
    if tbl:
        paper = f"{paper}\n\n## ТАБЛИЦЫ\n\n{tbl}"

    result = critic.critique(paper[:400000], prompt)
    findings = result.get("findings")

    ver = verify_findings(findings, gathered["source_text"]) if findings else None

    lvl = gathered["level"]["level"]
    return {
        "meta": gathered["meta"],
        "level": gathered["level"],
        "tables": {"main": len(gathered["jats_tables"]),
                   "appendix": len(gathered["appendix_tables"])},
        "findings": findings,
        "parse_error": result.get("parse_error"),
        "verification": ver["summary"] if ver else None,
        "unverified_numbers": [c for c in (ver["claims"] if ver else [])
                               if c["status"] in ("UNVERIFIED", "GROUP_MISMATCH")][:20],
        "max_confidence": gathered["level"]["max_confidence"],
        "caveat": (None if lvl == "L1" else
                   "Разбор сделан на неполных данных. По замеру проекта на этом уровне "
                   "модель систематически ошибается направлением confounding — "
                   "ни одна находка не может считаться подтверждённой."),
        "usage": result.get("usage"),
    }
