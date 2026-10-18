# How Agri Sus works

This document explains what Agri Sus does, what happens behind every click, and how the Google Cloud services fit
together. It is written for judges, reviewers and new team members: the first sections need no technical
background, and the later sections go into the detail.

Related documents:
- [DEPLOY.md](DEPLOY.md): deploy it yourself.
- [../data/README.md](../data/README.md): every dataset and cleaning step.

## Contents

1. [In one minute](#1-in-one-minute)
2. [The problem and who it is for](#2-the-problem-and-who-it-is-for)
3. [A tour of the screen](#3-a-tour-of-the-screen)
4. [Architecture on Google Cloud](#4-architecture-on-google-cloud)
5. [What happens when you…](#5-what-happens-when-you)
6. [Data: from public sources to BigQuery](#6-data-from-public-sources-to-bigquery)
7. [The models](#7-the-models)
8. [The Gemini agent in depth](#8-the-gemini-agent-in-depth)
9. [Deployment and operations](#9-deployment-and-operations)
10. [Security, privacy and responsible AI](#10-security-privacy-and-responsible-ai)
11. [Testing](#11-testing)
12. [Limitations](#12-limitations)
13. [Glossary](#13-glossary)
14. [Code map](#14-code-map)

---

## 1. In one minute

Agri Sus answers one planning question for India:

> *Which crops should change in which districts between 2027 and 2030 (or over any 2–8-year horizon from 2027), and
> which policy instruments should pay for it? The aim is for India to import fewer pulses and edible oils and pump less groundwater, without any district's
> farmers earning less and without putting cereal security at risk.*

It works in four steps:

1. **Data.** Public datasets are cleaned into a district-level feature store in **BigQuery**: 752 districts, 16
   crops the plan can shift (cotton and sugarcane stay at today's area), two seasons. The sources are the India Data
   Portal, the Central Ground Water Board (CGWB), IMD, NASA and DGCI&S trade statistics.
2. **Prediction.** A climate-aware yield model predicts 2027–30 yields under the CMIP6 climate projection and under
   any shock you choose. The model is an agronomic prior with a **BigQuery ML** boosted-tree challenger, and it can
   use climate data computed by **Earth Engine**.
3. **Optimisation.** A **Google OR-Tools** linear program chooses each district's crop mix for every season and
   year, and how to spend the policy budget, inside hard guardrails.
4. **Explanation and conversation.** The map and tabs compare the plan with doing nothing, and every decision is
   explained. A **Gemini agent on Vertex AI** answers questions and runs what-ifs. It reads the live outlook with
   **Google Search grounding** and queries BigQuery in plain English. A **policy document agent** turns any plan
   into a structured, versioned policy proposal in 11 languages, including whether the extra production will really
   cut imports and which existing schemes it should converge with.
5. **Saved policies.** Any plan, whether set up by hand or created by the agent in conversation, can be saved as a
   named policy. The levers and the full solved result go to **Firestore** and **Cloud Storage** (free tier), so a
   policy re-opens in about a second with its map and every number, and the agent can discuss and compare saved
   policies. Co-pilot conversations are saved there too.

```mermaid
flowchart LR
  A["Public data<br/>India Data Portal, CGWB,<br/>IMD, NASA, trade statistics"] --> B["BigQuery<br/>district feature store"]
  B --> C["Yield and climate model<br/>BigQuery ML, Earth Engine"]
  B --> D["Optimiser<br/>Google OR-Tools LP"]
  C --> D
  D --> E["Plan for 2027-2030 (2-8 years)<br/>752 districts"]
  E --> F["Map, scorecard, budget,<br/>trade and forecast views"]
  E --> G["Gemini agent<br/>explains, re-plans, writes policy documents"]
  G -- "what-if" --> D
  E -- "save" --> H["Saved policies and chats<br/>Firestore, Cloud Storage"]
  G -- "create, read, compare" --> H
  H -- "re-open without re-solving" --> F
```

**Headline result** (baseline scenario, default levers, 2030, versus doing nothing):

| Measure | Do nothing | Agri Sus plan |
|---|---|---|
| Sustainability index (ASSI) | 69.7 | **76.0** |
| Pulses and edible-oil import bill | $27.1 bn | **$21.5 bn** ($5.6 bn a year saved) |
| Groundwater pumped | 170.4 BCM | **161.7 BCM** (8.7 BCM a year saved) |
| Farmer income | 100% of today | **100.1%**, with no district below today |

Annual policy budget: ₹6,000 crore. The full table is in the [README](../README.md#results).

## 2. The problem and who it is for

| | |
|---|---|
| **Import dependence** | India spent **$23 bn** importing pulses and edible oils in FY 2024-25. If nothing changes, the model projects **$27.1 bn by 2030**. India grows only about 37% of the edible oil it uses and about 78% of its pulses. |
| **Groundwater** | Paddy in the Punjab–Haryana belt draws groundwater at **150–300% of recharge**. In the 2030 do-nothing case, **75 districts** are over-exploited. |
| **Why it is hard** | Any shift must keep farm income, keep India self-sufficient in cereals, leave room for rice exports, stay within a budget, and survive El Niño, heatwaves and supplier shocks. The plan covers 752 districts × 16 crops × 2 seasons × 4 years by default (2 to 8), with national balances linking them all. That is too much to reason about by hand or inside a chat prompt. |

**Who uses it**
- **Primary users:** agriculture planners in the Union Department of Agriculture & Farmers Welfare and in state
  agriculture departments. They set the levers, compare scenarios, check the budget, and take a structured policy
  document (sections A–H, versioned) to the Secretary.
- **Secondary users:** district officers, Farmer Producer Organisations (FPOs) and extension staff. They ask the
  co-pilot, in their own language and by voice, what to grow next season and why.

**Why an optimiser plus an agent, and not just a chatbot?** The optimiser finds a plan that is provably the best
under the rules. Gemini explains the plan, drives it ("what if…?") and connects it to today's news. **The agent
never invents numbers: every figure it gives comes from a tool result.**

## 3. A tour of the screen

| Area | What it shows | What you can do |
|---|---|---|
| **Header** | App name, data source and model status | **📁 Policies** (the shared library of saved policies). **📄 Policy document** (the plan's policy proposal, sections A–H, written once and saved). **How it works** (guided tour). **Method & data** (method notes and sources) |
| **Scenarios** (left) | Baseline (CMIP6 SSP2-4.5), 2028 El Nino drought, Palm-oil price shock, Zero groundwater depletion mandate, Indonesia halts palm-oil exports, Canada lentil supply cut, Wheat-belt Rabi heatwave (2029), **✦ Gemini live outlook** | One click loads a pre-solved plan. Shock scenarios jump the view to the shock year |
| **Levers** (left) | Groundwater guardrail; budget mode (**Fixed budget** or **Least cost to hit target**); three instrument check-boxes; eight sliders, the first being the **plan horizon** (2–8 years from 2027); a trade-shock picker for several countries and products at once. Each lever is tagged **limiting**, **not limiting** or **not reached** for the current plan | Change anything, then **Run optimizer** (a progress bar shows the expected solve time). **Reset** restores the defaults. **💾 Save as policy** keeps the plan on screen |
| **KPI ribbon** (above the map) | Sustainability index, import bill (with pulses and oil self-sufficiency and whether the import target is met), groundwater pumped, farmer income, food security (cereal self-sufficiency) | Hover over ⓘ for plain-language definitions |
| **Map** (centre) | All districts, in nine layers: District sustainability score, Groundwater use vs recharge, Legume share, Paddy share, Groundwater saved, Paddy area change, Farmer income vs today, Policy outlay, Public return per rupee (MROPI) | **▶** animates the plan years (2027 → 2030 by default). Toggle **Do nothing / Agri Sus plan / Difference**. **Zoom to state**. Hover for district values; click a district for its card |
| **Scorecard** (on the map) | ASSI for doing nothing vs the plan in the selected year | Use the year scrubber across the plan years |
| **Saved-policy banner** (above the map) | When a saved policy is open: its name, scenario, date, and "rendered from its snapshot, no re-run" | **✦ Ask about it** sends the policy to the co-pilot. **📄 Document** opens its policy document. **All policies** opens the library |
| **Tabs** (below the map) | 🌍 Sustainability forecast · 💰 Budget & returns · 🚢 Trade shocks · 🏆 State leaderboard · 📈 Trajectory · ⚖ Binding guardrails | See the sections below. "Show on map" in the budget tab switches to the MROPI layer |
| **District card** | **Plan & why**: crop moves with reasons, rejected alternatives, rotation timeline, outlay by instrument, MROPI, a sustainability radar. **🌱 Next crop**: the ranked candidate crops for the next season | **✦ Ask the Co-pilot about…** sends the district to the co-pilot |
| **"Last change" strip** | After each re-run, what moved and why, or why nothing moved | — |
| **Co-pilot** (right) | Each step in plain language (*"Reading the national scorecard"* → *"National scorecard ✓"*), the answer, source links, model tag, an **Open policy** button when it has saved a policy, and an **📄 Open document** button when it has read or written a policy document | Type or use **🎤** voice; pick one of 11 languages; **🔊 Listen**. **💬 Chats** lists earlier conversations (shared, deletable); **＋ New chat** starts over. Suggestion chips show while the chat is empty |

The six tabs in more detail:
- **🌍 Sustainability forecast:** ASSI from 2027 to 2030 with its four pillars, the stress-test band, and the Gemini
  outlook rationale when that scenario is active.
- **💰 Budget & returns:** the instrument mix and the budget path, **the whole-plan total** (outlay, public returns
  and net cost over all plan years; in least-cost mode, the total cost of hitting the target), the fiscal ledger,
  and MROPI tiers.
- **🚢 Trade shocks:** who India buys each pulse and edible oil from, how concentrated the suppliers are, and how much
  must be replaced at home after a cut. Several cuts (country × product) can be combined. A card answers *"will more
  production actually cut imports?"* per group, with the risks and the policy clause that answers each.
- **🏆 State leaderboard:** states ranked by sustainability gain, water saved, paddy → pulses shift and outlay. Click
  a state to fly to it.
- **📈 Trajectory:** every KPI, year by year, for doing nothing and for the plan.
- **⚖ Binding guardrails:** the national constraints that limit the plan, with their shadow prices.

## 4. Architecture on Google Cloud

```mermaid
flowchart LR
  user["Planner's browser<br/>React, deck.gl, MapLibre<br/>voice via Web Speech API"]
  subgraph CR["Cloud Run service"]
    api["FastAPI<br/>rate limits, run cache, SSE"]
    agent["Gemini agent<br/>22 tools"]
    docs["Policy document agent<br/>facts, import check,<br/>research, writer"]
    lp["Optimiser<br/>OR-Tools LP, COIN-OR CLP"]
    ym["Yield and climate model"]
  end
  subgraph VX["Vertex AI"]
    gem["Gemini 3.8 Flash<br/>fallback 3.5 Flash, 2.5 Flash"]
    reg["Model Registry"]
  end
  search["Google Search<br/>grounding"]
  subgraph BQ["BigQuery"]
    feat["Feature store<br/>13 tables"]
    logs["Run logs, decision traces,<br/>policy budget ledger,<br/>policy documents"]
    bqml["BigQuery ML<br/>LINEAR_REG champion<br/>BOOSTED_TREE challenger"]
  end
  ee["Google Earth Engine<br/>CHIRPS, ERA5-Land, CMIP6"]
  subgraph ST["Free-tier storage"]
    fs["Firestore<br/>policies, chats"]
    gcs["Cloud Storage<br/>solved-plan snapshots,<br/>policy documents"]
  end
  cb["Cloud Build"] --> ar["Artifact Registry"]
  user -- "HTTPS and streaming" --> api
  api --> agent
  api --> lp
  agent --> lp
  lp --> ym
  agent -- "function calling" --> gem
  gem -- "grounding" --> search
  agent -- "read-only SQL" --> feat
  api -- "loads at start-up" --> feat
  api -- "logs every run" --> logs
  feat --> bqml
  bqml -- "climate factors" --> ym
  bqml -- "registered" --> reg
  ee -- "district climate" --> feat
  api -- "save and re-open policies, chats" --> fs
  api -- "plan snapshots" --> gcs
  api --> docs
  agent -- "read, write, regenerate" --> docs
  docs -- "writer, grounded research" --> gem
  docs -- "versions" --> gcs
  docs -- "one row per section" --> logs
  ar -- "container image" --> api
```

| Google product | What Agri Sus uses it for | Code |
|---|---|---|
| **Cloud Run** | Hosts the API and the web app in one container: HTTPS, autoscaling (1–5 instances), a least-privilege service account | `Dockerfile`, `deploy/deploy.sh` |
| **Vertex AI: Gemini** | The live agent (function calling, 22 tools, including creating and reading saved policies and their documents), the policy document agent (sections A–H in 11 languages, versioned), and the climate and price outlook. Model chain `gemini-3.8-flash` → `gemini-3.5-flash` → `gemini-2.5-flash` | `backend/app/agent.py`, `gemini.py` |
| **Grounding with Google Search** | Cited answers on current monsoon and ENSO outlooks, prices and policy. The outlook is turned into structured shocks for the optimiser. For policy documents: the existing central and state schemes to converge with, and today's import duties | `backend/app/gemini.py`, `documents.py` |
| **BigQuery** | Feature store (partitioned and clustered). Every simulation run, decision trace and budget-ledger row is logged, and every policy-document version (one row per section, `policy_documents`). The agent queries it in plain English | `deploy/load_bigquery.py`, `bigquery/01_schema.sql`, `03_analytics.sql` |
| **BigQuery ML** | Two yield models on 105,000 district-crop-years: a `LINEAR_REG` champion and a `BOOSTED_TREE_REGRESSOR` challenger. The challenger is promoted only if it wins on the 2020–24 hold-out | `bigquery/02_bqml_yield_model.sql`, `backend/app/data.py` |
| **Vertex AI Model Registry** | Both BigQuery ML models are registered automatically (`MODEL_REGISTRY = 'VERTEX_AI'`) | `bigquery/02_bqml_yield_model.sql` |
| **Google Earth Engine** *(optional)* | District climate from CHIRPS, ERA5-Land and NASA NEX-GDDP-CMIP6, averaged over each district polygon, written to BigQuery | `scripts/gee_climate.py`, `bigquery/04_climate_from_earth_engine.sql` |
| **Firestore** *(free tier)* | The shared library: saved-policy metadata (levers, headline numbers) and co-pilot chat sessions, plus one small "version stamp" per list so a listing costs a single read when nothing changed | `backend/app/store.py`, `policies.py`, `chats.py` |
| **Cloud Storage** *(free tier)* | Snapshots of solved plans (gzipped JSON plus the district table as Parquet): every saved policy keeps its full result (`policies/`), and solved runs are cached per model version (`runs/`, deleted after 30 days by a lifecycle rule) so other instances and restarts reuse them instead of solving again. Policy documents are stored with their policy (`policies/ID/documents/`) or per run (`documents/`) | `backend/app/store.py`, `service.py` |
| **Cloud Build + Artifact Registry** | Builds the container in the cloud and stores the image. `cloudbuild.yaml` supports push-to-deploy | `deploy/deploy.sh`, `cloudbuild.yaml` |
| **IAM** | The app runs as its own service account with only the roles it needs | `deploy/deploy.sh`, `deploy/grant_dataset_access.py` |
| **Google OR-Tools** (open source, by Google) | The linear-programming optimiser | `backend/app/optimizer.py` |

**Offline mode.** Without a Google Cloud project, the same container runs on the bundled CSV snapshot. It uses
DuckDB for the SQL tool, a rule-based co-pilot, and local disk for saved policies and chats. `/api/meta` always says which mode is live (`data_backend`,
`gemini.backend`, `yield_engine`, `climate_source`).

## 5. What happens when you…

### 5.1 …open the site

1. Cloud Run keeps one instance warm (`min-instances 1`), so visitors almost never wait for a cold start.
2. When an instance starts, it loads the feature store: from BigQuery when `DATA_BACKEND=bigquery`, otherwise from
   the CSV snapshot. It then **pre-solves every scenario in the background** (about 7 minutes), which is why
   clicking a scenario takes about 2 seconds instead of 40 or more. Once one instance has solved them, the plans
   are in Cloud Storage, so later instances and restarts load them in seconds instead of solving again.
3. The browser loads the React app. It fetches `/api/meta` (data source, Gemini status, yield engine, languages),
   `/api/geo` (district shapes) and the baseline plan.

### 5.2 …change a lever and press Run optimizer

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant A as Cloud Run API
  participant C as Run cache
  participant O as OR-Tools LP
  participant Q as BigQuery
  B->>A: POST /api/simulate with scenario, levers, trade cuts
  A->>C: run_id = hash of all inputs
  alt solved before, on this instance or any other
    C-->>A: cached plan from memory or Cloud Storage, instant
  else new inputs
    A->>A: rate limit, 40 new solves per IP per 10 minutes
    A->>O: build and solve, about 129k variables
    O-->>A: optimal plan, shadow prices, reduced costs
    A->>A: KPIs, ASSI, stress tests, ledger, MROPI, trade
    A->>Q: log run, decision traces, budget ledger in a background thread
    A->>A: store the plan snapshot in Cloud Storage, in the background
  end
  A-->>B: plan summary
  B->>B: update map, KPI ribbon, tabs and the Last change strip
```

- The `run_id` is a hash of every input. Identical requests share one solve, even when they arrive at the same time
  (in-flight de-duplication). Recent plans are kept in a memory cache, and every solved plan is also kept in Cloud
  Storage for 30 days.
- **Plan horizon.** The *Plan horizon* slider sets how many years the transition takes (2–8, from 2027). The import
  target, groundwater caps and glide path all end in the final year, and every tab, the year scrubber and the map
  animation follow the chosen years. Longer plans have more variables, so they take longer to solve: while it
  solves, a progress bar counts towards this instance's typical solve time (`GET /api/solve-estimate`).
- A fresh national solve takes **about 40 seconds** on Cloud Run with 2 vCPUs (37–48 s measured). A cached plan
  comes back in 1–2 seconds, most of it spent sending the 2.5 MB plan. See [PERFORMANCE_REPORT.md](PERFORMANCE_REPORT.md).
- BigQuery logging never slows the answer, because it runs in a background thread.

### 5.3 …ask the co-pilot

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser co-pilot
  participant A as Cloud Run API
  participant J as Background job
  participant G as Gemini on Vertex AI
  participant T as Tools
  B->>A: POST /api/agent/chat with messages, language, chat session and the plan on screen
  A->>J: start the answer as a job (it outlives the connection)
  A-->>B: event stream opens: job id, session id, keep-alive every 10 s
  loop until Gemini answers, at most 8 tool steps
    J->>G: streamed call: conversation, system rules, 22 tool declarations
    G-->>J: thought summary, then function calls
    J-->>B: "Checking Bathinda's water data…", then one plain-language line per step
    par the calls of one step run in parallel
      J->>T: optimiser, BigQuery, Google Search, policy store
    end
    T-->>J: numbers, tables, citations
    J-->>B: ✓ per step, plus a simulation event that moves the dashboard
  end
  G-->>J: the answer, streamed as it is written
  J-->>B: text, citations, done
  J->>J: save the exchange and how it was built to the chat session (Firestore)
  Note over B,A: a dropped connection reconnects to GET /api/jobs/ID/events?after=N and carries on from the next event
```

- **A live activity feed**, as in a coding assistant. While it works the co-pilot shows, in plain words, what it is
  doing: Gemini's current thinking headline (*"Comparing water savings across districts…"*), then one line per step
  — *Looking up the water, soil and crop profile of Bathinda*, *Searching the web with Google: "…"*, *Running the
  optimizer on this what-if (Rs 20,000 cr a year)*, *Reading the policy document (section G)* — each with a spinner,
  a timer and, for slow steps, the usual duration (*"12 s of ~30 s"*). Retries and wrap-ups are announced too
  (*"The model was slow to respond - retrying with a faster setting"*). When the answer is ready the steps fold into
  **▸ Worked for 24 s · 3 steps** above it, and they are saved with the chat. Tool names, parameters and JSON are
  never shown.
- **■ Stop** ends the answer on the server; what was written so far is kept.
- **Close the tab or reload mid-answer:** the answer carries on on the server. Coming back, the co-pilot picks up
  the live feed where it was, or shows the saved answer.
- **Topic guard.** Questions about the app, its data and its plans are answered with its tools. Questions about the
  wider domain — agriculture, water, climate, trade, the rural economy and government schemes in India — are answered
  from Google Search and labelled *Web sources (Google Search)*. Anything else (coding, films, general trivia) is
  declined politely; obvious cases are caught before any Gemini call.
- The answer streams as **Server-Sent Events**:
  - event types: `job`, `model`, `session`, `status` (thinking, plan, retry, wrap-up, scope, fallback, stopped),
    `tool_call` (an id, the plain-language label and the usual duration), `progress`, `tool_result`, `text`,
    `text_reset`, `citations`, `simulation`, `policy`, `document`, `doc_job`, `error`, `done`;
  - every event carries a sequence number `seq`, so a resumed stream continues exactly where it stopped;
  - a keep-alive comment goes out every 10 seconds, and gzip is bypassed so each step shows the moment it happens.
- The co-pilot knows which plan is on screen (`run_id`), so *"save this"* or *"why did this plan cut paddy in
  Sangrur?"* refer to what you are looking at.
- When a tool re-runs the plan, the `simulation` event loads that plan into the dashboard. That is why the map,
  sliders and tabs move when you ask *"what if the budget is ₹20,000 crore?"*
- **Voice** uses the browser's built-in Web Speech API: 🎤 turns speech into text and 🔊 reads answers aloud. Only
  the text of the question is sent; no audio reaches the Agri Sus server.

### 5.4 …pick "✦ Gemini live outlook"

1. `GET /api/forecast` asks Gemini, **grounded with Google Search**, for the current ENSO and IMD outlook and global
   oilseed and pulse prices.
2. Gemini returns structured JSON:
   - for each year 2027–34 (so it covers the longest plan): the ENSO phase, the Kharif rainfall anomaly (%), the Rabi
     temperature anomaly (°C) and a confidence level. Years far ahead are expected to be near climatology with low
     confidence;
   - import-price multipliers;
   - a short rationale.
3. The values are clamped to safe ranges (rain −30% to +20%, heat −1 to +3 °C, prices 0.6× to 2.0×). The outlook is
   cached for 6 hours.
4. The shocks run through the optimiser as the `gemini_outlook` scenario, for whatever plan horizon is set
   (shocks in years outside the plan are ignored). The 🌍 tab shows *"Gemini outlook: rationale"*.

### 5.5 …click a district

- **Plan & why** (`GET /api/district/{code}`): the decision trace for that district.
  - Crop moves for each year, with their reasons.
  - Rejected alternatives, with their reduced costs.
  - The binding constraints that shaped the moves.
  - A rotation timeline, the groundwater stage, the outlay by instrument and MROPI.
  - A radar of the district score (water, soil, diversity).
- **🌱 Next crop** (`GET /api/district/{code}/candidates`): every candidate crop for each season of the chosen plan
  year.
  - For each crop: the decision (expand, keep or reduce), area today → plan, climate-adjusted yield, irrigation need,
    net margin, and flags (groundwater cap, heat, rotation).
  - It names the recommended next crop after the preceding season's crop, and the closest rejected alternative with
    the reason.

*Example: Sangrur (Punjab).*
- Groundwater is pumped at 310% of recharge.
- The plan moves 10,172 ha from paddy to moong by 2030, saving 50 million m³ of water a year.
- Moong is the recommended Kharif 2027 crop; sesame is the closest rejected alternative.

### 5.6 …open the policy document

**📄 Policy document** (in the header, on a saved policy, on the saved-policy banner, from the Trade tab or from the
co-pilot) turns a plan into a structured policy proposal that a ministry could circulate for consultation. A
dedicated **document agent** writes it once; after that it is read, not rewritten.

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser (📄 dialog or co-pilot)
  participant A as Cloud Run API
  participant G as Gemini on Vertex AI
  participant S as Cloud Storage
  participant Q as BigQuery
  B->>A: GET /api/documents/RUN?state=&language=
  A->>S: read the saved document
  A-->>B: the saved version, or null ("Write the policy document")
  B->>A: POST /api/documents (write, or regenerate with instructions)
  A-->>B: a background job starts (the dialog can be closed and reopened while it works)
  A-->>B: step: reading the plan (a fact sheet from the optimizer)
  par grounded research, reused for 14 days
    A->>G: existing central and state schemes (Google Search)
    A->>G: today's import duties (Google Search)
  end
  A-->>B: step: testing whether more production cuts imports
  A->>G: write the sections from the facts (streamed)
  A-->>B: step: writing, with the section reached ("section D")
  A->>S: save version N (before the browser is told)
  A->>Q: one row per section (a free load job)
  A-->>B: the document
```
**What is in it.** An executive summary, eight sections and an annex. The sections follow the usual structure for
reviewing an agricultural policy, adapted to an *ex-ante proposal*: where a review would report measured results,
the document says what will be measured, against which baseline, and what the model can and cannot attribute.

| Section | Content |
|---|---|
| A. Policy identification and context | Title, proposing authority, coverage (India or a state), instrument type, period, status (*draft for consultation*), objectives and the SDGs it serves |
| B. Objectives and scope | The problems addressed (groundwater, import dependence, mono-cropping, income risk); target crops with area and output changes; beneficiaries and eligibility; states and districts covered; short-, medium- and long-term targets |
| C. Sustainable agriculture dimensions | Soil health, water, crop diversity, climate mitigation and adaptation (the stress tests), inputs (urea saved through legumes), farmer income and equity, food security |
| D. Policy instruments and implementation | The instrument mix the optimizer chose (DBT, seed & FPO kits, price-deficiency payments) with rates, standards and compliance, extension, infrastructure, implementing agencies, delivery and partnerships |
| E. Funding and resources | Total and annual outlay by instrument, public returns and MROPI, cost to farmers, fiscal sustainability |
| F. Monitoring, outcomes and evaluation | Indicators with baselines and yearly targets, reporting frequency, independent evaluation and audit, attribution limits |
| G. Will higher domestic production cut imports? | The import-displacement check below: a verdict per import group and a numbered clause, in policy wording, for each reason displacement could fall short |
| H. Convergence with existing central and state schemes | Each scheme found by grounded search, with its role: *deliver through it*, *top up or align rates*, *reform*, or *works against the plan* |
| Annex | Data, method, assumptions and sources (with the grounding citations) |

**The import-displacement check (Section G, and the 🚢 Trade tab).** The optimizer counts every extra tonne of pulses
or edible oil as a tonne less imported. The check tests that assumption against the latest full year of DGCI&S
import statistics (product mix, unit values, suppliers), the 2026-27 MSPs and the plan's final-year crop moves:
- **Product match.** How much of the extra output is the same product as an import line, up to that line's volume:
  extra chickpea does not replace imported peas, and soybean oil does not replace crude palm oil. The matching
  share gives a *low case* for the import cut.
- **Price gap.** The domestic cost at MSP against the landed import price (oilseeds valued as oil, net of a meal
  credit), including today's duty when the grounded search found it.
- **Cheap substitutes.** Imports that land well below the MSP of the crop they compete with.
- **Price realisation.** What farmers would earn at import parity, and the price support needed to keep them at MSP,
  against the plan's own price-support line.
- **Demand growth.** Whether imports still rise above today's level even though they fall below the no-policy path.
- **Supplier concentration and preferential routes.** HHI, and the share from trade-agreement partners whose
  concessional duties cap the duty lever.
- **Structural factors** the data cannot quantify: refining capacity built at the ports, palm oil's industrial uses,
  harvest-time price crashes, and trade-policy volatility.

Each risk is graded and becomes a numbered clause (G.1, G.2, …) in policy language: a tariff-rate quota for peas, a
price-parity duty band, ring-fenced price support, processing capacity near the new production, a published
trade-policy calendar. Each group gets a verdict: *unlikely* when less than half of the extra output matches the
import basket or three or more measured risks are high; *partly* when any measured risk is high or the match is
below 80%; otherwise *likely*. For the baseline plan in 2030:

| | Pulses: **partly** | Edible oils: **unlikely** |
|---|---|---|
| Import cut the optimizer assumes | 4.74 Mt | 1.89 Mt |
| Low case (matching products only) | 3.89 Mt (82% of the extra output matches) | 0.35 Mt (18% matches: India mainly imports crude palm oil) |
| Domestic cost at MSP / landed import price | 0.96× | 1.68× |
| Main reasons | Imported peas (30% of pulse imports) land 37% below the chana MSP; 631 kt of urad/moong above what India imports | Support to keep farmers at MSP ≈ ₹5,441 cr a year vs ₹911 cr in the plan; imports still rise with demand; 48% of imports come from ASEAN partners |

**Written once, versioned.** The document is stored in Cloud Storage per plan, scope (India or a state) and language
— with the policy (`policies/ID/documents/…`) for a saved policy, so deleting the policy deletes its documents.
Opening it again reads the saved version: no model call and no rewrite. **↻ Regenerate…** writes the next version,
optionally with instructions (up to 2,000 characters, for example *"lead with rice-fallow pulses in the eastern
states"*) and optionally searching again; otherwise the research of the previous version is reused for 14 days. The
last 20 versions are listed with their date, generator, source (dialog or co-pilot) and instructions; BigQuery
(`policy_documents`, one row per section) keeps the text of every version. One writer per document at a time: a
second request (another tab, another visitor or the co-pilot) joins the job already running and sees the same
progress and the same result.

**Gemini or template.** With Gemini, the writer gets the fact sheet, the import check, the schemes and the duties as
JSON, at temperature 0.3. Its rules: every number from the JSON, policy wording for clauses, the fixed headings, and
instructions treated as editorial guidance only (a request for numbers the plan does not contain is answered with
"this needs a new optimizer run"). Any section it leaves out is filled from the template and marked. Without Gemini,
or if the call fails, a deterministic template writes every section from the same facts in English, and the document
says so. Offline, the schemes come from a reference list marked *not verified*.

**Long writes never time out.** The document is written in a background job, and the writer streams. A stall (no
first words within 90 s, or 60 s without new text) is retried on the next model; the job gives up after 8 minutes
in total. If the writer stalls after the summary and at least two sections are written, that draft is kept and only
the missing sections come from the template (the document says so). Closing the dialog, a dropped connection or a
reload does not stop the job: reopening the dialog re-attaches to it, and if it ran on another instance the dialog
waits for the saved version.

**Print and download.** **Print / PDF** prints the document once, as continuous pages: a print-only copy of the
article sits at the root of the page and everything else is hidden for print. **Download .md** saves the Markdown.

**The co-pilot and the document.** Four tools connect them:

| Tool | What it does |
|---|---|
| `get_policy_document` | Reads the saved document of a policy or the plan on screen: the outline, or one section's full text |
| `generate_policy_document` | Writes it, or with `regenerate` and `instructions` writes the next version from points agreed in the conversation |
| `check_import_displacement` | The import-displacement check on its own (seconds, no model call) |
| `find_existing_schemes` | Grounded search for central and state schemes to converge with, with a focus (e.g. *"micro-irrigation in Maharashtra"*) |

The co-pilot writes at most one new version per answer. It relays the writer's progress in its activity feed for up
to 150 s (`COPILOT_DOC_WAIT_S`); a longer write carries on in the background while the co-pilot answers, and a card
under the answer follows it and turns into **📄 Open document** when the version is saved (the button is also
added to the saved chat).

**Why a separate document agent rather than the co-pilot writing documents itself?**
- **Consistency.** The dialog and the co-pilot call the same pipeline: same fact sheet, same structure, same storage.
- **Cost and speed.** A document is about 6,000 words and takes one to three minutes with two grounded searches.
  Saved once, it reopens in milliseconds; a chat turn that wrote it inline would compete with the chat's own deadline
  and token budget and rewrite it every time it is mentioned.
- **Auditability.** Versions record what triggered them and with which instructions; BigQuery keeps all of them.
- **One writer, one quota.** A lock per document prevents conflicting versions, and only a new version counts towards
  the visitor's quota (6 per 10 minutes, shared by the dialog and the co-pilot).
- **Separation of concerns.** The co-pilot converses and decides *when* to regenerate and *what* should change; the
  document agent decides *how* to write it, under its own hard rules.

### 5.7 …save, open and discuss a policy

A **policy** is a named, saved plan: its levers (scenario, budget mode, instruments, sliders, horizon, trade cuts)
and the complete solved result.

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant A as Cloud Run API
  participant F as Firestore
  participant S as Cloud Storage
  B->>A: POST /api/policies with run_id, name, note
  A->>S: write the plan snapshot to policies/ID (JSON + Parquet)
  A->>F: write the policy document: levers, headline KPIs, totals
  A->>F: bump the library's version stamp
  A-->>B: saved, Open it
  B->>A: GET /api/run/p-ID (open the policy)
  A->>S: read the snapshot, no optimiser run
  A-->>B: the plan, exactly as saved
  B->>B: restore levers, scenario, map, tabs; show the saved-policy banner
```
- **Save.** **💾 Save as policy** (under the levers) saves the plan on screen; it is disabled while the levers have
  unsaved changes, so a policy always matches its numbers. The name is suggested from the levers.
- **Library.** **📁 Policies** lists every saved policy with its scenario, date, headline numbers (ASSI, import
  saving, water saved, total outlay over the plan) and note. You can open it, ask the co-pilot to compare it with the
  plan on screen (**✦ Ask**), rename it, delete it (to *Deleted*, restorable) and delete it permanently.
- **Open.** A policy opens from its snapshot in about a second: the levers, scenario, map, KPI ribbon, every tab,
  district cards and the policy documents all come back exactly as saved. If a snapshot is ever missing, the server
  re-solves from the saved levers once and flags the result as recomputed.
- **Discuss.** **✦ Ask about it** on the banner, or any question naming a policy, uses the agent's policy tools:
  `list_policies`, `get_policy` (levers, final-year KPIs, totals over the plan, top states) and `create_policy`,
  and the document tools of section 5.6 (*"what does section G of its policy document say?"*, *"regenerate it with
  a stronger focus on Rabi pulses"*).
- **Create by conversation.** Ask *"Create a 6-year plan for an El Niño year and save it as El Niño-proof"*, or
  answer *yes* when the co-pilot offers to save a what-if. The agent sets every lever itself, solves, saves the
  policy, and shows an **Open policy** button.
- **Listing is cheap.** Each instance caches the policy list and re-validates it with one Firestore read of a
  version stamp that every write bumps, so the library list costs one read rather than one per policy.

### 5.8 …come back to a conversation

- Every co-pilot exchange is saved to a **chat session** in Firestore: the question, the answer, its sources, the
  plans it showed, any policy it created, any policy document it opened or wrote, and how the answer was built
  (its plain-language steps and how long it took).
- **💬 Chats** lists recent sessions (shared by everyone, no login). Click one to continue it, or delete it.
  **＋ New chat** starts a fresh session.
- At most 200 sessions are kept; the oldest are dropped automatically.

## 6. Data: from public sources to BigQuery

```mermaid
flowchart LR
  subgraph SRC["Public sources"]
    idp["India Data Portal<br/>APY, Agricultural Census, Soil Health Card,<br/>groundwater, land use and irrigation, MSP,<br/>Import Trade Statistics"]
    ts["TradeStat, Ministry of Commerce"]
    cl["IMD rainfall, NASA POWER,<br/>NEX-GDDP-CMIP6"]
    gw["CGWB groundwater assessment"]
  end
  sc["Pipeline scripts<br/>scripts/"]
  csv["data/processed/app<br/>13 clean tables"]
  bq["BigQuery dataset<br/>agri_sus_mvp"]
  ml["BigQuery ML<br/>yield models"]
  ee["Earth Engine, optional"]
  app["Agri Sus on Cloud Run"]
  idp --> sc
  ts --> sc
  cl --> sc
  gw --> sc
  sc --> csv
  csv -- "deploy/load_bigquery.py" --> bq
  bq --> ml
  ml -- "climate factors" --> bq
  ee -- "climate_projection_ee" --> bq
  bq --> app
  csv -- "offline snapshot" --> app
```

| Dataset | What it gives Agri Sus |
|---|---|
| **Area, Production & Yield** (APY / UPAG, India Data Portal) | District × crop × season area and yield. The 2020–24 average is the baseline; 2004–24 trains the yield models |
| **Agricultural Census: crop** | Irrigated vs unirrigated area by crop (2010-11), kept as a district × crop irrigation reference table |
| **Agricultural Census: non-crop** | Farm holdings (average holding size), wells and tubewells per district |
| **Soil Health Card** | Village-level nutrient samples (10.8 million rows) aggregated to district soil N, P, K, organic carbon and pH. They feed the district profile and the BigQuery ML yield features |
| **Groundwater** (CGWB wells via India Data Portal, plus the CGWB 2024 assessment) | Water depth and its trend, and each district's *stage of extraction* (% of recharge pumped), which drives the groundwater cap and the water pillar |
| **Land use: sources of irrigation** | Net irrigated area by canal, tank, tubewell and well. This gives each district's irrigation coverage (which sets the rainfed share in the yield model), the groundwater share of irrigation, and the canal supply in the water-availability rule (CWAR) |
| **Minimum Support Price** | MSPs to 2026-27, used for crop margins and price-deficiency payments |
| **Export and import (TradeStat)** | HS 8-digit trade, used for the national import bill and food balance |
| **Import Trade Statistics** (India Data Portal, DGCI&S) | Country × product × month imports, used for supplier shares, concentration and trade shocks |
| Climate | IMD gridded rain and NASA POWER temperature (history); NEX-GDDP-CMIP6 SSP2-4.5 (2026–30). Optionally Earth Engine: CHIRPS, ERA5-Land, CMIP6 |
| Reference | CACP cost of cultivation, FAO-56 / ICAR crop constants (water need, nitrogen credit, oil recovery) |

- The pipeline scripts are `fetch_idp.sh`, `build_datasets.py`, `build_features.py`, `build_geo.py`,
  `build_trade_idp.py`, `fetch_gwra.py`, `fetch_climate.py` and `gee_climate.py`. They clean, join and impute: missing
  context values take the state median and are flagged. District splits are mapped to their parent districts.
- `deploy/load_bigquery.py` loads the 13 app tables with descriptions. Tables are partitioned by `agri_year` where
  they have one and clustered on their filter columns.
- `bigquery/01_schema.sql` creates the log tables: `simulation_runs`, `simulation_decision_traces`,
  `policy_budget_ledger`. They are time-partitioned.
- Every source, file and cleaning decision is listed in [data/README.md](../data/README.md).

## 7. The models

### 7.1 Yield and climate

- **Agronomic prior** (always available):
  `yield = trend-adjusted 5-year baseline × climate_factor`, where `climate_factor = clip(1 + λ·Σ βₖ·xₖ, 0.4, 1.3)`.
  λ is a single calibrated shrinkage factor.
  - The terms cover rainfall deficit or excess (scaled by the rainfed share), Tmax anomaly, terminal heat days for
    wheat, dry spells and extreme-rain days.
  - The coefficients come from the published literature; the table is in [MODEL_CARD.md](MODEL_CARD.md#form).
- **BigQuery ML champion and challenger**, both trained on 2004–19 and tested on a 2020–24 hold-out:
  - `yield_climate_response` (`LINEAR_REG`, the champion): an unconstrained twin of the prior;
  - `yield_boosted_tree` (`BOOSTED_TREE_REGRESSOR`, the challenger): learns crop × region × weather interactions.
- **Promotion rule:** with `YIELD_MODEL=auto`, the app uses the boosted tree's climate factors **only if** its
  hold-out WMAPE beats the champion's. `/api/meta` → `yield_engine` reports the active engine, both scores and the
  reason.
- **Earth Engine** (optional) recomputes district climate over whole district polygons rather than the centroid. The
  deploy script then retrains BigQuery ML on it and points the app at it (`climate_source` in `/api/meta`).
- **Honest limit:** at district level the model does not beat a simple persistence forecast for single-season
  yields: on the 2020–24 hold-out, the prior's WMAPE is 18.2% against 17.9% for persistence. It is used for
  **directional climate shocks** in a multi-year plan, not for point forecasts. See [MODEL_CARD.md](MODEL_CARD.md).

### 7.2 The optimiser

| Element | What it does |
|---|---|
| **Decision variables** | Hectares `area[d,c,s,t]` for every district × candidate crop × season (Kharif, Rabi) × plan year, plus the policy outlay by instrument. The plan runs 2027–30 by default; the *Plan horizon* lever sets 2–8 years from 2027 |
| **Glide path** | `area[t] = baseline + (t − 2026)/H × (area[final] − baseline)` for a plan of H years. Each district moves an equal step each year towards its final-year mix (a quarter a year in the default 4-year plan), so it never pays farmers to switch and then switch back |
| **Calibration (PMP)** | Today's crop mix is treated as each district's own optimum, revealed by farmers' choices. The LP cannot "discover" profit farmers are ignoring: every move must be paid for by a public benefit (water, imports, soil) |
| **Hard constraints** | Season land (at most 3% may be left fallow); year-on-year flexibility (staples shrink at most 15% a year, other crops 5%); maximum land shift per year; water availability (CWAR) blocks expansion where water is short; a national seed and market scale-up cap per crop; **each district's farm income ≥ today**, funded by the instruments within the budget |
| **Soft constraints** (penalised, so every scenario solves and any shortfall is reported) | Cereal self-sufficiency floor, rice and wheat floors (including rice exports), district groundwater caps ramping to the target, legume-share ramp, the final-year import-bill target (ramped linearly) and any supplier cuts |
| **Objective** (₹ crore) | Farmer welfare + strategic value of imports avoided + soil nitrogen credit − groundwater cost − extra cost of public funds (MCPF 1.25) − transition costs − penalty on soft-constraint shortfalls |
| **Size and speed** | About 129,000 variables and 124,000 constraints for all India (baseline, 4 years); the size grows in proportion to the number of plan years. COIN-OR CLP (through OR-Tools) solves the default plan in about 10 seconds; GLOP is the fallback |
| **Budget modes** | *Fixed budget*: the best plan for the money. *Least cost*: minimise the budget, with the import target as a hard constraint. Both report the budget for each year **and the total over the whole plan** (outlay, public returns, net cost); the baseline least-cost plan needs ₹26,246 crore over 2027–30 and returns ₹1.47 lakh crore |

Default levers: at most 12% of a district's land shifted a year; cereal floor 101.5%; groundwater guardrail
"cap 80" (over-exploited and critical districts ramp towards 80% of recharge); a 25% import-bill cut target;
₹6,000 crore a year; 75% of rice exports retained; 90% MSP realisation for non-procured crops; seed and market
scale-up 5% a year.

### 7.3 Policy instruments, fiscal ledger and MROPI

| Instrument | How it works in the model |
|---|---|
| **Income-parity transfer (DBT)** | Cash per district, as needed, so farm income stays at or above today. Similar to Haryana's *Mera Pani Meri Virasat* |
| **Seed & FPO kit** | ₹4,000 per hectare per season in kind; +10% yield on pulses and oilseeds |
| **Price-deficiency payment** | Tops prices up to MSP (+3% admin cost) and removes half of the farmer's risk premium for switching. Similar to PM-AASHA |

- **Fiscal ledger.** The outlay is set against what it returns: forex on imports avoided, the farm-power subsidy on
  water not pumped (₹1.5/m³), and the urea subsidy saved through legume nitrogen (₹32,000/t).
  - **Public return** = all three returns ÷ outlay. In the baseline plan this is **8.2×**.
  - **Budget payback** = power + urea only ÷ outlay, because forex is an economy-wide gain, not money paid back to the
    exchequer.
- **MROPI** is the public return per rupee in each district:
  - Tier 1: ≥ 10×;
  - Tier 2: 1–10×;
  - Tier 3: < 1×. These districts are funded for farm income and aquifer security, not for payback;
  - Self-financing: no outlay, positive return;
  - No outlay: no public money spent and no net public return.

  MROPI is also a map layer.

### 7.4 Trade shocks and supplier concentration

- Supplier shares come from India Data Portal *Import Trade Statistics*, using the latest complete year (FY 2024-25).
- A supplier cut ("Indonesia halts palm-oil exports") becomes a tonnage. It is phased in over the plan years (¼, ½,
  ¾, all in the default 4-year plan) and enters the LP's commodity balance.
- Up to 8 cuts can be combined: several countries for one product, or several products (for example, 50% less palm
  oil from Indonesia and Malaysia plus 40% less lentil from Canada). Cuts on the same commodity group add up.
- The result shows the substitute crops and districts that replace it, any shortfall, and the remaining suppliers.
  *Example:* Indonesia supplies 49% of India's palm oil (3,882 kt). The plan replaces about 2.2 Mt at home by 2030,
  mainly with groundnut, mustard and soybean, and lists Malaysia (31%), Thailand and Singapore as other suppliers.
- **Supplier concentration** is the Herfindahl index (HHI, 0–1) of country shares for every pulse and edible oil.

### 7.5 Sustainability index, district score and stress tests

**ASSI (0–100)**, the Agri Sustainability & Self-reliance Index, uses one absolute scale, so doing nothing and the
plan are measured with the same yardstick:

| Pillar | Weight | Score |
|---|---|---|
| Self-reliance | 30% | Average of pulses and edible-oil self-sufficiency, each capped at 100% |
| Water | 30% | From the groundwater stage of extraction: ≤ 60% of recharge scores 100, 100% scores 71, ≥ 200% scores 0 |
| Soil | 20% | Legume share of cropped area against a one-in-three rotation target (33.3%) |
| Diversity | 20% | 1 − HHI of crop-area shares |

- **District sustainability score (0–100):** water 40%, soil 30%, diversity 30%. Farmer income is a hard guardrail,
  not a scored pillar.
- **Stress tests:** the *fixed* plan is re-scored, without re-optimising, under three unplanned climate shocks:
  - El Niño 2028: −18% monsoon, +0.8 °C;
  - Rabi heatwave 2029: +2.2 °C;
  - back-to-back weak monsoons 2029–30: −12% each year.

  In the baseline plan the worst-year ASSI is 71.6, and the plan still beats doing nothing under every shock.

### 7.6 Explainability

- **Shadow prices (duals)** of the binding national constraints show what each guardrail costs the plan
  (⚖ Binding guardrails tab).
- **Reduced costs** explain why a crop was rejected in a district: the cost to the plan of forcing one hectare of it.
- **Lever tags** (*limiting*, *not limiting*, *not reached*) come from the duals, so a planner can see which knob
  actually holds the plan back.
- **"Last change" strip:** after each re-run it says what moved, or why nothing moved, for example because the lever
  is not limiting.
- **Decision traces** for every district are logged to BigQuery (`simulation_decision_traces`) for audit.

## 8. The Gemini agent in depth

**Tools.** Gemini chooses among 22 functions; every number in its answers comes from one of them.

| Tool | What it does |
|---|---|
| `get_national_scorecard` | National KPIs (ASSI, import bill, self-sufficiency, groundwater, income) for a scenario, plan vs doing nothing |
| `run_simulation` | Re-runs the optimiser with custom levers and/or climate shocks; returns KPIs, trajectory and binding constraints |
| `solve_min_budget` | Least-cost mode: the smallest budget and instrument mix that meets the import target |
| `decompose_trade_target` | Turns "cut country X's product by N%" into tonnes and hectares, then re-runs the optimiser with the cut |
| `get_trade_dependencies` | Who India buys each pulse and edible oil from: shares, HHI, 6-year history |
| `get_fiscal_ledger` | Outlay by instrument and year, forex, power and urea returns, MROPI tiers |
| `get_district_allocation_audit` | Next-crop audit for a district and year |
| `get_sustainability_outlook` | ASSI forecast, pillars, stress tests and the state ranking |
| `get_district_profile` | Water, soil, climate and cropping profile of a district |
| `explain_district` | The decision trace for a district |
| `predict_yield` | Climate-aware yield for a crop and district, with an optional shock |
| `get_schema` | Lists the BigQuery tables and columns |
| `query_bigquery` | Read-only SQL over the BigQuery dataset (NL→SQL) |
| `web_grounded_search` | A Google Search-grounded answer with citations |
| `gemini_outlook_forecast` | The grounded 2027–34 outlook, run through the optimiser for the chosen plan horizon |
| `list_policies` | The shared library of saved policies, with headline numbers and whole-plan totals |
| `get_policy` | One saved policy (or the plan on screen): levers, KPIs for every plan year, budget totals, trade cuts and binding constraints, read from its snapshot |
| `create_policy` | Saves the plan on screen, or a plan with the levers the agent chose, as a named policy; the app then shows **Open policy** |
| `get_policy_document` | Reads the saved policy document of a policy or the plan on screen: its outline (version, sections, import verdicts, clauses, schemes, history) or one section's full text |
| `generate_policy_document` | Writes the policy document if none is saved, or with `regenerate` (and optional `instructions` from the conversation) writes the next version |
| `check_import_displacement` | Tests whether the plan's extra pulses and oilseeds will really cut imports: verdicts, risks and the clause that answers each |
| `find_existing_schemes` | Grounded search for existing central and state schemes the plan should converge with, with an optional focus |

`get_national_scorecard`, `get_fiscal_ledger`, `get_sustainability_outlook`, `explain_district` and
`get_district_allocation_audit` also take a `policy` argument, so the agent can analyse any saved policy (or the
plan on screen) from its snapshot without re-running the optimiser. `run_simulation`, `solve_min_budget` and
`create_policy` accept the plan horizon (`horizon_years`, 2–8) and several trade cuts.

**System rules.** The agent:
- never invents numbers: every figure comes from a tool, and it names the tool or model that produced it;
- cites sources for current events, prices and outlooks;
- states trade-offs and binding constraints honestly, and flags model limits;
- answers in plain language, in the language picked in the co-pilot;
- after a what-if, offers to save it as a policy, and saves only when the user asks or agrees (never claims a save
  that did not happen);
- stays on topic: the app and its data through its tools; related questions about Indian agriculture, water,
  climate, trade, the rural economy and schemes through Google Search, labelled as web sources; anything else is
  declined in one sentence. A fast English pre-filter declines obvious requests (code, films, sport, recipes…)
  without calling Gemini, unless they mention the domain; other languages and long questions go to Gemini's own
  judgement.

**Loop and limits**

| Setting | Value |
|---|---|
| Tool steps per question | At most 8 (`AGENT_MAX_STEPS`); the calls Gemini makes in one step run in parallel |
| Thinking budget | 1,024 tokens (`GEMINI_THINKING_BUDGET`); 128 on a retry (`GEMINI_FAST_THINKING_BUDGET`) |
| Stall detection (streamed calls) | No first output within 60 s (`GEMINI_FIRST_TOKEN_S`), or no new output for 45 s (`GEMINI_IDLE_S`) |
| Retries | 2 (`GEMINI_RETRIES`), each on the next model in the chain; the rest of the answer stays on that model |
| Wrap-up point | 240 s (`AGENT_DEADLINE_S`): no more tools, Gemini answers from what it has gathered |
| Hard cap | 480 s per answer (`AGENT_MAX_S`) |
| Non-streamed calls (outlook, grounded search) | 90 s each (`GEMINI_TIMEOUT_S`), retried on the next model |
| Per answer | At most 16 tool calls, 3 new optimiser solves, 3 web searches (including scheme searches), 2 saved policies and 1 new policy-document version |
| Answers in progress | 2 per visitor and 40 per instance (policy documents: 2 and 8) |

**Long answers without time-outs.** A question such as *"compare every Punjab district, check which schemes apply
and update the policy document"* can take minutes. A longer time-out would only make people wait longer for the same
failure. The earlier design had three weak points: one Gemini call that thought for too long, steps that added up
past the deadline, and a browser connection that took the work down with it when it dropped. The co-pilot and the
document writer now follow one pattern:

1. **The work is a job, not a request.** The answer runs as a background job on the server, and the browser only
   watches its event log. Every event has a sequence number. If the connection drops (a phone locks its screen, a
   proxy cuts a long stream, the tab reloads), the browser reconnects to `GET /api/jobs/ID/events?after=N` with a
   back-off of 1–8 s and carries on from the next event: nothing is repeated or lost.
2. **Gemini streams, and silence is the signal.** A call is judged by progress, not by its length: no first output
   within 60 s, or 45 s without new output, is a stall. A stalled, rate-limited (429) or failing (5xx) call is
   retried at once on the next model in the chain with a smaller thinking budget. Text already shown from a stalled
   call is withdrawn (`text_reset`), so an answer is never stitched from two attempts.
3. **Steps run in parallel.** A district profile and a web search no longer queue behind each other.
4. **A soft deadline, then a summary.** At 240 s the agent stops calling tools and asks Gemini to answer from what it
   has (tools switched off for that call). If Gemini fails after some steps, a composer writes the answer from those
   steps. The offline analyst answers, labelled as such, only when nothing was gathered.
5. **Results are saved whatever the browser does.** A finished answer goes to its chat session and a document to its
   versions, so someone who closed the tab finds both when they come back.
6. **The user stays in control.** The live feed shows that work is happening, and **■ Stop** cancels the job on the
   server.

Jobs live in the memory of the Cloud Run instance that runs them, and session affinity keeps a visitor on it. A
resume that reaches another instance is told the job is unknown; the browser then reloads the saved chat or document
instead.

**Grounding.** `web_grounded_search` and the outlook call Gemini with the Google Search tool. Both prompts, and the
co-pilot's own, include today's date and ask for the most recent sources, so "latest" means latest rather than
whatever the model remembers. Source links are taken from the response's grounding metadata (de-duplicated, at
most 8) and shown under the answer.

**NL→SQL sandbox.** `query_bigquery` accepts only a **single read-only SELECT** (or `WITH … SELECT`). It enforces:
- no `;`, a table allow-list and a forbidden-keyword check;
- a function check: a small SQL lexer (it understands strings and comments) blocks qualified or quoted function
  names such as `AI.GENERATE`, `ML.PREDICT` or project UDFs (only `SAFE.` is allowed), plus `EXTERNAL_QUERY`,
  `SESSION_USER`, `VECTOR_SEARCH`, change-history functions and file readers;
- on BigQuery, a dry run first: the query runs only if BigQuery reports a `SELECT` whose tables all belong to the
  app's own dataset;
- BigQuery's `maximum_bytes_billed` set to 200 MB and a 30 s job time-out;
- at most 200 rows returned.

Offline, the same SQL runs on DuckDB with external access disabled. *Example:* "Which 10 districts have the worst
groundwater stage and how much paddy do they grow?" returns Sangrur 310%, Malerkotla 302%, Jalandhar 281%,
Kapurthala 243%, Moga 233%…

**Languages and voice.** There are 11 languages: English, हिन्दी, தமிழ், తెలుగు, ಕನ್ನಡ, मराठी, বাংলা, ਪੰਜਾਬੀ, ગુજરાતી,
മലയാളം and ଓଡ଼ିଆ. Gemini answers in the chosen language. The browser's Web Speech API handles 🎤 input and
🔊 read-aloud with the matching Indian locale.

**Offline co-pilot.** Without Gemini credentials, a rule-based router maps common questions to the same tools. It
covers scenarios, districts and next crop, trade shocks and suppliers, least-cost budgets, the fiscal ledger,
budget, import-target and plan-horizon what-ifs (*"a 6-year plan"*), state rankings, the sustainability outlook,
the policy library: listing policies, describing a named policy, and creating one (*"save it as …"*, or *yes*
after its offer), and policy documents: reading a section (*"what does section G say?"*), writing or regenerating
the document (the template writer), the import-displacement check and the scheme list (marked *not verified*
offline). The demo keeps working, and its answers are
labelled `offline-rule-router`, or `local fallback (Gemini unavailable)` when Gemini fails mid-chat.

## 9. Deployment and operations

**Deploy:** one command in Cloud Shell, `bash deploy/deploy.sh`. A step-by-step guide for non-technical users is in
[DEPLOY.md](DEPLOY.md). The script:

1. Enables the Google Cloud APIs.
2. Creates the app's service account with least privilege:
   - `roles/aiplatform.user` (Gemini);
   - `roles/bigquery.jobUser` (queries);
   - write access on its own dataset only. If that grant fails, the script falls back to project-level BigQuery
     Data Viewer (read-only, so run logging is off) and says so.
3. Creates the free-tier storage: the Firestore `(default)` database if the project has none (`roles/datastore.user`
   for the service account), and a private bucket `<project>-agri-sus-store` (uniform access, public access
   prevention, no soft-delete copies, a 30-day lifecycle rule on cached `runs/`) on which only the service account
   gets `roles/storage.objectUser`. The bucket name is passed to the service as `STORE_BUCKET`. If Firestore cannot
   be created, documents go to the bucket too; if neither works, the script warns that saved policies will not
   survive a restart.
4. Loads BigQuery and trains the BigQuery ML models.
5. Optionally runs the Earth Engine climate pipeline.
6. Builds the container with Cloud Build (e2-highcpu-8) and pushes it to Artifact Registry.
7. Deploys Cloud Run: 2 vCPU, 4 GiB, CPU always on, startup CPU boost, concurrency 10, 600 s timeout, 1–5 instances,
   and session affinity. Each instance keeps a memory cache of recent plans, so affinity keeps a visitor on the
   instance that holds them; any other instance loads them from Cloud Storage instead of solving again.

**Persistence.** `backend/app/store.py` picks the store when the app starts: Firestore for documents when it is
reachable, otherwise the Cloud Storage bucket, otherwise local disk (offline runs and tests). Plan snapshots go to the
bucket, or local disk. Every document key and id is validated, writes go snapshot first and document second, and
each list has a version stamp (collection `agrisus_meta`) so every instance can cache the list and re-check it with
a single read. `/api/health` and the **📁 Policies** window show which store is live. Redeploys keep everything:
the data lives outside the container.

**No API keys.** On Cloud Run the app reaches Gemini through Vertex AI with its service account. No Gemini key is
stored anywhere.

**Settings** are environment variables on the Cloud Run service. They are listed in
[DEPLOY.md](DEPLOY.md#settings-you-can-change): the model, thinking budget, stall limits and deadlines, yield engine
and minimum instances.

**Health and status**
- `/api/health`: is the service up, and which settings are configured.
- `/api/meta`: what is actually live:
  - `data_backend` (BigQuery or local);
  - `gemini.backend` (Vertex AI);
  - `yield_engine` (the active engine and its scores);
  - `climate_source`.
- **Logs:** Cloud Run → Logs. Every run is also in BigQuery: `simulation_runs`, `simulation_decision_traces` and
  `policy_budget_ledger`.
- **Saved data:** Firestore → Data (collections `policies`, `chats`, `agrisus_meta`) and the bucket
  `<project>-agri-sus-store` (`policies/`, `runs/`). The deploy script prints both links.

**Cost control**
- Rate limits per visitor IP, per 10 minutes:
  - 40 new optimiser solves, including the ones the co-pilot runs (cached plans are free);
  - 30 co-pilot questions;
  - 6 new policy-document versions (30 when Gemini is off; opening a saved document is free);
  - 10 outlooks.
- A cap on paid Gemini calls per instance across all visitors (`GEMINI_MAX_REQUESTS_PER_10MIN`, default 200).
- Per co-pilot answer: at most 16 tool calls, 3 web searches and 3 new solves.
- Caches for plans and the outlook; solved plans are shared across instances through Cloud Storage. Policy documents
  are written once and saved, and their grounded research (schemes, duties) is reused for 14 days.
- Saving, renaming and deleting policies: 60 writes per IP per 10 minutes, at most 300 policies and 200 chats. A
  policy must be in Deleted before it can be purged, so a single request can never wipe a live policy.
  Firestore and Cloud Storage stay inside the always-free tier (listing costs one read while nothing has changed).
- Per policy document: one streamed Gemini writer call (retried on a stall, 8 minutes at most in total) and two
  grounded searches, only when a new version is written; the last 20 versions are listed, and older text stays in
  BigQuery.
- At most 2 answers and 2 documents in progress per visitor, and 40 answers and 8 documents per instance.
- The BigQuery byte cap and job time-out on agent SQL.
- `--min-instances 0` after judging, to scale to zero.
- `deploy/teardown.sh` removes everything.

## 10. Security, privacy and responsible AI

**Security and privacy**
- **No secrets in code or images.** The app uses Vertex AI through the service account, and `.env` files are excluded
  from the deploy bundle.
- **Least privilege.** The service account can use Gemini, run BigQuery jobs, write only to its own dataset, use
  Firestore, and read and write objects only in its own private bucket.
- **Saved policies and chats.** Ids are random and checked against strict patterns before any lookup, and storage
  keys cannot leave their prefix, so a crafted id cannot reach another file or document. Names, notes and messages
  are length-capped and stripped of control characters, and they are shown as plain text. The library is shared and
  needs no login (the UI says so); deletes are soft (restorable) until someone chooses *Delete permanently*.
- **Read-only, bounded SQL from the agent:** a single SELECT, an allow-list, a function check, a BigQuery dry run,
  a byte cap, a time-out and a row cap.
- **Hardened web layer.** A strict Content-Security-Policy, `frame-ancestors 'self'`, `nosniff`, HSTS, Referrer- and
  Permissions-Policy headers; a 512 KB request-body limit; validated scenario and state names; API docs hidden
  unless `API_DOCS=1`; patched FastAPI/Starlette.
- **Prompt-injection containment.** Tool results and web pages are treated as data, not instructions. Answers are
  rendered without raw HTML or images, so injected text cannot make the browser call a tracking URL.
- **Bounded Gemini outputs.** Outlook shocks are clamped to plausible ranges before they reach the optimiser.
- **No personal data.** All data is public and aggregated to district level. Voice is handled by the browser's
  speech service, and no audio reaches the Agri Sus server. Co-pilot conversations are saved as shared chat sessions
  (the co-pilot says so and asks users not to enter personal information; any session can be deleted). No IP
  addresses or user identifiers are stored with them.

**Responsible AI**
- **Grounded numbers.** Gemini must take every figure from a tool, and policy documents are written from a JSON fact
  sheet. Revision instructions are treated as editorial guidance only: they cannot add numbers, change the rules or
  reach the system prompt, and they are length-capped and stripped of control characters.
- **Cited sources.** Web facts come with source links.
- **Transparency.** The model tag on every answer and `/api/meta` show which model and data are live. Fallbacks are
  labelled, never hidden.
- **Humans decide.** Agri Sus advises planners. Plans are explained (shadow prices, reduced costs, rejected
  alternatives) so they can be challenged.
- **Farmers protected by design.** No district's farm income falls below today: this is a hard constraint, not a
  goal.

## 11. Testing

| Test | What it checks | Command |
|---|---|---|
| **Smoke test** (48 checks) | The API end to end: all-India coverage, map shapes, the glide path, the run cache, levers and lever status, scenarios, input validation, instrument budgets, MROPI tiers, least-cost mode and its whole-plan total, trade data, single and multi-country shocks, a 6-year plan, next crop, decision traces, co-pilot streaming as a resumable job (ordered events, replay from any point, unknown and malformed job ids), the topic guard, saving and re-opening a policy with identical numbers and no re-solve, rename / delete / restore / purge, chat sessions (saved with how each answer was built), the co-pilot creating a policy, the policy document (sections A–H, saved and reopened without a rewrite, regenerated with instructions as version 2, read by the co-pilot, purged with its policy), the import-displacement check and document input validation | `python scripts/smoke_test.py http://localhost:8080` |
| **Browser end-to-end** (87 checks) | Real Chrome: map and basemap, Do nothing / plan / Difference, ▶ animation, leaderboard and state zoom, the district card and next crop, lever tags and the "Last change" strip, scenarios, the budget tab and MROPI layer, least-cost mode and its plan total, instruments, single and multi-country trade shocks and the import-displacement card, the plan-horizon slider, the method modal and tour, the policy document (sections A–H, tables, printing exactly one copy as a multi-page PDF, reopening without a rewrite, regenerating with instructions), co-pilot answers with plain-language steps folded into *Worked for … · N steps* (no tool names or JSON) and what-ifs that move the dashboard, the co-pilot reading section G and **📄 Open document**, saving / opening / asking about / deleting a policy, the co-pilot creating a policy and **Open policy**, chat sessions, no console errors | `cd tests/e2e && npm install && URL=http://localhost:8080/?notour node e2e.mjs` |
| **Layout** | Nine screen sizes, from a 390×844 phone to 1920×1080: KPI cards fit, the map and scorecard stay on screen, India is never clipped | `URL=http://localhost:8080/?notour node layout.mjs` |
| **Security** (15 tests, offline) | The SQL sandbox (blocked functions, comment and string tricks, the BigQuery dry-run gate), scenario and state validation, per-answer solve budgets, security headers, the body-size limit, cache headers, rate-limit keys, storage key and policy/chat id validation (no path traversal), bounded names and messages, the shared list cache re-checking across instances, id checks on the policy and chat endpoints, the rule that only a deleted policy can be purged, document inputs (run id, scope, language, instructions, storage keys) and the document-writing rate limit | `.venv/bin/python backend/tests/test_security.py` |
| **Resilience** (12 tests, offline) | A scriptable fake Gemini behind a real server: a slow first token retried on a faster model (which then keeps the answer), a stalled stream withdrawn and retried, the soft deadline wrapping up without tools, an outage after some steps summarised from them, a total outage answered by the offline analyst, **Stop**, a dropped connection resumed from the last event, parallel steps with a plain-language feed, the topic guard, shared document jobs and their errors, and a mostly written document draft kept | `.venv/bin/python backend/tests/test_agent_resilience.py` |
| **Benchmark** | Latency of every endpoint, cached and new optimiser runs, a load test, policy documents (first write, reopen, regenerate, Hindi) and the import check, and co-pilot timings. Results for the live site: [PERFORMANCE_REPORT.md](PERFORMANCE_REPORT.md) | `python scripts/benchmark.py https://<cloud-run-url> [all]` |

The suites run without Google Cloud: the security and resilience tests use a throw-away store, so they never touch
saved policies or chats. The live activity feed was also checked in a real browser against the scripted stand-in for
Gemini: the thinking headline, parallel steps with their timers and usual duration, then the folded summary. The policy-document window was checked on a phone-sized screen as well (the whole window scrolls,
with a close button that stays in view).

## 12. Limitations

- **Yield model.** It is for directional climate shocks over a multi-year plan, not single-season district yield
  forecasts (section 7.1).
- **Assumptions.** Instrument parameters (kit cost and yield uplift, price-support admin cost, power and urea subsidy
  per unit) come from the literature and are shown in the Budget tab. Forex savings are valued at landed import
  prices, not as fiscal receipts.
- **Trade shocks** are replaced by domestic production only. Other suppliers are listed, but the price effect of
  re-sourcing is not modelled.
- **Crop scope.** 16 crops are reallocated; cotton and sugarcane stay at today's area.
- **Shocks** are one-year yield shocks. Use the stress-test band to see how the plan copes with surprises.
- **Groundwater caps** are soft and limited by the budget. The water pillar treats any district above 200%
  extraction as fully stressed.
- **Data vintages differ.** For example, the Agricultural Census is from 2010–16, while yields run to 2024-25.
  Missing district context takes the state median and is flagged.
- **Gemini outlooks are uncertain.** Each year carries a confidence level, and the values are clamped.
- **Long plans.** The CMIP6 district projections end in 2030; plan years after that use each district's 2026–30
  projected mean.
- **Import-displacement check.** One year of DGCI&S imports (FY2024-25), static unit values, the 2026-27 MSPs and
  today's duty; no price elasticity or world-price response. The *low case* is a product-matching bound, not a
  forecast.
- **Policy documents.** Schemes and duties found by grounded search are cited but not checked by a person (offline,
  the reference list is marked *not verified*). Without Gemini, documents are in English and revision instructions
  cannot be applied. Every number comes from the plan, so a document is only as good as the model behind it.
- **Answers in progress live in instance memory.** A running answer or document survives a dropped connection, not a
  restart of its Cloud Run instance (a redeploy, for example). Finished work is saved; an answer interrupted that
  way can simply be asked again.
- **Shared library, no login.** Anyone with the link can see, rename or delete saved policies and chats (deleted
  policies are restorable until purged, and only a policy already in Deleted can be purged), or regenerate a policy
  document (every version's instructions stay in its history). A production deployment would add sign-in (for
  example Identity-Aware Proxy).
- Agri Sus is **decision support**: its outputs are scenarios for planners, not instructions to individual
  farmers.

The full list is in the README's [Honest limitations](../README.md#honest-limitations) and the
[model card](MODEL_CARD.md).

## 13. Glossary

| Term | Meaning |
|---|---|
| **ASSI** | Agri Sustainability & Self-reliance Index, 0–100 (section 7.5) |
| **MROPI** | Public return per rupee of policy outlay in a district (section 7.3) |
| **SSR** | Self-sufficiency ratio = production ÷ (production + imports − exports) |
| **CWAR** | Crop water availability ratio: irrigation need ÷ (safe groundwater + canal supply) |
| **Stage of extraction** | Groundwater pumped as a % of annual recharge (CGWB). Above 100% is over-exploited |
| **HHI** | Herfindahl–Hirschman index: concentration of supplier or crop shares, 0–1 |
| **PMP** | Positive mathematical programming: calibrates the model so today's crop mix is optimal for farmers |
| **MCPF** | Marginal cost of public funds: each ₹1 of public money costs society ₹1.25 |
| **BCM / MCM** | Billion / million cubic metres of water |
| **Kharif / Rabi** | Monsoon season (June–October) / winter season (November–March) |
| **LGD code** | Government of India's Local Government Directory code for a district |
| **DBT** | Direct benefit transfer (cash to farmers) |
| **MSP** | Minimum support price announced by the Government of India |
| **PM-AASHA** | The national scheme for price support and price-deficiency payments |
| **MPMV** | *Mera Pani Meri Virasat*, Haryana's payment for shifting away from paddy |
| **Glide path** | The straight-line path from today's crop mix to the final-year mix (2030 by default) |
| **Policy** (saved) | A named plan in the shared library: its levers plus a snapshot of the solved result |
| **Shadow price** | How much better the plan would get if a constraint were relaxed by one unit |
| **Reduced cost** | How much worse the plan would get if one hectare of a rejected crop were forced in |
| **WMAPE** | Weighted mean absolute percentage error, used to compare the yield models |

## 14. Code map

| Path | What it contains |
|---|---|
| `backend/app/main.py` | FastAPI app: API routes, start-up warm-up, rate limits, streaming, policy, chat, document and job endpoints, static web app |
| `backend/app/jobs.py` | Background jobs: an event log with sequence numbers, resumable Server-Sent Events with keep-alives, per-visitor and per-instance limits, Stop |
| `backend/app/service.py` | Run cache, plan snapshots, scenario runs, solve-time estimate, BigQuery logging, SQL sandbox |
| `backend/app/store.py` | Storage layer: Firestore / Cloud Storage / local disk, key validation, the shared list cache |
| `backend/app/policies.py`, `chats.py` | The saved-policy library (save, open, rename, delete, restore, purge, whole-plan totals) and chat sessions |
| `backend/app/optimizer.py` | OR-Tools LP, ASSI, district score, stress tests, ledger, explanations |
| `backend/app/agent.py` | Gemini agent: system rules and topic guard, 22 tools, the streamed loop with parallel steps, retries and wrap-up, the plain-language activity feed, offline co-pilot |
| `backend/app/documents.py` | Policy document agent: fact sheet, import-displacement check, grounded scheme and duty research, Gemini writer and template, versions, storage, BigQuery log |
| `backend/app/gemini.py` | Vertex AI client, streaming with stall detection, retries along the model chain, Google Search grounding, outlook |
| `backend/app/trade.py` | Supplier dependencies, trade-shock decomposition, HHI |
| `backend/app/yield_model.py`, `data.py` | Yield prior, BigQuery ML factor loading and promotion rule, data store |
| `backend/app/scenarios.py`, `config.py` | Scenario definitions; settings from environment variables |
| `frontend/src/` | React app: `App.tsx`, map (`MapView`), tabs, district card, co-pilot with its live activity feed and chat sessions, policy library and save dialog (`Policies.tsx`), policy document window (`DocumentModal.tsx`) |
| `bigquery/` | Schema, BigQuery ML models, example analytics, Earth Engine climate SQL |
| `scripts/` | Data pipeline, Earth Engine export, smoke test |
| `deploy/` | `deploy.sh`, `load_bigquery.py`, `grant_dataset_access.py`, `make_bundle.sh`, `teardown.sh` |
| `backend/tests/` | Security and resilience suites, and the scriptable fake Gemini they use (`fake_gemini.py`) |
| `tests/e2e/` | Browser end-to-end and layout tests |
| `data/` | Raw → processed data and reference tables ([data/README.md](../data/README.md)) |
