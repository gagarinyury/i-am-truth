# 09 · Сценарий демо-видео (S1)

**Требования:** ≤4 минуты, публично на YouTube или Vimeo, английский или английские
субтитры, обязателен видимый пруф работы в облаке.

**Разделение труда:** этот файл, прогнанные заранее команды и субтитры — моя часть;
запись экрана и голос — твоя.

**Правило записи:** всё выполняется **против живого сервиса**, не локально. Судья
должен видеть `.run.app` — в адресной строке браузера и в терминале. Это и есть пруф
деплоя (R4, S7).

**Главное изменение сценария от 28.08 (F-54): кадры 2–4 снимаются в браузере, а не в
терминале.** У проекта появилась страница, и снимать `curl` там, где судья мог бы
увидеть работающий продукт, — значит своими руками отдать треть оценки
(«Demo & Production Readiness» — 30%). Терминал остаётся ровно в одном месте: кадр 5,
где показывается облако. Там он уместен, потому что это и есть пруф инфраструктуры.

---

## Подготовка перед записью

```bash
export URL=https://i-am-truth-242136767009.us-central1.run.app
# PDF McDonald положить рядом и назвать коротко — длинное имя портит кадр
cp ~/Downloads/mcdonald-et-al-2026-*.pdf ./mcdonald.pdf
```

Проверить, что сервис отвечает и не «холодный» (первый запрос после простоя идёт
дольше — прогреть заранее):

```bash
curl -s $URL/health
```

**Браузер:** окно 1440×900 или шире, масштаб 100%, никаких закладок и расширений в
кадре. Открыть страницу заранее и один раз прогнать разбор, чтобы сервис не был
«холодным» — первый запрос после простоя идёт дольше.

**Терминал** (нужен только для кадра 5): крупный шрифт, тёмная тема, ширина ~100
колонок, вывод фильтровать через `python3 -c`, иначе JSON зальёт экран.

---

## Кадр 1 · 0:00–0:30 — проблема

**Что на экране:** заголовок в прессе → страница статьи → строка вывода авторов.

| Показать | Источник |
|---|---|
| «GLP-1 use linked to 30% reduced breast cancer risk» | Penn Medicine / Oncology Nursing News |
| Из самой статьи: *"While our results are intriguing, they are largely hypothesis generating. We propose advancing to a randomized trial."* | Discussion |
| Абсолютная разница: **2.31% → 1.62%**, ARR **0.69 п.п.**, NNT ≈ 145 | Table 5 |

**Голос (EN):**
> A paper reports a thirty percent drop in breast cancer. The paper's own conclusion
> calls it hypothesis generating. The absolute difference is zero point six nine
> percentage points. Nothing here is fabricated — the distortion happens after peer
> review, in the packaging.

---

## Кадр 2 · 0:30–1:15 — та же статья, только DOI

**Что на экране:** страница сервиса. В поле вставляется `10.1200/OP-26-00485`, нажимается
Audit. В кадре видно, как идут этапы: fetching the paper → pulling tables → three agents
reading → checking every number.

**Что видно в отчёте:** крупная метка **`L3`**, «abstract only — ceiling
PLAUSIBLE-UNVERIFIED», ноль приложенных таблиц и текст оговорки: разбор сделан на
неполных данных, подтверждённым здесь ничто называться не может.

**Голос (EN):**
> Give it just the DOI. This paper sits behind bot protection, so all the agent can reach
> is the abstract. It says so itself: level three, no tables, and nothing here may be
> called confirmed. That refusal is the product.

> [для нас] Не торопить этот кадр. Пустой блок «appendix tables: 0» рядом с честной
> оговоркой — половина смысла проекта. Дальше он заполнится, и разница будет видна
> глазами, без единого слова.

---

## Кадр 3 · 1:15–2:30 — та же статья, файл в руках · **главный кадр**

**Что на экране:** `mcdonald.pdf` перетаскивается прямо на страницу, Audit.

**Что видно в отчёте** (проверено на живом сервисе):

- метка меняется на **`L1`**, «full text + appendix tables — ceiling **CONFIRMED**»,
  счётчик приложенных таблиц перестаёт быть нулём;
- в блоке Verdict — `away_from_null` и `real_association_explained_by_selection`;
- в домене Confounding раскрывается цитата: *Matched non-users had a prior breast cancer
  prevalence of 7.8% (1,197/15,264) compared to 5.9% (907/15,264) in GLP-1 users
  (Table A2)*;
- у агента baseline comparability — красная плашка с противоречием: группа GLP-1 была
  тяжелее по ожирению, диабету и Charlson, а рака у неё меньше.

**Голос (EN):**
> Now hand it the file. Level one. It reads an appendix table nobody quoted, and finds
> that the comparison group carried more prior breast cancer than the treated group:
> seven point eight against five point nine percent. That variable was never part of the
> matching. Its verdict: a real association explained by selection — word for word what a
> human expert concluded reading the same paper.

> [для нас] Держать в кадре обе вещи разом: `away_from_null` и цитату из Table A2.
> Направление важнее балла — именно оно отличает «модель что-то написала» от «модель
> поняла, куда смещает». Раскрыть домен Confounding заранее, до записи, чтобы не тратить
> секунды на клик.

---

## Кадр 4 · 2:30–3:15 — агент проверяет сам себя

**Что на экране:** прокрутка того же отчёта к блоку **Every number checked against the
source**. Четыре счётчика: confirmed with its label · found, label unclear · not found in
source · **groups inverted**.

Затем — короткая вставка: в поле вводится текст, где числа намеренно переставлены
местами относительно статьи, и в отчёте загорается ненулевой счётчик.

**Голос (EN):**
> Every number the model writes is taken back to the source and looked up together with
> what it describes. Not asked of the model — checked against the document. It catches
> invented values, and it catches real values attached to the wrong group. That second
> mistake is one we made ourselves twice while building this, and both times the
> instrument caught it, not us.

> [для нас] Усиление, если останется секунда: показать `docs/02-verified-facts.md`,
> F-12 и F-51 — наши собственные ошибки, найденные инструментом. Признанная ошибка
> работает на доверие сильнее любых зелёных галочек.

---

## Кадр 5 · 3:15–4:00 — облако и масштаб

```bash
gcloud run jobs executions list --job i-am-truth-batch --region us-central1 --limit 3
curl -s $URL/runs | python3 -m json.tool
curl -s $URL/runs/run-20260827-222336 | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('papers:', d.get('total'), '| ok:', d.get('ok'), '| numbers verified:', d.get('numbers_checked'))
print('levels:', d.get('levels'), '| storage_ok:', d.get('storage_ok'))"
```

Затем — **консоль GCP** (это и есть требуемый пруф): Cloud Run → сервис `i-am-truth`,
ревизия; Cloud Run Jobs → `i-am-truth-batch`, выполнение; Cloud Storage → бакет с
результатами.

**Голос (EN):**
> It also runs unattended. A Cloud Run Job takes a query, finds a corpus in Europe PMC,
> and audits it in the background — eight papers, five hundred seventeen numbers
> checked, results in Cloud Storage. All eight came back level three. That is not a
> failure. Only about a quarter of this literature is machine-reachable, and the agent
> refuses to call anything confirmed when all it read was an abstract.

**Финальная строка (EN):**
> It tells you what it could not check. That is the whole idea.

---

## Хронометраж

| Кадр | Длительность | Накопительно |
|---|---|---|
| 1 · проблема | 0:30 | 0:30 |
| 2 · DOI → L3, в браузере | 0:45 | 1:15 |
| 3 · файл перетащили → L1, приложение, вердикт | 1:15 | 2:30 |
| 4 · блок сверки чисел на экране | 0:45 | 3:15 |
| 5 · облако | 0:45 | 4:00 |

Запас нулевой — если что-то режется, режется кадр 4, а не кадр 3.

**Ожидаемое время ответа сервиса:** `/analyze` по DOI ≈ 50 с (три агента), `/analyze/upload`
с PDF ≈ 35–48 с. В кадре паузы вырезать монтажом, но **не подменять вывод** — он должен
быть настоящим.

---

## Субтитры

Файл `docs/video/subtitles.srt` собирается из блоков «Голос (EN)» после того, как
станет известен фактический хронометраж записи. Тайминги проставляются по готовому
видео — заранее их писать бессмысленно.
