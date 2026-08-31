"""
HTTP-сервис «Я Правда».

Разворачивается на Cloud Run — это закрывает требования хакатона R3 (сервис
инфраструктуры Google Cloud) и R4 (доказательство, что бэкенд работает в облаке).

Эндпоинты:
  GET  /            статус сервиса и конфигурация — годится как пруф деплоя
  GET  /health      проверка живости
  POST /analyze     разбор статьи: {"doi": "..."} или {"text": "..."};
                    engine="direct" (по умолчанию) или "adk"
  POST /analyze/upload  разбор принесённых файлов (.pdf/.docx) — путь B
  GET  /levels      описание уровней доказательности с измеренными ценами
  GET  /audits      сохранённые одиночные разборы, новые первыми
  GET  /audits/{id} сохранённый разбор целиком
  GET  /audits/{id}/brief.md  одностраничный бриф в Markdown

Разбор закрыт ключом (`TRUTH_API_KEY`), чтение — нет: см. `require_key`.
"""
import hmac
import os
import pathlib
import sys
import threading

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import (__version__, brief, confidence, critic, pipeline,  # noqa: E402
                   retrieval, store)
from truth.batch import BUCKET                                # noqa: E402

PROMPT = (pathlib.Path(__file__).resolve().parent.parent
          / "truth" / "prompt_robins_e.md").read_text()

app = FastAPI(
    title="Я Правда / I Am Truth",
    description="Методологический аудит биомедицинских публикаций. "
                "Уровень доказательности определяется тем, что удалось достать, "
                "и выносится в отчёт вместе с находками.",
    version=__version__,
)


# --------------------------------------------------------------------- доступ
#
# Почему ключ вообще появился. Сервис стоит на публичном `.run.app` без
# авторизации, а один `POST /analyze` — это три вызова Gemini на чужой квоте.
# Пока об адресе никто не знает, это работает; как только адрес попадает в
# заявку и в README, стоимость запроса перестаёт быть нашей заботой и
# становится чьей угодно.
#
# Закрыты только те эндпоинты, которые тратят деньги. Чтение сохранённых
# разборов остаётся открытым намеренно: README ссылается на конкретные отчёты
# как на доказательство, и доказательство за паролем доказательством не
# является.
#
# Ключ не задан — сервис открыт, и это видно в `/health` полем `auth`. Тихо
# «защититься» пустым ключом было бы хуже, чем не защищаться: тогда открытый
# сервис выглядел бы закрытым.
API_KEY = os.environ.get("TRUTH_API_KEY", "").strip()

# Сколько разборов идут одновременно. Один разбор держит поток 40-130 секунд и
# три соединения к Vertex; без предела десяток одновременных запросов исчерпает
# и пул потоков uvicorn, и квоту (429 начинается с пятого вызова подряд, F-11).
MAX_CONCURRENT = int(os.environ.get("TRUTH_MAX_CONCURRENT", "2"))
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


def require_key(x_api_key: str | None = Header(None),
                authorization: str | None = Header(None)):
    """Ключ для эндпоинтов, которые стоят вызовов модели.

    Принимается и `X-API-Key: <key>`, и `Authorization: Bearer <key>` — второй
    вариант нужен потому, что curl-примеры в README пишут именно так, а третий
    способ передать одно и то же значение заводить незачем.

    Сравнение — `hmac.compare_digest`, а не `==`: разница во времени сравнения
    строк утекает длину совпавшего префикса. Это дешёвая привычка, а не защита
    от реального противника, но обратное тоже верно — писать `==` здесь не
    экономит ничего.
    """
    if not API_KEY:
        return
    given = x_api_key or ""
    if not given and authorization and authorization.lower().startswith("bearer "):
        given = authorization[7:].strip()
    if not given:
        raise HTTPException(401, {
            "message": "нужен ключ: заголовок X-API-Key или Authorization: Bearer",
            "hint": "чтение сохранённых разборов (/audits, /levels) открыто и ключа "
                    "не требует — закрыт только разбор, который тратит вызовы модели",
        })
    if not hmac.compare_digest(given, API_KEY):
        raise HTTPException(403, "неверный ключ")


class Busy(HTTPException):
    """Слотов нет. 429, а не 503: повторить имеет смысл, и мы говорим когда."""

    def __init__(self):
        super().__init__(429, {
            "message": f"сейчас идёт {MAX_CONCURRENT} разбора, больше одновременно "
                       f"не запускаем",
            "hint": "разбор занимает 40-130 секунд; повторите запрос через минуту",
        }, headers={"Retry-After": "60"})


class AnalyzeRequest(BaseModel):
    doi: str | None = None
    text: str | None = None
    # "direct" — оркестрация кодом, "adk" — граф Google ADK с инструментами у агентов.
    # По баллам пути равноценны на нашей выборке, но выборка неравная: у прямого
    # пути десять прогонов на эталон, у ADK два (медиана 5.25 против 5.0 на
    # McDonald, 3.5 против 3.5 на Cheng — F-46). Двух прогонов не хватает, чтобы
    # утверждать различие или его отсутствие. Что измерено твёрдо — время: ADK
    # шёл 137 и 291 секунду там, где прямой путь укладывается в 33-70. Поэтому
    # выбор явный, а по умолчанию стоит быстрый путь.
    engine: str = "direct"


INDEX = pathlib.Path(__file__).resolve().parent / "static" / "index.html"


@app.get("/", response_class=HTMLResponse)
def root():
    """Страница аудита. Один самодостаточный файл — без сборки и без CDN.

    Отчёт нельзя показывать как голый JSON: главное в нём — не текст находок, а
    **чем они обеспечены**. Уровень доказательности, число проверенных значений и
    ноль инверсий групп должны читаться раньше формулировок, иначе выводы выглядят
    убедительнее, чем данные под ними, — ровно та ошибка, которую продукт ищет в
    чужих статьях.
    """
    if INDEX.exists():
        return INDEX.read_text(encoding="utf-8")
    # запасной вид, если статика не доехала в образ
    return f"""<h1>I Am Truth</h1>
<p>Methodology audit for biomedical papers. Version {__version__}.</p>
<ul>
  <li>model: <code>{critic.MODEL}</code> (Vertex AI, {critic.LOCATION})</li>
  <li>project: <code>{critic.PROJECT}</code></li>
</ul>
<p>API docs: <a href="/docs">/docs</a></p>"""


@app.get("/health")
def health():
    # `auth` показывается намеренно: открытый сервис должен выглядеть открытым.
    # Иначе не отличить «ключ настроен» от «переменная не доехала в ревизию», а
    # второе выглядит как первое ровно до первого чужого счёта за Vertex.
    return {"status": "ok", "version": __version__, "model": critic.MODEL,
            "auth": "key" if API_KEY else "open",
            "max_concurrent": MAX_CONCURRENT}


@app.get("/levels")
def levels():
    """Уровни доказательности и измеренная цена каждого."""
    return {
        "levels": retrieval.LEVELS,
        "note": "Цены уровней получены замером на эталонном разборе, а не назначены. "
                "Ниже L1 статус CONFIRMED недостижим не потому, что модель ошибается — "
                "направление смещения она называет верно и на абстракте, — а потому, "
                "что обосновать его там нечем: находки опираются на общие свойства "
                "дизайна, а не на числа из документа (F-44).",
        "how_the_ceiling_is_applied":
            "Потолок — не подпись, а ограничение на шкале, значения которой "
            "проставляются по правилу в truth/confidence.py: вывод получает "
            "CONFIRMED, только если под ним есть число с весом улики не ниже "
            "шести бит, стоящее в ячейке с сошедшимся адресом. Где потолок "
            "реально понизил статус, отчёт пишет capped_from.",
        "statuses": confidence.WHY,
    }


def _persist(report: dict) -> dict:
    """Сохранить разбор и вернуть его же с меткой хранения.

    Ошибка записи не отменяет ответ: разбор уже сделан и стоил вызовов Vertex.
    Но и молчать о ней нельзя — исход записи виден в поле `stored`, иначе отчёт,
    которого нет в хранилище, выглядел бы неотличимо от сохранённого.
    """
    # Идентификатор проставляется ДО записи. Иначе сохранённая копия не знает
    # собственного имени: показанный отчёт нёс `audit_id`, а лежащий в хранилище —
    # нет, и по поднятому файлу нельзя было сказать, тот ли он самый. Поймано
    # сверкой показанного отчёта с поднятым по ссылке.
    try:
        audit_id = store.new_id(report)
        report["audit_id"] = audit_id
        r = store.put(report, audit_id=audit_id)
    except Exception as e:                                   # noqa: BLE001
        r = {"id": report.get("audit_id"), "stored": "none",
             "error": f"{type(e).__name__}: {e}"[:200]}
    # `stored` и `store_error` описывают запись, а не разбор, поэтому живут только
    # в ответе: внутри сохранённого файла поле «сохранено» было бы тавтологией.
    report["stored"] = r["stored"]
    if r.get("error"):
        report["store_error"] = r["error"]
    return report


# Ответ, когда разбирать нечего. Отдельный от 500 намеренно: 500 означает «у нас
# сломалось», а здесь не сломалось ничего — не дали входа. Код выбирается по
# журналу добычи: если отказал вышестоящий источник при выполненном условии
# доступности, это 503 и повторять имеет смысл; если источник законно ничего не
# должен, повторять бессмысленно, и это 422.
UPSTREAM = {"upstream_error", "unreachable", "degraded"}


def _nothing_retrieved(e) -> HTTPException:
    lvl = (e.gathered.get("level") or {})
    log = lvl.get("retrieval") or []
    upstream = [r for r in log if r.get("status") in UPSTREAM]
    hint = ("Приложите PDF статьи (и файл приложения, если он есть) — "
            "POST /analyze/upload, путь B. Он не зависит от Europe PMC.")
    return HTTPException(
        503 if upstream else 422,
        detail={
            "message": ("Источник не отдал текст статьи, разбирать нечего."
                        if upstream else
                        "Текст статьи получить неоткуда, разбирать нечего."),
            "hint": hint,
            "level": lvl.get("level"),
            # Журнал уходит пользователю целиком: «почему не получилось» — это
            # ответ, а не диагностика для логов.
            "retrieval": log,
        })


@app.post("/analyze", dependencies=[Depends(require_key)])
def analyze(req: AnalyzeRequest):
    if not req.doi and not req.text:
        raise HTTPException(400, "нужен doi или text")
    if not _slots.acquire(blocking=False):
        raise Busy()
    try:
        return _persist(pipeline.run(doi=req.doi, text=req.text, prompt=PROMPT,
                                     engine=req.engine))
    except pipeline.NothingRetrieved as e:
        raise _nothing_retrieved(e)
    except HTTPException:
        raise
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}"[:500])
    finally:
        _slots.release()


# Предел на файл. Статья с приложением — единицы мегабайт; 25 МБ с запасом
# перекрывает виденное (самый тяжёлый PDF в проверках — 5.9 МБ) и не даёт
# положить контейнер одним запросом.
MAX_UPLOAD = 25 * 1024 * 1024
# Предел на весь запрос и на число файлов. Одного предела на файл мало: сто
# файлов по 24 МБ проходили поштучную проверку и складывались в память целиком,
# потому что размер сверялся уже ПОСЛЕ `read()`. Статья с приложениями — это
# два-три файла; десять взято с запасом на журналы, дробящие приложение.
MAX_FILES = 10
MAX_REQUEST = 60 * 1024 * 1024


@app.post("/analyze/upload", dependencies=[Depends(require_key)])
def analyze_upload(request: Request,
                   files: list[UploadFile] = File(...),
                   doi: str | None = Form(None),
                   engine: str = Form("direct")):
    """Путь B: статья принесена пользователем.

    Существует потому, что автоматическая добыча берёт около 55% статей класса
    (F-25) — остальное закрыто Cloudflare или TDM-токеном издателя. Человеку та же
    статья, как правило, доступна. Принимаются .pdf (как видит статью человек) и
    .docx (как приходят приложения). Файлов может быть несколько: у большинства
    журналов приложение лежит отдельным файлом, а именно оно поднимает уровень
    до L1 — см. замер F-26.

    `doi` необязателен: с ним подтягиваются метаданные и, если приложения есть в
    Europe PMC, они добавляются к принесённому тексту.
    """
    # Обработчик СИНХРОННЫЙ намеренно. Внутри стоит `pipeline.run`, который идёт
    # 40-130 секунд и ничего не отдаёт циклу событий. В `async def` он блокировал
    # весь uvicorn: пока разбирается один принесённый файл, не отвечали ни
    # /health, ни статика, ни второй запрос. Синхронный обработчик FastAPI уводит
    # в пул потоков, и это ровно то, что здесь нужно.
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"файлов {len(files)}, принимаем не больше {MAX_FILES}")
    # Общий вес запроса — до чтения хоть одного байта в память.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REQUEST:
        raise HTTPException(413, f"запрос больше {MAX_REQUEST // 1024 // 1024} МБ")

    uploads = []
    for f in files:
        # Размер спрашивается у Starlette, а не измеряется после `read()`:
        # `read()` тянет файл в память целиком, и проверять его размер потом —
        # значит проверять уже случившееся. `size` бывает None на клиентах,
        # не приславших длину части; тогда остаётся прежняя проверка постфактум,
        # но она уже прикрыта общим пределом на запрос выше.
        if f.size is not None and f.size > MAX_UPLOAD:
            raise HTTPException(413, f"{f.filename}: больше {MAX_UPLOAD // 1024 // 1024} МБ")
        blob = f.file.read()
        if len(blob) > MAX_UPLOAD:
            raise HTTPException(413, f"{f.filename}: больше {MAX_UPLOAD // 1024 // 1024} МБ")
        if not blob:
            raise HTTPException(400, f"{f.filename}: пустой файл")
        uploads.append((f.filename or "file", blob))
    if not uploads:
        raise HTTPException(400, "нужен хотя бы один файл")
    if not _slots.acquire(blocking=False):
        raise Busy()
    try:
        return _persist(pipeline.run(doi=doi, prompt=PROMPT, uploads=uploads,
                                     engine=engine))
    except pipeline.NothingRetrieved as e:
        raise _nothing_retrieved(e)
    except HTTPException:
        raise
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}"[:500])
    finally:
        _slots.release()


@app.get("/audits")
def audits(limit: int = 50):
    """Сохранённые одиночные разборы. Отдельно от батча: `/runs` — прогоны Job."""
    return {"bucket": store.BUCKET, "prefix": store.PREFIX,
            "audits": store.list_ids(limit)}


@app.get("/audits/{audit_id}")
def audit(audit_id: str):
    r = store.get(audit_id)
    if r is None:
        raise HTTPException(404, f"разбор {audit_id} не найден")
    return r


@app.get("/audits/{audit_id}/brief.md", response_class=PlainTextResponse)
def audit_brief(audit_id: str):
    """Одностраничный бриф. Отдаётся файлом: его прикладывают к письму, а не читают
    с экрана — за этим он и заведён."""
    r = store.get(audit_id)
    if r is None:
        raise HTTPException(404, f"разбор {audit_id} не найден")
    return PlainTextResponse(
        brief.render(r), media_type="text/markdown; charset=utf-8",
        headers={"content-disposition":
                 f'attachment; filename="{audit_id}-brief.md"'})


@app.get("/runs")
def runs():
    """Батч-прогоны, выполненные Cloud Run Job."""
    try:
        from google.cloud import storage
        b = storage.Client().bucket(BUCKET)
        ids = sorted({n.name.split("/")[0] for n in b.list_blobs()
                      if n.name.endswith("summary.json")}, reverse=True)
        return {"bucket": BUCKET, "runs": ids}
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(503, f"хранилище недоступно: {type(e).__name__}")


@app.get("/runs/{run_id}")
def run_summary(run_id: str):
    """Сводка одного батч-прогона: распределение по уровням и статистика сверки."""
    import json as _json
    try:
        from google.cloud import storage
        blob = storage.Client().bucket(BUCKET).blob(f"{run_id}/summary.json")
        if not blob.exists():
            raise HTTPException(404, f"прогон {run_id} не найден")
        return _json.loads(blob.download_as_text())
    except HTTPException:
        raise
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(503, f"хранилище недоступно: {type(e).__name__}")
