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

from . import cells, critic, retrieval, subagents
from .docx_tables import extract as extract_docx
from .jats_tables import parse_tables, to_claims
from .pdf_tables import extract as extract_pdf
from .pdf_tables import extract_text as pdf_text
from .pdf_tables import has_text_layer, is_appendix
from .direction import summarise as direction_summary
from . import grounding
from .recompute import recompute
from .verify_numbers import verify


def _supplementary_text(blob: bytes) -> str:
    """Плоский текст всех .docx-приложений — нужен для сверки чисел.

    Без него верификатор обвиняет модель в выдумывании чисел, которые она честно
    процитировала из eTable. Поймано на первом сквозном прогоне 27.08: четыре
    значения HR были помечены UNVERIFIED, а на деле лежали в приложении.
    Обвинить в галлюцинации того, кто не галлюцинировал, — та же ошибка, которую
    продукт создан ловить.
    """
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return ""
    for name in z.namelist():
        low = name.lower()
        if low.endswith(".docx"):
            try:
                dz = zipfile.ZipFile(io.BytesIO(z.read(name)))
                xml = dz.read("word/document.xml").decode("utf-8", "replace")
                out.append(re.sub(r"<[^>]+>", " ", xml))
            except Exception:                                # noqa: BLE001
                continue
        elif low.endswith(".pdf"):
            # Приложение бывает и PDF — у BMJ это штатный формат («web only»).
            # Разбирали только .docx, и такие статьи молча падали с L1 на L2:
            # уровень оказывался свойством нашего добытчика, а не статьи (F-60).
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
                fh.write(z.read(name))
                tmp_path = fh.name
            try:
                if has_text_layer(tmp_path):
                    out.append(pdf_text(tmp_path))
            except Exception:                                # noqa: BLE001
                pass
            finally:
                os.unlink(tmp_path)
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
        low = name.lower()
        if low.endswith(".pdf"):
            # Тот же разбор, что для принесённого пользователем PDF: парсер уже
            # есть, писать второй незачем.
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
                fh.write(z.read(name))
                tmp_path = fh.name
            try:
                for t in extract_pdf(tmp_path):
                    t["source_file"] = name
                    tables.append(t)
            except Exception:                                # noqa: BLE001
                pass
            finally:
                os.unlink(tmp_path)
            continue
        if not low.endswith(".docx"):
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


class NothingRetrieved(RuntimeError):
    """Разбирать нечего: источник пуст.

    Отдельный класс, а не общее исключение, потому что ответ пользователю здесь
    другой. Это не поломка нашего кода и не поломка модели — это отсутствие
    входа, и сказать об этом надо теми словами, что случилось: какой источник
    отказал и имел ли он на это право.

    Чем было раньше. Пустой источник спокойно доезжал до Vertex, тот отвечал
    `400 INVALID_ARGUMENT … Model input cannot be empty`, `critic.call` считал
    400 неповторяемой ошибкой и поднимал её, а `/analyze` заворачивал в 500 с
    чужим текстом. Пользователь узнавал про внутренности Vertex вместо того, что
    Europe PMC не отдал статью. Наблюдалось живьём 29.08 на DOI из README.
    """

    def __init__(self, gathered: dict):
        self.gathered = gathered
        super().__init__("источник пуст — разбирать нечего")


def gather(doi: str = None, text: str = None, uploads: list = None) -> dict:
    """Шаги 1-2: достать что можно и определить уровень.

    Три входа, один порядок: принесённые файлы (путь B) сильнее автоматической
    добычи, потому что дают то, что за платным доступом; DOI используется вместе
    с ними — ради метаданных и на случай, если приложения лежат в Europe PMC.

    Каждый разбор несёт журнал добычи `level["retrieval"]`: по записи на каждый
    источник, к которому обращались, со статусом и причиной. Без него уровень
    доказательности — утверждение без обеспечения, а именно за такие утверждения
    этот продукт и критикует чужие статьи. Статусы: `ok`, `not_found`,
    `not_applicable` (предусловие эндпоинта не выполнено — законное отсутствие),
    `upstream_error` (предусловие выполнено, а источник отказал — сбой сервиса),
    `unreachable`, `degraded`, `parse_error`.
    """
    if uploads:
        got = _from_uploads(uploads)
        meta = retrieval.lookup(doi) if doi else {"found": False, "doi": doi,
                                                  "status": "not_asked"}
        journal = [{"source": "user_upload", "status": "ok" if got["text"] else "empty",
                    "tables": len(got["main"]) + len(got["appendix"])}]
        if doi:
            journal.append({"source": "europepmc.search", "status": meta.get("status"),
                            **({"note": meta["error"]} if meta.get("error") else {})})
        src = got["text"] or (meta.get("abstract") or "")
        # приложения могут прийти из Europe PMC, даже когда основной текст принесён
        if doi and meta.get("pmcid") and not got["appendix"]:
            blob, note = retrieval.attempt(
                "europepmc.supplementaryFiles",
                lambda: retrieval.fetch_supplementary(meta["pmcid"]),
                expected=bool(meta.get("has_supplementary")),
                why_not="hasSuppl=N — по метаданным приложений у статьи нет")
            journal.append(note)
            if blob:
                got["appendix"] = _tables_from_supplementary(blob)
                src += "\n\n" + _supplementary_text(blob)
        level = retrieval.assess_level(meta, bool(got["text"]), bool(got["appendix"]))
        level["source"] = "файлы пользователя (путь B)"
        level["retrieval"] = journal
        if got["notes"]:
            level["upload_notes"] = got["notes"]
        return {"meta": meta, "source_text": src, "jats_tables": got["main"],
                "appendix_tables": got["appendix"], "level": level,
                "pdfs": got["pdfs"]}

    if text and not doi:
        return {"meta": {"found": False, "doi": None, "status": "not_asked"},
                "source_text": text, "jats_tables": [], "appendix_tables": [],
                "level": {"level": "L3", **retrieval.LEVELS["L3"],
                          "retrieval": [{"source": "caller", "status": "ok"}],
                          "note": "text supplied directly; the evidence level cannot be "
                                  "established, so the lowest one is assumed until shown "
                                  "otherwise"}}

    meta = retrieval.lookup(doi)
    journal = [{"source": "europepmc.search", "status": meta.get("status"),
                **({"note": meta["error"]} if meta.get("error") else {})}]
    source_text, jats, appendix = meta.get("abstract") or "", [], []
    full_text = ""

    if meta.get("pmcid"):
        # Предусловие взято из справочника, а не из `inEPMC`: fullTextXML отдаётся
        # только для OA-подмножества. См. шапку `retrieval`. Раньше здесь стоял
        # голый вызов под проверкой `in_epmc`, и на 4 млн статей вне OA сервис
        # отвечал 500 вместо честного «этой статьи нам не отдадут».
        xml, note = retrieval.attempt(
            "europepmc.fullTextXML",
            lambda: retrieval.fetch_fulltext(meta["pmcid"]),
            expected=bool(meta.get("open_access")),
            why_not="isOpenAccess=N — статья есть в Europe PMC, но вне Open "
                    "Access-подмножества, и по документации fullTextXML для неё не "
                    "отдаётся. Это ограничение доступа, а не отсутствие текста: "
                    "принесите файл (путь B)")
        journal.append(note)
        if xml:
            full_text = re.sub(r"<[^>]+>", " ", xml.decode("utf-8", "replace"))
            source_text = full_text
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as fh:
                fh.write(xml)
                ft_path = fh.name
            try:
                jats = parse_tables(ft_path)
                journal.append({"source": "jats.tables", "status": "ok",
                                "tables": len(jats)})
            except Exception as e:                           # noqa: BLE001
                jats = []
                journal.append({"source": "jats.tables", "status": "parse_error",
                                "note": f"{type(e).__name__}: {e}"[:160]})
            finally:
                os.unlink(ft_path)

        blob, note = retrieval.attempt(
            "europepmc.supplementaryFiles",
            lambda: retrieval.fetch_supplementary(meta["pmcid"]),
            expected=bool(meta.get("has_supplementary")),
            why_not="hasSuppl=N — по метаданным приложений у статьи нет")
        journal.append(note)
        if blob:
            appendix = _tables_from_supplementary(blob)
            # текст приложений идёт в источник для сверки чисел (D-14)
            source_text += "\n\n" + _supplementary_text(blob)

    # `has_fulltext` — «тело статьи добыто», а не «разобрались таблицы». Раньше
    # сюда шёл `bool(jats)`, и полнотекстовая статья без единой таблицы получала
    # L3 с подписью «разбор шёл по одному абстракту» при полном теле в руках.
    level = retrieval.assess_level(meta, bool(full_text), bool(appendix))
    level["retrieval"] = journal
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


def verify_findings(findings: dict, source_text: str, tables: list = None) -> dict:
    """Шаг 4 — D-14: каждое число из разбора сверяется с первоисточником.

    Меткой числа служит **самодостаточное утверждение**, а не путь в JSON.

    Так было до 28.08: меткой шёл путь по структуре ответа — `domains ·
    direction_justification`, `subagents · baseline_comparability ·
    characteristics · exposed`. Слова `domains` и `characteristics` в научной
    статье не встречаются никогда, поэтому `check_label` почти всегда не находил
    метку рядом с числом: статуса VERIFIED удостаивались 3-8 чисел из четырёхсот.
    Хуже того, имя поля `exposed` принималось за заявленную группу, и
    `check_group` объявлял инверсию там, где модель аккуратно положила значение
    в нужное поле — ложное обвинение в ошибке класса F-12.

    Приём взят у SAFE (google-deepmind/long-form-factuality): их конвейер
    отдельным шагом переписывает каждый атомарный факт так, чтобы он был
    самодостаточным, и только потом проверяет. Здесь то же самое собирается из
    соседних полей объекта: для `{"name": "History of breast cancer",
    "exposed": "907 (5.9%)", "unexposed": "1,197 (7.8%)"}` числа получают меткой
    имя характеристики, а не слово `exposed`.
    """
    claims = []

    # Ветка computed — это РЕЗУЛЬТАТЫ арифметики (ARR, NNT, проценты). Их в
    # первоисточнике нет по определению; их проверяет stats_tool пересчётом,
    # а не поиск по тексту. Поймано на первом же сквозном прогоне 27.08.
    SKIP_BRANCHES = {"computed", "arithmetic"}

    # Поля, чьё значение описывает СОСЕДНИЕ числа того же объекта: из них и
    # собирается самодостаточная метка. Порядок не важен, важен смысл — это
    # подпись строки таблицы, а не имя поля схемы.
    NAMING = ("name", "characteristic", "title", "label", "table", "source_table",
              "why", "mechanism", "statement")
    NUM = re.compile(r"\d{1,3}(?:[  ,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+")

    def self_contained(node, obj_context, path):
        """Метка для чисел внутри строки node: подпись объекта + сам текст."""
        parts = [p for p in (obj_context, node.strip()) if p]
        lab = " — ".join(parts)
        # если описания нет вовсе, честнее оставить путь, чем пустую метку
        return lab[:300] if lab else path

    def walk(node, path="", root="", obj_context=""):
        if isinstance(node, dict):
            # подпись объекта: короткие текстовые поля, называющие его содержимое
            own = [str(node[k]) for k in NAMING
                   if isinstance(node.get(k), str) and 0 < len(node[k]) <= 200]
            ctx = " ".join(own) or obj_context
            for k, v in node.items():
                if not root and k in SKIP_BRANCHES:
                    continue
                walk(v, k if not path else f"{path} · {k}", root or k, ctx)
        elif isinstance(node, list):
            for v in node:
                walk(v, path, root, obj_context)
        elif isinstance(node, str):
            # Число целиком, вместе с разделителями разрядов: «3,609» и «151 691» —
            # иначе regex рвёт их на куски и они не находятся в источнике.
            # Ведущая точка тоже часть числа: в статьях сплошь «P < .0001», и без
            # этой альтернативы из него выдёргивалось «0001», которого в тексте нет
            # как отдельного числа, — модель получала UNVERIFIED за точную цитату.
            for raw in NUM.findall(node):
                num = raw.replace(",", "").replace(" ", "").replace("\u00a0", "")
                claims.append({"value": num,
                               "label": self_contained(node, obj_context, path)})
    walk(findings or {})

    # Четыре числа таблицы 2×2 лежат в пропущенной ветке `computed`, но проверять
    # их надо: это не результат арифметики, а выписка из документа, и на неё
    # опирается весь пересчёт. Отдельным проходом ещё и потому, что walk достаёт
    # числа только из строк, а counts приходят целыми.
    COUNT_LABEL = {
        "exposed_events": "2×2 table used for the absolute risk — events in the exposed arm",
        "exposed_total": "2×2 table used for the absolute risk — size of the exposed arm",
        "control_events": "2×2 table used for the absolute risk — events in the control arm",
        "control_total": "2×2 table used for the absolute risk — size of the control arm",
    }
    # Скорректированная оценка статьи — тоже выписка из документа, а не результат
    # арифметики, поэтому она проверяется наравне с прочими числами. Без этого
    # E-value считался бы от значения, которое никто не сверял (D-14).
    adj = ((findings or {}).get("computed") or {}).get("adjusted_effect") or {}
    if isinstance(adj, dict):
        meas = str(adj.get("measure") or "effect estimate")
        what = str(adj.get("outcome") or "the primary outcome")[:120]
        for key, part in (("value", "point estimate"),
                          ("ci_low", "lower confidence limit"),
                          ("ci_high", "upper confidence limit")):
            v = adj.get(key)
            if isinstance(v, (int, float)) and v > 0:
                claims.append({"value": str(v),
                               "label": f"adjusted {meas} for {what} — {part}"})

    cnt = ((findings or {}).get("computed") or {}).get("counts") or {}
    # Совпадающие размеры рук — норма для сопоставления 1:1, и метка «руки
    # экспозиции» на числе, которое принадлежит обеим, даёт ложную инверсию:
    # проверка группы ищет ближайший маркер и находит соседнюю руку. Поэтому одно
    # число на две руки и метку получает одну, без принадлежности.
    matched = cnt.get("exposed_total") and cnt.get("exposed_total") == cnt.get("control_total")
    for k, lab in COUNT_LABEL.items():
        v = cnt.get(k)
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        if matched and k in ("exposed_total", "control_total"):
            if k == "control_total":
                continue
            lab = ("2×2 table used for the absolute risk — size of each arm, "
                   "matched 1:1")
        claims.append({"value": str(int(v)), "label": lab})

    # дубликаты одного и того же числа с одной меткой не проверяем дважды
    seen, uniq = set(), []
    for c in claims:
        k = (c["value"], c["label"])
        if k not in seen:
            seen.add(k); uniq.append(c)
    res = verify(uniq, source_text)
    return _attach_cells(res, tables)


def _attach_cells(res: dict, tables: list) -> dict:
    """Шаг 4б: у числа есть не окрестность, а адрес — если оно из таблицы.

    Прежняя проверка метки сравнивала формулировку модели со словами в окне 380
    знаков вокруг числа. Замер F-63 показал, чего это стоит: своё от подложного
    такая мера отделяет втрое, распределения перекрываются. Иначе и быть не могло —
    метка есть описание, а не строка из статьи.

    У числа из таблицы описание не нужно: у него есть подпись строки и заголовок
    колонки, то есть адрес. Сверка адреса — решение, а не шкала. Разбор таблиц для
    этого написан давно и до сих пор не использовался ни разу.

    Здесь же чинится проверка групп. Она стояла на выражениях, вшитых под первую
    статью проекта, и на живом разборе выносила вердикт для 4 чисел из 474.
    Заголовки колонок — это и есть названия рук, взятые из самого документа.
    """
    idx = cells.build_index(tables)
    if not idx:
        return res
    in_cell = in_table = 0
    for c in res["claims"]:
        if c.get("status") == "UNVERIFIED":
            c["located"] = "absent"
            continue
        cell = cells.locate(c["value"], c.get("label", ""), idx)
        if not cell:
            # Число есть в документе, но не в разобранной таблице: проза,
            # приложение, разобранное как текст, или таблица не поддалась.
            c["located"] = "text"
            continue
        # `cells.column_conflict` здесь НЕ вызывается: замерено — 6 срабатываний
        # на живом разборе, ложных 6 из 6 (подробности в шапке `truth/cells.py`).
        # Проверка без измеренной точности обвинений не выносит.
        c["cell"] = {k: cell.get(k) for k in ("table", "row", "column", "agreement")}
        if (cell.get("agreement") or 0) >= cells.CELL_THRESHOLD:
            c["located"] = "cell"
            in_cell += 1
        else:
            # Число стоит в таблице, но не там, где сказано в метке. Это не
            # обвинение: адрес мог не сойтись из-за формулировки. Но и «сверено
            # с ячейкой» тут писать нельзя.
            c["located"] = "table"
            in_table += 1
    s = res["summary"]
    # Три тира вместо булева «найдено». `cell` — число стоит в ячейке, чей адрес
    # (подпись строки и заголовок колонки) согласуется с меткой модели: это
    # решение, а не шкала. `table` — число в таблице есть, адрес не сошёлся.
    # `text` — только в прозе. `absent` — нет вовсе.
    s["in_cell"] = in_cell
    s["in_table_address_unmatched"] = in_table
    return res


CAVEAT = {
    "L2": ("The body of the paper was retrieved, and the numbers quoted from it were "
           "checked — but the appendix was not. Baseline tables, sensitivity analyses "
           "and subgroup results usually live there, and a finding that rests on them "
           "cannot be checked here. CONFIRMED stays out of reach not because the "
           "reasoning is weaker, but because part of the evidence was never in front "
           "of it (F-44)."),
    "L3": ("This audit ran on the abstract alone. Findings at this level rest on "
           "generic design properties rather than numbers from the document: there is "
           "nothing to verify because there is nothing to quote. The direction of bias "
           "may still be named correctly — but a correct guess is not evidence, so "
           "CONFIRMED is unreachable here (F-44)."),
    # L0 не должен встречаться в готовом отчёте: `run` до модели не доходит.
    # Текст оставлен на случай, если отчёт всё же соберут — например, из
    # сохранённой копии, — чтобы уровень не читался как «низкий», когда он «никакой».
    "L0": ("Nothing was retrieved for this paper — not the full text, not the "
           "appendix, not even the abstract. Whatever stands below rests on no "
           "document at all. This is a statement about the retrieval, not about "
           "the paper: the `retrieval` log says which source refused and whether "
           "it was entitled to refuse."),
}


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
    all_tables = list(gathered["appendix_tables"]) + list(gathered["jats_tables"])
    ver = (verify_findings(findings, f"{src}\n\n{tbl_text}", all_tables)
           if findings else None)
    # Пересчёт заявленной арифметики функцией. Раздел отчёта называется
    # «посчитано функцией, а не моделью» — до 29.08 это было неправдой на прямом
    # пути: `stats_tool` там не вызывался ни разу (F-55).
    recalc = recompute((findings or {}).get("computed"))
    # Свод направлений: общее направление модели сверяется с её же доменами.
    dirs = direction_summary(findings)
    # Обеспеченность каждого вывода по отдельности. Одна цифра на весь разбор
    # («566 найдено») не говорит, какой из семи доменов стоит на числах из
    # документа, а какой на общих словах, — а вся идея продукта в этом различии.
    ground = grounding.owners(findings, ver["claims"]) if ver else None
    weak = grounding.statements(findings, ver["claims"]) if ver else None

    lvl = gathered["level"]["level"]
    out = {
        "meta": gathered["meta"],
        "level": gathered["level"],
        "tables": {"main": len(gathered["jats_tables"]),
                   "appendix": len(gathered["appendix_tables"])},
        "findings": findings,
        "recomputed": recalc,
        "direction_summary": dirs,
        "grounding": ground,
        "weakly_grounded_statements": weak,
        "parse_error": parse_error,
        "verification": ver["summary"] if ver else None,
        "unverified_numbers": [c for c in (ver["claims"] if ver else [])
                               if c["status"] in ("UNVERIFIED", "GROUP_MISMATCH")][:20],
        "max_confidence": gathered["level"]["max_confidence"],
        # Предупреждение пишется под уровень. Один текст на L2 и L3 противоречил
        # собственной странице: на L2 он утверждал «нечего цитировать», а рядом
        # стояли сотни сверенных с документом чисел (найдено 29.08 на
        # 10.1136/bmj-2023-076990, F-60).
        "caveat": CAVEAT.get(lvl),
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

    # Пустой вход в модель — не разбор, а его имитация, и стоит он трёх вызовов
    # Vertex. Проверка стоит здесь, а не в обработчике HTTP, потому что через
    # `run` ходят все: сервис, батч и bench.
    if not paper.strip():
        raise NothingRetrieved(gathered)

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
            findings = subagents.merge_time(findings, a.get("timing") or {})
            return _assemble(gathered, findings,
                             parse_error=(a.get("parse_errors") or None),
                             usage=None, engine="adk", tool_calls=a.get("tool_calls"),
                             agents={k: {"via": "adk"} for k in
                                     ("critic_robins_e", "baseline_comparability",
                                      "time_related_biases")})
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
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        f_main = ex.submit(critic.critique, paper[:400000], prompt,
                           pdfs=gathered.get("pdfs"))
        f_base = ex.submit(subagents.baseline_comparability, paper[:400000],
                           gathered.get("pdfs"))
        f_time = ex.submit(subagents.time_related_biases, paper[:400000],
                           gathered.get("pdfs"))
        result = f_main.result()
        baseline = f_base.result()
        timing = f_time.result()

    findings = result.get("findings")
    if findings:
        findings = subagents.merge_into_confounding(findings, baseline)
        findings = subagents.merge_time(findings, timing)

    return _assemble(gathered, findings, parse_error=result.get("parse_error"),
                     usage=result.get("usage"), engine="direct",
                     agents={
                         "critic_robins_e": result.get("usage"),
                         "subagent_baseline_comparability":
                             (baseline or {}).get("_usage")
                             or {"error": (baseline or {}).get("error")},
                         "subagent_time_related_biases":
                             (timing or {}).get("_usage")
                             or {"error": (timing or {}).get("error")},
                     },
                     engine_note=engine_note)
