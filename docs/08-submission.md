# 08 · Текст заявки на Devpost

Готовый под копипаст текст (английский — язык подачи). Русские пометки в блоках
`> [для нас]` в форму **не переносить**.

Требование S5: features, technologies, data sources, learnings. Плюс обязательная
декларация pre-existing work — правило новизны, `docs/01-requirements.md:42`.

---

## Название

**I Am Truth — a methodological auditor for biomedical papers**

## Tagline (одна строка)

An agent that audits how a study was actually done — and tells you how much of that
audit it could ground in real data.

---

## Inspiration

In June 2026 a paper in *JCO Oncology Practice* reported that women on GLP-1 agonists
had roughly a third lower breast cancer incidence (OR 0.649, 95% CI 0.569–0.741).
The press ran with it: *"Ozempic cuts breast cancer risk."*

The paper itself says something quieter. Its own conclusion reads: *"While our results
are intriguing, they are largely hypothesis generating. We propose advancing to a
randomized trial."* The absolute risk difference is **0.69 percentage points** — 2.31%
versus 1.62%, NNT ≈ 145. And in the appendix, a table nobody quoted: 9.2% of the control
group had a **prior history of breast cancer** versus 5.9% of the GLP-1 group — a
variable that was never part of the matching. The comparison group was enriched with the
single strongest predictor of the outcome.

Nothing was fabricated. The numbers are consistent and the authors are candid in their
limitations. The distortion happens in the packaging, and it happens *after* peer review.

The obvious response is to point a language model at the abstract and ask it to be
critical. We tried that first, and the result is more interesting than a simple failure:
the model reaches a *plausible* verdict, and often a correct one — but it supports it
with statements that would fit any observational study. "Unmeasured health-seeking
behaviour." Nothing in that sentence can be checked, because nothing in it comes from
this paper.

Given the appendix, the same model supports the same verdict with 7.8% versus 5.9% from
Table A2 — a number that can be looked up, and was. And one thing it never reaches
without the appendix: that the GLP-1 arm was *sicker* (Charlson 5+: 19.7% vs 10.4%) and
still had *fewer* cancers, which is the whole anomaly.

So the problem is not the prompt. It is that the evidence lives in files nobody reads.

## What it does

Give it a DOI. It retrieves the full text, pulls the supplementary tables, runs a
seven-domain **ROBINS-E** risk-of-bias assessment, checks every single number it reports
back against the source document, computes the statistics itself, and returns a report
that states **what level of evidence it was actually able to reach**.

That last part is the point. Three levels, and their cost is measured, not assigned:

| Level | What was retrieved | Max confidence | Measured score |
|---|---|---|---|
| L1 | full text + appendix tables | `CONFIRMED` | 6.0 / 6 (prepared input) · 3.5 / 6 (live PDF) |
| L2 | full text, no appendices | `PLAUSIBLE` / `UNVERIFIED` | 4.5 / 6 |
| L3 | abstract only | `PLAUSIBLE` / `UNVERIFIED` | 4.0 / 6 |

`CONFIRMED` is structurally unreachable below L1 — because below it the agent has nothing
to cite. It will still name the direction of bias correctly from the abstract alone, but
it justifies that with reasoning that fits any observational study; with the appendix it
justifies the same conclusion with a number from the document. A correct guess is not
evidence, so only the second is allowed to be called confirmed.

When a paper is locked (Cloudflare, or a publisher's TDM token), you hand it the file
instead: `POST /analyze/upload` takes PDFs and .docx and reaches the same L1.

## How we built it

- **Gemini 3.7 Flash** on **Vertex AI**, `global` endpoint, temperature 0 — two parallel
  passes per paper: a ROBINS-E critic across seven domains, and a narrow sub-agent that
  reads nothing but the baseline characteristics table.
- **google-genai SDK** 1.56.0, and the same audit expressed as a **Google ADK**
  `ParallelAgent` of three agents that get tools they can call mid-reasoning.
- **Cloud Run** for the synchronous service, **Cloud Run Jobs** for unattended batch
  work over a corpus, **GCS** for results — one object per paper, so a single failure
  never takes the run down.
- **Europe PMC REST** for metadata, JATS full text and supplementary files; **Unpaywall**
  as a fallback channel.
- Table extraction on the standard library (JATS, .docx) and **pdfplumber** (PDF).
- **ROBINS-E**, the published risk-of-bias framework for observational studies, as the
  assessment rubric — the same one the paper's own authors cite.

Two decisions carry the architecture.

**The number verifier sits after the model, not inside it.** Asking a model to check
itself is asking the same process for a second opinion. A separate layer takes every
number the model wrote, locates it in the retrieved source with its surrounding context,
and compares group labels — so it catches not only an invented value (`UNVERIFIED`) but
a real value attached to the wrong group (`GROUP_MISMATCH`).

**We built the same thing twice and published the losing comparison.** The audit runs
either as plain-code orchestration or as a Google ADK `ParallelAgent` whose agents can
call a risk calculator and a source-checker mid-reasoning. Three runs each: both reach a
median of 5.5/6. ADK did not improve the audit. It is offered as an option and is not
the default, because making it one would sell a tie as an improvement — and the tie is
worth reporting, since "we adopted the framework and it changed nothing measurable" is a
finding too.

**Sub-agents are added where a measurement demands one, not by default.** On the real
PDF the single critic never reached one expert point in three runs: that the exposed arm
was sicker at baseline (Charlson 5+: 19.7% vs 10.4%) and still had fewer cancers. One
call cannot both survey seven domains and put two distant numbers side by side. A second
pass doing only the baseline table reached it in three runs out of three, and lifted the
overall score from a median of 3.5 to 5.5. The remaining six domains have no sub-agent,
because they show no comparable failure.

**Statistics are computed, never generated.** E-value, absolute risk reduction, NNT and
confidence intervals come from a function that self-tests on import. The model is never
asked to do arithmetic.

## Challenges we ran into

**The model tier we needed does not exist.** The rules require Gemini 3.5 or newer. On
Vertex, the Pro line stops at `gemini-3.1-pro-preview` — everything ≥3.5 is Flash. Our
original result was measured on 2.5 Pro, which does not qualify. We re-measured the
whole thing on 3.7 Flash; the effect held.

**Background agents on Vertex are not what they look like.** `background=True` in the
Interactions API only accepts managed agents — all eight generateContent models return
`400 Unsupported model interaction`. So the unattended path is a Cloud Run Job, not a
framework feature.

**Only a quarter of the literature is machine-reachable.** Europe PMC serves full text
for 27.5% of papers in this class; appendices arrive in 9 of 11 cases and always as
`.docx`, never PDF. Adding publisher PDFs where a machine-readable pointer exists brings
automatic retrieval to roughly 55%. The rest is behind bot protection we will not work
around — which is why the upload path exists.

**Our own worst bug was the one this product is built to catch.** The verifier stripped
markup with the pattern `<[^>]+>`. In a scientific paper `<` and `>` are also comparison
signs — *"p < 0.001 … coefficients > 0.80"* — so everything between them was deleted as
if it were a tag. On one PDF that removed **24,488 characters out of 72,468**, a third
of the source, and numbers the model had honestly quoted came back `UNVERIFIED`.
Accusing of hallucination something that did not hallucinate is the worst failure this
system can have.

## Accomplishments we're proud of

**The levels are measured.** Three runs, same model, same prompt, same temperature,
three different inputs. The abstract→appendix gap is positive in every run (+1.0, +2.5,
+2.0). And it is mechanism, not correlation: each level unlocks exactly the expert
points that physically live in it. The point about comparator composition rises only
when Table 2 appears; the two appendix points are never reached without the appendix.

**We measured how far it does *not* generalise, and published that too.** Calibration
originally rested on one paper, critiqued by this project's own author — a setup that
measures agreement with oneself. So a second reference was taken from outside: a
published letter to the editor about a different GLP-1/cancer cohort, with the authors'
reply printed next to it. The system scores 92% against our own reference and **25%
against the external one**, stably across three runs. The per-point breakdown shows why:
the second paper's flaws are time-related — lag periods, latency, the shape of the
duration gradient — and this system is built around confounding and group comparability.
So we made that move: one more pass that does nothing but reconstruct the study's
timeline — new-user versus prevalent-user, immortal time, lag period, latency, and the
shape of the duration gradient. Re-measured, three runs each: **92% on our own reference, 88% on the external one**, with
no loss on the case we designed around. The spread between the two fell from 67 points to
4. What we are claiming is not a higher score but a narrower gap between a case we built
for and one we did not — and every run behind those numbers is stored in the repository,
reproducible with one command.

**We found our own errors with our own instrument.** Twice the reference standard itself
was wrong — once with an inverted direction, once missing a whole defect the model kept
correctly finding and never got credit for. Both were caught by the harness, not by
reading. The measurement was fixed, and both failures are written up in the repository
rather than quietly corrected.

**It works on the real file, and we know exactly how well.** Handed the published
10-page PDF of the paper that started this, the agent reaches L1, verifies every number
it reports, and returns the same verdict the human expert reached —
`real_association_explained_by_selection`. It catches the appendix imbalance with the
correct direction in all three runs, the same direction it gets *wrong* when given only
the abstract. Against the expert reference it scores a **median of 5.5 / 6** on the published PDF — up
from 3.5 with a single critic, and deliberately not the 6.0 it reaches on a prepared input
where the numbers already sit side by side. We report the real-file number, since
inflating a score is the exact failure this project exists to detect.

**It runs in the cloud, unattended.** The last batch: 8 papers, 8 successes, 232 numbers
verified. All eight came back L3 — which is not a malfunction. It is the system refusing
to call anything confirmed when it only read an abstract.

## What we learned

The valuable part of this project is not the critical prompt. Anyone can copy a prompt.
The valuable part is forcing retrieval of the appendix — and being able to say, in
numbers, what it is worth.

Two related lessons, both learned the hard way:

**Never carry a number across a change in how it was produced.** When our reference gained
a sixth point, the denominator in the code was updated to `/6` and the scores were left
as they were. Genuine values, attached to the wrong scale — exactly the defect our
verifier reports as `GROUP_MISMATCH` in other people's work. We re-measured instead of
re-labelling.

**A test written against fixed code proves nothing until you put the bug back.** Twice a
test we were satisfied with failed to detect the defect it was written for. Both are now
verified in both directions.

## What's next

More sub-agents — but only where a measurement shows the single critic failing, the way
it did for baseline comparability. A second reference paper, because calibration
currently rests on one. And OCR for scanned PDFs, which the system today
declines honestly rather than pretending to read.

---

## Technologies

`Gemini 3.7 Flash` · `Vertex AI` · `google-genai SDK` · `Google ADK` · `Cloud Run` · `Cloud Run Jobs` ·
`Cloud Storage` · `Cloud Build` · `FastAPI` · `pdfplumber` · `Python 3.12` ·
`Europe PMC REST API` · `Unpaywall API` · `ROBINS-E`

## Data sources

| Source | Use | Access |
|---|---|---|
| Europe PMC REST API | metadata, JATS full text, supplementary files | public, no key |
| Unpaywall API | open-access location lookup | public, email-identified |
| Publisher PDFs | only where a machine-readable `citation_pdf_url` is published | public |
| Files supplied by the user | papers not reachable automatically | user-provided |

No dataset was scraped, no site protection was circumvented, and no paper text is
redistributed in the repository.

---

## ⚠️ Disclosure of pre-existing work

> [для нас] Это обязательная строка правила новизны, `docs/01-requirements.md:42`.
> В форму переносится дословно.

All code in this project was written during the Submission Period and its authorship is
traceable in the repository's git history.

One input is **pre-existing** and is disclosed here: the expert methodological critique
of McDonald et al. (*JCO Oncology Practice* 2026, DOI `10.1200/OP-26-00485`), written by
the author in June 2026, before the hackathon. It is used **as ground truth for
evaluation** — the six-point reference against which the agent's output is scored — and
not as part of the product. The agent does not contain, consult, or ship it at runtime.

Two clarifications, since the distinction decides what the measurement means:

1. The evaluation inputs in `eval/inputs/` are **restatements** of that paper's design
   and figures in our own words, not reproductions of the publisher's text.
2. All measurements reported in this submission were produced during the Submission
   Period. The June work contributed the reference standard, not the results — and
   during the hackathon that reference standard was twice found to be **wrong** and
   corrected (an inverted direction, and a missing sixth defect).

---

## Links

| What | Where |
|---|---|
| Live service | https://i-am-truth-242136767009.us-central1.run.app |
| Repository | https://github.com/gagarinyury/i-am-truth |
| Architecture | `docs/07-architecture.md` |
| Demo video | *(S1 — заполнить)* |

> [для нас] Перед подачей: перепроверить правила на Devpost (правятся без
> уведомления), выяснить отметку для номинации Individual/Hobbyist,
> подставить ссылку на видео.
