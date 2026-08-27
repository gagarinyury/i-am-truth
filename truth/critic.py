"""
Layer 2/3 — вызов модели с промптом критика.

Модель: gemini-3.7-flash на Vertex AI. Выбор обоснован в D-01: правила хакатона
требуют Gemini 3.5+, а Pro-линейка на Vertex обрывается на 3.1 (F-01).
Только Flash на всех слоях — D-08.
"""
import json
import os
import re
import time

from google import genai
from google.genai import types

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "merci-prod")
LOCATION = os.environ.get("VERTEX_LOCATION", "global")
MODEL = os.environ.get("TRUTH_MODEL", "gemini-3.7-flash")
MAX_OUTPUT_TOKENS = 32000     # F-02: thinking-токены считаются сюда же

_client = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    return _client


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


def call(system: str, user: str, model: str = MODEL, attempts: int = 5) -> dict:
    """Вызов с backoff: Vertex отдаёт 429 уже на пятом запросе подряд (F-11)."""
    delay = 8
    for i in range(attempts):
        try:
            resp = client().models.generate_content(
                model=model,
                contents=user,
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


def critique(paper_text: str, prompt: str, model: str = MODEL) -> dict:
    """Разбор статьи. Возвращает разобранный JSON и метаданные вызова."""
    r = call(prompt, paper_text, model=model)
    parsed, err = parse_json_answer(r["text"])
    return {"findings": parsed, "parse_error": err,
            "usage": r["usage"], "model": model,
            "raw": None if parsed else r["text"][:2000]}
