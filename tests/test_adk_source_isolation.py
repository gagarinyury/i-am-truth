"""
Источник для инструмента самопроверки ADK не должен быть общим на весь процесс.

Дефект (F-62): `_SOURCE` был модульным словарём. Сервис обрабатывает запросы
конкурентно, поэтому два одновременных разбора через ADK перетирали источник друг
друга — числа одной статьи сверялись бы с текстом другой, — а завершение любого из
них обнуляло источник у второго. Тот же класс подмены, который проект запретил себе
для временных файлов (D-14) и ищет у чужих работ.

Тест не вызывает Vertex: проверяется изоляция значения, а не работа графа.
"""
import concurrent.futures as cf
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import adk_agent                      # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


A = "In the GLP-1 arm the Charlson 5+ share was 19.7 percent among matched patients."
B = "Vaccine effectiveness against post-covid condition reached 58.3 percent overall."

barrier = threading.Barrier(2)


def worker(source, value):
    """Ставит свой источник, ждёт напарника и только потом спрашивает инструмент —
    так одновременность гарантирована, а не оставлена на удачу планировщика."""
    token = adk_agent._SOURCE.set(source)
    try:
        barrier.wait(timeout=10)
        return {
            "own": adk_agent.check_number_in_source(value, "share in the arm"),
            # число напарника: если контексты протекают, оно найдётся здесь
            "foreign": adk_agent.check_number_in_source(
                "58.3" if value == "19.7" else "19.7", "share in the arm"),
        }
    finally:
        adk_agent._SOURCE.reset(token)


with cf.ThreadPoolExecutor(max_workers=2) as ex:
    fa = ex.submit(worker, A, "19.7")
    fb = ex.submit(worker, B, "58.3")
    ra, rb = fa.result(), fb.result()

check("свой источник виден первому", ra["own"]["status"] != "NO_SOURCE",
      ra["own"]["status"])
check("свой источник виден второму", rb["own"]["status"] != "NO_SOURCE",
      rb["own"]["status"])
check("первый нашёл своё число",
      ra["own"]["status"] in ("VERIFIED", "FOUND_LABEL_MISMATCH"))
check("второй нашёл своё число",
      rb["own"]["status"] in ("VERIFIED", "FOUND_LABEL_MISMATCH"))
check("чужое число первому не видно", ra["foreign"]["status"] == "UNVERIFIED",
      ra["foreign"]["status"])
check("чужое число второму не видно", rb["foreign"]["status"] == "UNVERIFIED",
      rb["foreign"]["status"])

# После выхода из обоих контекстов источника быть не должно.
check("вне разбора источника нет",
      adk_agent.check_number_in_source("19.7", "x")["status"] == "NO_SOURCE")

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
