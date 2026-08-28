"""
Layer 2/3 — вызов модели с промптом критика.

Модель: gemini-3.7-flash на Vertex AI. Выбор обоснован в D-01: правила хакатона
требуют Gemini 3.5+, а Pro-линейка на Vertex обрывается на 3.1 (F-01).
Только Flash на всех слоях — D-08.
"""
import json
import os
import re
import threading
import time

from google import genai
from google.genai import types

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "merci-prod")
LOCATION = os.environ.get("VERTEX_LOCATION", "global")
MODEL = os.environ.get("TRUTH_MODEL", "gemini-3.7-flash")
MAX_OUTPUT_TOKENS = 32000     # F-02: thinking-токены считаются сюда же

# Клиент — на поток, а не один на процесс.
# Батч гоняет статьи в ThreadPoolExecutor, и общий клиент там разваливается:
# «RuntimeError: Cannot send a request, as the client has been closed» —
# поймано на первом облачном прогоне Cloud Run Job 27.08, 2 статьи из 8.
_local = threading.local()


def client() -> genai.Client:
    c = getattr(_local, "client", None)
    if c is None:
        c = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
        _local.client = c
    return c


def parse_json_answer(raw: str):
    """Модель просят вернуть голый JSON, но иногда приходит в ```-заборе."""
    if not raw:
        return None, "пустой ответ (возможно, бюджет ушёл в thinking)"
    t = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, flags=re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t), None
    except json.JSONDecodeError as e:
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1]), None
            except json.JSONDecodeError:
                pass
        return None, f"не JSON: {e}"


def call(system: str, user: str, model: str = MODEL, attempts: int = 5,
         pdfs: list = None) -> dict:
    """Вызов с backoff: Vertex отдаёт 429 уже на пятом запросе подряд (F-11).

    `pdfs` — список байтовых PDF, которые уходят модели как есть. Gemini читает
    PDF нативно, вместе с вёрсткой: на McDonald et al. он берёт из Appendix
    Table A1 строку «5+ very high» правильно, тогда как в текстовом слое она
    вырождается в «51 very high» из-за шрифта. Свой разбор таблиц соревноваться
    с этим не может, поэтому и не пытается — правило «сначала искать готовое».
    """
    parts = ([types.Part.from_bytes(data=b, mime_type="application/pdf") for b in (pdfs or [])]
             + [types.Part.from_text(text=user)])
    delay = 8
    for i in range(attempts):
        try:
            resp = client().models.generate_content(
                model=model,
                contents=parts,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    temperature=0,
                ),
            )
            u = resp.usage_metadata
            return {
                "text": resp.text,
                "usage": {
                    "prompt": getattr(u, "prompt_token_count", None),
                    "output": getattr(u, "candidates_token_count", None),
                    "thoughts": getattr(u, "thoughts_token_count", None),
                    "total": getattr(u, "total_token_count", None),
                },
            }
        except Exception as e:                                # noqa: BLE001
            transient = any(s in str(e) for s in
                            ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500"))
            if not transient or i == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("исчерпаны попытки")


def critique(paper_text: str, prompt: str, model: str = MODEL,
             pdfs: list = None) -> dict:
    """Разбор статьи. Возвращает разобранный JSON и метаданные вызова."""
    r = call(prompt, paper_text, model=model, pdfs=pdfs)
    parsed, err = parse_json_answer(r["text"])
    return {"findings": parsed, "parse_error": err,
            "usage": r["usage"], "model": model,
            "raw": None if parsed else r["text"][:2000]}
