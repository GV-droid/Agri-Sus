# Agri Sus: an AI co-pilot for climate-resilient crop policy in India

**Google Cloud AI Builder Cup 2026 entry, theme: Sustainability & Social Impact**

India spent **$23 bn importing pulses and edible oils in FY 2024-25**, heading for $27 bn by 2030 if nothing changes. Meanwhile, the Punjab-Haryana rice belt is drawing groundwater at **150–300% of recharge**. Agri Sus is a planning tool for state and central agriculture planners. It answers one question: *which crop shifts, in which districts, paid for by which policy instruments, can cut imports and groundwater use without hurting farmer income or cereal security, and how do those shifts hold up under El Niño, heatwave, price and supplier shocks?*

It covers **all of India: 752 districts across 35 states and UTs**, over a plan of **2 to 8 years from 2027** (default 2027–2030). Plans can be **saved as named policies** and reopened instantly, and the Gemini co-pilot can create, open and discuss them.

**Deploy it yourself in about 30 minutes, with no coding:** [docs/DEPLOY.md](docs/DEPLOY.md), one command in Google Cloud Shell.

**How it works, click by click, with architecture diagrams:** [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md). **Demo video script (3–4 minutes):** [docs/DEMO_VIDEO_SCRIPT.md](docs/DEMO_VIDEO_SCRIPT.md). **Measured performance of the live site:** [docs/PERFORMANCE_REPORT.md](docs/PERFORMANCE_REPORT.md).

## Built on Google Cloud

| Google product | What Agri Sus uses it for | Where |
|---|---|---|
| **Vertex AI: Gemini** | The live agent (function calling, 22 tools, including creating and reading saved policies and their policy documents), the policy document agent (sections A–H, 11 languages, versioned), and the climate/price outlook scenario. Model fallback chain: `gemini-3.8-flash` → `gemini-3.5-flash` → `gemini-2.5-flash` | `backend/app/agent.py`, `gemini.py` |
| **Grounding with Google Search** | Cited answers on current monsoon/ENSO outlooks, prices and policy; the outlook is turned into structured shocks that feed the optimizer; the existing central and state schemes a new policy should converge with, and today's import duties, for the policy document | `gemini.py`, `documents.py` |
| **Cloud Run** | Hosts the API and the web app in one container, with HTTPS, autoscaling and a least-privilege service account | `Dockerfile`, `deploy/deploy.sh` |
| **Firestore** (free tier) | The shared policy library and co-pilot chat sessions: names, notes, levers, headline numbers, messages. Survives restarts and is shared by every Cloud Run instance | `backend/app/store.py`, `policies.py`, `chats.py` |
| **Cloud Storage** (free tier) | Each saved policy's full result snapshot (national KPIs, 752 district plans, budget path, trade, stress tests), so opening a policy re-renders it without re-running the optimizer; its policy documents (written once, reopened as saved); also a shared cache of solved runs (deleted after 30 days) | `backend/app/store.py`, `service.py` |
| **Cloud Build + Artifact Registry** | Builds the container in the cloud and stores it; `cloudbuild.yaml` supports push-to-deploy triggers | `cloudbuild.yaml` |
| **BigQuery** | The feature store (yield and climate history range-partitioned by `agri_year`; every table clustered on its filter columns, such as district, crop or state), plus time-partitioned run logs, decision traces, the policy budget ledger and every policy-document version (one row per section). The agent queries it in natural language (NL→SQL, read-only) | `deploy/load_bigquery.py`, `bigquery/01_schema.sql`, `03_analytics.sql` |
| **BigQuery ML** | Two yield models on 105k district-crop-years: a `LINEAR_REG` twin of the agronomic prior (champion) and a `BOOSTED_TREE_REGRESSOR` challenger. The app promotes the challenger only if it wins on 2020–24 hold-out WMAPE | `bigquery/02_bqml_yield_model.sql`, `backend/app/data.py` |
| **Vertex AI Model Registry** | Both BigQuery ML models are registered automatically (`MODEL_REGISTRY = 'VERTEX_AI'`) | `02_bqml_yield_model.sql` |
| **Google Earth Engine** *(optional)* | Recomputes district climate from CHIRPS, ERA5-Land and NASA NEX-GDDP-CMIP6 (SSP2-4.5), averaged over every district polygon instead of sampled at its centroid, and writes it to BigQuery for the app and BQML to use | `scripts/gee_climate.py`, `bigquery/04_climate_from_earth_engine.sql` |

Without a Google Cloud project, the same app runs fully offline: CSV snapshot, DuckDB for SQL, and a rule-based co-pilot.

## What it does

| Capability | How |
|---|---|
| **Live AI agent** (Gemini on Vertex AI) | A multilingual function-calling agent with **22 tools**: run the optimizer, find the least budget that meets a target, translate supplier cuts into hectares, list who India buys from, read the fiscal ledger, audit a district's next-crop decision, forecast the sustainability index with stress tests, explain a district, predict yield, query BigQuery in natural language (NL→SQL), Google-Search-grounded research with citations, a Gemini climate/price outlook, **list, read and create saved policies**, and **read, write or regenerate a policy's document, check whether more production will really cut imports, and find the existing schemes to converge with**. While it works, a **live activity feed** shows what it is doing in plain words — Gemini's thinking headline, *"Looking up the water, soil and crop profile of Bathinda"*, *"Searching the web with Google: …"*, *"Running the optimizer on this what-if"* — with timers and the usual duration of slow steps, then folds into **"Worked for 24 s · 3 steps"** above the answer (never tool names or JSON). Long answers **never time out**: each answer is a background job with a resumable event stream, Gemini is streamed and judged by stalls rather than total time (a stalled call is retried on a faster model), independent steps run in parallel, and at the soft deadline the agent answers from what it has. **■ Stop** cancels on the server; a reload picks the answer up where it was. It stays on topic: app questions → its tools; related agriculture, water, climate, trade and scheme questions → Google Search, labelled as web sources; anything else is politely declined. A what-if asked in chat ("what if the budget is ₹20,000 cr?") re-runs the plan and moves the dashboard and sliders with it. Without credentials, a rule-based offline co-pilot answers the same core questions. |
| **Saved policies** | **💾 Save as policy** stores the plan on screen (its levers and every computed result) in a shared library. **📁 Policies** lists them with their headline numbers; **Open** re-renders the map, KPIs, district cards, budget, trade and stress tests from the saved snapshot in about a second, without re-running the optimizer, and puts the levers back on the sliders. Rename, delete (restorable from the Deleted tab) and delete forever. Ask the co-pilot about any saved policy ("✦ Ask"), or ask it to *create* one ("create a 6-year policy that cuts imports 40% at least cost") — it sets every lever, runs the optimizer, saves the policy and offers **📂 Open policy**. After any what-if it offers to save the plan as a policy. |
| **Chat sessions** | Every co-pilot conversation is saved and listed under **💬 Chats**: reopen and continue one, start a new chat, or delete one. There is no login: policies and chats are shared by everyone using the deployment, and the UI says so. |
| **Gemini-grounded forecasts** | `gemini_outlook_forecast` uses **Google Search grounding** to read the current ENSO and IMD outlook and global oilseed and pulse prices. It turns them into structured shocks for every plan year (rain %, Rabi heat, import price multipliers) that feed straight into the simulation ("✦ Gemini live outlook" scenario). |
| **Policy-instrument mix** | One annual budget is split by the optimizer across three instruments: **income-parity transfers** (DBT), **seed & FPO kits** (₹4,000/ha in kind, +10% pulse/oilseed yield), and **assured price / price-deficiency payments** (top-up to MSP, which also removes half of the switching-risk premium). Any instrument can be switched off. Two budget modes: **fixed budget** (best plan for the money) or **least cost to hit the import target** (the LP minimises the budget). |
| **Fiscal ledger and MROPI** | Every rupee of outlay is set against what it returns to the exchequer and the economy: forex on imports avoided, farm-power subsidy on groundwater no longer pumped, and urea subsidy saved through legume nitrogen. **MROPI** (public return per rupee) sorts districts into Tier 1 (≥10×), Tier 2 (1–10×), Tier 3 (<1×: funded for farm income and aquifer security), self-financing, and no outlay. MROPI is also a map layer. |
| **Trade shocks** | "What if Indonesia and Malaysia cut palm oil and Canada stops lentils?" Pick **several products and several supplier countries** at once (up to 8 product × country cuts per plan). Country-wise import volumes come from the **India Data Portal's Import Trade Statistics** (DGCI&S, FY 2024-25). Each cut becomes a tonnage the plan must replace at home, phased over the plan, with the substitute crops and districts, any shortfall, and the remaining suppliers. The Trade tab shows supplier concentration (HHI) for every pulse and edible oil. |
| **Next-crop prediction** | For any district and plan year, the district card ranks every candidate crop for Kharif and Rabi: decision (expand / keep / reduce), area today → plan, climate-adjusted yield, irrigation need, net margin, and flags (groundwater cap, heat, rotation). It names the recommended next crop after the preceding season's crop and the closest rejected alternative, with the reason. |
| **Constrained optimisation** | An OR-Tools LP (COIN-OR CLP engine) with about 129k variables and 124k constraints, solved in about 10 s. Every district moves along a straight **glide path** to its final-year crop mix (¼ of the way in 2027, ½ in 2028, … for a 4-year plan), so the plan never pays farmers to switch a crop and then switch back. The **plan horizon** is a lever (2–8 years); targets, caps and the glide path all end in the final year. Constraints apply in every year: land (at most 3% left fallow), year-on-year crop-shift limits, CWAR water blocks, a groundwater cap ramp, cereal / rice-export / wheat floors, the national pulse and oil balance with an import-saving target and supplier cuts, a national seed-and-market growth cap per crop, and a district income floor funded by the instrument mix. |
| **Calibrated economics** | PMP-style calibration makes today's crop mix each district's own optimum, so the LP cannot "discover" profit farmers are ignoring: every move must be paid for by a public benefit (water, imports, soil). Expansion carries an adoption-risk premium, and public money costs society 1.25× (marginal cost of public funds). Staples can shrink at most 15% a year (other crops 5%). |
| **Explainability** | For each district: crop moves with reasons, rejected alternatives (with their reduced costs), binding national constraints (with shadow prices), a rotation timeline, outlay by instrument and MROPI. Every lever is tagged **limiting**, **not limiting** or **not reached** from the LP's duals, so a planner can see which knob actually limits the plan. After each re-run, a "Last change" strip says what moved, or why nothing did. |
| **Sustainability forecast** | **ASSI**, the Agri Sustainability & Self-reliance Index, scores 0–100 on an absolute scale, so "do nothing" and the plan are measured with the same yardstick. Its four pillars are self-reliance 30%, water 30%, soil 20% and crop diversity 20%. Every district also gets a 0–100 **sustainability score** (water 40%, soil/legumes 30%, diversity 30%). Farmer income is a hard guardrail rather than a scored pillar. The year-by-year forecast has a **climate stress-test band**: the fixed plan is re-scored under unplanned El Niño, heatwave and back-to-back weak-monsoon shocks. |
| **Scenarios and levers** | Seven scenarios (baseline, El Niño 2028, palm-oil price shock, zero groundwater depletion, Indonesia palm-oil halt, Canada lentil cut, Rabi heatwave 2029) plus "✦ Gemini live outlook". Shock scenarios jump the view to the shock year. Levers: plan horizon (2–8 years), budget mode, the three instruments, seven more sliders (annual policy budget, land shift, cereal floor, rice exports, import target, MSP realisation, seed & market scale-up), the groundwater guardrail and the multi-country trade-shock picker. In least-cost mode the Budget tab shows the **total cost over the whole plan** as well as the year-by-year path. |
| **State leaderboard** | States ranked by sustainability gain, water saved, paddy→pulses shift and outlay; click one to fly to it. |
| **Policy document agent** | **📄 Policy document** (header, a saved policy, or the co-pilot) writes a structured policy proposal from the optimizer's results, for India or any state, in 11 languages: **A** identification and context, **B** objectives and scope, **C** sustainability dimensions, **D** instruments and implementation, **E** funding, **F** monitoring and evaluation, **G** *will higher domestic production cut imports?* (each reason it might not — cheaper imported substitutes, output that does not match the import basket, domestic cost above landed price, supplier deals, price falls — answered by a numbered clause in policy wording) and **H** convergence with existing central and state schemes (Google Search grounded). It is written **once and saved**; reopening shows the saved version, and **↻ Regenerate** writes the next version with your instructions (history kept). The co-pilot can read any section, answer questions about it and regenerate it from the conversation. Print / PDF and Markdown download. The Trade tab shows the import-displacement verdicts too. |
| **Multilingual voice co-pilot** | Ask in English, हिन्दी, தமிழ், తెలుగు, ಕನ್ನಡ, मराठी, বাংলা, ਪੰਜਾਬੀ, ગુજરાતી, മലയാളം or ଓଡ଼ିଆ, by typing or by 🎤 voice (Web Speech API). Answers can be read aloud (🔊). |
| **Map UI** | React + deck.gl choropleth of every district, with nine layers (including MROPI). It has a ▶ year-by-year animation, a "Do nothing vs plan" toggle, zoom-to-state, plain-language legends and ⓘ tooltips, a first-visit guided tour (add `?notour` to skip it), and a district card with a sustainability radar, decision trace and next-crop ranking. The layout is responsive from 900-px tablets and 1280×650 laptops up to 1920×1080, with a scrolling layout on phones. |

## Results

Baseline scenario, 2030, default levers: a fixed **₹6,000 cr a year** budget across all three instruments, at most 12% of a district's land shifted a year, and a soft groundwater cap moving over-exploited and critical districts towards 80% of recharge.

| | Do nothing | Agri Sus plan |
|---|---|---|
| ASSI (0–100) | 69.7 | **76.0** (71.7 → 73.1 → 74.6 → 76.0 over 2027–30) |
| Pulses + edible-oil import bill | $27.1 bn | **$21.5 bn**: $5.6 bn saved, −20.6% |
| Pulses self-sufficiency | 78% | **92%** |
| Edible-oil self-sufficiency | 37% | **44%** |
| Groundwater irrigation draft | 170.4 BCM | **161.7 BCM**: 8.7 BCM a year saved |
| Over-exploited districts | 75 | **66** |
| Paddy area | 46.7 Mha | **42.8 Mha** (7.1 Mha of cropland changes crop by 2030) |
| Urea | – | **379 kt** a year saved through legume nitrogen credit |
| Farmer income | 100% | **100.1%** of today; no district falls below today's income |

**Where the money goes and what it returns (2030).**

- The ₹6,000 cr splits into ₹3,299 cr of income-parity transfers, ₹1,789 cr of seed & FPO kits (4.5 Mha) and ₹911 cr of price support (0.9 Mha). It ramps ₹1,521 → 3,028 → 4,520 → 6,000 cr along the glide path.
- **Fiscal ledger:**
  - forex saved: ₹46,928 cr;
  - power subsidy saved: ₹1,306 cr;
  - urea subsidy saved: ₹1,214 cr.
- That is a **public return of 8.2× per rupee**. The direct budget payback (power + urea) is 0.42×.
- **MROPI tiers:**
  - Tier 1 (≥10×): 451 districts, ₹1,408 cr returning ₹40,797 cr;
  - Tier 2: 54 districts;
  - Tier 3: 29 districts, half of them in the Punjab-Haryana paddy belt, funded for aquifers and farm income;
  - self-financing: 88 districts;
  - no outlay: 130 districts.

**Other results.**

- **Guardrails hold.** Cereal self-sufficiency stays at 104% (floor 101.5%), and a 15.9 Mt rice surplus remains for export.
- **Stress tests.** The fixed plan is re-scored under unplanned shocks. Its worst-year ASSI is 71.6 (El Niño 2028), 71.7 (Rabi heatwave 2029) and 71.7 (back-to-back weak monsoons). In every year it still beats doing nothing under the same shock.
- **Least cost to hit the 25% import cut.** In least-cost mode the import target becomes a hard constraint. It is met for ₹2,537 → 5,131 → 7,851 → 10,727 cr a year — **₹26,246 cr in total over 2027–2030**, returning ₹1.47 lakh cr to the public — with the money spread almost evenly across the three instruments; ASSI 2030 reaches 76.9.
  - In fixed-budget mode, the optimizer balances water, soil and imports rather than chasing the import target alone. So at ₹6,000 cr the target is missed by $1.2 bn, and even at ₹12,000 cr it is $0.85 bn short (water saved 10.3 BCM).
- **Indonesia halts palm-oil exports.** Indonesia supplies 49% of India's palm oil: 3,882 kt, worth $4.1 bn. The plan re-optimises to replace it at home and covers about 2.2 Mt by 2030 with groundnut, mustard, soybean, sesame and sunflower. The rest must come from Malaysia (31% share), Thailand or Singapore.
- **Cost of the switch.**
  - On paper, MSP-based farm margins would rise by ₹12,926 cr.
  - But the model is calibrated to farmers' revealed preferences: they grow today's mix for reasons margins don't capture, such as assured procurement, risk and labour. So it treats the switch as a ₹10,690 cr welfare loss, and the instruments deliver ₹11,587 cr of benefit to farmers to cover it, district by district.
  - In Punjab this is about ₹60k per hectare switched (₹50k of it as transfers). That is roughly 3.5× the ₹7,000 an acre (₹17.3k/ha) paid under Haryana's *Mera Pani Meri Virasat* scheme, which fits with how hard voluntary schemes have found it to move paddy.

> These are scenario outputs from a calibrated optimiser, not a forecast. Read them as what a funded, well-targeted crop-diversification programme could achieve within the stated guardrails.

## Architecture

```
 Browser (React, deck.gl, MapLibre)
      │  REST + SSE
      ▼
 Cloud Run: FastAPI (one container, also serves the web app)  ◄── Cloud Build ── Artifact Registry
  ├─ agent.py       Gemini agent: streamed function-calling loop (22 tools, parallel steps, retries on the
  │                 model chain, soft deadline), plain-language activity feed, topic guard + offline router
  ├─ jobs.py        background jobs: event log with sequence numbers, resumable SSE, limits, Stop
  ├─ documents.py   policy document agent: plan facts, import-displacement check, grounded scheme and duty
  │                 research, Gemini writer (template fallback), versions, readers for the co-pilot
  ├─ gemini.py      streaming with stall detection and retries, Google Search grounding, outlook → JSON shocks
  ├─ optimizer.py   OR-Tools LP: crops × instruments × supplier cuts, KPIs, ASSI, fiscal ledger, MROPI,
  │                 stress tests, decision traces, next-crop candidates
  ├─ trade.py       bilateral import dependencies (India Data Portal), HHI, supplier-cut decomposition
  ├─ data.py        feature store loader (BigQuery or CSV), BigQuery ML champion/challenger yield engine
  ├─ service.py     run cache, NL→SQL (read-only, allow-listed), BigQuery logging (runs, ledger, traces)
  ├─ policies.py    saved policies: snapshot a run, reopen it without re-solving, rename / delete / restore
  ├─ chats.py       co-pilot chat sessions (shared, no login)
  └─ store.py       Firestore + Cloud Storage, with a local-disk fallback for development
      │
      ├── Firestore: policy library + chat sessions      Cloud Storage: policy snapshots + documents, run cache
      ├── Vertex AI Gemini + Grounding with Google Search
      ├── BigQuery: feature store (partitioned + clustered), simulation_runs, policy_budget_ledger,
      │             simulation_decision_traces, policy_documents, analytics views
      │     └── BigQuery ML: LINEAR_REG twin + BOOSTED_TREE challenger ──► Vertex AI Model Registry
      └── Google Earth Engine (optional): CHIRPS + ERA5-Land + NEX-GDDP-CMIP6 ──► BigQuery climate table
```

## Deploy to Google Cloud

**Non-technical guide:** [docs/DEPLOY.md](docs/DEPLOY.md). Open Google Cloud Shell, get the code, and run one command. A guided Cloud Shell tutorial is in [docs/DEPLOY_TUTORIAL.md](docs/DEPLOY_TUTORIAL.md) (`teachme docs/DEPLOY_TUTORIAL.md`).

```bash
bash deploy/deploy.sh          # asks for the project if PROJECT_ID is not set; safe to re-run
```

The script:

1. Enables the Cloud Run, Cloud Build, Artifact Registry, Vertex AI, BigQuery, Firestore and Cloud Storage APIs.
2. Creates a least-privilege service account: Vertex AI user, BigQuery job user, and data editor on the app's dataset only. If the dataset grant fails, it falls back to read-only Data Viewer and warns that run logging is off.
3. Creates the free-tier stores for saved policies and chats: the Firestore `(default)` database (if the project has none) and a private Cloud Storage bucket (`<project>-agri-sus-store`, cached runs deleted after 30 days), with access for the service account only.
4. Loads the data into BigQuery (partitioned and clustered) and trains both BigQuery ML models, registered in the Vertex AI Model Registry.
5. Optionally runs the Earth Engine climate pipeline.
6. Builds the container with Cloud Build into Artifact Registry.
7. Deploys to Cloud Run and prints the URL.

To delete everything, run `bash deploy/teardown.sh`. To make a zip for upload to Cloud Shell, run `bash deploy/make_bundle.sh`.

| Setting | Default | Purpose |
|---|---|---|
| `PROJECT_ID` / `REGION` | asked / `us-central1` | Where to deploy |
| `MIN_INSTANCES` | `1` | `1` = no cold starts (≈ $3.6/day); `0` = scale to zero, pay nothing when idle |
| `GEMINI_AGENT_MODEL` | `gemini-3.8-flash` | Primary Gemini model. `GEMINI_FALLBACK_MODELS` (`gemini-3.5-flash,gemini-2.5-flash`) is used if it is unavailable |
| `GEMINI_THINKING_BUDGET` | `1024` | Thinking tokens per Gemini call. Keeps each co-pilot step to seconds; `default` = the model's own (slower) setting |
| `GEMINI_FIRST_TOKEN_S` / `GEMINI_IDLE_S` | `60` / `45` | Stall detection for streamed Gemini calls (co-pilot, document writer): no first output / no new output for this long → retried |
| `GEMINI_RETRIES` / `GEMINI_FAST_THINKING_BUDGET` | `2` / `128` | Retries after a stall, time-out, 429 or 5xx, each on the next model in the chain with a smaller thinking budget |
| `AGENT_DEADLINE_S` / `AGENT_MAX_S` | `240` / `480` | Soft deadline (the co-pilot stops calling tools and answers from what it has) / hard cap per answer |
| `GEMINI_TIMEOUT_S` | `90` | Per non-streamed Gemini call (outlook, grounded search), then retried on the next model |
| `GEMINI_THOUGHT_SUMMARIES` | `true` | Show Gemini's thinking headlines in the co-pilot's activity feed |
| `COPILOT_DOC_WAIT_S` | `150` | How long the co-pilot follows a document it is writing before answering (the document then finishes in the background) |
| `YIELD_MODEL` | `auto` | `auto` = champion/challenger; `prior` = agronomic prior only; `bqml` = force the BigQuery ML boosted tree |
| `USE_EARTH_ENGINE` | `ask` | `1` runs the Earth Engine climate pipeline (needs an Earth Engine-registered project), `0` skips it |
| `SKIP_BIGQUERY` | `0` | `1` = code-only redeploy that keeps the BigQuery tables and models |
| `STORE_BUCKET` / `FIRESTORE_LOCATION` | `<project>-agri-sus-store` / the region | Where saved policies, chats and cached runs are kept (Cloud Storage bucket; Firestore location if the database is new) |
| `BQ_DATASET` / `BQ_LOCATION` | `agri_sus_mvp` / `US` | BigQuery dataset |
| `GEMINI_MAX_REQUESTS_PER_10MIN` | `200` | Cap on paid Gemini calls (co-pilot questions, policy documents, outlooks) per server instance per 10 minutes, across all visitors |
| `API_DOCS` | `false` | `1` publishes the interactive API docs at `/docs` and `/openapi.json` (hidden by default) |

## Run locally (no GCP needed)

Without GCP the app runs fully offline: local CSV data, DuckDB for SQL, a rule-based agent router, and saved policies and chats in `.agrisus-store/` on local disk (`AGRISUS_STORE_DIR` to move it).

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
# (optional) rebuild the data. Processed outputs are already in data/processed/app
.venv/bin/python scripts/build_features.py
cd backend && ../.venv/bin/python -m app.yield_model train
../.venv/bin/python -m uvicorn app.main:app --port 8080        # API + built UI
cd ../frontend && npm install && npm run dev                    # hot-reload UI on :5173
```

**Live Gemini locally:** copy `.env.example` to `.env` in the project folder (`cp .env.example .env`), paste your key
from [Google AI Studio](https://aistudio.google.com/apikey) after `GEMINI_API_KEY=`, and restart the server.
`/api/meta` then shows `"backend": "Gemini API"`. The server reads `.env` when it starts (variables set in the shell
win). With Docker: `docker build -t agrisus . && docker run --env-file .env -p 8080:8080 agrisus`. To use Vertex AI
instead, set `GOOGLE_GENAI_USE_VERTEXAI=true` and `GOOGLE_CLOUD_PROJECT=...` in `.env` and run
`gcloud auth application-default login`. Cloud Run needs no key (see [docs/DEPLOY.md](docs/DEPLOY.md)).

## Verify it works

With the server running on :8080:

```bash
# API: every scenario solves, the plan beats do-nothing every year, guardrails hold, levers move the plan the right way,
# budget/ledger/MROPI add up, least-cost mode meets its target and reports its whole-plan cost, single and
# multi-country trade shocks re-plan, a 6-year plan runs, next-crop ranks, bad input is rejected, a saved policy
# reopens with identical numbers without re-solving, the library renames / deletes / restores / purges, chat
# sessions are kept, the co-pilot creates a policy, the policy document is written once, reused, regenerated as v2,
# read by the co-pilot and purged with its policy, the import check gives verdicts, trace / co-pilot respond, the
# co-pilot answer is a resumable job (ordered events, replay, bad job ids rejected), off-topic questions are declined
# (48 checks, a few minutes on a cold server)
.venv/bin/python scripts/smoke_test.py http://localhost:8080

# Browser (real headless Chrome with GPU): India map fully drawn, both futures scored, every layer, tab, scenario
# and lever, budget tab + MROPI map, least-cost mode, instrument switches, single and multi-country trade shocks,
# next-crop card, the import-displacement card, the policy document (sections A-H, tables, reopened without a
# rewrite, regenerated, printed as ONE copy), co-pilot what-if with plain-language steps folded into
# "Worked for ... · N steps", a 6-year plan, save /
# open / ask about / delete a policy, the co-pilot reading the policy document and creating a policy, chat
# sessions, no console errors (87 checks), plus a layout
# probe across 9 viewport sizes
cd tests/e2e && npm install
W=1440 H=900 node e2e.mjs
node layout.mjs

# Security regression (offline, a few seconds, no server needed): SQL sandbox, input validation, solve budgets,
# security headers, body-size limit, rate-limit keys, storage keys / policy & chat ids (no path traversal),
# bounded text, the shared listing cache, policy-document keys, scopes, languages, instructions and write quota
.venv/bin/python backend/tests/test_security.py

# Resilience (offline, about a minute, a scriptable fake Gemini behind a real server): slow first token → retried on a
# faster model, stalled stream withdrawn and retried, soft-deadline wrap-up without tools, outage after some steps →
# summarised from them, total outage → offline analyst, Stop, dropped connection resumed from the last event,
# parallel steps with a plain-language feed, topic guard, shared document jobs, a mostly written draft kept
.venv/bin/python backend/tests/test_agent_resilience.py
```

The browser tests need Google Chrome. Set `CHROME_PATH` if it isn't in the default location, and `URL` to test another server, for example a Cloud Run URL with `?notour` appended. Screenshots are written to `$TMPDIR/agrisus-e2e`.

To measure latency, throughput and the Gemini features of a deployed site, run
`python scripts/benchmark.py https://<cloud-run-url>` (needs `pip install requests`; add `all` for the load test and
the Gemini phases, which cost money). The results for the live site are in
[docs/PERFORMANCE_REPORT.md](docs/PERFORMANCE_REPORT.md).

## API

- `GET /api/health`, `/api/meta`, `/api/model-card`, `/api/geo`, `/api/districts`, `/api/forecast`
- `POST /api/simulate` with `{scenario, overrides}`.
  - Scenarios: `baseline`, `el_nino_2028`, `palm_oil_shock`, `zero_gw_depletion`, `indonesia_palm_halt`, `canada_lentil_halt`, `northern_heatwave`, `gemini_outlook`.
  - Overrides include the slider values, `horizon_years` (2–8, default 4), `gw_mode`, `budget_mode` (`fixed` | `min_budget`), `instruments` (`dbt`, `seed_kits`, `price_support`) and `trade_cuts` (`[{country, product, pct}]`, up to 8).
- `GET /api/run/{id}` (a run id, or `p-<policy id>` for a saved policy's snapshot), `GET /api/district/{code}?run_id=`, `GET /api/solve-estimate`
- Saved policies: `GET /api/policies`; `POST /api/policies` with `{run_id, name, note?}`; `GET/PATCH /api/policies/{id}` (`{name?, note?, deleted?}`); `DELETE /api/policies/{id}` (soft delete; `?purge=true` then deletes a policy that is already in Deleted for good, and returns 409 for a live one).
- Chat sessions: `GET /api/chats`, `GET /api/chats/{id}`, `DELETE /api/chats/{id}`.
- `GET /api/district/{code}/candidates?run_id=&year=`: next-crop ranking for both seasons.
- `GET /api/trade?product=`: suppliers, shares and HHI. `GET /api/trade/decompose?country=&product=&pct=`: the volume to replace and the hectares needed per substitute crop.
- Policy documents: `GET /api/documents/{run_id}?state=&language=` returns the saved document (or `null`); `POST /api/documents` with `{run_id, state?, language?, regenerate?, instructions?, refresh_research?}` starts (or joins) a background job and returns its SSE stream: `job`, `step` events and then the `document` (the saved version, unless `regenerate` or `instructions` ask for a new one; instructions ≤ 2,000 characters). `run_id` may be `p-<policy id>`: the document is stored with the policy and deleted with it. While a version is being written, the GET also returns its `job`.
- `GET /api/import-check/{run_id}?state=`: will the plan's extra production actually cut imports? A verdict per import group (pulses, edible oils), the low case, the risks and the clause that answers each.
- `POST /api/agent/chat` with `{messages:[{role,content}], language?, session_id?, run_id?}`. Starts the answer as a background job and returns its SSE stream: job, model, session, status, tool_call (plain-language label, usual duration), progress, tool_result, text, text_reset, citations, simulation, policy, document, doc_job, error, done — each with a sequence number `seq`. The exchange, with its steps and timing, is saved to the chat session.
- Jobs: `GET /api/jobs/{id}/events?after=N` resumes a stream after event N (it replays a finished job for 30 minutes); `POST /api/jobs/{id}/cancel` stops a co-pilot answer (what was written so far is saved).

## Data

All data is public:

- India Data Portal: APY, Agri Census crop and non-crop, Soil Health Card, CGWB wells, irrigation sources, MSP, and **Import Trade Statistics** (country-wise imports).
- TradeStat exports and imports.
- CGWB GWRA 2024 (IN-GRES).
- IMD gridded rainfall, NASA POWER and NEX-GDDP-CMIP6 SSP2-4.5, or CHIRPS / ERA5-Land / CMIP6 via Earth Engine.
- FAO-56 and CACP.

See [`data/README.md`](data/README.md).

## Honest limitations

- **Yield model** (see [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md)).
  - At district level, year-to-year yield is dominated by non-climate noise. On the all-India 2020–24 holdout (28.5k rows), the prior's WMAPE is 18.2% against 17.9% for persistence. It is built for stress-testing climate shocks (getting their direction and size right), not for point forecasting.
  - The BigQuery ML boosted tree replaces it only if it beats the prior's twin on that holdout. `/api/meta` reports which engine is active and why.
- **Instrument parameters** (kit cost and yield uplift, price-support admin cost, power and urea subsidy per unit) are literature-based assumptions shown in the Budget tab.
- **Fiscal ledger.** Forex savings are valued at landed import prices, not as fiscal receipts.
- **Trade shocks** replace the lost volume with domestic production only. Re-sourcing from other suppliers is shown, but its price effect is not modelled.
- **Crop scope.** 16 crops are reallocated. Cotton and sugarcane are held at today's area.
- **Shocks.** The El Niño and heatwave scenarios are one-year yield shocks. A *planned* shock reshapes the glide path only slightly, because the plan is a single linear pathway. To see how a plan copes with surprise, use the stress-test band.
- **Groundwater.** Caps are soft and budget-limited. The water pillar treats any district above 200% extraction as fully stressed, so improvements there show in BCM but not in the score.
- **Plan horizon.** The district CMIP6 projections end in 2030; later plan years use each district's 2026–30 projected mean. Longer plans take proportionally longer to solve (the UI's progress bar uses this instance's recent solve times).
- **Import-displacement check.** It uses one year of DGCI&S imports (FY2024-25), static unit values, the 2026-27 MSPs and today's duty, with no price elasticity or world-price response. The *low case* is a product-matching bound, not a forecast; the verdicts flag where the optimizer's one-for-one import cut is optimistic.
- **Policy documents.** Existing schemes and duties come from Google Search grounding (with citations) and are not verified by a person; offline, a curated reference list marked *not verified* is used. Without Gemini the document is written in English by a template and revision instructions cannot be applied (the version says so). Every number comes from the plan's fact sheet, so the document is only as good as the model behind it.
- **Shared, login-free library.** Saved policies and chats are visible to, and deletable by, anyone with the URL (deletes are restorable until purged, and only a policy already in Deleted can be purged). This suits a demo or a single team; a production deployment would add Identity-Aware Proxy or sign-in.
- **Rotation rules** are approximated through a soft legume-ramp constraint, which most districts don't fully reach within the default budget. Rice export value lost is reported as a KPI rather than priced into the objective.
- This is a **decision-support** tool. Its outputs are scenarios, not recommendations to individual farmers.

## Repository layout

```
backend/app/        FastAPI app, agent, policy document agent, optimizer, trade, saved policies, chat sessions,
                    storage, models
backend/models/     yield model artefacts + metrics
frontend/           React UI
bigquery/           schema + ledger/trace tables, BQML models, analytics SQL, Earth Engine climate SQL
deploy/             deploy.sh, teardown.sh, make_bundle.sh, load_bigquery.py
cloudbuild.yaml     Cloud Build → Artifact Registry → Cloud Run (push-to-deploy trigger)
scripts/            data acquisition (India Data Portal, TradeStat, IN-GRES, climate, Earth Engine), feature pipeline,
                    API smoke test, benchmark
tests/e2e/          browser end-to-end and layout checks (puppeteer)
data/               raw, reference, processed
docs/               how it works, deploy guide + Cloud Shell tutorial, model card, performance report, demo video script,
                    submission kit
```

## Security & privacy

- **Data.** The app uses only public government, NASA and Copernicus data. It holds no personal data and no secrets; saved policies and chats contain only what users type (the UI says they are shared). The bucket is private (uniform access, public access prevention) and only the app's service account can read or write it and Firestore. Credentials come from Cloud Run's service-account ADC or from env vars, and are never stored in the repo.
- **NL→SQL sandbox.** Queries are limited to a single `SELECT` over an allow-list of Agri Sus tables. A small SQL lexer checks every function call. It blocks qualified or quoted function names such as `AI.GENERATE`, `ML.PREDICT` or project UDFs (only `SAFE.` is allowed), plus `EXTERNAL_QUERY`, `SESSION_USER`, `VECTOR_SEARCH`, change-history functions and file readers. On BigQuery, every query is first dry-run, and it runs only if BigQuery itself reports a `SELECT` that touches tables of the app's own dataset. Jobs are capped at 200 MB billed and 30 s. In local DuckDB mode, file and network access are turned off with `enable_external_access=false` and the config is locked.
- **Abuse limits.** Each client IP is rate-limited per 10 minutes, using the trusted right-most `X-Forwarded-For` hop (IPv6 by /64): 40 new optimiser solves, including the ones the co-pilot runs; 30 co-pilot questions; 6 new policy-document versions (reopening a saved document is free; 30 when Gemini is off); and 10 outlooks. On top of that, each instance makes at most `GEMINI_MAX_REQUESTS_PER_10MIN` paid Gemini calls across all visitors. One co-pilot answer may make at most 16 tool calls, 3 web searches and 3 new solves. Request bodies are capped at 512 KB, and chat history at 100 messages. Repeat requests that hit the run cache or a saved document don't count. Identical solves that arrive at the same time share one solver run. Scenarios and state names are validated, and overrides, trade cuts and instruments are allow-listed and clamped. Policy, chat and run ids are checked against strict patterns and storage keys cannot leave their prefix; names, notes and messages are length-capped and stripped of control characters; saving, renaming and deleting share a 60-per-10-minutes write limit per IP; the library holds at most 300 policies and 200 chats. CORS is same-origin by default.
- **Browser hardening.** A strict Content-Security-Policy (scripts only from the site itself; map tiles only from CARTO), `frame-ancestors 'self'`, `nosniff`, HSTS, a Referrer-Policy and a Permissions-Policy are sent on every response. `/docs` and `/openapi.json` are off unless `API_DOCS=1`. FastAPI 0.128.8 / Starlette 0.49.3 include the fix for the Range-header DoS (CVE-2025-62727).
- **Output safety.** Markdown (co-pilot answers and policy documents) is rendered without raw HTML or images, so an injected web page cannot make the co-pilot load a tracking URL; document links open in a new tab with `noopener`. Citation links are restricted to `http(s)`. Revision instructions are capped at 2,000 characters, stripped of control characters, and given to the writer as a reviewer's request inside the prompt, never as system rules. The agent's system prompt treats tool results and web pages as data, never as instructions.
- **Accepted risks.** The service account can write to the app's own dataset (for run logs and decision traces). Rate limits and the run cache live in each instance's memory, so with *n* instances the limits are *n* times higher. As a backstop, set a [budget alert](https://cloud.google.com/billing/docs/how-to/budgets) and a Vertex AI quota on the project.
