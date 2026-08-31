#!/usr/bin/env python3
"""
Прогон всех тестов одной командой.

    python3 tests/run_all.py            # всё
    python3 tests/run_all.py cells      # только те, чьё имя содержит «cells»

Зачем отдельный раннер, а не pytest. Тесты здесь написаны как сценарии с
печатью ✓/✗ и осмысленными подписями: каждая строка называет свойство, а не имя
функции, и читается как утверждение о продукте. Переписывать их под pytest
значило бы поменять читаемый вывод на стандартный ради стандартности, а
поставить pytest в `requirements.txt` — тянуть в образ Cloud Run зависимость,
которой там нечего делать. Раннер нужен ровно за одним: чтобы «прогнать тесты»
было одной командой, а не пятнадцатью, и чтобы падение хоть одного файла давало
ненулевой код возврата.

Тесты не ходят в сеть и не зовут модель — ни один из них не стоит ни цента и не
требует ADC. Сетевые проверки живут в `eval/`, и это разные вещи: здесь
проверяется код, там измеряется продукт.
"""
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted(p for p in HERE.glob("test_*.py") if needle in p.name)
    if not files:
        print(f"нет тестов по фильтру «{needle}»")
        return 1

    failed, t0 = [], time.time()
    for p in files:
        r = subprocess.run([sys.executable, str(p)], cwd=ROOT,
                           capture_output=True, text=True)
        body = r.stdout + r.stderr
        # Код возврата — не единственный признак: часть файлов печатает ✗ и
        # всё равно завершается нулём, если автор забыл `sys.exit`. Смотрим на оба.
        # Разметка в файлах разная — ✓/✗ в одних, ✅/❌ в других, — и приводить
        # её к общей ради красоты счётчика значило бы переписать восемнадцать
        # рабочих тестов. Раннер считает обе.
        ok_marks, bad_marks = body.count("✓") + body.count("✅"), \
                              body.count("✗") + body.count("❌")
        bad = r.returncode != 0 or bad_marks > 0
        checks = ok_marks + bad_marks
        print(f"{'✗ ПРОВАЛ' if bad else '✓ ok    '}  {p.name:<32} {checks:>3} проверок")
        if bad:
            failed.append(p.name)
            print("\n".join("      " + ln for ln in body.strip().splitlines()[-25:]))

    print(f"\nфайлов {len(files)}, провалено {len(failed)}, "
          f"{time.time() - t0:.1f} c")
    if failed:
        print("провалились: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
