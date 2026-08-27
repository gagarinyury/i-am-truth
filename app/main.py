"""
HTTP-сервис «Я есть Правда».

Разворачивается на Cloud Run — это закрывает требования хакатона R3 (сервис
инфраструктуры Google Cloud) и R4 (доказательство, что бэкенд работает в облаке).

Эндпоинты:
  GET  /            статус сервиса и конфигурация — годится как пруф деплоя
  GET  /health      проверка живости
  POST /analyze     разбор статьи: {"doi": "..."} или {"text": "..."}
  GET  /levels      описание уровней доказательности с измеренными ценами
"""
import os
import pathlib
import sys

from fastapi import FastAPI, HTTPException
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


@app.get("/", response_class=HTMLResponse)
def root():
    return f"""<h1>Я есть Правда / I Am Truth</h1>
<p>Методологический аудит биомедицинских публикаций. Версия {__version__}.</p>
<ul>
  <li>модель: <code>{critic.MODEL}</code> (Vertex AI, {critic.LOCATION})</li>
  <li>проект: <code>{critic.PROJECT}</code></li>
  <li>рубрика разбора: ROBINS-E, семь доменов</li>
</ul>
<p>Интерактивная документация: <a href="/docs">/docs</a></p>
<pre>curl -X POST $URL/analyze -H 'Content-Type: application/json' \\
     -d '{{"doi": "10.1136/jitc-2025-014726"}}'</pre>"""


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__, "model": critic.MODEL}


@app.get("/levels")
def levels():
    """Уровни доказательности и измеренная цена каждого."""
    return {
        "levels": retrieval.LEVELS,
        "note": "Цены уровней получены замером на эталонном разборе, а не назначены. "
                "На L2 и L3 модель систематически ошибается направлением confounding, "
                "поэтому статус CONFIRMED там недостижим.",
    }


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if not req.doi and not req.text:
        raise HTTPException(400, "нужен doi или text")
    try:
        return pipeline.run(doi=req.doi, text=req.text, prompt=PROMPT)
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
