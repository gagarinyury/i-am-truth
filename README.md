# I Am Truth · Я Правда

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
**published 10-page PDF** and a single critic scored **3.5–4.5 / 6 (median 3.5)** across
three runs — lower, because the numbers now have to be *found* before they can be
reasoned about. Adding one narrow sub-agent for baseline comparability took that to
**5.0–6.0 / 6 (median 5.5)**, and the point it was built for went from 0.0 in every run
to 1.0 in every run (F-45).
What survives on the real file is the part that matters most: the appendix imbalance is
caught with the **correct direction** every time, and the verdict matches the human
expert's word for word (`real_association_explained_by_selection`). What does not
survive is the point requiring two distant numbers to be combined — never scored on the
real file, always scored on the prepared one. That gap is the argument for per-domain
sub-agents, and it is written up in `docs/02-verified-facts.md`, F-43.

The interesting part is not the trend. It is the **mechanism**: each level unlocks
exactly those expert points that physically live in it, and no others (`docs/02-verified-facts.md`, F-26).

What the appendix changes is not whether the model sounds right — it is whether anything
it says can be checked. One expert point stays out of reach without it in every run: that
the GLP-1 arm was *sicker* (Charlson 5+: 19.7% vs 10.4%) and yet had *fewer* cancers.
Both numbers sit in an appendix table, ten pages in, and the paradox only exists once
they are put side by side.

---

## Live service

**Open it in a browser — the audit runs from the page:**

```
https://i-am-truth-242136767009.us-central1.run.app
```

![The audit page: a DOI goes in, the evidence level and what was retrieved come out](docs/img/ui-report.jpg)

Paste a DOI, or drop the PDF and its appendix if the paper is not open. The page reports,
in this order: **the evidence level actually reached** and the confidence ceiling that
follows from it, the verdict, the risk numbers recomputed by a function rather than by the
model, then every number checked against the source, then the two narrow agents, and only
last the seven ROBINS-E domains.

That order is the design. A conclusion should not be readable before the thing that backs
it — which is precisely the failure this tool looks for in other people's papers.

![Verification block: 389 numbers confirmed with their label, 2 not found, 0 group inversions](docs/img/ui-verification.jpg)

The same audit is available over HTTP:

```bash
URL=https://i-am-truth-242136767009.us-central1.run.app

curl $URL/health

curl -X POST $URL/analyze \
     -H 'Content-Type: application/json' \
     -d '{"doi": "10.1136/jitc-2025-014726"}'
```

That DOI is an open-access paper: the service pulls the JATS full text **and 35 appendix
tables**, runs a seven-domain ROBINS-E assessment across three agents, and back-checks
every number it reports against the source. Measured on the live service, 28.08:
**52 seconds, level L1, 598 numbers checked — 478 confirmed together with their label,
3 unverified, 0 group inversions** (F-51).

The earlier figure here read "102 numbers verified". It was not wrong, it was measured
with a broken ruler: until F-51 a number's label was taken from the JSON path of the
model's own answer, so almost nothing could match and the verifier was nearly blind.
Re-measured, not rewritten — the same rule this project applies to other people's
numbers.

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
| `POST /analyze` | `{"doi": "..."}` or `{"text": "..."}` → full report; `"engine": "adk"` runs the same audit as a Google ADK graph |
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

`CONFIRMED` is structurally unreachable below L1 — not because the model is wrong below
it, but because it has nothing to cite. Given only the abstract it still names the
direction of bias correctly, yet it justifies that with reasoning that would fit any
observational study ("unmeasured health-seeking behaviour"). Given the appendix it
justifies the same conclusion with a number from the document (7.8% vs 5.9%, Table A2).
A correct guess is not evidence, so only the second one is allowed to be called
confirmed (F-44).

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
| 3 | `truth/critic.py`, `truth/subagents.py` | three parallel passes: ROBINS-E across seven domains, baseline comparability, and temporal structure |
| 4 | `truth/verify_numbers.py` | every reported number checked back against the source |
| 5 | `truth/stats_tool.py` | E-value, ARR, NNT, RR with CI — computed, never generated |
| 6 | `app/main.py`, `truth/batch.py` | Cloud Run service and Cloud Run Job |

Two design decisions carry the project:

**The number verifier sits after the model, not inside it.** Asking a model to check
itself is asking the same process for a second opinion. Layer 4 takes each number the
model wrote, finds it in the retrieved source with its surrounding context, and compares
group labels — which is how it catches an inverted direction (`GROUP_MISMATCH`), not
just an invented value.

**A sub-agent exists only where a measurement demands one.** Both of them were added the
same way: a benchmark showed a reproducible failure, one narrow agent was written for it,
and the benchmark was re-run. The first fixed a point the single critic never reached in
three runs — that the GLP-1 arm was sicker and yet had fewer cancers. The second fixed an
entire class the system was blind to, and collapsed the spread between our own reference
and an external one from 67 points to 4.5. The remaining five ROBINS-E domains have no
sub-agent, because no failure has been measured on them — and adding agents without a
measurement is exactly what this project avoids.

**Statistics are computed, never generated.** E-value, absolute risk reduction, NNT and
confidence intervals come from `stats_tool.py`, which self-tests on import. The model is
never asked to do arithmetic.

**Two orchestrations, and we publish the comparison.** The default path orchestrates in
plain code. `truth/adk_agent.py` expresses the same audit as a Google ADK
`ParallelAgent`, where the two agents additionally get *tools* — the risk calculator and
a source-checker they can call mid-reasoning, instead of learning about a bad number
afterwards from layer 4. Measured over three runs each, both reach a **median of 5.5/6**;
ADK's spread is tighter and its wall-clock is twice as long. So ADK is available via
`engine: "adk"` and is **not** the default: making it the default for the sake of a line
in a submission would sell as an improvement something we measured as a tie (F-46).

---

## Reproduce the measurements

Two benchmarks, and they measure different things.

**`eval/bench.py` — the product, end to end.** DOI (or a file) in, report out, exactly as
a user gets it: retrieval, table parsing, three agents, number verification. Every run is
written to `eval/results/bench-*.json` with the full report *and* the judge's per-point
scoring, so the numbers quoted above can be checked rather than trusted.

```bash
python3 eval/bench.py run              # both references, 3 runs each
python3 eval/bench.py run --engine adk # same, through the ADK graph
python3 eval/bench.py report           # table + medians
```

**`eval/harness.py` — the model on a prepared input.** Feeds a file from `eval/inputs/`
straight to the model and judges the answer. It cannot see retrieval or verification, and
that is the point: it is the tool for comparing prompts and evidence levels.

```bash
python3 eval/harness.py run --models gemini-3.7-flash --prompt v2 \
        --inputs abstract,fulltext_no_appendix,with_appendix
python3 eval/harness.py report
```

```bash
python3 tests/test_parallel_isolation.py
python3 tests/test_normalise_math.py
```

The McDonald PDF is not in the repository — it is under the publisher's copyright. Put
it in `eval/pdf/mcdonald.pdf` (that path is gitignored) and `bench.py` will find it;
the Cheng case needs nothing, it comes from Europe PMC by DOI.

The harness scores model output against an expert ground truth
(`eval/ground_truth/mcdonald-2026.yaml`, six points) using an LLM judge, and reports
what the model found *beyond* the reference as well. Method and its limitations:
`eval/README.md`.

**The honest limitation, now measured instead of guessed.** The calibration used to rest
on a single reference written by this project's own author. A second reference was added
from an outside source — a published letter to the editor (`10.1111/1753-0407.70202`)
criticising a different GLP-1/cancer cohort study, with the authors' reply printed
alongside it. Against our own reference the system scores **92%** (5.5/6). Against the
external one it scores **25%** (1.0/4), stably across three runs.

The gap was not noise, and the per-point breakdown said why: the second paper's defects
are *time-related* — no lag period, latency shorter than follow-up, a duration gradient
pointing the wrong way for causation — while the system was built around confounding and
group comparability. It handled what it had been tuned on and missed a neighbouring class.

So a third pass was added, doing nothing but reconstructing the study's timeline. The
result is the number this project is actually judged by:

| Reference | before | after |
|---|---|---|
| ours (McDonald, 6 points) | 5.5 — **92%** | 5.5 · 5.5 · 5.5 — **92%** |
| external (Cheng, 4 points) | 1.0 — **25%** | 3.5 · 3.5 · 3.5 — **88%** |
| **spread between cases** | **67 pts** | **4 pts** |

The point is not the higher score. It is that the spread collapsed: a system that knew
its own case and failed a neighbouring one now performs the same on both, with no loss
on the case it was designed around. Every run above is stored in `eval/results/` and
reproducible with one command (F-48).

---

## Hackathon requirements

| # | Requirement | How |
|---|---|---|
| R1 | Gemini 3.5+ | `gemini-3.7-flash` on Vertex AI (Pro line stops at 3.1 — F-01) |
| R2 | Google agent framework | `google-genai` SDK 1.56.0; the same audit is also expressed as a **Google ADK** graph (`truth/adk_agent.py`) |
| R3 | Google Cloud service | Cloud Run + Cloud Run Jobs + GCS |
| R4 | Backend running in the cloud | live `.run.app` URL above |
| R5 | Background work over data | Cloud Run Job, 8/8 papers, 517 numbers checked, 3 unverified |

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
