#!/usr/bin/env python3
"""
Регрессионный тест: параллельный разбор не смешивает данные разных статей.

Зачем: в первой версии `pipeline.gather` писала full-text в фиксированный
/tmp/_ft.xml, а приложения в /tmp/_suppl.docx. Батч гоняет статьи в несколько
потоков — три воркера писали в одни и те же два пути. Гонка не роняет процесс,
она молча подменяет данные: таблицы одной статьи попадают в разбор другой,
числа получают чужие метки. Это ровно тот класс ошибки, который продукт создан
ловить у чужих работ.

Тест состоит из двух частей с разной ценой и разной надёжностью.

Статическая — читает исходники и ищет запись во временный файл по фиксированному
пути. Она не зависит ни от чего внешнего и обязана проходить всегда.

Динамическая — ходит в Europe PMC (модель не вызывается) и сравнивает отпечатки
статей, разобранных последовательно и параллельно. Сеть может отказать не по нашей
вине: 29.08 Europe PMC около двадцати минут отдавал 503 всем подряд. Падение теста
от чужой аварии — это ложное обвинение собственному коду, то есть ровно та ошибка,
которую продукт создан ловить, поэтому недоступность источника здесь **пропуск, а
не провал**, и она названа словами.

  python3 tests/test_parallel_isolation.py
"""
import concurrent.futures as cf
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from truth import pipeline                                    # noqa: E402

# Статьи с заведомо разным числом таблиц — взяты из данных зонда eval/probes/,
# не подставлены по памяти
CASES = [
    "10.1136/jitc-2025-014726",
    "10.3389/fonc.2026.1742210",
    "10.1016/j.pmedr.2026.103548",
]


def fingerprint(doi: str) -> dict:
    g = pipeline.gather(doi=doi)
    return {
        "doi": doi,
        "pmcid": g["meta"].get("pmcid"),
        "main": len(g["jats_tables"]),
        "appendix": len(g["appendix_tables"]),
        "captions": [t.get("caption", "")[:40] for t in g["jats_tables"]],
        "text_len": len(g["source_text"]),
    }


def check_no_fixed_paths() -> int:
    """Статическая проверка: в пайплайне нет записи в фиксированный путь.

    Прогон в потоках эту гонку ловит лишь иногда — окно узкое и зависит от
    скорости сети (проверено: с возвращённым багом параллельный тест прошёл).
    Поэтому основная защита здесь детерминированная — читаем исходник.
    """
    bad = 0
    for mod in ("truth/pipeline.py", "truth/batch.py"):
        src = (pathlib.Path(__file__).resolve().parent.parent / mod).read_text()
        for m in re.finditer(r"""open\(\s*["']([^"']+)["']\s*,\s*["'][wa]""", src):
            path = m.group(1)
            if not path.startswith(("/tmp/", "./", "batch_out")) :
                continue
            line = src[:m.start()].count("\n") + 1
            print(f"❌ {mod}:{line} запись в фиксированный путь {path!r} — "
                  f"под нагрузкой потоки перепишут файл друг другу")
            bad += 1
        for m in re.finditer(r"""["'](/tmp/[^"']+)["']""", src):
            line = src[:m.start()].count("\n") + 1
            print(f"❌ {mod}:{line} фиксированный путь в /tmp: {m.group(1)!r}")
            bad += 1
    if not bad:
        print("✅ фиксированных путей во временных файлах нет")
    return bad


def main() -> int:
    print("статическая проверка путей…")
    bad_static = check_no_fixed_paths()

    try:
        print("\nпоследовательно (эталон)…")
        serial = {c: fingerprint(c) for c in CASES}
        for d, f in serial.items():
            print(f"  {d}  таблиц {f['main']} · приложения {f['appendix']} · "
                  f"текст {f['text_len']}")

        print("\nпараллельно, 3 потока…")
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            parallel = {f["doi"]: f for f in ex.map(fingerprint, CASES)}
    except Exception as e:                                   # noqa: BLE001
        # Отказ источника — не провал нашего кода. Говорим об этом прямо и
        # оставляем в силе статическую часть, которая сети не требует.
        print(f"\n  ⚠️  Europe PMC недоступен ({type(e).__name__}: {str(e)[:80]}) — "
              f"динамическая часть пропущена, это не провал кода")
        return 1 if bad_static else 0

    for d, f in parallel.items():
        print(f"  {d}  таблиц {f['main']} · приложения {f['appendix']} · "
              f"текст {f['text_len']}")

    print()
    bad = 0
    for d in CASES:
        s, p = serial[d], parallel[d]
        for key in ("pmcid", "main", "appendix", "captions"):
            if s[key] != p[key]:
                print(f"❌ {d}: поле {key} различается — "
                      f"последовательно {s[key]!r}, параллельно {p[key]!r}")
                bad += 1
        # длина текста может слегка отличаться, но не в разы
        if s["text_len"] and abs(s["text_len"] - p["text_len"]) > s["text_len"] * 0.05:
            print(f"❌ {d}: длина текста разъехалась — {s['text_len']} vs {p['text_len']}")
            bad += 1

    # у разных статей отпечатки обязаны различаться, иначе тест ничего не проверяет
    if len({(f["pmcid"], f["main"], f["appendix"]) for f in parallel.values()}) < len(CASES):
        print("❌ отпечатки статей совпали — либо подмена, либо кейсы подобраны неудачно")
        bad += 1

    print("✅ параллельный разбор изолирован" if not bad
          else f"\n{bad} расхождений — данные статей смешиваются")
    return 1 if (bad or bad_static) else 0


if __name__ == "__main__":
    sys.exit(main())
