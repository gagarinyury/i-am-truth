"""
HTTP-сервис «Я есть Правда».

Разворачивается на Cloud Run — это закрывает требования хакатона R3 (сервис
инфраструктуры Google Cloud) и R4 (доказательство, что бэкенд работает в облаке).

Эндпоинты:
  GET  /            статус сервиса и конфигурация — годится как пруф деплоя
  GET  /health      проверка живости
  POST /analyze     разбор статьи: {"doi": "..."} или {"text": "..."};
                    engine="direct" (по умолчанию) или "adk"
  POST /analyze/upload  разбор принесённых файлов (.pdf/.docx) — путь B
  GET  /levels      описание уровней доказательности с измеренными ценами
"""
import os
import pathlib
import sys

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import __version__, critic, pipeline, retrieval   # noqa: E402
from truth.batch import BUCKET                                # noqa: E402

PROMPT = (pathlib.Path(__file__).resolve().parent.parent
          / "truth" / "prompt_robins_e.md").read_text()

app = FastAPI(
    title="Я есть Правда / I Am Truth",
    description="Методологический аудит биомедицинских публикаций. "
                "Уровень доказательности определяется тем, что удалось достать, "
                "и выносится в отчёт вместе с находками.",
    version=__version__,
)


class AnalyzeRequest(BaseModel):
    doi: str | None = None
    text: str | None = None
    # "direct" — оркестрация кодом, "adk" — граф Google ADK с инструментами у агентов.
    # По баллам пути равноценны (медиана 5.5/6 у обоих, F-46); ADK вдвое медленнее,
    # поэтому выбор явный, а не спрятанный в дефолте.
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
    return {"status": "ok", "version": __version__, "model": critic.MODEL}


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
    }


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if not req.doi and not req.text:
        raise HTTPException(400, "нужен doi или text")
    try:
        return pipeline.run(doi=req.doi, text=req.text, prompt=PROMPT,
                            engine=req.engine)
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}"[:500])


# Предел на файл. Статья с приложением — единицы мегабайт; 25 МБ с запасом
# перекрывает виденное (самый тяжёлый PDF в проверках — 5.9 МБ) и не даёт
# положить контейнер одним запросом.
MAX_UPLOAD = 25 * 1024 * 1024


@app.post("/analyze/upload")
async def analyze_upload(files: list[UploadFile] = File(...),
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
    uploads = []
    for f in files:
        blob = await f.read()
        if len(blob) > MAX_UPLOAD:
            raise HTTPException(413, f"{f.filename}: больше {MAX_UPLOAD // 1024 // 1024} МБ")
        if not blob:
            raise HTTPException(400, f"{f.filename}: пустой файл")
        uploads.append((f.filename or "file", blob))
    if not uploads:
        raise HTTPException(400, "нужен хотя бы один файл")
    try:
        return pipeline.run(doi=doi, prompt=PROMPT, uploads=uploads, engine=engine)
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}"[:500])


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
