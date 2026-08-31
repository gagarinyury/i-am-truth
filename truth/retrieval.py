"""
Layer 1 — получение статьи и определение уровня доказательности.

Уровень определяется тем, что реально удалось достать, и он же задаёт потолок
качества разбора. Цены уровней измерены (F-26, F-40, решение D-13); ниже — медианы
трёх прогонов на подготовленных входах, шкала шестибалльная:

  L1  full-text + таблицы приложения   6.0 / 6
  L2  полный текст без приложений      4.5 / 6
  L3  только абстракт и метаданные     4.0 / 6
  L0  не добыто ничего                 разбор не запускается

На живой статье целиком (опубликованный PDF, а не подготовленный вход) цена другая:
одиночный критик давал 3.5-4.5 / 6, медиана 3.5 — числа там надо ещё найти (F-43), а
нынешние три прохода дают 5.0-6.0, медиана 5.5 по шестнадцати прогонам
(`eval/bench.py report`, 31.08). Здесь стояло только первое число, и оно устарело на
два суб-агента. Доступность уровней: Europe PMC отдаёт full-text для 27.5% статей
класса, объединение каналов ~55% (F-25).

Ниже L1 находки не опираются на числа из документа и потому непроверяемы —
это не предположение, а результат замера.

## Предусловия добычи взяты из первоисточника, а не из наблюдений

EBI Europe PMC Web Service 6.9.0 Reference Guide, doc version 1.51 (31.08.2023):

  getFullTextXML — «The full text XML is available only for the full-text **OA
    subset** of the Europe PMC database»; в сводной таблице методов — «For Open
    Access articles». Значит предусловие — `isOpenAccess = Y`, а НЕ `inEPMC = Y`.
  inEPMC — «indicates whether the citation is available as full text in Europe
    PMC»: это про наличие текста на сайте, а не про право забрать его через API.
  hasSuppl — «indicates that the article has supplemental data associated
    (**and available**) with it».

Различие не академическое. До 29.08 код проверял `in_epmc` и звал эндпоинт,
которого для такой статьи не существует; 404 летел наружу необработанным, и
`POST /analyze` отдавал 500. Класс не редкий: запрос `IN_EPMC:y AND OPEN_ACCESS:n`
даёт 4 049 378 записей, из них 20 549 за 2026 год — примерно в десять раз больше,
чем OA-подмножество, на котором проект и мерился. Проверено адресно на трёх
статьях класса: fullTextXML → 404 во всех трёх, при исправном сервисе.

Отсюда правило, на котором держится весь модуль: **отказ при выполненном
предусловии — это отказ сервиса, а при невыполненном — законное отсутствие
данных.** Смешивать их нельзя, иначе уровень доказательности снова становится
свойством нашего добытчика, а не статьи (F-60).
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UA = {"User-Agent": "i-am-truth/0.1 (methodology audit)"}

# Цены уровней замерены на промпте ROBINS-E (том, что в проде) по эталону version 2,
# шесть пунктов, три прогона 27–28.08 — F-40. Указан диапазон и медиана: разброс между
# прогонами реален и скрывать его нечестно. Числа из пятибалльной эпохи (до F-32) сюда
# переносить нельзя — знаменатель изменился, а баллы нет.
# ⚠️ `measured_score` получен ХАРНЕСОМ на подготовленных входах `eval/inputs/`:
# файл отдаётся модели напрямую, добычи и сверки чисел в этом замере нет. Продукт
# целиком на живой статье даёт другое — на том же эталоне медиана 5.5 из 6 по
# шестнадцати прогонам (`eval/bench.py report`). Числа не взаимозаменяемы, и до
# 31.08 `/levels` отдавал первое без указания, откуда оно: читатель API видел цену
# уровня, относящуюся к эксперименту, которого он не запускал. Поэтому источник
# замера теперь стоит в самом поле — рядом со значением, а не в README.
_HARNESS = "harness on prepared inputs (eval/inputs/), 3 runs, no retrieval step"

LEVELS = {
    "L1": {"name": "full text + appendix tables", "max_confidence": "CONFIRMED",
           "measured_score": "5.0-6.0 / 6 (median 6.0)", "measured_by": _HARNESS},
    "L2": {"name": "full text, no appendices", "max_confidence": "PLAUSIBLE-UNVERIFIED",
           "measured_score": "4.0-5.0 / 6 (median 4.5)", "measured_by": _HARNESS},
    "L3": {"name": "abstract only", "max_confidence": "PLAUSIBLE-UNVERIFIED",
           "measured_score": "3.5-4.0 / 6 (median 4.0)", "measured_by": _HARNESS},
    # Дна у решётки не было: «не добыто ничего» попадало в тот же `else`, что и
    # «есть абстракт», и пустой источник объявлялся уровнем L3 с подписью «разбор
    # шёл по одному абстракту». Уровень, который нечем обеспечить, — это не
    # низкий уровень, это его отсутствие, и называться он должен иначе.
    "L0": {"name": "nothing retrieved", "max_confidence": "NONE",
           "measured_score": "— (the audit does not run at this level)",
           "measured_by": "not applicable"},
}

# Тот же продукт, измеренный целиком: DOI или файл на входе, отчёт на выходе, всё
# как у пользователя. Медианы по ВСЕМ сохранённым прогонам `eval/results/` на
# 31.08, пересчёт командой `python3 eval/bench.py report`. Оба эталона доезжают до
# L1, поэтому разложения по уровням здесь нет и быть не может: это цена продукта,
# а не цена уровня.
END_TO_END = {
    "how": "eval/bench.py — retrieval, tables, three agents, number verification",
    "mcdonald-2026 (ours, 6 points)": "5.0-6.0 (median 5.5), n=16, level L1",
    "cheng-2024 (external, 4 points)": "3.0-3.5 (median 3.5), n=20, level L1",
    "note": ("These are not comparable with `measured_score` above: that one is the "
             "model on a prepared file, this one is the whole product on a real "
             "paper, where the numbers have to be found before they can be used."),
}


# Темп обращений к Europe PMC и повторы при отказе.
#
# У вызовов Vertex backoff был с первого дня (F-11), у Europe PMC не было ничего:
# ни паузы, ни повтора. При этом батч гонит три воркера, и каждый тянет поиск,
# full-text и zip приложений на десятки мегабайт — без всякого темпа.
#
# Почему это важно даже тогда, когда виноват не ты. 29.08 сервис отдавал голый
# `503 Service Temporarily Unavailable` от nginx — и ровно это же nginx отдаёт по
# умолчанию, когда срабатывает `limit_req`. То есть **с одной точки обзора авария
# и наказание за темп неотличимы**. Различить их удалось только вторым адресом:
# наш Cloud Run получил тот же 503, значит это была авария. Но полагаться на
# такую проверку в проде нельзя, и правильный вывод не «мы не виноваты», а
# «клиент обязан держать темп и переживать отказ».
#
# ⚠️ Интервал — допущение, а не документированный лимит: в справочнике 6.9.0
# ни лимитов, ни кодов ошибок нет (проверено), на страницах разработчика тоже.
# 0.34 с ≈ 3 запроса в секунду — величина того же порядка, что общепринятая
# вежливая норма для публичных научных API. Названа допущением намеренно.
_PACE_SECONDS = 0.34
_pace_lock = threading.Lock()
_last_call = 0.0

# Отказы, которые имеет смысл повторять. 404 сюда не входит: для наших вызовов он
# означает «этого у нас нет», и повтор ничего не изменит.
_RETRIABLE = {429, 500, 502, 503, 504}


def _wait_turn():
    """Не больше одного обращения к Europe PMC за интервал — на весь процесс.

    Замок общий на процесс, а не на поток: ограничение у сервиса на клиента, и
    три воркера батча, каждый со своим темпом, дают втрое больший общий поток.
    """
    global _last_call
    with _pace_lock:
        gap = time.monotonic() - _last_call
        if gap < _PACE_SECONDS:
            time.sleep(_PACE_SECONDS - gap)
        _last_call = time.monotonic()


def _get(url: str, as_json=True, timeout=60, attempts: int = 3):
    """Запрос к Europe PMC с темпом и повтором.

    `attempts` задаётся вызывающим, а не берётся общий: бюджет запроса Cloud Run —
    300 с, из которых 40-130 уходит на три вызова Vertex. Дешёвый поиск может
    позволить себе три попытки (задержки 2 и 4 с), а zip приложений с таймаутом
    90 с — ни одной: второй такой заход съел бы бюджет целиком.
    """
    delay = 2
    for i in range(attempts):
        _wait_turn()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            return json.loads(raw) if as_json else raw
        except urllib.error.HTTPError as e:
            if e.code not in _RETRIABLE or i == attempts - 1:
                raise
        except Exception:                                    # noqa: BLE001
            if i == attempts - 1:
                raise
        time.sleep(delay)
        delay *= 2


def attempt(name: str, fn, expected: bool = True, why_not: str = "") -> tuple:
    """Одна попытка добычи вместе с записью о ней. Возвращает `(результат|None, запись)`.

    Не бросает — и это главное в ней. Отказ добычи есть **сведение**, которое
    обязано дойти до отчёта, а не исключение, которое отчёт заменит. Раньше
    отказы делились на два одинаково плохих исхода: `except Exception: pass`
    (приложения) — и тогда отсутствие данных выдавалось за свойство статьи, либо
    голый вызов (full-text) — и тогда 404 вышестоящего превращался в наш 500.

    `expected` — выполнено ли документированное предусловие эндпоинта. Только от
    него зависит, чем считать отказ: сбоем сервиса или законным «этого нет».
    """
    if not expected:
        return None, {"source": name, "status": "not_applicable", "note": why_not}
    try:
        return fn(), {"source": name, "status": "ok"}
    except urllib.error.HTTPError as e:
        return None, {"source": name, "status": "upstream_error",
                      "note": f"HTTP {e.code}. Документированное условие доступности "
                              f"выполнено, значит это отказ сервиса, а не отсутствие "
                              f"данных у статьи"}
    except Exception as e:                                   # noqa: BLE001
        return None, {"source": name, "status": "unreachable",
                      "note": f"{type(e).__name__}: {e}"[:160]}


def lookup(doi: str) -> dict:
    """Метаданные статьи по DOI из Europe PMC.

    Поле `status` различает четыре исхода, которые раньше схлопывались в один
    `found: False`:

      `ok`          — статья найдена;
      `not_found`   — сервис ответил по существу, статьи у него нет;
      `degraded`    — сервис ответил, но не по существу;
      `unreachable` — сервис не ответил.

    Разница стоит ровно одной проверки, а её отсутствие стоило вот чего: 29.08
    Europe PMC около двадцати минут отдавал `200` и тело `{"version":"6.9"}` —
    17 байт, без `hitCount`, без эха `request`, без `resultList`, на любой запрос,
    включая `query=cancer`. Исправный ответ на тот же запрос — 1156 байт с
    `hitCount: 5553722`. Код читал `d.get("resultList", {}).get("result", [])`,
    получал пусто и объявлял, что такой статьи не существует; дальше пустой
    источник уходил в Vertex, тот отвечал `400 Model input cannot be empty`, и
    пользователь видел 500 с внутренностями Vertex.

    ⚠️ Признак `degraded` — **допущение, а не документированное правило**:
    структура ответа поиска, коды ошибок и лимиты в справочнике 6.9.0 не описаны
    (проверено: раздел searchPublications описывает только параметры и три
    resultType). Допущение выбрано заведомо безопасным — оно способно превратить
    молчаливо неверный ответ в честное «не знаю», но не наоборот.
    """
    q = urllib.parse.quote(f'DOI:"{doi}"')
    try:
        d = _get(f"{EPMC}/search?query={q}&format=json&resultType=core",
                 attempts=3)
    except Exception as e:                                   # noqa: BLE001
        return {"found": False, "doi": doi, "status": "unreachable",
                "error": f"{type(e).__name__}: {e}"[:160]}
    if not isinstance(d, dict) or "resultList" not in d:
        return {"found": False, "doi": doi, "status": "degraded",
                "error": "поиск ответил без resultList: сервис отвечает, но не по "
                         "существу — это не то же самое, что «статьи нет»"}
    res = (d.get("resultList") or {}).get("result") or []
    if not res:
        return {"found": False, "doi": doi, "status": "not_found"}
    r = res[0]
    return {
        "found": True, "doi": doi, "status": "ok",
        "pmid": r.get("pmid"), "pmcid": r.get("pmcid"),
        "title": r.get("title"),
        "journal": (r.get("journalInfo") or {}).get("journal", {}).get("title"),
        # Ключевое поле, а не справочное: именно оно, а не `in_epmc`, решает,
        # отдаст ли Europe PMC полный текст. См. шапку модуля.
        "open_access": r.get("isOpenAccess") == "Y",
        "in_epmc": r.get("inEPMC") == "Y",
        "has_supplementary": r.get("hasSuppl") == "Y",
        "abstract": r.get("abstractText"),
    }


def fetch_fulltext(pmcid: str) -> bytes:
    return _get(f"{EPMC}/{pmcid}/fullTextXML", as_json=False, attempts=2)


def fetch_supplementary(pmcid: str) -> bytes:
    # Таймаут больше, чем у метаданных: это zip приложений, и он бывает десятками
    # мегабайт — на 60 с наблюдался обрыв на статье с 35 таблицами приложения, то
    # есть ровно на той, ради которой путь и нужен. Но не «побольше на всякий
    # случай»: у запроса Cloud Run бюджет 300 с, из которых 40-130 уходит на три
    # вызова Vertex. 90 с — то, что остаётся, если добыче отдать примерно треть.
    return _get(f"{EPMC}/{pmcid}/supplementaryFiles", as_json=False,
                timeout=90, attempts=1)


def assess_level(meta: dict, has_fulltext: bool, has_appendix: bool) -> dict:
    """Какой уровень доказательности доступен для этой статьи.

    `has_fulltext` означает «тело статьи добыто». Означало это же только на пути
    B: путь A передавал сюда `bool(jats)` — «разобрались таблицы», — и
    полнотекстовая статья без единой таблицы получала L3, а отчёт печатал «This
    audit ran on the abstract alone», держа при этом всё тело в руках. Один
    параметр, два пути, разный смысл — тот же класс, что F-60 и F-61.
    """
    if has_fulltext and has_appendix:
        lvl = "L1"
    elif has_fulltext:
        lvl = "L2"
    elif (meta or {}).get("abstract"):
        lvl = "L3"
    else:
        lvl = "L0"
    out = {"level": lvl, **LEVELS[lvl]}
    if lvl == "L2":
        out["missing"] = ("appendix tables — the body of the paper is here, but baseline "
                          "tables and sensitivity analyses usually are not, and findings "
                          "resting on them cannot be checked (F-44)")
    elif lvl == "L3":
        out["missing"] = ("full text and appendix tables — with only the abstract there "
                          "are no numbers from this paper to check the audit against "
                          "(F-44)")
    elif lvl == "L0":
        out["missing"] = ("full text, appendix tables and the abstract — nothing was "
                          "retrieved at all. This is a statement about the retrieval, "
                          "not about the paper: see the `retrieval` log for which source "
                          "refused and whether it was entitled to")
    return out
