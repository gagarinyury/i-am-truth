"""
Layer 1 — получение статьи и определение уровня доказательности.

Уровень определяется тем, что реально удалось достать, и он же задаёт потолок
качества разбора. Цены уровней измерены (F-26, решение D-13):

  L1  full-text + таблицы приложения   ~22% статей класса   4.0-4.5 / 6
  L2  полный текст без приложений      ~27%                 3.5 / 6
  L3  только абстракт и метаданные     ~50%                 3.0 / 6

На L2 и L3 модель систематически ошибается направлением confounding —
это не предположение, а результат замера.
"""
import json
import urllib.parse
import urllib.request

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UA = {"User-Agent": "i-am-truth/0.1 (methodology audit)"}

LEVELS = {
    "L1": {"name": "full-text + приложения", "max_confidence": "CONFIRMED",
           "measured_score": "4.0-4.5 / 6"},
    "L2": {"name": "полный текст без приложений", "max_confidence": "PLAUSIBLE-UNVERIFIED",
           "measured_score": "3.5 / 6"},
    "L3": {"name": "только абстракт", "max_confidence": "PLAUSIBLE-UNVERIFIED",
           "measured_score": "3.0 / 6"},
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
        out["missing"] = ("таблицы приложения — без них модель систематически "
                          "ошибается направлением confounding (измерено, F-26)")
    return out
