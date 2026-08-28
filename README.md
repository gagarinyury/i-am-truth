# I Am Truth · Я есть Правда

**An agent that audits the methodology of biomedical papers — and reports how much of
that audit it was actually able to ground in the data.**

Submission to the Google **All Things Agentic Hackathon** (deadline 01.09.2026, 02:00 CEST).

> Working documentation in `docs/` is written in Russian; this README, the architecture
> diagram and the submission text are in English.

---

## The claim this project makes

A critical prompt is not a product. Anyone can copy one.

What is hard — and what we measured — is **forcing the retrieval of full text and, above
all, appendix tables**. The same model, the same prompt, the same temperature, three
different inputs:

| Input | What the model can see | Score, 3 runs | Median |
|---|---|---|---|
| abstract only | title, abstract, metadata | 4.0 · 3.5 · 4.0 | **4.0 / 6** |
| full text, no appendices | Tables 1–5, authors' own caveats | 4.5 · 5.0 · 4.0 | **4.5 / 6** |
| full text **+ appendix tables** | Appendix Tables 1–2 | 5.0 · 6.0 · 6.0 | **6.0 / 6** |

Scored by an LLM judge against a six-point expert reference, temperature 0,
`gemini-3.7-flash`, the same ROBINS-E prompt that runs in production.

The gap between the first row and the last is positive in **every** run (+1.0, +2.5,
+2.0). The middle row is not: in one run out of three, the full text gave nothing over
the abstract. So the honest statement is not "each step up helps a little" — it is
**the appendix is where the audit becomes real**.

**And the honest caveat.** Those three inputs are prepared documents, where the relevant
appendix numbers sit next to each other. Run the same system end-to-end on the
**published 10-page PDF** and it scores **3.5–4.5 / 6 (median 3.5)** across three runs —
lower, because the numbers now have to be *found* before they can be reasoned about.
What survives on the real file is the part that matters most: the appendix imbalance is
caught with the **correct direction** every time, and the verdict matches the human
expert's word for word (`real_association_explained_by_selection`). What does not
survive is the point requiring two distant numbers to be combined — never scored on the
real file, always scored on the prepared one. That gap is the argument for per-domain
sub-agents, and it is written up in `docs/02-verified-facts.md`, F-43.

The interesting part is not the trend. It is the **mechanism**: each level unlocks
exactly those expert points that physically live in it, and no others (`docs/02-verified-facts.md`, F-26).

And one specific failure survives two levels out of three: given only the abstract or
only the main text, the model confidently reports a *"healthy user effect"* — and gets
the **direction of confounding backwards**. It is corrected only by Appendix Table 1,
which shows the GLP-1 arm was *sicker* (Charlson 5+: 19.7% vs 10.4%) yet had *fewer*
cancers. That is the measured price of the retrieval layer.

---

## Live service

```
https://i-am-truth-242136767009.us-central1.run.app
```

```bash
URL=https://i-am-truth-242136767009.us-central1.run.app

curl $URL/health

curl -X POST $URL/analyze \
     -H 'Content-Type: application/json' \
     -d '{"doi": "10.1136/jitc-2025-014726"}'
```

That DOI is an open-access paper: the service pulls the JATS full text **and 35 appendix
tables**, runs a seven-domain ROBINS-E assessment, and back-checks every number it
reports against the source. Measured: 34 seconds, level L1, 102 numbers verified,
0 invalid (F-36).

### When the paper is not open

Automatic retrieval reaches about 55% of papers in this class; the rest sits behind
Cloudflare or a publisher's TDM token, and we do not work around site protection. But a
paper unavailable to a script is usually available to a person. So it can be handed over
directly:

```bash
curl -X POST $URL/analyze/upload \
     -F "files=@paper.pdf" -F "files=@appendix.pdf" \
     -F "doi=10.1200/OP-26-00485"
```

Several files are accepted because most journals ship the appendix separately — and the
appendix is what raises the level to L1. `doi` is optional; with it, metadata is filled
in and, if Europe PMC happens to hold the supplement, it is added to what you brought.

Validated against path A on a paper reachable both ways: same level (L1), same tables
(5 main + 2 appendix), 0 unverified numbers on both.

Interactive API docs: `$URL/docs`.

---

## Run it locally

Three commands, assuming a Google Cloud project with Vertex AI enabled:

```bash
gcloud auth application-default login
pip install -r requirements.txt
uvicorn app.main:app --port 8080
```

Configuration is entirely through environment variables, all with working defaults:

| Variable | Default | Note |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | `merci-prod` | project billed for Vertex calls |
| `VERTEX_LOCATION` | `global` | Gemini 3.x on Vertex answers on `v1beta1` only |
| `TRUTH_MODEL` | `gemini-3.7-flash` | see D-01 on why not Pro |
| `TRUTH_BUCKET` | `i-am-truth-runs-merci-prod` | GCS bucket for batch output |
| `TRUTH_WORKERS` | `3` | Vertex returns 429 from the fifth consecutive call (F-11) |

**Two traps worth knowing before the first call.** The account that pays is decided by
`GOOGLE_CLOUD_PROJECT`, but the account that *calls* is decided by Application Default
Credentials, and they live separately — verify with
`curl "https://www.googleapis.com/oauth2/v3/tokeninfo?access_token=$(gcloud auth application-default print-access-token)"`.
And on Vertex, Gemini 2.5+/3.x models are served on `v1beta1`; `v1` returns 404.

`bash scripts/preflight.sh` runs ten environment checks, including the integrity of the
evaluation inputs.

---

## API

| Endpoint | What it does |
|---|---|
| `GET /` | service status and live configuration — doubles as deployment proof |
| `GET /health` | liveness, version, active model |
| `GET /levels` | the three evidence levels with their **measured** cost |
| `POST /analyze` | `{"doi": "..."}` or `{"text": "..."}` → full report |
| `POST /analyze/upload` | the paper as files (`.pdf` / `.docx`) → same report, **path B** |
| `GET /runs` | batch runs completed by the Cloud Run Job |
| `GET /runs/{run_id}` | one run's summary: level distribution, verification statistics |

A `/analyze` report carries, alongside the findings: the evidence level actually
achieved, what was retrieved (main tables, appendix tables), the seven ROBINS-E domain
judgements, and a verification block listing every number checked against the source.

---

## Evidence levels

The system never claims more than its input allows. Levels are **measured, not assigned**:

| Level | Input | Max confidence | Measured score |
|---|---|---|---|
| **L1** | full text + appendix tables | `CONFIRMED` | 5.0–6.0 (median 6.0) |
| **L2** | full text, no appendices | `PLAUSIBLE` / `UNVERIFIED` | 4.0–5.0 (median 4.5) |
| **L3** | abstract only | `PLAUSIBLE` / `UNVERIFIED` | 3.5–4.0 (median 4.0) |

`CONFIRMED` is structurally unreachable below L1, because the confounding-direction
error persists at L2.

**How often each level is reachable** — also measured, not assumed (F-21, F-24, F-25,
sample of 40 papers of the class): Europe PMC serves full text for 27.5%, and where it
does, appendices arrive in 9 cases out of 11 — always as `.docx`, never PDF. Adding
publisher PDFs where the publisher offers a machine-readable pointer brings automatic
retrieval to roughly 55%. The remainder sits behind Cloudflare or Elsevier's TDM API.

This is why the last cloud batch returned **L3 for all 8 papers** (F-37). That is not a
malfunction — it is the system refusing to call anything confirmed when it only read an
abstract.

---

## Deploy your own copy

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
                       artifactregistry.googleapis.com aiplatform.googleapis.com

gcloud run deploy i-am-truth --source . --region us-central1 --allow-unauthenticated
```

The default Cloud Run service account reaches Vertex AI without extra role setup.

### Background batch over a corpus

The batch is the part the hackathon actually asks for: an agent working unattended over
a heavy dataset, not a chat loop.

```bash
gsutil mb -l us-central1 gs://YOUR_BUCKET

gcloud run jobs deploy i-am-truth-batch --source . --region us-central1 \
       --command python3 --args=-m,truth.batch \
       --set-env-vars TRUTH_BUCKET=YOUR_BUCKET

gcloud run jobs execute i-am-truth-batch --region us-central1
```

Or locally:

```bash
python3 -m truth.batch --query '(GLP-1) AND cancer AND cohort' --limit 20
python3 -m truth.batch --dois 10.1136/jitc-2025-014726,10.3389/fonc.2026.1742210
```

Each paper is written to GCS as its own object, so one failure does not take the run
down with it. The summary is then readable through `GET /runs/{id}`.

---

## How it works

![Architecture](docs/img/architecture.png)

Full diagram with rationale: `docs/07-architecture.md`.

| Layer | Module | Responsibility |
|---|---|---|
| 0 | `truth/pipeline.py` | orchestration, DOI → report |
| 1 | `truth/retrieval.py` | Europe PMC, appendix files, Unpaywall fallback, level assessment |
| 2 | `truth/jats_tables.py`, `truth/docx_tables.py`, `truth/pdf_tables.py` | structural table parsing — colspan/rowspan, compound headers, PDF tables via pdfplumber |
| 3 | `truth/critic.py` | Gemini 3.7 Flash on Vertex, ROBINS-E prompt, seven domains |
| 4 | `truth/verify_numbers.py` | every reported number checked back against the source |
| 5 | `truth/stats_tool.py` | E-value, ARR, NNT, RR with CI — computed, never generated |
| 6 | `app/main.py`, `truth/batch.py` | Cloud Run service and Cloud Run Job |

Two design decisions carry the project:

**The number verifier sits after the model, not inside it.** Asking a model to check
itself is asking the same process for a second opinion. Layer 4 takes each number the
model wrote, finds it in the retrieved source with its surrounding context, and compares
group labels — which is how it catches an inverted direction (`GROUP_MISMATCH`), not
just an invented value.

**Statistics are computed, never generated.** E-value, absolute risk reduction, NNT and
confidence intervals come from `stats_tool.py`, which self-tests on import. The model is
never asked to do arithmetic.

---

## Reproduce the measurements

```bash
python3 eval/harness.py run --models gemini-3.7-flash --prompt v2 \
        --inputs abstract,fulltext_no_appendix,with_appendix
python3 eval/harness.py report

python3 tests/test_parallel_isolation.py
```

The harness scores model output against an expert ground truth
(`eval/ground_truth/mcdonald-2026.yaml`, six points) using an LLM judge, and reports
what the model found *beyond* the reference as well. Method and its limitations:
`eval/README.md`.

**The honest limitation:** the calibration rests on a single reference paper. It is a
mechanism demonstrated in detail, not a population estimate — see `docs/03-open-questions.md`, Q-01.

---

## Hackathon requirements

| # | Requirement | How |
|---|---|---|
| R1 | Gemini 3.5+ | `gemini-3.7-flash` on Vertex AI (Pro line stops at 3.1 — F-01) |
| R2 | Google agent framework | `google-genai` SDK 1.56.0 |
| R3 | Google Cloud service | Cloud Run + Cloud Run Jobs + GCS |
| R4 | Backend running in the cloud | live `.run.app` URL above |
| R5 | Background work over data | Cloud Run Job, 8/8 papers, 232 numbers verified |

---

## Repository map

| Path | Contents |
|---|---|
| `truth/` | the product: pipeline, retrieval, parsers, critic, verifier, stats, batch |
| `app/` | FastAPI service |
| `eval/` | measurement harness, ground truth, inputs, run results |
| `tests/` | parallel isolation; regression on comparison signs vs. markup (F-41) |
| `docs/` | facts, decisions, open questions, architecture *(Russian)* |
| `STATE.md` | design intent, layer architecture, next step |
| `TODO.md` | remaining work to submission, each item with its argument |
| `scripts/preflight.sh` | ten environment checks |

`docs/02-verified-facts.md` holds only what was actually verified, each entry with the
date, the command and the source — including our own mistakes and how they were caught.
