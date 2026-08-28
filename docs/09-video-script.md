# 09 · Сценарий демо-видео (S1)

**Требования:** ≤4 минуты, публично на YouTube или Vimeo, английский или английские
субтитры, обязателен видимый пруф работы в облаке.

**Разделение труда:** этот файл, прогнанные заранее команды и субтитры — моя часть;
запись экрана и голос — твоя.

**Правило записи:** все команды выполняются **против живого сервиса**, не локально.
Судья должен видеть `.run.app` в строке запроса — это и есть пруф деплоя (R4, S7).

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

Терминал: крупный шрифт, тёмная тема, ширина ~100 колонок. Вывод длинный — фильтровать
через `python3 -c`, как в командах ниже, иначе JSON зальёт экран.

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

## Кадр 2 · 0:30–1:15 — та же статья, только абстракт

```bash
curl -s -X POST $URL/analyze \
  -H 'Content-Type: application/json' \
  -d '{"doi":"10.1200/OP-26-00485"}' \
| python3 -c "
import sys,json; d=json.load(sys.stdin)
print('level:', d['level']['level'], '| max confidence:', d['max_confidence'])
print('tables:', d['tables'])
print(d['caveat'])"
```

**Что видно:** `L3`, таблиц ноль, потолок уверенности `PLAUSIBLE-UNVERIFIED` и явная
оговорка, что разбор сделан на неполных данных.

**Голос (EN):**
> Give it just the DOI. This paper is behind bot protection, so all the agent can reach
> is the abstract. It says so: level three, no tables, and nothing here may be called
> confirmed. That refusal is the product.

---

## Кадр 3 · 1:15–2:30 — та же статья, файл в руках · **главный кадр**

```bash
curl -s -X POST $URL/analyze/upload \
  -F "files=@mcdonald.pdf" -F "doi=10.1200/OP-26-00485" \
| python3 -c "
import sys,json; d=json.load(sys.stdin); f=d['findings']
print('level:', d['level']['level'], '| tables:', d['tables'])
c=[x for x in f['domains'] if 'onfound' in x['name']][0]
print('confounding:', c['risk'], '| direction:', c['direction'])
print(c['findings'][0]['evidence'][0])
print('verdict:', f['classification'])
print('numbers checked:', d['verification'])"
```

**Что видно** (проверено 28.08 на живом сервисе):

```
level: L1 | tables: {'main': 9, 'appendix': 6}
confounding: high | direction: away_from_null
Matched non-users had a prior breast cancer prevalence of 7.8% (1,197/15,264)
compared to 5.9% (907/15,264) in GLP-1 users (Table A2)
verdict: real_association_explained_by_selection
```

**Голос (EN):**
> Now hand it the file. Level one. It reads the appendix — a table nobody quoted — and
> finds that the comparison group carried more prior breast cancer than the treated
> group: seven point eight against five point nine percent. That variable was never part
> of the matching. Its verdict: a real association explained by selection. Word for word
> what a human expert concluded reading the same paper.

> [для нас] Держать в кадре обе строки — `direction: away_from_null` и цитату из
> Table A2. Направление важнее балла: именно оно превращает «модель что-то написала»
> в «модель поняла, куда смещает».

---

## Кадр 4 · 2:30–3:15 — агент проверяет сам себя

```bash
curl -s -X POST $URL/analyze \
  -H 'Content-Type: application/json' \
  -d '{"text":"In this cohort of 15264 patients, the GLP-1 group had 9.2% prior breast cancer versus 5.9% in controls, and the hazard ratio was 0.42 (95% CI 0.31-0.58)."}' \
| python3 -c "
import sys,json; d=json.load(sys.stdin)
print('verification:', d['verification'])
for u in d['unverified_numbers'][:4]: print(' ', u['status'], u['value'], '—', str(u['label'])[:50])"
```

**Что показать:** числа, которых в поданном тексте нет, помечаются `UNVERIFIED`;
9.2% и 5.9% в этом тексте **поменяны местами** относительно статьи — на полном
источнике такое ловится как `GROUP_MISMATCH`.

**Голос (EN):**
> Every number the model writes is taken back to the source document and looked up with
> its context. Not asked of the model — checked against the file. It catches invented
> values, and it catches real values attached to the wrong group. That second one is the
> mistake we ourselves made twice while building this, and both times the instrument
> caught it, not us.

> [для нас] Если хочется усилить — показать `docs/02-verified-facts.md`, F-12 и F-40:
> наши собственные ошибки, найденные харнесом. Это работает на доверие сильнее, чем
> зелёные галочки.

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
> and audits it in the background — eight papers, two hundred thirty-two numbers
> verified, results in Cloud Storage. All eight came back level three. That is not a
> failure. Only about a quarter of this literature is machine-reachable, and the agent
> refuses to call anything confirmed when all it read was an abstract.

**Финальная строка (EN):**
> It tells you what it could not check. That is the whole idea.

---

## Хронометраж

| Кадр | Длительность | Накопительно |
|---|---|---|
| 1 · проблема | 0:30 | 0:30 |
| 2 · только абстракт → L3 | 0:45 | 1:15 |
| 3 · файл → L1, приложение, вердикт | 1:15 | 2:30 |
| 4 · самопроверка чисел | 0:45 | 3:15 |
| 5 · облако | 0:45 | 4:00 |

Запас нулевой — если что-то режется, режется кадр 4, а не кадр 3.

**Ожидаемое время ответа сервиса:** `/analyze` по DOI ≈ 30 с, `/analyze/upload` с
PDF ≈ 35–48 с. В кадре паузы вырезать монтажом, но **не подменять вывод** — он должен
быть настоящим.

---

## Субтитры

Файл `docs/video/subtitles.srt` собирается из блоков «Голос (EN)» после того, как
станет известен фактический хронометраж записи. Тайминги проставляются по готовому
видео — заранее их писать бессмысленно.
