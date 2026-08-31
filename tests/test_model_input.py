#!/usr/bin/env python3
"""
Вход модели: таблицы приложения переживают предел, текст уступает первым.

Дефект, ради которого написан тест. `pipeline.run` собирал вход как
`текст статьи + блок таблиц`, а потом резал результат: `paper[:400000]`. Внутри
блока приложения аккуратно ставились первыми — забота, которая ничего не давала,
потому что весь блок лежал в хвосте, а хвост и срезался. То есть усечение било
ровно по тому, ради чего построен проект: приложение решает исход разбора (F-26).

Замер на живой статье показывал, насколько близко: `10.1136/jitc-2025-014726`
собирался в 340 807 знаков при пределе 400 000 — 85%. Статья с приложением вдвое
крупнее теряла бы приложение молча, сохраняя в шапке отчёта уровень L1: уровень
описывает добычу, а не то, что доехало до модели.

Тест держит три свойства: порядок частей, направление усечения и видимость
усечения в отчёте.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import pipeline                                    # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


APPENDIX = {"caption": "eTable 1. Baseline characteristics",
            "columns": ["Characteristic", "GLP-1", "Control"],
            "rows": [["Charlson 5+", "19.7", "10.4"], ["Age", "62.1", "61.8"]]}
MAIN = {"label": "Table 1", "caption": "Cohort",
        "columns": ["n", "events"], "rows": [["15264", "247"]]}


def g(text_len, tables=True):
    return {"source_text": "текст статьи. " * (text_len // 14),
            "jats_tables": [MAIN] if tables else [],
            "appendix_tables": [APPENDIX] if tables else []}


print("вход помещается целиком…")
paper, note = pipeline.compose_input(g(1000))
check("текст на месте", "текст статьи" in paper)
check("приложение на месте", "eTable 1" in paper)
check("усечения нет", note["truncated"] is False)
check("приложение стоит раньше текста статьи",
      paper.index("eTable 1") < paper.index("текст статьи"))
check("19.7 и 10.4 из приложения дошли", "19.7" in paper and "10.4" in paper)

print("\nтекст заведомо больше предела…")
paper, note = pipeline.compose_input(g(500_000))
check("предел соблюдён", len(paper) <= pipeline.MODEL_INPUT_LIMIT, str(len(paper)))
check("приложение уцелело", "eTable 1" in paper and "19.7" in paper)
check("таблицы отданы целиком", note["tables_kept"] == note["tables_chars"])
check("срезан именно текст", note["source_kept"] < note["source_chars"])
check("усечение видно в записи", note["truncated"] is True and "note" in note)

print("\nтаблицы сами по себе больше предела…")
# Модели таблицы подаются усечёнными по построению (40 таблиц по 40 строк),
# поэтому «блок больше предела» набирается длиной ячеек, а не их числом.
CELL = "значение " * 40
huge = {"source_text": "текст статьи",
        "jats_tables": [],
        "appendix_tables": [{"caption": f"eTable {i}", "columns": ["a", "b"],
                             "rows": [[CELL, CELL] for _ in range(40)]}
                            for i in range(40)]}
paper, note = pipeline.compose_input(huge)
check("предел соблюдён и здесь", len(paper) <= pipeline.MODEL_INPUT_LIMIT, str(len(paper)))
check("текст не отправлен вовсе", note["source_kept"] == 0)
check("причина названа словами", "exceed" in (note.get("note") or ""))

print("\nтаблиц нет вовсе…")
paper, note = pipeline.compose_input(g(100, tables=False))
check("текст отдан как есть", paper.startswith("текст статьи"))
check("таблиц ноль", note["tables_chars"] == 0)

print("\nзапись доходит до отчёта…")
gathered = {"source_text": "текст", "jats_tables": [], "appendix_tables": [],
            "meta": {}, "level": {"level": "L3", "max_confidence": "PLAUSIBLE-UNVERIFIED"}}
rep = pipeline._assemble(gathered, None, model_input={"truncated": True, "limit": 7})
check("поле model_input есть в отчёте", rep.get("model_input") is not None)
check("оно рядом с уровнем, а не в конце",
      list(rep).index("model_input") < list(rep).index("findings"))

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
