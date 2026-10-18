# Agri Sus: prototype performance and benchmark report

Measured on **18 October 2026** against the live deployment, https://agri-sus-hfgdbncaha-uc.a.run.app. Every number
below was measured, not estimated, unless it is marked *estimate*. To reproduce the API numbers on your own deployment:

```bash
python scripts/benchmark.py https://YOUR-URL            # cheap phases: headers, read endpoints, cached scenarios
python scripts/benchmark.py https://YOUR-URL all        # adds new solves, load test, Gemini (costs money, uses rate limits)
```

## Summary

| Area | Result | Verdict |
|---|---|---|
| Reliability | **0 failed requests** in a 415-request load test and in every sequential test. Every co-pilot question was answered by Gemini on Vertex AI, with no fallback | ✅ |
| Read API | 0.27–0.42 s median from India, of which about 0.27 s is the network round trip | ✅ |
| Preset scenarios (pre-solved at start-up) | 1.8–2.1 s median, end to end | ✅ |
| New optimiser run | **37–48 s** for a linear program with about 128,600 variables (always OPTIMAL) | ⚠️ slow for interactive what-ifs |
| Policy document (new; replaces the one-page brief, which took 9–14 s the first time and 0.27 s cached) | Reopening a saved document: **3 ms** (local container, no model call). A new version: 0.1 s with the template; with Gemini 1–3 minutes (*estimate*, re-measure after the redeploy). Import-displacement check: 18 ms cold, 2 ms cached | ✅ (local container) |
| Gemini co-pilot | first tool call after 1.6–2.7 s; answers in 8–20 s, 53 s when it re-runs the optimiser | ✅ / ⚠️ |
| Co-pilot and document resilience (new) | A slow, stalled, rate-limited or failing Gemini no longer ends in a time-out: answers are background jobs with a resumable stream, stalls are retried on a faster model, steps run in parallel, and the soft deadline answers from what was gathered. **12/12** fault-injection tests pass (section 5.4); a live activity feed shows every step | ✅ (offline; re-measure on Cloud Run) |
| Throughput, cached plans | 3.3 requests/s at 20 parallel users (median 4.8 s) | ⚠️ CPU-bound |
| Lighthouse | Live site: Performance **32** mobile / **49** desktop; Accessibility 95; Best practices 100; SEO 91. **After the front-end fixes** (local container, section 6.5): **73–95** mobile (median 88) / **99** desktop; Accessibility, Best practices and SEO **100** | ⚠️ → ✅ after redeploy (re-measure) |
| Responsive layout | **79/79** checks pass at 9 screen sizes | ✅ |
| Yield model, hold-out | WMAPE 18.2% against 17.9% for naive persistence | ⚠️ directional use only |
| Consistency with 2+ instances | a newly created plan was not found (404) on 4 of 12 requests | ❌ → ✅ fixed in code: session affinity plus a shared run store in Cloud Storage; applies after the redeploy (section 8) |
| Saved policies (new) | Re-opening a saved policy reads its snapshot instead of solving: **0.14 s** (6-year plan, 405 KB gzip) on a local container, 0.002 s once in memory; listing policies or chats 2–5 ms | ✅ (local container; re-measure on Cloud Run) |

## 1. Test set-up

| Item | Value |
|---|---|
| Service | Cloud Run, `us-central1`; 2 vCPU, 4 GiB; CPU always allocated; start-up CPU boost; concurrency 10; min 1, max 5 instances; 1 Uvicorn worker; request timeout 600 s |
| Data | BigQuery (`/api/meta` → `data_backend: BigQuery`); 752 districts |
| Gemini | `gemini-3.8-flash` on Vertex AI, with Grounding with Google Search |
| Client | a laptop in India. Python `requests` with keep-alive and gzip ([`scripts/benchmark.py`](../scripts/benchmark.py)); headless Chrome with Lighthouse 12.8.2 (default mobile and desktop throttling); the Puppeteer layout suite (`tests/e2e/layout.mjs`) |
| Network floor | `/api/health` does no work and takes **270 ms** (median). About 0.27 s of every number below is the India ↔ `us-central1` round trip |

## 2. Read endpoints

Sequential requests over one keep-alive connection, gzip on; 10–30 requests each.

| Endpoint | Median | p90 | Transfer (gzip) | Uncompressed |
|---|---|---|---|---|
| `/api/health` | 270 ms | 280 ms | 0.1 KB | 0.1 KB |
| `/api/meta` | 404 ms | 424 ms | 2.8 KB | 6.7 KB |
| `/api/geo` (district shapes) | **2,151 ms** | 2,189 ms | **983 KB** | **3.56 MB** |
| `/api/districts` | 424 ms | 431 ms | 29 KB | 186 KB |
| `/api/model-card` | 269 ms | 346 ms | 1.0 KB | 1.0 KB |
| `/api/trade` | 415 ms | 427 ms | 2.8 KB | 15 KB |
| `/api/trade/decompose` | 280 ms | 291 ms | 0.8 KB | 0.8 KB |
| `/api/district/{code}` (district card) | 332 ms | 420 ms | 1.9 KB | 8.0 KB |
| `/api/district/{code}/candidates` (next crop) | 290 ms | 294 ms | 2.3 KB | 10.5 KB |
| `/api/run/{id}` (a full plan) | **1,443 ms** | 1,491 ms | **217 KB** | **2.5 MB** |
| `/` (index.html) | 272 ms | 314 ms | 0.9 KB | 0.9 KB |

A second run with `scripts/benchmark.py` reproduced these within about 10%, except `/api/run/{id}` (898 ms median).

**Reading.** Server time is 0–150 ms for most endpoints. The two slow ones are slow because of their size: the
district shapes (3.56 MB of GeoJSON) and a full plan (2.5 MB of JSON, re-serialised on every request).

## 3. The optimiser (Google OR-Tools linear program)

The all-India plan (752 districts, 16 movable crops, 2 seasons, 2027–2030) is a linear program with about
**128,600 variables and 123,700 constraints**. Every run below returned **OPTIMAL**.

### 3.1 New runs

| Request | Wall time | Solver time | Same request again |
|---|---|---|---|
| Fixed budget, ₹5,000 cr | 42.8 s | 40.7 s | 1.2 s |
| Fixed budget, ₹7,500 cr | 38.8 s | 37.4 s | 1.2 s |
| Least cost to cut imports 25% | 47.8 s | 46.5 s | 1.1 s |
| Least cost + Indonesia palm-oil halt + El Niño 2028 | 44.1 s | 42.6 s | 1.3 s |
| Zero groundwater depletion, ₹9,000 cr | 40.1 s | 38.5 s | 1.1 s |
| ✦ Gemini live outlook scenario (first time on an instance) | 84.7 s | 82.3 s | cached |

### 3.2 Preset scenarios

Each instance pre-solves the seven presets in the background when it starts. Clicking a preset then returns the
cached plan in **1.8–2.1 s** (median, gzip on; 2.8 s without gzip).

| Preset | Solve at start-up | Cached request, median |
|---|---|---|
| Baseline | 39.6 s | 2.06 s |
| El Niño 2028 | 86.4 s | 2.05 s |
| Palm-oil price shock | 47.3 s | 1.84 s |
| Zero groundwater depletion | 75.8 s | 1.87 s |
| Indonesia halts palm-oil exports | 44.5 s | 2.03 s |
| Canada halts lentil exports | 44.0 s | 1.92 s |
| Northern heatwave | 72.1 s | 1.99 s |

The pre-solve costs about **6.8 minutes of CPU per new instance**. A request that reaches an instance before it has
finished waits for the solve; this caused the single 64.5 s outlier in the load test below.

## 4. Load test

Parallel users each with their own keep-alive connection. Cloud Run scaled out during the test.

| Load | Requests | Median | p90 | p99 | Max | Throughput | Errors |
|---|---|---|---|---|---|---|---|
| Cached plan × 1 user | 10 | 1.80 s | 1.84 s | 1.88 s | 1.89 s | 0.59/s | 0 |
| Cached plan × 5 users | 25 | 2.39 s | 2.84 s | 49.8 s | 64.5 s | 0.39/s ¹ | 0 |
| Cached plan × 10 users | 50 | 2.89 s | 6.00 s | 7.89 s | 8.33 s | 2.40/s | 0 |
| Cached plan × 20 users | 60 | 4.75 s | 6.78 s | 8.80 s | 9.40 s | 3.33/s | 0 |
| `/api/meta` × 1 user | 20 | 0.40 s | 0.41 s | 0.47 s | 0.48 s | 2.48/s | 0 |
| `/api/meta` × 10 users | 100 | 1.78 s | 1.94 s | 1.99 s | 2.03 s | 5.70/s | 0 |
| `/api/meta` × 25 users | 150 | 2.05 s | 2.36 s | 8.78 s | 14.97 s | 7.96/s | 0 |

¹ This run triggered the scale-out. One request waited 64.5 s on a new instance that was still pre-solving.

**Reading.** Nothing failed, but each instance is effectively single-threaded for CPU-bound work (one Uvicorn
worker and the Python GIL). Each request re-serialises the 2.5 MB plan, which costs about 1 s of CPU, and
`/api/meta` recomputes about 130 ms of work. Latency therefore grows with the number of parallel users on an
instance.

## 5. Gemini on Vertex AI

### 5.1 Policy briefs (`/api/brief`, since replaced by the policy document)

| Brief | Time | Length |
|---|---|---|
| Punjab, Hindi (first time) | 9.2 s | 2,793 characters |
| Punjab, English (first time) | 10.4 s | 3,731 characters |
| Maharashtra, Marathi (first time) | 13.7 s | 3,606 characters |
| Maharashtra, Marathi (again) | **0.27 s** (cached) | 3,606 characters |

**Policy documents (`/api/documents`, new).** The one-page brief has been replaced by a policy document of about
6,500 words (sections A–H), written once by a document agent and saved. Measured on the local container **without
Gemini** (the deterministic template writes every section from the same facts), with
`python scripts/benchmark.py http://localhost:8081 document`:

| Step | Time | Size |
|---|---|---|
| First write, India: fact sheet, import check, template, save | 0.1 s | 6,578 words |
| Reopen the saved version (`GET`, no model call; median of 5) | **3 ms** | 70.6 KB, 19.3 KB gzipped |
| Regenerate with instructions (research reused) → version 2 | under 0.1 s | 6,612 words |
| Import-displacement check (`/api/import-check`) | 18 ms cold, 2 ms cached | 19.1 KB, 5.6 KB gzipped |

With Gemini, a new version adds two grounded searches (run in parallel, then reused for 14 days) and one writer call
(up to 24,000 output tokens, streamed; a stall is retried on the next model, 8 minutes at most in total, see
section 5.4). The window tells users to expect 1–3 minutes: an *estimate*,
not yet measured on Cloud Run. Reopening stays a storage read. To measure it after the redeploy:
`python scripts/benchmark.py https://YOUR-URL document` (it writes three versions on a temporary saved policy and
then purges the policy). Each IP may write 6 new versions per 10 minutes, so run it apart from the test suites.

### 5.2 The co-pilot agent (`/api/agent/chat`, streamed)

The five questions of the demo video. In every case the stream started within **0.26–0.36 s**, the model was
`gemini-3.8-flash` on Vertex AI, and no fallback or error occurred.

| Question | Tools Gemini called | First tool | Answer complete |
|---|---|---|---|
| National scorecard | `get_national_scorecard` | 2.7 s | **7.6 s** |
| Latest IMD/ENSO outlook and import duty, then re-plan | `web_grounded_search`, `gemini_outlook_forecast` (10 citations) | 2.1 s | **20.2 s** |
| Least budget for −25% imports under an Indonesia halt + El Niño | `run_simulation` (least-cost mode) | 1.8 s | **52.9 s** (the solve dominates) |
| 10 districts with the worst groundwater stage, from BigQuery | `get_schema`, `query_bigquery` × 2 | 1.6 s | **13.6 s** |
| Hindi: what to grow instead of paddy in Punjab | `get_district_allocation_audit`, `explain_district` | 2.4 s | **9.1 s** |

The answers matched the dashboard: sustainability index 76.0 against 69.7, pulses self-sufficiency 78.2% → 91.9%,
and ₹16,288 cr in 2030 for the least-cost question (target missed by $0.07 bn, farm income 100.3% of today).

**Quality finding.** The grounded answer cited 10 sources, but it reported the 2024 monsoon and the September 2024
duty change as the *latest* news. The prompts do not pass today's date to Gemini (recommendation 2).

### 5.3 Live outlook (`/api/forecast`)

0.27 s when cached (the outlook is cached for 6 hours). The scenario built from it needs a new solve the first time
(section 3.1).

### 5.4 Resilience when Gemini is slow or failing (new, offline)

**Why it was needed.** Some co-pilot questions took long enough that the answer came from the offline analyst
instead of Gemini. The causes:
- each Gemini call was non-streamed with a 90 s time-out, and any time-out sent the whole answer to the fallback;
- tool steps ran one after another, so a long question added up past the 240 s deadline;
- the work was tied to the browser's connection, so a dropped connection lost the answer.

Raising the time-out would only have made people wait longer for the same failure. The fix is a pattern that
applies to every AI request (HOW_IT_WORKS §8):
- background jobs with a resumable event stream;
- streamed calls judged by stalls, not by total time;
- retries on the next model with less thinking;
- parallel steps;
- a soft deadline that answers from what was gathered;
- results saved whatever the browser does.

**Measured** with `backend/tests/test_agent_resilience.py`: a real server with a scriptable fake Gemini that injects
each fault. The stall limits were shortened to 1.5 s so the suite runs in about 40 s; production uses 60 s to the
first output and 45 s of silence.

| Fault injected | What the user sees | Time to a complete answer |
|---|---|---|
| The primary model sends nothing (slow first token) | *"The model was slow to respond - retrying with a faster setting"*, then the answer from the next model, which also writes the rest of the answer | 2.2 s |
| The stream stalls mid-answer | The partial text is withdrawn and replaced by the retry; the saved chat holds only the final answer | 2.0 s |
| The soft deadline passes while tools are still running | *"Writing the answer from what I have so far"*: one last call with tools switched off | 2.5 s |
| Gemini fails after two steps | *"Gemini is not responding - summarising what I found"*: the answer is composed from those steps | 3.3–3.5 s |
| Gemini fails from the start (503 on all three models) | The offline analyst answers, labelled `local fallback (Gemini unavailable)` | 3.8–12.6 s |
| **■ Stop** | The job is cancelled on the server; the partial answer is saved with *Stopped.* | 2.3–2.6 s |
| The connection drops mid-answer | The browser resumes from the last event it saw: no event repeated or lost, and the job never notices | 2.5 s |
| A second request for a document already being written | It joins the running job: same progress, same version | 1.5 s |
| The writer stalls after most of a document | The draft is kept; only the missing sections come from the template (one model call, not two) | 4.1 s |

**What changes for users on Cloud Run** (*estimate*, to re-measure after the redeploy):
- A stuck Gemini call costs at most a minute before it is retried on the next model; before, it cost 90 s and then
  the answer fell back to the offline analyst.
- Independent steps (a district profile and a web search, say) now overlap instead of queuing.
- No answer is lost when a phone sleeps or a proxy drops a long stream, and Cloud Run's 600 s request limit no longer
  caps an answer.

## 6. Front end

### 6.1 Lighthouse 12.8.2

| | Mobile (slow 4G, 4× CPU slowdown) | Desktop |
|---|---|---|
| Performance | **32** | **49** |
| Accessibility | 95 | 95 |
| Best practices | 100 | 100 |
| SEO | 91 | 91 |
| First Contentful Paint | 5.2 s | 1.8 s |
| Largest Contentful Paint | 5.4 s | 1.9 s |
| Speed Index | 6.7 s | 3.2 s |
| Total Blocking Time | 900 ms | 740 ms |
| Cumulative Layout Shift | 0.233 | 0.097 |
| Time to Interactive | 6.5 s | 2.8 s |
| Page weight | 3.47 MB in 24 requests | 3.47 MB |

The mobile run hit a DevTools protocol time-out near the end. Its scores were still computed, but treat them as
indicative. These are the numbers *before* the front-end fixes; see section 6.5 for the re-test.

### 6.2 What the page downloads

| Resource | Transfer |
|---|---|
| `/api/geo`, district shapes | 983 KB (3.56 MB uncompressed) |
| Main JavaScript bundle | 722 KB (2.6 MB uncompressed; about 366 KB unused at load) |
| CARTO basemap vector tiles | about 1.2 MB |
| `/api/simulate`, the default plan | 217 KB (2.5 MB uncompressed) |
| CSS | 15 KB |

### 6.3 Diagnostics

- **Largest Contentful Paint** is the page title. Nothing renders until the JavaScript bundle has run, which adds a
  4.4 s render delay on mobile.
- **Layout shift**: the *Rice exports retained* slider row moves when its "not limiting" tag appears after the plan
  loads.
- **Caching**: the six fingerprinted files under `/assets/` are served without a `Cache-Control` header, so browsers
  either re-check them with the server or guess how long to keep them. *Fixed in code (recommendation 3).*
- **SEO**: `/robots.txt` returns the app's HTML page instead of a robots file. *Fixed in code (recommendation 9).*
- **Accessibility**: one heading-order issue (an `h3`, *Crisis scenario*, with no `h2` before it) and three
  instrument checkboxes smaller than the 24 px touch-target minimum.

### 6.4 Responsive layout

`tests/e2e/layout.mjs` against the live site: **79 of 79 checks pass** at 1280×650, 1280×800, 1366×768, 1440×900,
1536×864, 1920×1080, 1100×800, 900×1000 and 390×844. The checks cover the KPI cards fitting, the map and scorecard
staying on screen, India never being clipped, both on load and after a new run, and no page errors.

### 6.5 Re-test after the front-end fixes

Same Lighthouse version and throttling, run against the production container image on a laptop (Docker,
`localhost`), not on Cloud Run. The container serves the same files Cloud Run will serve, but there is no
India ↔ `us-central1` round trip, so these numbers are better than the live site will be. **Re-run Lighthouse on
the Cloud Run URL after the redeploy** before quoting them.

| | Mobile (slow 4G, 4× CPU slowdown) | Desktop |
|---|---|---|
| Performance | **73–95** over four runs (median 88) | **99** |
| Accessibility | **100** | **100** |
| Best practices | 100 | 100 |
| SEO | **100** | **100** |
| First Contentful Paint | 0.8–2.1 s | 0.5 s |
| Largest Contentful Paint | 0.8–3.8 s | 0.8 s |
| Total Blocking Time | 260–540 ms | 10 ms |
| Cumulative Layout Shift | **0.016** | 0.058 |

Measured on the final build. Mobile scores on a laptop swing by about 20 points between runs, because the simulated
slow phone depends on how busy the laptop is. Quote the range, or the Cloud Run median after the redeploy.

What changed:
- A static header and loading shell in `index.html`, so the first paint no longer waits for the JavaScript bundle.
- The map libraries (deck.gl, MapLibre) load lazily in their own chunk (503 KB gzip); the main bundle is 253 KB
  gzip, down from 722 KB.
- Space is reserved for the scenario chips, the scenario description and the lever tags, so nothing jumps when the
  plan loads (mobile layout shift 0.233 → 0.016).
- Heading order fixed, instrument check-boxes on 24 px rows, `robots.txt` served, `Cache-Control: immutable` on
  `/assets/*`.
- The plan JSON is gzipped once per run and reused; `/api/meta` is computed once.
- The map fits India to its real size before the first frame. It used to start from a default zoom and fly in over
  0.7 s; it now flies only when you pick a state or press ⌂.

On mobile the largest paint is still the React header, which replaces the static shell when the app starts.

**Restart test (same container).** After `docker restart`, the seven preset scenarios loaded from the run store in
0.1 s in total instead of being solved again. Each was then served in 3–5 ms (about 235 KB gzipped), and a saved
policy reopened in 0.14 s. On Cloud Run the store is Cloud Storage, so a new instance benefits the same way once any
instance has solved the presets.

## 7. Yield model accuracy

Hold-out years 2020-21 to 2024-25, trained on 76,827 rows (from `/api/model-card` and `/api/meta`).

| Model | WMAPE, all hold-out years | WMAPE, climate-shock years | Role |
|---|---|---|---|
| Persistence (last year's yield) | **17.92%** | **17.61%** | benchmark |
| Agronomic prior | 18.23% | 18.48% | **champion (in use)** |
| BigQuery ML linear model (prior twin) | 18.88% | n/a | challenger |
| BigQuery ML boosted tree | 20.17% | n/a | challenger |

**Reading.** No model beats persistence at predicting next year's yield. The agronomic prior is used because it
responds to rainfall and heat shocks, which persistence cannot do, and it stays within 0.3 points of persistence.
Neither BigQuery ML challenger beat it, so the automatic champion/challenger selector kept it. As the
[model card](MODEL_CARD.md) says, the model is meant for directional climate shocks in a multi-year plan, not for
point forecasts.

## 8. Consistency when Cloud Run runs several instances

During the load test Cloud Run ran two or more instances. A plan created by one request was then fetched 12 times
over fresh connections: **4 of the 12 requests returned 404**. Plans, briefs and rate-limit counters live in each
instance's memory. With more than one instance:

- after a co-pilot what-if, the dashboard may not update to the new plan;
- the district card and the brief for a custom plan may silently show the default plan instead.

**Fixed in code.** Every solved plan is now written to a shared run store in Cloud Storage (`runs/<model
version>/<run id>`, free tier, deleted after 30 days). An instance that does not have a plan in memory loads it from
there instead of returning 404, and a new instance loads the pre-solved scenarios instead of solving them again.
Saved policies live in Firestore and Cloud Storage, so they are the same on every instance and survive restarts.
`deploy/deploy.sh` and `cloudbuild.yaml` also keep session affinity on, so a visitor normally stays on the instance
that already holds their plans. To reproduce after the redeploy: `python scripts/benchmark.py URL consistency`
(meaningful only while two or more instances are up).

## 9. Cost of this configuration

- One always-on instance (2 vCPU, 4 GiB, instance-based billing in `us-central1`): about **$3.80 a day** at list price,
  about **$109 a month** after the Cloud Run free tier. After judging, redeploy with `MIN_INSTANCES=0`.
- Each extra instance spends about 7 minutes of CPU pre-solving the presets (*estimate*: about $0.02).
- Gemini: a policy document costs one writer call and two grounded searches per new version; reopening it costs
  nothing. The outlook is cached for 6 hours. Each co-pilot question costs one Gemini call per reasoning step.

## 10. Recommendations, in priority order

| # | Change | Expected effect | Effort | Status |
|---|---|---|---|---|
| 1 | Session affinity now (`gcloud run services update … --session-affinity`); a shared run store later | no more 404s or silent fall-backs to the default plan | 5 min / 1 day | ✅ affinity in `deploy.sh` and `cloudbuild.yaml`; shared run store in Cloud Storage in code |
| 2 | Pass today's date to the grounding, outlook and co-pilot prompts | "latest" answers cite current sources | 15 min | ✅ in code |
| 3 | `Cache-Control: public, max-age=31536000, immutable` on `/assets/*` | repeat visits reuse the 750 KB of app files without asking the server | 10 min | ✅ in code; `index.html` is `no-cache` |
| 4 | Cache the serialised, gzipped plan per run, and compute `/api/meta` once | about 1 s less per cached plan; several times the throughput (*estimate*) | 2 h | ✅ in code |
| 5 | A static header and loading screen in `index.html`; load the map libraries lazily; reserve space for the slider tags | much faster first paint on mobile; layout shift below 0.1 | 3 h | ✅ in code (section 6.5: mobile CLS 0.016) |
| 6 | Simplify the district shapes (TopoJSON or quantised coordinates) | `/api/geo` several times smaller (*estimate*) | 2 h | open |
| 7 | Faster interactive solves: warm-start from the cached plan, try other OR-Tools LP back-ends, show progress with an ETA | a what-if in seconds instead of about 45 s | 1–2 days | ◐ progress bar with an ETA from this instance's recent solves (`/api/solve-estimate`) and solves shared across instances in code; warm start open |
| 8 | Separate rate-limit budgets for briefs/outlook and the co-pilot | heavy co-pilot use no longer blocks briefs | 15 min | ✅ in code (separate co-pilot, document, outlook and solve budgets) |
| 9 | Add `robots.txt`; fix the heading order; enlarge the checkboxes | SEO 100; accessibility close to 100 | 30 min | ✅ in code (SEO and accessibility 100, section 6.5) |
| 10 | Serve Indian users from `asia-south1` (Mumbai) | about 0.2 s less per request (*estimate*) | 1 h | open (`REGION=asia-south1`) |
| 11 | A yield model that beats persistence (for example persistence plus a climate-anomaly term) | a stronger accuracy claim | 1–2 days | open |
| 12 | Co-pilot and document writer as resumable background jobs: streamed Gemini with stall detection and retries on a faster model, parallel steps, a soft deadline, a live activity feed and Stop | no answer lost to a time-out or a dropped connection; users see progress throughout | 1–2 days | ✅ in code (section 5.4) |

"In code" means the change is in the project files and passes the local test suites (smoke 48/48, browser 87/87,
layout at 9 sizes, security 15/15, resilience 12/12), but the live site gets it only after the next deploy
(`SKIP_BIGQUERY=1 bash deploy/deploy.sh`, which also creates the Firestore database and the storage bucket). Apart
from section 6.5 and the saved-policy row in the summary, the measurements above are from before these changes.
