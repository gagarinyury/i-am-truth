"""
Свод направлений смещения по доменам — счётом, а не мнением модели.

Зачем. У каждого из семи доменов ROBINS-E есть направление, и есть общее
направление в `overall`. Общее до сих пор называла модель: она же писала домены,
она же подводила итог, и сойтись эти два утверждения были обязаны только по её
доброй воле. Проверить это стоит один проход по списку — ровно тот случай, когда
считать должна функция, как ARR считает `stats_tool`.

Чего этот свод **не** делает. Он не выносит вердикт «смещение суммарно тянет
вверх»: домены не равновелики, неучтённый конфаундинг и потеря наблюдений могут
двигать оценку на разный порядок, а весов у нас нет и взять их неоткуда. Поэтому
здесь счёт и указание на противоречие, а не взвешенный итог. Придумывать веса,
чтобы получить красивую стрелку, — ровно то, за что этот продукт критикует чужие
статьи.
"""
DIRECTIONS = ("away_from_null", "towards_null", "unpredictable", "no_information")
OPPOSITE = {"away_from_null": "towards_null", "towards_null": "away_from_null"}


def summarise(findings: dict) -> dict | None:
    """Счёт направлений по доменам и сверка с общим направлением.

    `agreement`:
      `consistent`   — общее направление совпало с преобладающим по доменам;
      `contradicts`  — общее направление противоположно преобладающему;
      `unsupported`  — общее направление названо, но по доменам перевеса нет
                       (поровну или направленных доменов нет вовсе);
      `not_comparable` — общее направление названо, но оно не направленное
                       (`unpredictable` / `no_information`), сравнивать не с чем;
      `not_stated`   — общего направления модель не дала вовсе.
    """
    domains = (findings or {}).get("domains") or []
    if not domains:
        return None
    counts = {d: 0 for d in DIRECTIONS}
    by_direction = {d: [] for d in DIRECTIONS}
    other = 0
    for dm in domains:
        d = (dm or {}).get("direction")
        if d in counts:
            counts[d] += 1
            by_direction[d].append((dm.get("name") or f"domain {dm.get('id')}"))
        else:
            other += 1

    a, t = counts["away_from_null"], counts["towards_null"]
    dominant = "away_from_null" if a > t else "towards_null" if t > a else None

    overall = ((findings or {}).get("overall") or {}).get("direction")
    if overall in ("unpredictable", "no_information"):
        # Направление названо, но оно не направленное. Это не молчание модели и не
        # согласие: сравнивать счёт по доменам не с чем.
        agreement = "not_comparable"
    elif overall not in ("away_from_null", "towards_null"):
        agreement = "not_stated"
    elif dominant is None:
        agreement = "unsupported"
    elif dominant == overall:
        agreement = "consistent"
    else:
        agreement = "contradicts"

    return {
        "counts": counts,
        "unclassified": other,
        "domains_by_direction": {k: v for k, v in by_direction.items() if v},
        "dominant_by_count": dominant,
        "model_overall": overall,
        "agreement": agreement,
        "note": ("A count of domains, not a weighted verdict: the domains are not "
                 "commensurable and no weights exist to make them so."),
    }
