"""
Layer 1 — получение статьи и определение уровня доказательности.

Уровень определяется тем, что реально удалось достать, и он же задаёт потолок
качества разбора. Цены уровней измерены (F-26, F-40, решение D-13); ниже — медианы
трёх прогонов на подготовленных входах, шкала шестибалльная:

  L1  full-text + таблицы приложения   6.0 / 6
  L2  полный текст без приложений      4.5 / 6
  L3  только абстракт и метаданные     4.0 / 6

На живой статье целиком (опубликованный PDF, а не подготовленный вход) L1 даёт
3.5-4.5 / 6, медиана 3.5 — числа там надо ещё найти (F-43). Доступность уровней:
Europe PMC отдаёт full-text для 27.5% статей класса, объединение каналов ~55% (F-25).

Ниже L1 находки не опираются на числа из документа и потому непроверяемы —
это не предположение, а результат замера.
"""
import json
import urllib.parse
import urllib.request

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UA = {"User-Agent": "i-am-truth/0.1 (methodology audit)"}

# Цены уровней замерены на промпте ROBINS-E (том, что в проде) по эталону version 2,
# шесть пунктов, три прогона 27–28.08 — F-40. Указан диапазон и медиана: разброс между
# прогонами реален и скрывать его нечестно. Числа из пятибалльной эпохи (до F-32) сюда
# переносить нельзя — знаменатель изменился, а баллы нет.
LEVELS = {
    "L1": {"name": "full text + appendix tables", "max_confidence": "CONFIRMED",
           "measured_score": "5.0-6.0 / 6 (median 6.0)"},
    "L2": {"name": "full text, no appendices", "max_confidence": "PLAUSIBLE-UNVERIFIED",
           "measured_score": "4.0-5.0 / 6 (median 4.5)"},
    "L3": {"name": "abstract only", "max_confidence": "PLAUSIBLE-UNVERIFIED",
           "measured_score": "3.5-4.0 / 6 (median 4.0)"},
}


def _get(url: str, as_json=True, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if as_json else raw


def lookup(doi: str) -> dict:
    """Метаданные статьи по DOI из Europe PMC."""
    q = urllib.parse.quote(f'DOI:"{doi}"')
    d = _get(f"{EPMC}/search?query={q}&format=json&resultType=core")
    res = d.get("resultList", {}).get("result", [])
    if not res:
        return {"found": False, "doi": doi}
    r = res[0]
    return {
        "found": True, "doi": doi,
        "pmid": r.get("pmid"), "pmcid": r.get("pmcid"),
        "title": r.get("title"),
        "journal": (r.get("journalInfo") or {}).get("journal", {}).get("title"),
        "open_access": r.get("isOpenAccess") == "Y",
        "in_epmc": r.get("inEPMC") == "Y",
        "has_supplementary": r.get("hasSuppl") == "Y",
        "abstract": r.get("abstractText"),
    }


def fetch_fulltext(pmcid: str) -> bytes:
    return _get(f"{EPMC}/{pmcid}/fullTextXML", as_json=False)


def fetch_supplementary(pmcid: str) -> bytes:
    return _get(f"{EPMC}/{pmcid}/supplementaryFiles", as_json=False)


def assess_level(meta: dict, has_fulltext: bool, has_appendix: bool) -> dict:
    """Какой уровень доказательности доступен для этой статьи."""
    if has_fulltext and has_appendix:
        lvl = "L1"
    elif has_fulltext:
        lvl = "L2"
    else:
        lvl = "L3"
    out = {"level": lvl, **LEVELS[lvl]}
    if lvl != "L1":
        out["missing"] = ("appendix tables — without them the audit rests on generic "
                          "design properties rather than numbers from this paper, and "
                          "there is nothing to check it against (F-44)")
    return out
