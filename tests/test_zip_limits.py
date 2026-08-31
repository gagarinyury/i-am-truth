#!/usr/bin/env python3
"""
Сжатый файл маленький, распакованный — какой угодно.

Дыра, ради которой написан тест. Загрузка ограничена 25 МБ, и предел проверялся
до чтения в память — это было сделано аккуратно. Но проверялся размер **сжатого**
файла, а в память читалось содержимое: `z.read("word/document.xml")` без всякого
предела, в трёх местах сразу. `word/document.xml` — повторяющаяся разметка, она
жмётся в сотни раз, поэтому 25 МБ на входе означают сколько угодно в памяти
контейнера, у которого всего два слота разбора.

Здесь проверяется, что предел стоит на распакованном размере и что законный файл
через него проходит. Архив собирается потоком, поэтому сам тест не держит в
памяти то, от чего защищается.
"""
import pathlib
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from truth import pipeline                                      # noqa: E402
from truth.docx_tables import MAX_UNCOMPRESSED, TooLarge        # noqa: E402
from truth.docx_tables import extract, read_limited             # noqa: E402

ok = total = 0


def check(name, cond, detail=""):
    global ok, total
    total += 1; ok += bool(cond)
    print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
GOOD = (f'<w:document {W}><w:body>'
        '<w:tbl><w:tr><w:tc><w:p><w:t>Charlson 5+</w:t></w:p></w:tc>'
        '<w:tc><w:p><w:t>19.7</w:t></w:p></w:tc></w:tr></w:tbl>'
        '</w:body></w:document>')


def make_docx(path, payload_mb=0):
    """.docx с обычной таблицей и, по желанию, раздутым document.xml."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if payload_mb:
            # пишем потоком: сам тест не должен держать сотню мегабайт в памяти
            with z.open("word/document.xml", "w") as fh:
                fh.write(b'<w:document ' + W.encode() + b'><w:body><w:p><w:t>')
                chunk = b" " * (1024 * 1024)
                for _ in range(payload_mb):
                    fh.write(chunk)
                fh.write(b'</w:t></w:p></w:body></w:document>')
        else:
            z.writestr("word/document.xml", GOOD)


tmp = pathlib.Path(tempfile.mkdtemp())
good, bomb = tmp / "good.docx", tmp / "bomb.docx"
make_docx(good)
make_docx(bomb, payload_mb=(MAX_UNCOMPRESSED // (1024 * 1024)) + 8)

print("законный файл проходит…")
tabs = extract(str(good))
check("таблица разобрана", len(tabs) == 1 and tabs[0]["rows"][0][1] == "19.7",
      str(tabs))

print("\nраздутый — отвергается, и до чтения в память…")
size_on_disk = bomb.stat().st_size
check("на диске он мал", size_on_disk < 1024 * 1024, f"{size_on_disk} байт")
try:
    extract(str(bomb))
    check("распаковка остановлена", False, "прошла целиком")
except TooLarge as e:
    check("распаковка остановлена", True, str(e)[:60])

with zipfile.ZipFile(bomb) as z:
    try:
        read_limited(z, "word/document.xml", limit=1024)
        check("предел задаётся параметром", False)
    except TooLarge:
        check("предел задаётся параметром", True)

print("\nоркестратор переживает такой файл без падения…")
got = pipeline._from_uploads([("bomb.docx", bomb.read_bytes())])
check("разбор не рухнул", isinstance(got, dict))
check("файл назван в примечаниях", any("bomb.docx" in n for n in got["notes"]),
      str(got["notes"]))
check("таблиц из него не взято", got["appendix"] == [])
check("плоский текст тоже пуст", pipeline._docx_text(bomb.read_bytes()) == "")

print("\nа обычный .docx через тот же путь разбирается…")
got = pipeline._from_uploads([("appendix.docx", good.read_bytes())])
check("таблица приложения на месте", len(got["appendix"]) == 1, str(got["notes"]))
check("текст извлечён", "19.7" in pipeline._docx_text(good.read_bytes()))

print(f"\n{ok}/{total} проверок пройдено")
sys.exit(0 if ok == total else 1)
