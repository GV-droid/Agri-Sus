# Agri Sus: demo video script (3 min 48 s)

> **Rule from the official Terms & Conditions:** the video must be **strictly three to four minutes long**.
> A video under 3:00 or over 4:00 breaks the rule. This script runs about **3:48**, leaving about 12 seconds of
> margin before the 4:00 limit. Check the length of the final export before you upload it.

How the video earns points (official weights):

| Criterion | Weight | Where the video shows it |
|---|---|---|
| Technical merit & Gen AI | 40% | Scenes 7–9: a Gemini agent with 18 tools, Google Search grounding with citations, a policy created and saved by conversation, BigQuery from plain English, voice in Hindi. Scene 3: an optimiser with about 129,000 variables. Scene 9: the Google Cloud architecture, including Firestore and Cloud Storage |
| Problem alignment & impact | 25% | Scenes 1, 3 and 4: imports, groundwater and farmer income in measurable numbers, plus the total cost and return of a plan |
| Innovation & creativity | 25% | Scenes 4–7: least-cost mode, multi-country trade shocks, the next-crop decision, explanations of every choice, saved policies the agent can discuss |
| User experience & design | 10% | The whole video: map, ▶ animation, plain-language agent steps, a policy library that re-opens instantly, one-click brief, voice |

---

## Before you record (about 25 minutes, once)

1. **Redeploy with the latest files** (see [DEPLOY.md → Updating the site](DEPLOY.md#updating-the-site-after-a-code-change)).
   Then open `https://YOUR-URL/api/meta` and check that:
   - `gemini.backend` says `Vertex AI`;
   - `data_backend` says `BigQuery`.
2. **Keep one server warm.** Use `MIN_INSTANCES=1`, the default. Open the site **10 minutes before recording**: the
   server pre-solves every preset scenario at start-up (about 7 minutes; seconds if an earlier server already solved
   them, because solved plans are kept in Cloud Storage), so clicking a preset then takes about 2 seconds. Briefs are
   cached in the memory of one server; the deploy script turns on session affinity so your clicks stay on it. Don't
   run load tests just before recording; if you did, wait 15 minutes so the extra servers shut down.
3. **Pre-warm the slow features.** Each one is cached once it has run:
   - Click the **✦ Gemini live outlook** scenario once and wait for the map to update (up to about 1½ minutes the
     first time). The outlook is cached for 6 hours.
   - **Reload the page** so you are back on the default plan.
   - Pick **Punjab** in the *Zoom to state* box and click **✦ Policy brief**. Wait for the English brief.
   - Then choose **हिन्दी** and wait for the Hindi brief.
   - Close the brief and set the state box back to *All India*. Briefs are cached for that exact plan, state and
     language until the server restarts. That is why scene 8 starts with a reload.
   - Pre-solve scene 4: click **Least cost to hit target**, then **Run optimizer**, and wait for the new budget path
     (about 45 seconds). It is then cached, so in the take it returns in about a second. **Reload the page** after.
   - Pre-solve scene 5: in the trade-shock picker add **Indonesia** and **Malaysia**, product **palm oil**, 50%, then
     **Run optimizer** and wait. **Reload the page** after.
   - **Prepare the policy library for scene 7.5:** open **📁 Policies** and delete any test policies (Deleted tab →
     *Delete permanently*). Then save one policy to open on camera: set **Plan horizon** to **6 yrs**, pick
     **2028 El Nino drought**, **Run optimizer**, wait, click **💾 Save as policy** and name it
     **El Niño-proof 6-year plan**. **Reload the page** after.
4. **Rehearse the co-pilot questions in scene 7 once.** Gemini's wording changes from run to run, so make sure
   you like the answers. Co-pilot answers are *not* cached, but the rehearsal also pre-solves the plans behind
   questions 7b and 7c, so in the take they are usually much faster than the first time. After the rehearsal, delete
   the policy that question 7c created (**📁 Policies** → Delete, then *Delete permanently* in the Deleted tab) and the
   rehearsal chats (**💬 Chats** → 🗑).
5. **Set up the browser:**
   - Google Chrome in a **Guest window**, so no extensions and no bookmarks bar.
   - Window **1920 × 1080**, or 1440 × 900 on a laptop, with zoom at 100%.
   - Open `https://YOUR-URL/?notour`. The `?notour` part skips the welcome tour.
   - Allow the **microphone** when Chrome asks; the Hindi voice question in scene 7 needs it.
   - Mute notifications: on macOS, turn on Focus / Do Not Disturb.
6. **Reset between takes:** reload the page and click **＋ New chat**. That returns to the baseline plan with an
   empty chat (earlier chats stay under **💬 Chats**).

### Numbers to check on screen before you record

The narration uses the values below, taken from the default settings. After your redeploy, `/api/meta` →
`yield_engine.active` may switch to the BigQuery ML boosted tree, which only happens if it wins the hold-out test.
In that case a few numbers can shift slightly: **read them off your screen and adjust the line**.

| Scene | Where to look | Reference value |
|---|---|---|
| 3 | Scorecard, year 2030 | Do nothing **69.7**, Agri Sus plan **76.0** |
| 3 | KPI ribbon, 2030 | Imports **$27.1 bn → $21.5 bn** (−$5.6 bn). Groundwater **8.7 BCM**/yr saved. Pulses self-sufficiency **78% → 92%**. Farmer income **100.1%** of today |
| 3 | 🌍 Sustainability forecast, stress tests | Worst year under an unplanned El Niño: **71.6**, still above doing nothing |
| 4 | 💰 Budget & returns | ₹6,000 cr split into **₹3,299 cr** transfers, **₹1,789 cr** seed & FPO kits and **₹911 cr** price support. Public return **8.2×** |
| 4 | Least cost to hit target | **₹10,727 cr**/yr by 2030 (₹2,537 → 5,131 → 7,851 → 10,727 cr). Plan total **₹26,246 cr** over 2027–2030, returning **₹1.47 lakh cr** |
| 5 | 🚢 Trade shocks | Indonesia: **49%** of palm oil, **3,882 kt**. Malaysia supplies **31%**. Read the volume to replace and the substitute crops off the tab |
| 6 | Sangrur district card | Groundwater at **310%** of recharge. Paddy **−10,172 ha**, moong **+10,172 ha**. **50 MCM**/yr of water saved. Score **19.1 → 21.7**. Next crop for Kharif 2027: **moong**; closest rejected alternative: **sesame** |
| 7 | Co-pilot, question 2 | About **₹16,300 cr**/yr by 2030 (₹3,745 → 7,629 → 11,842 → 16,288 cr). The 25% target is missed by only about $0.07 bn, and no district loses income |
| 7 | Co-pilot, question 3 | On BigQuery: Jaisalmer (Rajasthan) 322%, Sangrur 310%, Malerkotla 302%, Jalandhar 281%… The order can differ if Gemini filters differently |

---

## The script

Each scene lists what to do on **🎬 screen**, what to say (**🎙 voice-over**), and an optional **🏷 caption** to add
while editing. The voice-over is about 460 words (numbers take longer to say), roughly 3½ minutes at a calm 140
words a minute; the rest is room for pauses and on-screen action.

### Scene 1 · The problem (0:00 – 0:20)

🎬 **Screen**
- The live site, full India map.
- Map layer **Groundwater use vs recharge**; map mode **Do nothing**.
- Pick **Punjab** in *Zoom to state*: the map flies to the red districts. Hold for 2 seconds, then go back to *All India*.

🎙 **Voice-over**
> "India spends about twenty-three billion dollars a year importing pulses and edible oils. Meanwhile, Punjab's paddy
> fields pump groundwater up to three times faster than rain refills it. Shifting crops could fix both, if farmers
> don't lose income and India still grows enough grain."

🏷 **Caption:** *$23 bn imports (FY 2024-25) · Punjab pumps up to 310% of recharge*

### Scene 2 · Meet Agri Sus (0:20 – 0:33)

🎬 **Screen**
- Switch the layer to **District sustainability score** and the mode to **Agri Sus plan**.
- Slowly move the mouse over the left panel (scenarios and levers, including **Plan horizon**), then over the
  **Co-pilot** on the right.

🎙 **Voice-over**
> "Agri Sus is an AI co-pilot for agriculture planners. It plans crop shifts in every district of India, over two to
> eight years, with a Google OR-Tools optimiser and a live Gemini agent."

🏷 **Caption:** *752 districts · 2027 onwards, 2–8-year plans · built on Google Cloud*

### Scene 3 · The plan and the forecast (0:33 – 1:02)

🎬 **Screen**
- Press **▶** on the year bar: the map animates 2027 → 2030.
- Click **Do nothing**, then **Agri Sus plan**, then **Difference**: the green shows where the plan helps.
- Point at the scorecard (**69.7 → 76.0**) and the KPI ribbon.
- Open the **🌍 Sustainability forecast** tab and point at the stress-test band.

🎙 **Voice-over**
> "Press play and the plan unfolds. By 2030, our sustainability index reaches 76, against 70 if we do nothing.
> Imports fall by five-point-six billion dollars a year, groundwater pumping by 8.7 billion cubic metres, and pulse
> self-sufficiency rises from 78 to 92 percent. No farmer earns less than today, even under a surprise El Niño."

🏷 **Caption:** *Google OR-Tools · 129,000-variable linear program · optimal plan for 752 districts*

### Scene 4 · What it costs, what it returns (1:02 – 1:27)

🎬 **Screen**
- Open **💰 Budget & returns**: the policy mix, the fiscal ledger and the MROPI tiers.
- Click **show on map**: the map switches to *Public return per rupee*.
- In the left panel click **Least cost to hit target**, then **Run optimizer**. Show the new budget path and the
  **plan total** line. If you pre-solved it (*Before you record*, step 3) it returns in about a second; otherwise the
  solve takes about 45 seconds, so cut the wait in editing.

🎙 **Voice-over**
> "The optimiser splits a six-thousand-crore budget between cash transfers, seed kits and price support, and savings
> in forex, power and fertiliser return eight times the outlay. The cheapest way to cut imports by a quarter? Twenty-six
> thousand crore in total over four years, returning almost one-and-a-half lakh crore."

🏷 **Caption:** *Fiscal ledger · MROPI = public return per rupee · least-cost mode with whole-plan total*

### Scene 5 · A trade shock (1:27 – 1:43)

🎬 **Screen**
- Click **Reset**. In the trade-shock picker select **Indonesia** and **Malaysia**, product **palm oil**, 50%, click
  **Add**, then **Run optimizer** (pre-solved, so it is instant).
- Open **🚢 Trade shocks**: volume to replace, substitute crops, other suppliers and supplier concentration.

🎙 **Voice-over**
> "What if Indonesia and Malaysia, four-fifths of our palm oil, both cut supply? Using India Data Portal trade data,
> Agri Sus re-plans which crops replace it at home, where, and who else can supply the rest."

🏷 **Caption:** *India Data Portal · DGCI&S country-wise imports · multi-country, multi-product shocks*

### Scene 6 · Every decision explained (1:43 – 2:03)

🎬 **Screen**
- Click **Baseline (CMIP6 SSP2-4.5)**.
- Pick **Punjab** in *Zoom to state* and click **Sangrur**: the district card opens on **Plan & why**. Sangrur is in
  south-central Punjab, south of Ludhiana; hover over districts to see their names. Point at the moves, the reasons
  and the rejected alternatives.
- Click **🌱 Next crop**.

🎙 **Voice-over**
> "Every decision is explained. Sangrur pumps three times its recharge. The plan swaps ten thousand hectares of paddy
> for moong, saving fifty million cubic metres of water a year, and shows why and what it rejected. Next Kharif, it
> recommends moong over sesame."

🏷 **Caption:** *Decision trace · shadow prices & reduced costs from the optimiser*

### Scene 7 · The live Gemini agent (2:03 – 3:00)

This is the most important scene (40% of the score). Record each question as its own clip. Keep 1–2 seconds of
the streamed steps on screen, then cut the waiting time. If you speed a wait up instead, show a small
**⏩ sped up** badge. Judges value honesty.

**7a · Grounded outlook (chat must be empty, so reload first)**

🎬 **Screen**
- Click the suggestion chip **"What is the latest IMD / ENSO outlook and edible-oil import duty? Re-run the plan with it."**
- Show the plain-language steps streaming (*Searching Google for current facts*, *Building a grounded climate &
  price outlook*), then the answer.
- Scroll to the **citation links** under the answer.

🎙 **Voice-over**
> "Now the live agent: Gemini on Vertex AI, with eighteen tools. I ask about today's monsoon outlook and import duty.
> Gemini searches Google, cites its sources, turns the outlook into climate and price shocks, and re-runs the
> optimiser with them."

🏷 **Caption:** *Gemini on Vertex AI · function calling · Grounding with Google Search*

**7b · A hard what-if**

🎬 **Screen**
- Type: **"If Indonesia halts palm-oil exports and El Niño hits in 2028, what is the least annual budget that still
  cuts imports 25%, with no farmer losing income?"**
- Show the steps (*Finding the least-cost budget* or *Running the optimizer for this what-if*), then the answer,
  then the map and sliders updating.

🎙 **Voice-over**
> "Something harder: a palm-oil embargo plus an El Niño, at least cost. Gemini sets the levers itself, and the answer,
> about sixteen thousand crore a year, moves the dashboard with it."

**7c · A policy created by conversation**

🎬 **Screen**
- Click **＋ New chat**, then the suggestion chip **"Create a policy that cuts the pulse and edible-oil import bill 40%
  at least cost over 6 years, and save it."**
- Show *Saving the policy* → *Policy saved ✓* and the **📂 Open policy** button. Click it: the banner shows the
  policy name and *rendered from its snapshot, no re-run*, and the year bar runs to 2032.

🎙 **Voice-over**
> "Planners can simply ask for a policy. Gemini sets every lever, solves a six-year plan, and saves it to the shared
> library in Firestore and Cloud Storage, ready to open."

🏷 **Caption:** *Gemini function calling → create_policy · Firestore + Cloud Storage (free tier)*

**7d · BigQuery in plain English** *(optional: drop it if you run long)*

🎬 **Screen**
- Type: **"Which 10 districts have the worst groundwater stage and how much paddy do they grow? Use BigQuery."**
- Show the *Reading the BigQuery tables* and *Querying BigQuery* steps, then the table.

🎙 **Voice-over**
> "It also writes read-only SQL on BigQuery to answer ad-hoc questions straight from the data."

🏷 **Caption:** *Natural language → BigQuery SQL (read-only, sandboxed)*

**7e · Hindi, by voice**

🎬 **Screen**
- Set the co-pilot language box to **हिन्दी**, press **🎤** and say:
  **"पंजाब में धान की जगह कौन सी फसल उगाएँ?"**
  - Read aloud as: *"Punjab mein dhaan ki jagah kaun si fasal ugaayein?"*
  - Meaning: *Which crop should Punjab grow instead of paddy?*
  - If speech recognition struggles, paste the Hindi text instead.
- When the Hindi answer appears, click **🔊 Listen** and let 3 seconds play.

🎙 **Voice-over**
> "And it speaks the farmer's language: eleven Indian languages, by voice."

🏷 **Caption:** *11 Indian languages · voice in and out*

### Scene 8 · Saved policies and the brief (3:00 – 3:18)

🎬 **Screen**
- *Before this clip:* reload the page. Scene 7 changed the plan, and the pre-warmed brief belongs to the default plan.
- Click **📁 Policies** in the header: the library lists both policies with their totals. Click **Open** on
  **El Niño-proof 6-year plan**: the map, levers and tabs come back in about a second. Close it again by reloading.
- Pick **Punjab** in *Zoom to state* and click **✦ Policy brief**.
- Choose **हिन्दी**; it is pre-warmed, so it appears at once. Scroll it slowly. Point at **Print / PDF**.

🎙 **Voice-over**
> "Every policy is saved for the whole team and re-opens instantly, without re-solving. And one click turns any
> plan into a ministerial brief, in any of those languages."

🏷 **Caption:** *Policy library · Gemini-written brief from the run's own numbers*

### Scene 9 · Built on Google Cloud (3:18 – 3:36)

🎬 **Screen**
- Show the architecture diagram, from your deck or from [HOW_IT_WORKS.md](HOW_IT_WORKS.md#4-architecture-on-google-cloud).
- Cut for 3 seconds to `https://YOUR-URL/api/meta` with `Vertex AI`, `BigQuery` and `yield_engine` highlighted.
- Earth Engine is optional. Unless you ran the Earth Engine step ([DEPLOY.md](DEPLOY.md#optional-google-earth-engine-climate-pipeline))
  and `/api/meta` → `climate_source` starts with *Google Earth Engine*, keep the word **can** in the last sentence.

🎙 **Voice-over**
> "Under the hood, it's all Google Cloud: Cloud Run serves the app, Gemini on Vertex AI with Search grounding runs
> the agent, BigQuery and BigQuery ML hold the data and the yield model, Firestore and Cloud Storage keep the
> policies, and Earth Engine can recompute district climate."

🏷 **Caption:** *Cloud Run · Vertex AI Gemini · Google Search grounding · BigQuery · BigQuery ML · Vertex AI Model Registry · Firestore · Cloud Storage · Earth Engine · Cloud Build · Artifact Registry · one-command deploy*

### Scene 10 · Close (3:36 – 3:48)

🎬 **Screen**
- Back on the India map in **Difference** mode.
- Fade to an end card:
  - **Agri Sus: climate-resilient crop policy for India**
  - your live URL
  - your GitHub URL
  - team name

🎙 **Voice-over**
> "Agri Sus turns public data into plans a ministry can fund, explain and defend: less import dependence, healthier
> aquifers, and no farmer left worse off."

---

## Timing summary

| # | Scene | Starts | Length |
|---|---|---|---|
| 1 | The problem | 0:00 | 20 s |
| 2 | Meet Agri Sus | 0:20 | 13 s |
| 3 | The plan and the forecast | 0:33 | 29 s |
| 4 | What it costs, what it returns | 1:02 | 25 s |
| 5 | A multi-country trade shock | 1:27 | 16 s |
| 6 | Every decision explained | 1:43 | 20 s |
| 7 | The live Gemini agent (5 short clips) | 2:03 | 57 s |
| 8 | Saved policies and the brief | 3:00 | 18 s |
| 9 | Built on Google Cloud | 3:18 | 18 s |
| 10 | Close | 3:36 | 12 s |
| | **Total** | | **3:48** |

- **If you run long,** shorten scene 2 to *"Agri Sus is an AI co-pilot for agriculture planners, built on Google
  Cloud."* and drop question 7d (BigQuery).
- **If you run short,** let the ▶ animation in scene 3 and the Hindi audio in 7e play longer.
- **Never go below 3:00 or above 4:00.**

## Recording and editing, step by step

1. **Record the screen, one clip per scene.** That makes retakes easy.
   - **Mac:** press **⌘ Shift 5**, choose *Record Selected Portion*, drag over the browser window, then click *Record*.
     Stop with the ⏹ button in the menu bar.
   - **Windows:** use the screen recorder in **Clipchamp**, or press **Win + G** (Xbox Game Bar).
2. **Record the voice-over separately** in a quiet room, using a headset microphone if you have one. Read each scene's
   lines as their own audio clip. *(Or narrate live while recording if you prefer.)*
3. **Edit** in iMovie (Mac), Clipchamp (Windows), CapCut or Google Vids:
   - put the clips in order and lay each voice-over clip under its scene;
   - trim the waits;
   - add the 🏷 captions as lower-third titles;
   - optionally add quiet background music from the YouTube Audio Library (copyright-free).
4. **Check the length** in the editor's timeline: it must be between **3:00 and 4:00**.
5. **Export** as MP4, 1080p.
6. **Upload** the way the submission form asks, for example YouTube as *Unlisted*. Open the link in a private window
   to make sure it plays for others.

### If something goes wrong while recording

| Problem | What to do |
|---|---|
| A co-pilot answer takes long | Normal for multi-step questions: cut the wait in editing. If it says *"Gemini did not answer in time"*, reload and ask again |
| The model tag under an answer says `offline-rule-router` or `local fallback` | Gemini was not reached. Check `/api/meta` and see [DEPLOY.md → Troubleshooting](DEPLOY.md#troubleshooting), then re-take |
| The 🎤 button is missing or hears nothing | Use Chrome and allow the microphone (🔒 in the address bar → Microphone → Allow). Or paste the Hindi question |
| A number on screen differs from the script | Say the number you see. See *Numbers to check* above |
| The first click after a pause is slow | Keep the site open while recording. With `MIN_INSTANCES=1` the server stays warm |

## YouTube description template

```
Agri Sus — an AI co-pilot for climate-resilient crop policy in India (Google Cloud AI Builder Cup 2026,
Sustainability & Social Impact).

Live app: https://YOUR-URL/
Code: https://github.com/YOUR_GITHUB_USER/agrisus

0:00 The problem: $23 bn imports, aquifers at 300% of recharge
0:20 Agri Sus: every district of India, 2- to 8-year plans
0:33 The plan vs doing nothing, stress-tested
1:02 Budget, fiscal return, least-cost mode and the plan's total cost
1:27 Trade shock: Indonesia and Malaysia cut palm-oil supply
1:43 Every decision explained (Sangrur)
2:03 Live Gemini agent: grounding, what-ifs, a policy created by chat, BigQuery, Hindi voice
3:00 Saved policies and the one-click brief
3:18 Built on Google Cloud
3:36 Close

Built with Cloud Run, Vertex AI Gemini, Grounding with Google Search, BigQuery, BigQuery ML,
Vertex AI Model Registry, Firestore, Cloud Storage, Google Earth Engine, Cloud Build, Artifact Registry and
Google OR-Tools.
```
