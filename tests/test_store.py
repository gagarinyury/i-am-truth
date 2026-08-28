"""
Проверка того, что разбор, сделанный через сервис, действительно сохраняется.

Дыра, ради которой написан тест: `pipeline.run` возвращал отчёт в ответ и больше
нигде его не оставлял, поэтому разбор, показанный пользователю, невозможно было
поднять и перепроверить. Тест не вызывает Vertex — подменяет `pipeline.run`
заглушкой: проверяется проводка хранилища, а не качество разбора.
"""
import os
import pathlib
import shutil
import sys

TMP = pathlib.Path("/tmp/audit_test_suite")
shutil.rmtree(TMP, ignore_errors=True)
os.environ["TRUTH_STORE"] = "local"
os.environ["TRUTH_LOCAL_STORE"] = str(TMP)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient      # noqa: E402

from app import main                           # noqa: E402
from truth import store                        # noqa: E402

REPORT = {"meta": {"doi": "10.1136/jitc-2025-014726", "title": "Тест"},
          "level": {"level": "L1"}, "findings": {"overall": {"direction": "away_from_null"}},
          "verification": {"total": 42, "verified": 30}}

main.pipeline.run = lambda **kw: dict(REPORT)
client = TestClient(main.app)

ok = 0


def check(name, cond, detail=""):
    global ok
    ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


r = client.post("/analyze", json={"doi": "10.1136/jitc-2025-014726"}).json()
check("ответ несёт идентификатор", bool(r.get("audit_id")), str(r.get("audit_id")))
check("ответ честно называет место хранения", r.get("stored") == "local", str(r.get("stored")))

aid = r["audit_id"]
check("файл лежит на диске", (TMP / f"{aid}.json").exists())

got = client.get(f"/audits/{aid}")
check("разбор поднимается по ссылке", got.status_code == 200)
check("поднятый разбор совпадает с показанным",
      got.json().get("verification") == REPORT["verification"])
# Сохранённая копия обязана знать своё имя: без этого поднятый файл не отличить
# от любого другого. Поля `stored`/`store_error` описывают запись, не разбор, и
# в архив не попадают — поэтому сравнение идёт по отчёту без них.
check("сохранённая копия знает свой идентификатор",
      got.json().get("audit_id") == aid, str(got.json().get("audit_id")))
shown = {k: v for k, v in r.items() if k not in ("stored", "store_error")}
check("архив совпадает с показанным полностью", got.json() == shown,
      "" if got.json() == shown else
      f"расходятся: {set(shown) ^ set(got.json())}")

lst = client.get("/audits").json()
check("разбор виден в списке", aid in lst.get("audits", []))
check("несуществующий разбор — 404", client.get("/audits/audit-нет").status_code == 404)

# Отказ хранилища не должен ронять ответ: разбор уже стоил вызовов Vertex.
store.LOCAL = pathlib.Path("/proc/недоступно")
broken = client.post("/analyze", json={"doi": "x"})
store.LOCAL = TMP
check("при отказе хранилища ответ всё равно отдан", broken.status_code == 200,
      f"stored={broken.json().get('stored')}")
check("отказ хранилища виден в ответе", broken.json().get("stored") == "none")

print(f"\n{ok}/11 проверок пройдено")
sys.exit(0 if ok == 11 else 1)
