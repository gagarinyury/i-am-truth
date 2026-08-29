"""
Тот же аудит, выраженный в Google ADK.

Зачем это здесь. Требование R2 закрыто и без ADK — `google-genai` есть в списке
допустимых каркасов. Но два прохода по документу, идущие параллельно, в обычном коде
существуют как `ThreadPoolExecutor` в середине функции: чтобы это увидеть, надо читать
`pipeline.run`. В ADK то же самое — объявление:

    ParallelAgent(sub_agents=[critic, baseline])

Что здесь по-настоящему меняется, а не переоформляется: **у агентов появляются
инструменты**. В прямом пайплайне `stats_tool` считает ARR и NNT уже *после* модели, и
сама модель им воспользоваться не может — ей остаётся либо считать в уме (запрещено
D-09), либо не считать вовсе. Здесь она вызывает калькулятор во время рассуждения. То же
с проверкой числа: агент может сверить значение с источником *до* того, как напишет его
в отчёт, вместо того чтобы узнать о проблеме постфактум от слоя 4.

Слой 4 при этом никуда не девается. Проверка агентом самого себя — удобство, а не
гарантия: гарантией остаётся независимая сверка после модели (D-14).

Запуск:
    from truth import adk_agent
    result = adk_agent.run(pdf_bytes=..., paper_text=..., meta=...)
"""
import asyncio
import contextvars
import json
import os
import pathlib
import time

from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.runners import InMemoryRunner
from google.adk.workflow._retry_config import RetryConfig
from google.genai import types

from . import critic, verify_numbers
from .stats_tool import TwoByTwo, e_value

HERE = pathlib.Path(__file__).resolve().parent
APP = "i_am_truth"

# ADK ходит в Vertex через переменные окружения, а не через параметры клиента.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", critic.PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", critic.LOCATION)

# Источник для инструмента сверки. Кладётся сюда перед запуском: ADK передаёт
# инструментам только аргументы модели, а тащить весь текст статьи через них —
# значит просить модель его же и процитировать, что лишает проверку смысла.
# Источник для инструмента самопроверки. Хранится в ContextVar, а не в модульном
# словаре: сервис обрабатывает запросы конкурентно, и на глобальной переменной два
# одновременных разбора через ADK перетирали источник друг друга — числа статьи A
# сверялись бы с текстом статьи B, а завершение любого из них обнуляло источник у
# второго. Это ровно тот класс подмены, который проект ищет у чужих работ, и он же
# был запрещён себе для временных файлов (комментарий в pipeline.py, D-14).
# ContextVar изолирует значение по контексту исполнения: `asyncio.run` в каждом
# потоке заводит свой, и задачи внутри него наследуют копию.
_SOURCE: contextvars.ContextVar = contextvars.ContextVar("truth_adk_source", default="")


# --------------------------------------------------------------------------- tools

def compute_two_by_two(exposed_events: int, exposed_total: int,
                       control_events: int, control_total: int) -> dict:
    """Compute risk measures from a 2x2 table.

    Use this instead of doing arithmetic yourself. Returns relative risk, odds ratio,
    absolute risk reduction, number needed to treat and the confidence interval.

    Args:
        exposed_events: outcome events in the exposed group.
        exposed_total: total participants in the exposed group.
        control_events: outcome events in the control group.
        control_total: total participants in the control group.
    """
    try:
        t = TwoByTwo(exposed_events, exposed_total, control_events, control_total)
        return t.report()
    except Exception as e:                                   # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def compute_e_value(risk_ratio: float) -> dict:
    """Compute the E-value for an observed risk ratio (VanderWeele & Ding 2017).

    The E-value is how strong an unmeasured confounder would have to be, on the risk
    ratio scale, to explain the association away. Use it whenever you judge residual
    confounding.

    Args:
        risk_ratio: the observed risk ratio or odds ratio.
    """
    try:
        return {"risk_ratio": risk_ratio, "e_value": e_value(risk_ratio)}
    except Exception as e:                                   # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def check_number_in_source(value: str, label: str) -> dict:
    """Check that a number really appears in the source document before you report it.

    Use this for any figure you are about to cite. Returns the status and the
    surrounding context from the document, so you can confirm the number belongs to the
    group you think it does.

    Args:
        value: the number as you intend to write it, e.g. "19.7".
        label: what the number refers to, e.g. "Charlson 5+ in the GLP-1 group".
    """
    src = _SOURCE.get()
    if not src:
        return {"status": "NO_SOURCE", "note": "source text was not loaded"}
    r = verify_numbers.verify([{"value": str(value), "label": label}], src)
    c = r["claims"][0]
    return {"status": c["status"], "context": (c.get("context") or "")[:300]}


# -------------------------------------------------------------------------- agents

def build_graph(model: str = None) -> ParallelAgent:
    """Два прохода по документу, объявленные параллельными.

    Ровно те же промпты, что в прямом пайплайне: расхождение промптов между двумя
    путями означало бы, что сравнивать их результаты нельзя.
    """
    m = model or critic.MODEL
    # Vertex отдаёт 429 уже на пятом запросе подряд (F-11), а два агента с
    # инструментами делают по несколько раундов каждый — на третьем прогоне ADK-путь
    # так и упал. У ADK свой клиент, наш backoff из critic.call его не покрывает,
    # поэтому retry объявляется здесь же, в графе.
    retry = RetryConfig(max_attempts=4, initial_delay=8.0, max_delay=120.0,
                        backoff_factor=2.0)
    critic_agent = LlmAgent(
        name="critic_robins_e",
        model=m,
        instruction=(HERE / "prompt_robins_e.md").read_text(),
        tools=[compute_two_by_two, compute_e_value, check_number_in_source],
        output_key="robins_e",
        retry_config=retry,
    )
    baseline_agent = LlmAgent(
        name="baseline_comparability",
        model=m,
        instruction=(HERE / "prompt_baseline_table.md").read_text(),
        tools=[check_number_in_source],
        output_key="baseline",
        retry_config=retry,
    )
    time_agent = LlmAgent(
        name="time_related_biases",
        model=m,
        instruction=(HERE / "prompt_time_biases.md").read_text(),
        tools=[check_number_in_source],
        output_key="timing",
        retry_config=retry,
    )
    return ParallelAgent(
        name="audit",
        sub_agents=[critic_agent, baseline_agent, time_agent],
        description="Три независимых прохода по одной статье: семь доменов ROBINS-E, "
                    "сопоставимость групп и временнáя структура.",
    )


# ---------------------------------------------------------------------------- run

async def _run_async(pdfs: list, paper_text: str, model: str = None) -> dict:
    runner = InMemoryRunner(agent=build_graph(model), app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="svc")

    parts = [types.Part.from_bytes(data=b, mime_type="application/pdf") for b in (pdfs or [])]
    parts.append(types.Part(text=paper_text))

    tool_calls = []
    async for ev in runner.run_async(
            user_id="svc", session_id=session.id,
            new_message=types.Content(role="user", parts=parts)):
        if ev.content and ev.content.parts:
            for p in ev.content.parts:
                if p.function_call:
                    tool_calls.append(p.function_call.name)

    state = (await runner.session_service.get_session(
        app_name=APP, user_id="svc", session_id=session.id)).state

    out = {"tool_calls": tool_calls}
    for key in ("robins_e", "baseline", "timing"):
        parsed, err = critic.parse_json_answer(state.get(key) or "")
        out[key] = parsed
        if err:
            out.setdefault("parse_errors", {})[key] = err
    return out


def run(paper_text: str, pdfs: list = None, source_text: str = None,
        model: str = None, attempts: int = 3) -> dict:
    """Синхронная обёртка. `source_text` — то, по чему агент сверяет числа.

    Второй рубеж защиты от 429: `RetryConfig` внутри графа повторяет отдельный вызов,
    а здесь повторяется весь прогон, если из ADK всё-таки прилетела ошибка исчерпания
    квоты. Один упавший прогон из трёх на замере — достаточная причина.
    """
    token = _SOURCE.set(source_text or paper_text)
    delay = 15
    try:
        for i in range(attempts):
            try:
                return asyncio.run(_run_async(pdfs, paper_text, model))
            except BaseException as e:                       # noqa: BLE001
                # ADK заворачивает ошибки агентов в ExceptionGroup, поэтому
                # смотрим на текст: он сохраняется и во вложенных исключениях.
                if ("RESOURCE_EXHAUSTED" not in str(e) and "429" not in str(e)) \
                        or i == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
    finally:
        _SOURCE.reset(token)
