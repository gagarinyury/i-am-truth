#!/usr/bin/env python3
"""
Отказ добычи обязан быть виден, а не выдан за свойство статьи.

Дефекты, ради которых написан тест (найдены 29.08 живьём, во время аварии
Europe PMC и сразу после чтения справочника EBI 6.9.0):

  1. `lookup` схлопывал четыре исхода в один `found: False`. Europe PMC около
     двадцати минут отдавал `200` и тело `{"version":"6.9"}` — без `hitCount`,
     без эха `request`, без `resultList` — на любой запрос, включая
     `query=cancer`. Код читал это как «такой статьи не существует».
  2. `fullTextXML` вызывался под проверкой `inEPMC`, тогда как документация
     («available only for the full-text OA subset», «For Open Access articles»)
     требует `isOpenAccess`. На 4 049 378 статей класса `IN_EPMC:y AND
     OPEN_ACCESS:n` сервис отдавал 500 при полностью исправном Europe PMC.
  3. Отказ приложений уходил в `except Exception: pass`, и отчёт сообщал, что у
     статьи нет baseline-таблиц, вместо «мы их не смогли забрать» (F-60).
  4. Решётка уровней не имела дна: пустой источник объявлялся L3 с подписью
     «This audit ran on the abstract alone».
  5. Пустой источник доезжал до Vertex, тот отвечал `400 Model input cannot be
     empty`, и наружу шло 500 с чужим текстом ошибки.

Сети тест не требует и модель не вызывает: подставляются те самые ответы.
"""
import pathlib
import sys
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import pipeline, retrieval                        # noqa: E402

_REAL_LOOKUP = retrieval.lookup
_REAL_GET = retrieval._get

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


def http(code):
    def raise_it(*a, **k):
        raise urllib.error.HTTPError("http://x", code, "boom", {}, None)
    return raise_it


def status_of(level, source):
    return next((r.get("status") for r in level.get("retrieval") or []
                 if r["source"] == source), None)


# Ответы Europe PMC, снятые с провода 29.08.2026 — не сочинённые.
DEGRADED = {"version": "6.9"}
EMPTY = {"version": "6.9", "hitCount": 0, "request": {"queryString": "x"},
         "resultList": {"result": []}}


def meta(**kw):
    base = {"found": True, "doi": "10.0/x", "status": "ok", "pmcid": "PMC1",
            "title": "T", "journal": "J", "open_access": True, "in_epmc": True,
            "has_supplementary": True, "abstract": "abs"}
    base.update(kw)
    return base


print("1. четыре исхода поиска различимы")
_orig_get = retrieval._get
retrieval._get = lambda *a, **k: DEGRADED
check("сервис ответил не по существу → degraded",
      retrieval.lookup("10.0/x")["status"] == "degraded")
retrieval._get = lambda *a, **k: EMPTY
check("сервис ответил по существу, статьи нет → not_found",
      retrieval.lookup("10.0/x")["status"] == "not_found")
retrieval._get = http(503)
check("сервис не ответил → unreachable",
      retrieval.lookup("10.0/x")["status"] == "unreachable")
check("исходы 'нет статьи' и 'нет сервиса' больше не равны",
      retrieval.lookup("10.0/x")["status"] != "not_found")
retrieval._get = _orig_get

print("\n2. предусловие fullTextXML — isOpenAccess, а не inEPMC (справочник 6.9.0)")
retrieval.lookup = lambda doi: meta(open_access=False)
retrieval.fetch_fulltext = http(404)
retrieval.fetch_supplementary = lambda p: b""
g = pipeline.gather(doi="10.0/x")
check("статья вне OA не роняет разбор", g["level"]["level"] in ("L3", "L0"),
      g["level"]["level"])
check("отказ назван законным, а не сбоем",
      status_of(g["level"], "europepmc.fullTextXML") == "not_applicable")
check("причина названа словами",
      "Open Access" in (next(r for r in g["level"]["retrieval"]
                             if r["source"] == "europepmc.fullTextXML")["note"]))

print("\n3. отказ сервиса при выполненном условии — это сбой, а не отсутствие данных")
retrieval.lookup = lambda doi: meta()
retrieval.fetch_fulltext = http(404)
g = pipeline.gather(doi="10.0/x")
check("404 при isOpenAccess=Y → upstream_error",
      status_of(g["level"], "europepmc.fullTextXML") == "upstream_error")
check("уровень не выдан за свойство статьи: отказ записан",
      any(r["status"] == "upstream_error" for r in g["level"]["retrieval"]))

print("\n4. падение приложений видно, а не списано на статью")
retrieval.fetch_fulltext = lambda p: b"<article><body><p>full text 12.3</p></body></article>"
retrieval.fetch_supplementary = http(500)
g = pipeline.gather(doi="10.0/x")
check("уровень L2 — полный текст без приложений", g["level"]["level"] == "L2",
      g["level"]["level"])
check("но причина записана как отказ сервиса",
      status_of(g["level"], "europepmc.supplementaryFiles") == "upstream_error")

print("\n5. полный текст без таблиц — это L2, а не «только абстракт»")
check("тело статьи не объявляется абстрактом", g["level"]["level"] != "L3")
check("таблиц действительно нет", len(g["jats_tables"]) == 0)

print("\n6. у решётки уровней есть дно")
retrieval.lookup = lambda doi: {"found": False, "doi": doi, "status": "degraded",
                                "error": "нет resultList"}
g = pipeline.gather(doi="10.0/x")
check("ничего не добыто → L0, а не L3", g["level"]["level"] == "L0",
      g["level"]["level"])
check("потолок доверия — NONE", g["level"]["max_confidence"] == "NONE")

print("\n7. пустой источник не уходит в модель")
raised = None
try:
    pipeline.run(doi="10.0/x", prompt="p")
except pipeline.NothingRetrieved as e:
    raised = e
except Exception as e:                                       # noqa: BLE001
    raised = e
check("поднято NothingRetrieved, а не ошибка Vertex",
      isinstance(raised, pipeline.NothingRetrieved), type(raised).__name__)
check("исключение несёт журнал добычи",
      bool((getattr(raised, "gathered", {}).get("level") or {}).get("retrieval")))

print("\n8. темп и повторы для Europe PMC")
# Причина, по которой этот раздел существует, оказалась не той, что думали
# сначала. 29.08 Europe PMC отдавал 503 на всё — и это выглядело как авария, пока
# запрос с постороннего жилого адреса не вернул 200. Ограничение было наложено на
# нашего клиента за темп: около полусотни поисковых запросов и полтора-два десятка
# архивов приложений за час, без единой паузы. Через несколько минут простоя
# ограничение снялось само. У вызовов Vertex backoff был с первого дня (F-11), у
# Europe PMC не было ничего — при том что батч гонит три воркера параллельно.
import time as _t
import urllib.request as _ureq

retrieval.lookup = _REAL_LOOKUP
retrieval._get = _REAL_GET
_real_urlopen = _ureq.urlopen
calls = {"n": 0}


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"version":"6.9","hitCount":0,"resultList":{"result":[]}}'


def _flaky(req, timeout=None):
    """Два отказа 503, потом успех — так вело себя Europe PMC под ограничением."""
    calls["n"] += 1
    if calls["n"] < 3:
        raise urllib.error.HTTPError("http://x", 503, "boom", {}, None)
    return _Resp()


try:
    _ureq.urlopen = _flaky
    retrieval._last_call = 0.0
    t0 = _t.monotonic()
    r = retrieval.lookup("10.0/x")
    spent = _t.monotonic() - t0
    check("503 повторяется, а не роняет разбор", r["status"] == "not_found", r["status"])
    check("сделано три попытки", calls["n"] == 3, str(calls["n"]))
    check("между попытками была выдержка (2 с + 4 с)", spent >= 6.0, f"{spent:.1f} c")

    # 404 повторять бессмысленно: он означает «этого у нас нет».
    calls["n"] = 0
    _ureq.urlopen = lambda req, timeout=None: (
        calls.__setitem__("n", calls["n"] + 1)
        or (_ for _ in ()).throw(urllib.error.HTTPError("http://x", 404, "no", {}, None)))
    try:
        retrieval._get("http://x", attempts=3)
    except urllib.error.HTTPError:
        pass
    check("404 не повторяется", calls["n"] == 1, f"{calls['n']} попыток")

    # Темп общий на процесс: три воркера батча не должны втрое ускорять поток.
    _ureq.urlopen = lambda req, timeout=None: _Resp()
    retrieval._last_call = 0.0
    t0 = _t.monotonic()
    for _ in range(4):
        retrieval._get("http://x", attempts=1)
    gap = _t.monotonic() - t0
    check("темп выдерживается между запросами", gap >= 3 * retrieval._PACE_SECONDS,
          f"{gap:.2f} c на 4 запроса")
finally:
    _ureq.urlopen = _real_urlopen

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
