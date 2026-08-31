#!/usr/bin/env python3
"""
Защита сервиса: ключ, пределы загрузки, предел одновременности, коды отказа.

Почему тест написан только 31.08. Вся эта логика существовала и была объяснена
комментариями — и не проверялась ничем. При этом она охраняет не удобство, а
чужой счёт: один `POST /analyze` — три вызова Gemini на квоте проекта, о чём
README говорит отдельным абзацем. Утверждение «разбор закрыт ключом» без теста
держится ровно до первой правки зависимостей FastAPI.

Здесь не вызывается ни модель, ни облако: `pipeline.run` подменён заглушкой,
хранилище переключено на локальное. Проверяется проводка, а не разбор.

Три вещи, которые тест держит отдельно:
  * ключ закрывает **только** платные эндпоинты — чтение остаётся открытым, иначе
    ссылки на отчёты в README перестают быть доказательством;
  * предел размера срабатывает ДО чтения файла в память;
  * «источник ничего не дал» — это 503 или 422, но никогда 500: 500 означает
    «сломались мы», а здесь не сломался никто.
"""
import os
import pathlib
import shutil
import sys
import threading

TMP = pathlib.Path("/tmp/http_guards_store")
shutil.rmtree(TMP, ignore_errors=True)
os.environ["TRUTH_STORE"] = "local"
os.environ["TRUTH_LOCAL_STORE"] = str(TMP)
os.environ["TRUTH_API_KEY"] = "correct-horse"
os.environ["TRUTH_MAX_CONCURRENT"] = "2"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient                        # noqa: E402

from app import main                                             # noqa: E402
from truth import pipeline                                       # noqa: E402

KEY = {"X-API-Key": "correct-horse"}
REPORT = {"meta": {"doi": "10.0/x"}, "level": {"level": "L1"},
          "verification": {"total": 1}}

main.pipeline.run = lambda **kw: dict(REPORT)
client = TestClient(main.app)

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


print("ключ закрывает платное и только его…")
r = client.post("/analyze", json={"doi": "10.0/x"})
check("без ключа — 401", r.status_code == 401, str(r.status_code))
check("401 объясняет, что читать можно без ключа",
      "чтение" in str(r.json().get("detail")))
check("чужой ключ — 403",
      client.post("/analyze", json={"doi": "10.0/x"},
                  headers={"X-API-Key": "wrong"}).status_code == 403)
check("свой ключ — разбор идёт",
      client.post("/analyze", json={"doi": "10.0/x"}, headers=KEY).status_code == 200)
check("Authorization: Bearer принимается наравне",
      client.post("/analyze", json={"doi": "10.0/x"},
                  headers={"Authorization": "Bearer correct-horse"}).status_code == 200)
check("здоровье открыто", client.get("/health").status_code == 200)
check("/health честно говорит, что ключ включён",
      client.get("/health").json()["auth"] == "key")
check("уровни открыты", client.get("/levels").status_code == 200)
check("список разборов открыт", client.get("/audits").status_code == 200)
check("загрузка тоже закрыта ключом",
      client.post("/analyze/upload",
                  files={"files": ("a.pdf", b"%PDF-1.4", "application/pdf")}
                  ).status_code == 401)

print("\nпределы загрузки…")
many = [("files", (f"{i}.pdf", b"%PDF-1.4 data", "application/pdf")) for i in range(11)]
r = client.post("/analyze/upload", files=many, headers=KEY)
check("одиннадцать файлов — 413", r.status_code == 413, str(r.status_code))
big = b"%PDF-1.4" + b"x" * (26 * 1024 * 1024)
r = client.post("/analyze/upload",
                files={"files": ("big.pdf", big, "application/pdf")}, headers=KEY)
check("файл больше 25 МБ — 413", r.status_code == 413, str(r.status_code))
r = client.post("/analyze/upload",
                files={"files": ("empty.pdf", b"", "application/pdf")}, headers=KEY)
check("пустой файл — 400 с именем файла", r.status_code == 400, str(r.status_code))
r = client.post("/analyze", json={}, headers=KEY)
check("ни DOI, ни текста — 400", r.status_code == 400)

print("\nпредел одновременности…")
release = threading.Event()
entered = threading.Semaphore(0)


def slow(**kw):
    entered.release()
    release.wait(10)
    return dict(REPORT)


main.pipeline.run = slow
threads = [threading.Thread(
    target=lambda: client.post("/analyze", json={"doi": "10.0/x"}, headers=KEY))
    for _ in range(2)]
for t in threads:
    t.start()
for _ in range(2):
    entered.acquire(timeout=5)                 # оба слота заняты
r = client.post("/analyze", json={"doi": "10.0/x"}, headers=KEY)
check("третий разбор — 429, а не очередь", r.status_code == 429, str(r.status_code))
check("429 говорит, когда повторить", r.headers.get("retry-after") == "60")
release.set()
for t in threads:
    t.join(10)
r = client.post("/analyze", json={"doi": "10.0/x"}, headers=KEY)
check("слоты освобождаются после ответа", r.status_code == 200, str(r.status_code))

print("\n«разбирать нечего» — это не 500…")


def nothing(level_log):
    def _run(**kw):
        raise pipeline.NothingRetrieved({"level": {"level": "L0",
                                                   "retrieval": level_log}})
    return _run


main.pipeline.run = nothing([{"source": "europepmc.search", "status": "not_found"}])
r = client.post("/analyze", json={"doi": "10.0/x"}, headers=KEY)
check("законное отсутствие данных — 422", r.status_code == 422, str(r.status_code))
check("журнал добычи уходит пользователю",
      r.json()["detail"]["retrieval"][0]["source"] == "europepmc.search")
check("подсказан путь B", "upload" in r.json()["detail"]["hint"])

main.pipeline.run = nothing([{"source": "europepmc.fullTextXML",
                              "status": "upstream_error"}])
r = client.post("/analyze", json={"doi": "10.0/x"}, headers=KEY)
check("отказ вышестоящего источника — 503, повторять есть смысл",
      r.status_code == 503, str(r.status_code))


def boom(**kw):
    raise RuntimeError("совсем сломалось")


main.pipeline.run = boom
r = client.post("/analyze", json={"doi": "10.0/x"}, headers=KEY)
check("настоящая поломка — 500", r.status_code == 500, str(r.status_code))
check("тип ошибки виден в ответе", "RuntimeError" in r.json()["detail"])

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
