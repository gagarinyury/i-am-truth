#!/usr/bin/env python3
"""
Извлечение текста и таблиц из PDF — путь B.

Зачем он вообще нужен. Автоматически статью удаётся достать примерно в 55% случаев
(F-25): Europe PMC отдаёт full-text для 27.5%, ещё часть даёт прямой PDF у издателя.
Остальное закрыто Cloudflare или TDM-токеном Elsevier, и обходить это мы не будем.
Но статья, недоступная машине, обычно доступна человеку — у него подписка, институт
или это bronze OA, который просто не отдаётся скрипту. Путь B принимает такой файл
от пользователя и доводит статью до того же уровня L1.

Почему pdfplumber, а не что-то другое (правило «сначала искать готовое»):
  - `page.extract_tables()` — готовый детектор таблиц, писать свой не нужно;
  - лицензия MIT. PyMuPDF отвергнут: он под AGPL, а репозиторий публичный
    и сервис сетевой — вирусность лицензии распространилась бы на проект;
  - Document AI не понадобился: он платный и требует процессора, а pdfplumber
    даёт структуру строка/столбец локально. Q-08 закрывается без облачного OCR
    для текстовых PDF (для сканов — по-прежнему открыт, см. `has_text_layer`).

Выход намеренно совпадает по форме с `docx_tables.extract` и `jats_tables.parse_tables`
— {caption, rows, n_rows, n_cols} — чтобы оркестратор не различал источник таблиц.

  python3 -m truth.pdf_tables файл.pdf [--table 3] [--json]
"""
import argparse
import json
import re
import sys

# Заголовок таблицы в статьях выглядит однообразно; тот же набор, что в docx_tables,
# плюс формы, встречающиеся в основном тексте, а не только в приложении.
CAPTION_RE = re.compile(
    r"^\s*((?:e|Supplementary\s+|Appendix\s+|Supplemental\s+)?Tables?\s*[Ss]?\d+"
    r"(?![\d)])[.:]?\s*.{0,160})", re.I)

# Пробелы между словами в PDF ставятся по расстоянию между глифами, и у части
# издателей (проверено на Frontiers) стандартный порог pdfplumber = 3 склеивает
# слова: «Baselinedemographicandclinical». 1.5 даёт правильный текст и не рвёт
# числа — проверено на той же статье, разрывов вида «62. 1» не появилось.
X_TOL = 1.5


def _clean(cell) -> str:
    """Ячейка pdfplumber бывает None; переносы внутри ячейки схлопываем."""
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def has_text_layer(path: str) -> bool:
    """Есть ли в PDF текстовый слой. Скан без OCR отличается от статьи тем, что
    текста в нём нет вовсе — и тогда никакой парсер таблиц не поможет."""
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:5]:
            if (page.extract_text(x_tolerance=X_TOL) or "").strip():
                return True
    return False


def extract_text(path: str) -> str:
    """Плоский текст всего документа — источник для обратной сверки чисел (D-14).

    Именно этот текст, а не пересказ модели, потом ищет верификатор. Поэтому
    берём его как есть, без нормализации: чем ближе к странице, тем честнее сверка.
    """
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text(x_tolerance=X_TOL) or "")
    return "\n".join(out)


def extract(path: str) -> list:
    """Таблицы документа в том же формате, что даёт разбор .docx и JATS."""
    import pdfplumber
    tables = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            words = page.extract_text(x_tolerance=X_TOL) or ""
            # заголовок ищем на той же странице: pdfplumber не связывает подпись
            # с таблицей сам, а без подписи таблица теряет половину смысла
            caps = [m.group(1).strip() for m in
                    (CAPTION_RE.match(ln) for ln in words.splitlines()) if m]
            for i, raw in enumerate(page.extract_tables({"text_x_tolerance": X_TOL})):
                rows = [[_clean(c) for c in r] for r in raw]
                rows = [r for r in rows if any(c for c in r)]
                if len(rows) < 2:
                    continue
                cap = caps[i] if i < len(caps) else (caps[0] if caps else "")
                # Первая строка — заголовок колонок: так же устроен выход
                # jats_tables, и оркестратор подаёт обе таблицы модели одинаково.
                tables.append({
                    "label": cap.split(".")[0].split(":")[0].strip()[:24],
                    "caption": cap,
                    "columns": rows[0],
                    "rows": rows[1:],
                    "n_rows": len(rows),
                    "n_cols": max(len(r) for r in rows),
                    "page": pno,
                })
    return tables


def is_appendix(table: dict) -> bool:
    """Приложение или основная таблица. Различие не косметическое: замер показал,
    что уровень L1 открывают именно приложения (F-26), поэтому оркестратор кладёт
    их в подачу модели первыми."""
    cap = (table.get("caption") or "").lower()
    return bool(re.match(r"^\s*(e|supplementary|appendix|supplemental)", cap))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--table", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--text", action="store_true", help="выдать текстовый слой")
    a = ap.parse_args()

    if a.text:
        print(extract_text(a.path))
        return

    tables = extract(a.path)
    if a.json:
        json.dump(tables, sys.stdout, ensure_ascii=False, indent=2)
        return

    if a.table is not None:
        t = tables[a.table]
        print(f"[{a.table}] стр.{t['page']} {t['caption']}  ({t['n_rows']}×{t['n_cols']})")
        for r in t["rows"]:
            print("  | " + " | ".join(c[:26] for c in r))
        return

    print(f"текстовый слой: {'есть' if has_text_layer(a.path) else 'НЕТ — нужен OCR'}")
    print(f"таблиц найдено: {len(tables)}\n")
    for i, t in enumerate(tables):
        nums = sum(1 for r in t["rows"] for c in r if re.search(r"\d", c))
        mark = "ПРИЛ" if is_appendix(t) else "    "
        print(f"[{i:>2}] {mark} стр.{t['page']:>3} {t['n_rows']:>3}×{t['n_cols']:<3} "
              f"чисел:{nums:>4}  {(t['caption'] or '(без заголовка)')[:64]}")


if __name__ == "__main__":
    main()
