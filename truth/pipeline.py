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
import concurrent.futures as cf
import io
import os
import re
import tempfile
import zipfile

from . import critic, retrieval, subagents
from .docx_tables import extract as extract_docx
from .jats_tables import parse_tables, to_claims
from .pdf_tables import extract as extract_pdf
from .pdf_tables import extract_text as pdf_text
from .pdf_tables import has_text_layer, is_appendix
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


def _docx_text(blob: bytes) -> str:
    """Плоский текст одиночного .docx. Отличается от `_supplementary_text` тем,
    что там на входе zip-архив приложений Europe PMC, а здесь сам документ."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    except Exception:                                        # noqa: BLE001
        return ""
    return re.sub(r"<[^>]+>", " ", xml)


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


def _from_uploads(uploads: list) -> dict:
    """Путь B: статья принесена пользователем файлами.

    Нужен потому, что автоматически достаётся около 55% статей (F-25), а остальное
    закрыто Cloudflare или TDM-токеном. При этом человеку та же статья обычно
    доступна — по подписке или как bronze OA, который просто не отдаётся скрипту.
    Файл от пользователя доводит такую статью до того же уровня, что открытую.

    uploads — список (имя, байты). Принимаются .pdf и .docx: первый формат — то,
    как статью видит человек, второй — то, как приходят приложения из Europe PMC.
    """
    texts, main, appendix, notes, pdfs = [], [], [], [], []
    for name, blob in uploads:
        low = name.lower()
        with tempfile.NamedTemporaryFile(
                suffix=".pdf" if low.endswith(".pdf") else ".docx", delete=False) as fh:
            fh.write(blob)
            tmp = fh.name
        try:
            if low.endswith(".pdf"):
                if not has_text_layer(tmp):
                    # Скан без текстового слоя: таблиц из него не достать ничем,
                    # кроме OCR. Молчать об этом нельзя — иначе пустой разбор
                    # выглядел бы как «в статье ничего не нашлось».
                    notes.append(f"{name}: нет текстового слоя, нужен OCR — файл пропущен")
                    continue
                texts.append(pdf_text(tmp))
                # Сам файл уходит модели: Gemini читает PDF вместе с вёрсткой и
                # берёт таблицы точнее, чем любой наш парсер (F-42). Наш разбор
                # остаётся ради двух вещей, которые модели поручать нельзя:
                # источник для обратной сверки чисел и определение уровня.
                pdfs.append(blob)
                for t in extract_pdf(tmp):
                    t["source_file"] = name
                    (appendix if is_appendix(t) else main).append(t)
            elif low.endswith(".docx"):
                for t in extract_docx(tmp):
                    t["source_file"] = name
                    # .docx у издателей — это почти всегда приложение (F-22)
                    appendix.append(t)
                texts.append(_docx_text(blob))
            else:
                notes.append(f"{name}: формат не поддержан, нужен .pdf или .docx")
        except Exception as e:                               # noqa: BLE001
            notes.append(f"{name}: {type(e).__name__}")
        finally:
            os.unlink(tmp)
    return {"text": "\n\n".join(t for t in texts if t),
            "main": main, "appendix": appendix, "notes": notes, "pdfs": pdfs}


def gather(doi: str = None, text: str = None, uploads: list = None) -> dict:
    """Шаги 1-2: достать что можно и определить уровень.

    Три входа, один порядок: принесённые файлы (путь B) сильнее автоматической
    добычи, потому что дают то, что за платным доступом; DOI используется вместе
    с ними — ради метаданных и на случай, если приложения лежат в Europe PMC.
    """
    if uploads:
        got = _from_uploads(uploads)
        meta = retrieval.lookup(doi) if doi else {"found": False, "doi": doi}
        src = got["text"] or (meta.get("abstract") or "")
        # приложения могут прийти из Europe PMC, даже когда основной текст принесён
        if doi and meta.get("in_epmc") and meta.get("pmcid") and meta.get("has_supplementary") \
                and not got["appendix"]:
            try:
                blob = retrieval.fetch_supplementary(meta["pmcid"])
                got["appendix"] = _tables_from_supplementary(blob)
                src += "\n\n" + _supplementary_text(blob)
            except Exception:                                # noqa: BLE001
                pass
        level = retrieval.assess_level(meta, bool(got["text"]), bool(got["appendix"]))
        level["source"] = "файлы пользователя (путь B)"
        if got["notes"]:
            level["upload_notes"] = got["notes"]
        return {"meta": meta, "source_text": src, "jats_tables": got["main"],
                "appendix_tables": got["appendix"], "level": level,
                "pdfs": got["pdfs"]}

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
            # Число целиком, вместе с разделителями разрядов: «3,609» и «151 691» —
            # иначе regex рвёт их на куски и они не находятся в источнике.
            # Ведущая точка тоже часть числа: в статьях сплошь «P < .0001», и без
            # этой альтернативы из него выдёргивалось «0001», которого в тексте нет
            # как отдельного числа, — модель получала UNVERIFIED за точную цитату.
            for raw in re.findall(
                    r"\d{1,3}(?:[  ,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+", node):
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


def _assemble(gathered: dict, findings: dict, parse_error=None, usage=None,
              engine: str = "direct", tool_calls: list = None,
              agents: dict = None, engine_note: str = None) -> dict:
    """Сборка отчёта — одна на оба движка.

    Сверка чисел (шаг 4) стоит здесь, а не внутри движка, намеренно: чем бы разбор
    ни делался, числа проверяются одинаково и независимо от того, кто их написал.
    В ADK у агента есть инструмент самопроверки, но он не отменяет этот шаг (D-14).
    """
    # Источник для сверки = текст статьи ПЛЮС разобранные таблицы.
    # Без таблиц числа из приложения не находятся: `_supplementary_text` заменяет
    # XML-теги пробелами, а Word разбивает число на несколько <w:t>-фрагментов —
    # «3.04» превращается в «3 . 04». Разбор таблиц склеивает ячейку правильно,
    # поэтому он и есть надёжный источник цифр. Четвёртый случай ложного обвинения
    # за проект (после F-41 и двух в приложениях) — и снова найден замером.
    src = gathered["source_text"]
    tbl_text = tables_as_text(gathered, limit=200)
    ver = verify_findings(findings, f"{src}\n\n{tbl_text}") if findings else None
    lvl = gathered["level"]["level"]
    out = {
        "meta": gathered["meta"],
        "level": gathered["level"],
        "tables": {"main": len(gathered["jats_tables"]),
                   "appendix": len(gathered["appendix_tables"])},
        "findings": findings,
        "parse_error": parse_error,
        "verification": ver["summary"] if ver else None,
        "unverified_numbers": [c for c in (ver["claims"] if ver else [])
                               if c["status"] in ("UNVERIFIED", "GROUP_MISMATCH")][:20],
        "max_confidence": gathered["level"]["max_confidence"],
        "caveat": (None if lvl == "L1" else
                   "Разбор сделан на неполных данных. Находки этого уровня опираются на "
                   "общие свойства дизайна, а не на числа из документа: проверять нечего, "
                   "потому что цитировать нечего. Направление смещения при этом может быть "
                   "названо верно — но верная догадка не является доказательством, "
                   "поэтому статус CONFIRMED здесь недостижим (F-44)."),
        "usage": usage,
        "engine": engine,
    }
    if agents:
        out["agents"] = agents
    if tool_calls is not None:
        out["tool_calls"] = tool_calls
    if engine_note:
        out["engine_note"] = engine_note
    return out


def run(doi: str = None, text: str = None, prompt: str = None,
        uploads: list = None, engine: str = "direct") -> dict:
    """`engine`: "direct" — оркестрация кодом, "adk" — граф Google ADK.

    По баллам пути равноценны (медиана 5.5/6 у обоих, F-46). ADK даёт агентам
    инструменты и объявляет параллельность декларативно; прямой путь быстрее
    (~35 с против ~73 с) и потому оставлен по умолчанию для батча.
    """
    gathered = gather(doi=doi, text=text, uploads=uploads)
    paper = gathered["source_text"]
    tbl = tables_as_text(gathered)
    if tbl:
        paper = f"{paper}\n\n## ТАБЛИЦЫ\n\n{tbl}"

    if engine == "adk":
        # Тот же аудит через граф ADK. Отличие не косметическое: там у агентов есть
        # инструменты — калькулятор рисков и проверка числа в источнике, — которыми
        # они пользуются во время рассуждения, а не после него.
        try:
            from . import adk_agent
            a = adk_agent.run(paper_text=paper[:400000],
                              pdfs=gathered.get("pdfs"),
                              source_text=gathered["source_text"])
            findings = subagents.merge_into_confounding(
                a.get("robins_e") or {}, a.get("baseline") or {})
            return _assemble(gathered, findings,
                             parse_error=(a.get("parse_errors") or None),
                             usage=None, engine="adk", tool_calls=a.get("tool_calls"))
        except Exception as e:                               # noqa: BLE001
            # Падать целиком из-за каркаса нельзя: у прямого пути тот же результат
            # по баллам (F-46), он просто выражен кодом. Отмечаем подмену в отчёте,
            # чтобы никто не считал, будто отработал ADK.
            engine_note = f"ADK не отработал ({type(e).__name__}), разбор сделан прямым путём"
    else:
        engine_note = None

    # Два прохода по одному документу идут параллельно: основной критик по семи
    # доменам и суб-агент по сопоставимости групп. Параллельно, а не последовательно,
    # потому что суб-агент не зависит от вывода критика — он смотрит в тот же
    # документ под другим углом. Два вызова Vertex одновременно укладываются в лимит
    # (429 начинается с пятого подряд, F-11), у каждого свой backoff.
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        f_main = ex.submit(critic.critique, paper[:400000], prompt,
                           pdfs=gathered.get("pdfs"))
        f_base = ex.submit(subagents.baseline_comparability, paper[:400000],
                           gathered.get("pdfs"))
        result = f_main.result()
        baseline = f_base.result()

    findings = result.get("findings")
    if findings:
        findings = subagents.merge_into_confounding(findings, baseline)

    return _assemble(gathered, findings, parse_error=result.get("parse_error"),
                     usage=result.get("usage"), engine="direct",
                     agents={
                         "critic_robins_e": result.get("usage"),
                         "subagent_baseline_comparability":
                             (baseline or {}).get("_usage")
                             or {"error": (baseline or {}).get("error")},
                     },
                     engine_note=engine_note)
