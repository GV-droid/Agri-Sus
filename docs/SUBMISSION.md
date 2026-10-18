# AI Builder Cup 2026: submission kit

**Deadline: Oct 18, 2026** (check the exact time zone on aibuildercup.com)

## Checklist
- [ ] Team of 2–4 members, all working professionals (students are not eligible)
- [ ] Deployed link: follow [DEPLOY.md](DEPLOY.md) (`bash deploy/deploy.sh` in Cloud Shell) and copy the Cloud Run URL
- [ ] Check the deployment:
  - `/api/health` should return `"gemini": true`, `"data_backend": "bigquery"` and a `"store"` with `"shared": true`
    (Firestore + Cloud Storage, so saved policies and chats survive restarts). These are the *configured* settings.
  - `/api/meta` shows what is actually live:
    - `data_backend` should say `BigQuery`;
    - `gemini.backend` should say `Vertex AI`;
    - it also shows the active yield engine (BigQuery ML champion/challenger) and the climate source.
- [ ] Run `python scripts/smoke_test.py https://<cloud-run-url>` (40 checks), and the browser suite with `URL=https://<cloud-run-url>/?notour` (80 checks; see README → Verify it works). Both create test policies and chats ("Smoke test policy", "E2E …"): delete them afterwards in **📁 Policies** (Deleted tab → Delete permanently) and **💬 Chats**
- [ ] Save two or three good-looking policies for judges to open (for example *El Niño-proof 6-year plan*, *Least cost 25% import cut*, *Indonesia + Malaysia palm-oil halt*)
- [ ] Keep `MIN_INSTANCES=1` while judges may visit (no cold starts, ≈ $3.6/day). Afterwards, redeploy with `MIN_INSTANCES=0 SKIP_BIGQUERY=1 bash deploy/deploy.sh`
- [ ] Public GitHub repo containing this README. Replace `YOUR_GITHUB_USER` in the "Open in Cloud Shell" button in [DEPLOY.md](DEPLOY.md)
- [ ] Demo video **strictly 3 to 4 minutes long** (official rule: shorter or longer breaks it). The script, timing,
  numbers to check and recording steps are in [DEMO_VIDEO_SCRIPT.md](DEMO_VIDEO_SCRIPT.md) (runs about 3:43)
- [ ] A comprehensive presentation deck explaining the **solution architecture and business case** (official rule).
  Name the problem statement and the theme (*Sustainability & Social Impact*). Outline below; diagrams and technical
  detail are in [HOW_IT_WORKS.md](HOW_IT_WORKS.md)

## Demo video
Follow [DEMO_VIDEO_SCRIPT.md](DEMO_VIDEO_SCRIPT.md): ten scenes in about 3:43. For each scene it gives the screen
actions, the voice-over and a caption naming the Google product in use. It also covers what to pre-warm, the numbers
to check on screen, how to record and edit without special software, and a YouTube description with chapters.

## Deck outline (11 slides)
1. Title, team and theme
2. Problem statement, with numbers (imports, groundwater, farmer income)
3. Who uses it: state agriculture departments, NITI Aayog, CACP, the Department of Expenditure
4. Solution overview (screenshot)
5. Architecture on Google Cloud: each Google product and what it does (diagram in [HOW_IT_WORKS.md §4](HOW_IT_WORKS.md#4-architecture-on-google-cloud); product table in README → Built on Google Cloud)
6. Gen AI depth: the agent's 18 tools, grounding, NL→SQL, outlook → shocks, creating and discussing saved policies in conversation
7. Optimisation and explainability: constraints, shadow prices, decision traces, next-crop ranking
8. Business case: what the policy costs and returns. Three instruments; ₹6,000 cr/yr returns 8.2× in forex, power and urea savings; least cost for a 25% import cut is ₹10,727 cr/yr by 2030, ₹26,246 cr in total over 2027–30, returning ₹1.47 lakh cr; MROPI tiers show which districts to fund first; free-tier running costs for the policy library
9. Results: KPIs under the baseline, stress tests, Indonesia halt, least-cost budget
10. Data and responsible AI: public data (India Data Portal), honest model metrics with champion/challenger, human in the loop
11. Impact and roadmap: block-level resolution, farmer-facing advisories, PM-AASHA / crop-diversification scheme targeting

## Judging alignment
| Criterion (weight) | Evidence |
|---|---|
| Technical merit / Gen AI (40%) | A Gemini function-calling agent with 18 tools that can also create, read and compare saved policies; Search grounding with citations; grounded forecasts turned into structured JSON and fed into the LP; NL→BigQuery SQL. Saved policies and chats in Firestore, plan snapshots in Cloud Storage (free tier). BigQuery ML: a `LINEAR_REG` twin plus a `BOOSTED_TREE` challenger, registered in the Vertex AI Model Registry, with automatic champion/challenger promotion. Earth Engine climate pipeline; Cloud Build → Artifact Registry → Cloud Run in one command |
| Problem alignment & impact (25%) | National import bill, groundwater crisis, farmer income floor and fiscal cost; 752 real districts across every state/UT of India, using public data. Answers the questions ministries ask: what it costs, what it returns, which districts to fund first, and what happens if a supplier stops selling |
| Innovation (25%) | An LLM agent driving a calibrated 129k-variable all-India optimizer. It has a no-switch-back glide path, a policy-instrument mix with a least-cost mode, a fiscal ledger and MROPI, bilateral trade shocks, next-crop prediction, live grounded climate and price scenarios, stress-tested sustainability forecasts, lever tags read from LP duals, and multilingual voice |
| UX (10%) | Guided tour, plain-language tooltips, animated map with do-nothing comparison, nine map layers, state leaderboard, one-click policy brief in 11 languages, voice in/out, streamed agent steps in plain language, a saved-policy library that re-opens plans instantly, saved chat sessions, a 2–8-year plan horizon, multi-country trade shocks, a no-code deploy guide |
