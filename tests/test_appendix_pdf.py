"""
Приложение в PDF, предупреждение по уровню, знаки NNT.

Три дефекта, найденные 29.08 на разборе `10.1136/bmj-2023-076990` (F-60):

1. Приложения разбирались только из `.docx`. У BMJ приложение приходит одним PDF
   («web only»), и статья молча падала с L1 на L2 — уровень оказывался свойством
   нашего добытчика, а не статьи.
2. Предупреждение о неполноте данных было одно на L2 и L3 и на L2 утверждало
   «нечего цитировать», когда рядом стояли сотни сверенных чисел.
3. Разность рисков и NNT по построению имеют противоположные знаки, и в отчёте
   стояли «−1.02» и «+98.1» одновременно.
"""
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import pipeline                       # noqa: E402
from truth.recompute import recompute            # noqa: E402
from truth.retrieval import assess_level         # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


# 1. Предупреждение и «чего не хватает» пишутся под уровень.
check("на L1 предупреждения нет", pipeline.CAVEAT.get("L1") is None)
check("L2 не утверждает, что цитировать нечего",
      "nothing to quote" not in pipeline.CAVEAT["L2"])
check("L2 говорит именно про приложение", "appendix" in pipeline.CAVEAT["L2"])
check("L3 говорит про абстракт", "abstract" in pipeline.CAVEAT["L3"])
check("на L2 не хватает приложения",
      "appendix" in assess_level({}, True, False)["missing"])
check("на L3 не хватает и полного текста",
      "full text" in assess_level({}, False, False)["missing"])
check("на L1 замечаний нет", "missing" not in assess_level({}, True, True))

# 2. Знаки: пользователю выдаётся модуль и слово по знаку.
harm = recompute({"counts": {"exposed_events": 1986, "exposed_total": 3609,
                             "control_events": 1629, "control_total": 3609}})
benefit = recompute({"counts": {"exposed_events": 1201, "exposed_total": 299692,
                                "control_events": 4118, "control_total": 290030}})
check("вред назван вредом", harm["nnt_kind"] == "number needed to harm", harm["nnt_kind"])
check("польза названа пользой", benefit["nnt_kind"] == "number needed to treat")
check("наружу идёт модуль", harm["nnt_abs"] > 0 and benefit["nnt_abs"] > 0,
      f"{harm['nnt_abs']} / {benefit['nnt_abs']}")
check("сырость оценки помечена", "unadjusted" in benefit["note"])

# 3. Приложение в PDF действительно разбирается. Тест ходит в сеть — если
#    Europe PMC недоступен, это не провал кода, и он назван отдельно.
URL = ("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC10666099"
       "/supplementaryFiles")
try:
    blob = urllib.request.urlopen(URL, timeout=90).read()
except Exception as e:                                       # noqa: BLE001
    print(f"  ⚠️  Europe PMC недоступен ({type(e).__name__}) — проверка приложения "
          f"пропущена, это не провал кода")
else:
    tables = pipeline._tables_from_supplementary(blob)
    text = pipeline._supplementary_text(blob)
    check("таблицы из PDF-приложения извлекаются", len(tables) > 0, f"{len(tables)} шт.")
    check("текст приложения извлекается", len(text) > 2000, f"{len(text)} символов")
    check("источник помечен именем файла",
          any((t.get("source_file") or "").endswith(".pdf") for t in tables))

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
