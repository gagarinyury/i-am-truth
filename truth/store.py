"""
Хранилище одиночных разборов.

Зачем отдельный модуль. Батч складывает результаты сам (`batch.save`), а разбор,
сделанный через сайт или API, до сих пор не сохранялся нигде: `pipeline.run`
возвращал словарь, FastAPI отдавал его в ответ, и на этом отчёт переставал
существовать. Практическое следствие проявилось 28.08 — обсуждали разбор с
конкретными числами, которого нет ни в одном файле репозитория, то есть проверить
его было нечем. Инструмент, который требует от чужих статей воспроизводимости,
обязан начинать с себя.

Почему не переиспользован `batch.save`. У него другая семантика, заточенная под
задачу: при отказе облака он пишет в локальную файловую систему Cloud Run Job и
копит отказы в глобальный `STORAGE_FAILURES`, потому что «успешный» батч без
единого сохранённого результата обязан быть виден как провал. Для веб-сервиса это
неверно вдвойне: процесс живёт долго, глобальный список рос бы бесконечно, а отказ
хранилища не должен ронять ответ пользователю — разбор уже сделан и стоил вызовов
Vertex. Поэтому здесь запись best-effort, а её исход возвращается наверх честной
меткой, а не молчанием.

Раскладка в бакете:
  run-*/summary.json      — батч (не трогаем)
  audits/<id>.json        — одиночные разборы
`id` = дата, время и короткий хеш источника: отсортированный список имён идёт по
времени, а хеш не даёт двум одновременным разборам затереть друг друга.
"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import uuid

BUCKET = os.environ.get("TRUTH_BUCKET", "i-am-truth-runs-merci-prod")
PREFIX = "audits"
# Локальный запасной путь. В Cloud Run он эфемерен и это нормально: он нужен для
# работы без облака (тесты, локальный прогон), а не как замена хранилищу.
LOCAL = pathlib.Path(os.environ.get("TRUTH_LOCAL_STORE", "audit_out"))
# "gcs" (по умолчанию) или "local" — второе используют тесты и запуск без ADC.
BACKEND = os.environ.get("TRUTH_STORE", "gcs")


def new_id(report: dict) -> str:
    """Идентификатор разбора: время + 6 знаков, различающих одновременные разборы.

    Хеш берётся от источника И от случайного значения. Только от источника было
    нельзя: у входа `{"text": ...}` метаданных нет, seed выходил пустым, и все
    такие разборы получали один и тот же хвост `e3b0c4` — два разбора в одну
    секунду затирали друг друга. То же и для двух прогонов одного DOI.
    """
    meta = report.get("meta") or {}
    seed = str(meta.get("doi") or meta.get("title") or "")[:200] + uuid.uuid4().hex
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]
    return dt.datetime.now().strftime("audit-%Y%m%d-%H%M%S-") + h


def _bucket():
    from google.cloud import storage
    return storage.Client().bucket(BUCKET)


def put(report: dict, audit_id: str = None) -> dict:
    """Сохранить разбор. Возвращает `{"id", "stored", "error"}`.

    `stored` — где именно осел отчёт: "gcs", "local" или "none". Поле попадает в
    ответ API специально: пользователь должен видеть разницу между «сохранено» и
    «сделано вид, что сохранено».
    """
    audit_id = audit_id or new_id(report)
    # allow_nan=False: с `inf` или `nan` в отчёте наружу уходил бы литерал
    # `Infinity`, невалидный JSON для всех потребителей, кроме Python. Пусть
    # лучше запись честно провалится, чем в хранилище ляжет то, что нельзя
    # прочитать.
    body = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if BACKEND == "gcs":
        try:
            _bucket().blob(f"{PREFIX}/{audit_id}.json").upload_from_string(
                body, content_type="application/json")
            return {"id": audit_id, "stored": "gcs", "error": None}
        except Exception as e:                               # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:200]
    else:
        err = None
    try:
        LOCAL.mkdir(parents=True, exist_ok=True)
        (LOCAL / f"{audit_id}.json").write_text(body, encoding="utf-8")
        return {"id": audit_id, "stored": "local", "error": err}
    except Exception as e:                                   # noqa: BLE001
        return {"id": audit_id, "stored": "none",
                "error": err or f"{type(e).__name__}: {e}"[:200]}


def get(audit_id: str) -> dict | None:
    """Разбор по идентификатору или None. Локальная копия — запасной вариант."""
    if BACKEND == "gcs":
        try:
            b = _bucket().blob(f"{PREFIX}/{audit_id}.json")
            if b.exists():
                return json.loads(b.download_as_text())
        except Exception:                                    # noqa: BLE001
            pass
    p = LOCAL / f"{audit_id}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def list_ids(limit: int = 50) -> list:
    """Идентификаторы разборов, новые первыми."""
    ids = []
    if BACKEND == "gcs":
        try:
            ids = [b.name.split("/")[-1][:-5] for b in
                   _bucket().list_blobs(prefix=f"{PREFIX}/")
                   if b.name.endswith(".json")]
        except Exception:                                    # noqa: BLE001
            ids = []
    if not ids and LOCAL.exists():
        ids = [p.stem for p in LOCAL.glob("*.json")]
    return sorted(ids, reverse=True)[:limit]
