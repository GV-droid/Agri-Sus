# Deploy Agri Sus to Google Cloud — step by step (no coding needed)

You will copy-paste **one command**. A script does everything else and prints the public web address of your
Agri Sus site at the end. Total time: about **30 minutes**, most of it waiting.

What you get:

| Google product | What Agri Sus uses it for |
|---|---|
| **Cloud Run** | Hosts the website + API (container, autoscaling, HTTPS URL) |
| **Cloud Build** + **Artifact Registry** | Builds the container in the cloud and stores it (nothing to install on your computer) |
| **Vertex AI — Gemini** | The live AI agent, the policy document agent (with Google Search grounding for existing schemes and import duties) and the Gemini climate/price outlook scenario |
| **Grounding with Google Search** | The agent's cited answers about current news, monsoon outlooks, prices and policies |
| **BigQuery** | All data tables (partitioned + clustered), the optimizer's decision traces, the policy budget ledger and every policy-document version |
| **BigQuery ML** | Two yield models (linear + **boosted tree**) trained on 105k district-crop-years, champion/challenger |
| **Vertex AI Model Registry** | Both BigQuery ML models are registered there automatically |
| **Firestore** *(free tier)* | The shared library of saved policies and co-pilot chat sessions (survives restarts and redeploys) |
| **Cloud Storage** *(free tier)* | Snapshots of solved plans, so a saved policy re-opens instantly with its map and every number, without re-running the optimizer; and the policy documents, written once and reopened without a rewrite |
| **Google Earth Engine** *(optional)* | Recomputes the climate inputs from CHIRPS, ERA5-Land and NASA NEX-GDDP-CMIP6 for all 785 districts |

---

## Before you start (5 minutes, one time)

1. **A Google account** (Gmail or Google Workspace).
2. **A Google Cloud project with billing turned on.**
   - Create a project: <https://console.cloud.google.com/projectcreate> → give it a name (for example `agri-sus`) → **Create**.
   - Turn on billing: <https://console.cloud.google.com/billing/linkedaccount> → pick the project → **Link a billing
     account**. New accounts get free trial credits, which are more than enough.
   - Write down the **Project ID** (shown on the project dashboard, e.g. `agri-sus-123456`).
3. You must be **Owner** of the project. If you created it, you already are.

> Tip: use a project in your *personal* Google account. Company/school accounts often block public websites
> (see [Troubleshooting](#troubleshooting)).

---

## Step 1 — Open Cloud Shell

Open <https://shell.cloud.google.com/?show=terminal> . A black terminal window opens in your browser.
Cloud Shell is a free computer in Google Cloud with every tool already installed, and you are already signed in.

Tell it which project to use (replace `YOUR_PROJECT_ID`):

```bash
gcloud config set project YOUR_PROJECT_ID
```

If a box asks **"Authorize Cloud Shell"**, click **Authorize**.

## Step 2 — Get the Agri Sus files into Cloud Shell

Pick **one** of the two options.

**Option A — from GitHub** (if the code is in a GitHub repository). Paste:

```bash
git clone https://github.com/YOUR_GITHUB_USER/agrisus.git && cd agrisus
```

**Option B — from a zip file** (no GitHub needed).

1. Get the zip: someone with the code runs `bash deploy/make_bundle.sh` in the project folder, which creates
   `agrisus-deploy.zip` (~7 MB).
2. In Cloud Shell, click the **⋮ (More)** menu at the top of the terminal → **Upload** → choose `agrisus-deploy.zip`.
3. Paste:

   ```bash
   mkdir -p ~/agrisus && cd ~/agrisus && unzip -oq ~/agrisus-deploy.zip && ls
   ```

   You should see `Dockerfile`, `backend`, `frontend`, `deploy`, …

## Step 3 — Run the installer

```bash
bash deploy/deploy.sh
```

It asks two questions:

| Question | Answer |
|---|---|
| `Continue? [Y/n]` | press **Enter** |
| `Run the Earth Engine pipeline now? [y/N]` | press **Enter** (= No) the first time. You can add it later, see [below](#optional-google-earth-engine-climate-pipeline). |

Then wait. You will see green lines for each of the 7 steps:

| Step | What happens | Time |
|---|---|---|
| 1/7 | Turns on the Google Cloud services (Cloud Run, Cloud Build, Artifact Registry, Vertex AI, BigQuery, Firestore, Cloud Storage) | 1–2 min |
| 2/7 | Creates a dedicated service account `agri-sus-run` that may only call Gemini and use the Agri Sus BigQuery dataset | < 1 min |
| 3/7 | Creates the storage for saved policies and chats: the Firestore `(default)` database (only if the project has none) and a private Cloud Storage bucket `YOUR_PROJECT_ID-agri-sus-store`. Cached plans in it are deleted after 30 days; saved policies are kept. Only the app's service account can use them. Both stay inside Google's always-free tier | < 1 min |
| 4/7 | Creates the BigQuery dataset `agri_sus_mvp`, loads 13 tables, creates the ledger/trace tables and views, trains the two BigQuery ML yield models and registers them in Vertex AI | 5–10 min |
| 5/7 | Google Earth Engine (skipped unless you said yes) | — |
| 6/7 | Cloud Build builds the website container and stores it in Artifact Registry | 6–10 min |
| 7/7 | Deploys to Cloud Run and prints the URL | 1–2 min |

You never paste a key or password: the site reaches Gemini, BigQuery, Firestore and Cloud Storage with its own
service account.

At the end you will see:

```
==> Done! Agri Sus is live at:  https://agri-sus-xxxxxxxx-uc.a.run.app
```

That link is your site. Share it with anyone — no login needed.

## Step 4 — Check that it works

1. Open the URL. The first visit can take **about 1 minute** while the optimizer pre-solves the scenarios; then
   the map of India appears.
2. In the left panel pick a **Crisis scenario** (for example *2028 El Nino drought*) or change a lever, click
   **Run optimizer**, and look at the tabs under the map: **🌍 Sustainability forecast**, **💰 Budget & returns**,
   **🚢 Trade shocks**, …
3. Ask the AI agent (chat box *"Ask about a district, a crisis, a policy…"*), for example
   *"What if Indonesia stops selling palm oil to India?"* — you will see each tool the agent calls.
4. Click a district on the map → **🌱 Next crop** for the recommended crop next season.
5. Click **📄 Policy document** → **✦ Write the policy document**. In one to three minutes Gemini writes the plan's
   policy proposal (sections A–H): section **G** checks whether more production will really cut imports and adds a
   counter-measure clause for each risk, section **H** lists existing central and state schemes found with Google
   Search. It is saved: open it again and it appears at once. **↻ Regenerate…** writes a new version with your
   instructions; **Print / PDF** prints it.
6. Click **💾 Save as policy** (left panel), give it a name, then open **📁 Policies**. Your policy is in the list; click **Open** and
   the map and every tab come back instantly from the saved copy. Ask the co-pilot *"Create a 6-year plan for an El
   Niño year and save it as El Niño-proof"*: it builds and saves the policy and shows an **Open policy** button.
   Earlier conversations are under **💬 Chats** in the co-pilot.
7. To check that saved policies survive a restart, restart the site (the command is in *Troubleshooting*) and open
   **📁 Policies** again: they are still there. The note at the bottom of that window says
   *Stored in Firestore + Cloud Storage: shared by everyone and kept across restarts.*

Technical check (optional): open `https://YOUR-URL/api/meta`. It shows `"data_backend": "BigQuery"`, the Gemini
model in use, which yield model is active (`yield_engine`) and which climate table is used (`climate_source`).
`https://YOUR-URL/api/health` shows the policy library's storage (`"store"`).

---

## Optional: Google Earth Engine climate pipeline

The site already ships with climate tables. Earth Engine recomputes them from satellite and climate-model data
(CHIRPS rainfall, ERA5-Land temperature, NASA NEX-GDDP-CMIP6 SSP2-4.5 at ~25 km, averaged over each of the
785 district boundaries) and writes them to BigQuery.

1. Register the project for Earth Engine (free for non-commercial and research use):
   open <https://code.earthengine.google.com/register>, choose the non-commercial (unpaid) option that fits you and
   select your project. One time only; the script also prints this link.
2. In Cloud Shell, from the `agrisus` folder:

   ```bash
   USE_EARTH_ENGINE=1 bash deploy/deploy.sh
   ```

3. It takes **1–2 hours** (Earth Engine runs the jobs on Google's servers). Keep the browser tab open.
   If Cloud Shell disconnects, open it again, `cd ~/agrisus` and run the same command — finished parts are
   skipped and running jobs are picked up where they were.

When it finishes, the app automatically uses the Earth Engine table `climate_projection_ee`, and the BigQuery ML
climate factors are retrained on it. Later re-deploys keep using it.

---

## Settings you can change

Put a setting in front of the command, e.g. `MIN_INSTANCES=0 bash deploy/deploy.sh`.

| Setting | Default | Meaning |
|---|---|---|
| `MIN_INSTANCES` | `1` | `1` keeps one server always warm (no waiting, see costs). `0` = pay nothing when nobody uses the site (first visit then takes ~1–2 min). |
| `REGION` | `us-central1` | Where the website runs. `asia-south1` (Mumbai) is closer to India. |
| `GEMINI_AGENT_MODEL` | `gemini-3.8-flash` | The Gemini model for the agent. If unavailable in your project the app automatically falls back to `gemini-3.5-flash`, then `gemini-2.5-flash`. |
| `GEMINI_THINKING_BUDGET` | `1024` | How much Gemini may "think" before each co-pilot step. Keeps each step to seconds instead of about a minute. `default` = the model's own, slower setting. |
| `GEMINI_FIRST_TOKEN_S` / `GEMINI_IDLE_S` | `60` / `45` | Stall detection for the co-pilot and the document writer, which stream: a Gemini call with no first output within `GEMINI_FIRST_TOKEN_S` seconds, or no new output for `GEMINI_IDLE_S`, is retried. Long answers are fine as long as Gemini keeps writing. |
| `GEMINI_RETRIES` / `GEMINI_FAST_THINKING_BUDGET` | `2` / `128` | Retries after a stall, time-out, rate limit or server error, each on the next model in the chain with this smaller thinking budget (the answer then stays on that model). |
| `AGENT_DEADLINE_S` / `AGENT_MAX_S` | `240` / `480` | After `AGENT_DEADLINE_S` seconds the co-pilot stops gathering information and writes its answer from what it has; `AGENT_MAX_S` is the hard cap per answer. Answers run as background jobs, so a stream cut by Cloud Run's 600 s request limit, a phone going to sleep or a reload just reconnects and continues. |
| `GEMINI_TIMEOUT_S` | `90` | The longest wait for one non-streamed Gemini call (the outlook, a Google Search-grounded lookup) before it is retried on the next model. |
| `GEMINI_THOUGHT_SUMMARIES` | `true` | Show Gemini's thinking headlines (*"Comparing water savings…"*) in the co-pilot's live activity feed. `false` hides them. |
| `COPILOT_DOC_WAIT_S` | `150` | How long the co-pilot follows a policy document it is writing before it answers; the document then finishes in the background and the chat shows **📄 Open document** when it is ready. |
| `YIELD_MODEL` | `auto` | `auto`: use the BigQuery ML boosted tree only if it beats the linear model on the 2020–24 hold-out; `bqml`: always use it; `prior`: always use the transparent agronomic model. |
| `USE_EARTH_ENGINE` | ask | `1` run the Earth Engine pipeline, `0` skip without asking. |
| `SKIP_BIGQUERY` | `0` | `1` = code-only update: rebuild and redeploy the site but keep the BigQuery tables and models. |
| `STORE_BUCKET` | `YOUR_PROJECT_ID-agri-sus-store` | The Cloud Storage bucket for saved policies, chats and cached plans. Bucket names are global: if the default is taken, the script adds your project number, or you can pick a name yourself. |
| `FIRESTORE_LOCATION` | same as `REGION` | Where the Firestore database is created (only if the project has none yet). Cannot be changed later. |
| `BQ_LOCATION` | `US` | BigQuery location (multi-region `US` or `EU`, or a region like `asia-south1`). |
| `GEMINI_MAX_REQUESTS_PER_10MIN` | `200` | Safety cap: the most paid Gemini calls (co-pilot questions, policy documents, outlooks) one server makes in 10 minutes, across all visitors. Each visitor also has their own smaller limits. |
| `API_DOCS` | `false` | `1` publishes the interactive API documentation at `/docs`. Hidden by default so the site exposes less to attackers. |

Change a setting on the running site without redeploying (example: scale to zero):

```bash
gcloud run services update agri-sus --region us-central1 --min-instances 0
gcloud run services update agri-sus --region us-central1 --update-env-vars GEMINI_AGENT_MODEL=gemini-2.5-flash
```

### Use a Gemini API key instead of Vertex AI (optional)

Not needed normally: the site reaches Gemini through Vertex AI with its own service account, so there is no key to
manage. If Vertex AI Gemini is not available in your project, use a key from <https://aistudio.google.com/apikey>,
kept in Secret Manager (never in the code). In Cloud Shell:

```bash
PROJECT_ID=$(gcloud config get-value project)
gcloud services enable secretmanager.googleapis.com
read -rsp 'Paste your Gemini API key, then press Enter: ' KEY; echo
printf '%s' "$KEY" | gcloud secrets create gemini-api-key --data-file=-
gcloud secrets add-iam-policy-binding gemini-api-key --role roles/secretmanager.secretAccessor \
  --member serviceAccount:agri-sus-run@${PROJECT_ID}.iam.gserviceaccount.com
gcloud run services update agri-sus --region us-central1 \
  --update-secrets GEMINI_API_KEY=gemini-api-key:latest --update-env-vars GOOGLE_GENAI_USE_VERTEXAI=false
```

`https://YOUR-URL/api/meta` then shows `"backend": "Gemini API"`. Running `deploy/deploy.sh` again switches the site
back to Vertex AI; run the last command again afterwards. To replace the key, run the `read` line, then
`printf '%s' "$KEY" | gcloud secrets versions add gemini-api-key --data-file=-`, then the last command again.

## Updating the site after a code change

Upload the new files (Option A: `git pull`; Option B: upload the new zip and unzip again), then:

```bash
SKIP_BIGQUERY=1 bash deploy/deploy.sh      # about 10 minutes
```

This also sets up anything new: for example, a site deployed before saved policies existed gets its Firestore
database and storage bucket in step 3/7. Saved policies and chats are kept across updates.

### Automatic deploys from GitHub (optional, for developers)

`cloudbuild.yaml` builds, pushes to Artifact Registry and deploys to Cloud Run. Run `deploy/deploy.sh` once first
(it creates the service account, BigQuery dataset and image repository), then:

1. Give the Cloud Build identity permission to deploy (Cloud Shell):

   ```bash
   PROJECT_ID=$(gcloud config get-value project)
   PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
   BUILD_SA=${PROJECT_NUMBER}-compute@developer.gserviceaccount.com   # older projects: ${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com
   gcloud projects add-iam-policy-binding $PROJECT_ID --member serviceAccount:$BUILD_SA --role roles/run.admin --condition=None
   gcloud projects add-iam-policy-binding $PROJECT_ID --member serviceAccount:$BUILD_SA --role roles/cloudbuild.builds.builder --condition=None
   gcloud iam service-accounts add-iam-policy-binding agri-sus-run@$PROJECT_ID.iam.gserviceaccount.com \
     --member serviceAccount:$BUILD_SA --role roles/iam.serviceAccountUser
   ```

2. Run it by hand with `gcloud builds submit --config cloudbuild.yaml`, or connect the repository in
   **Cloud Build → Triggers → Create trigger** (event: push to `main`, configuration: `cloudbuild.yaml`,
   service account: the one above). If you ran the Earth Engine pipeline, add the substitution
   `_CLIMATE_TABLE=climate_projection_ee`.

---

## Costs (approximate, US prices)

| Item | Cost |
|---|---|
| Cloud Run, `MIN_INSTANCES=1` (2 vCPU, 4 GiB always on) | about **$3.5–4 per day** (~$110/month) |
| Cloud Run, `MIN_INSTANCES=0` | only while people use it (~$0.15 per busy hour), **$0 when idle** |
| Gemini on Vertex AI | well under $0.05 per agent question with Gemini Flash; Google Search grounding is billed per grounded answer (a few cents) |
| BigQuery storage + queries | ~100 MB of data — inside the free tier |
| Firestore (saved policies, chats) | **$0** — far inside the always-free tier (1 GiB stored, 50,000 reads and 20,000 writes a day). Listing the library costs one read while nothing has changed |
| Cloud Storage (plan snapshots) | **$0** in `us-central1`, `us-east1` or `us-west1` (5 GB free); a few cents a month in other regions. Cached plans are deleted after 30 days |
| BigQuery ML training (each full deploy) | typically under $1 |
| Cloud Build | about $0.20 per build |
| Artifact Registry | ~$0.05 per month per stored image |
| Earth Engine | free for registered non-commercial/research projects |

To keep costs near zero between demos: `gcloud run services update agri-sus --region us-central1 --min-instances 0`.
Set it back to `1` on demo day so judges never wait. Set a budget alert at
<https://console.cloud.google.com/billing/budgets>.

---

## Troubleshooting

| You see | Do this |
|---|---|
| `Billing is not enabled` | Link a billing account (see *Before you start*), then run the script again. |
| `Could not grant roles/... (do you have Owner on the project?)` or `PERMISSION_DENIED` | You need the **Owner** role. Use a project you created yourself. |
| The site URL shows **403 Forbidden** / the deploy printed `Setting IAM policy failed` | Your organisation forbids public websites (policy *Domain restricted sharing*). Use a project in a personal Google account, or ask your admin to allow `allUsers` for Cloud Run. Private preview: `gcloud run services proxy agri-sus --region us-central1`, then use Cloud Shell's **Web preview** on port 8080. |
| `You are not signed in` | Run `gcloud auth login --update-adc`, follow the link, then re-run the script. |
| `The BigQuery step did not finish` | The site still works with the data built into the container. Read the red error above it, fix it, and re-run (it is safe to re-run). |
| `Vertex AI Model Registry registration failed - training without it` | Harmless: the BigQuery ML models are still trained and used; they just do not appear in the Vertex AI Model Registry. |
| The header shows **Data snapshot** instead of **BigQuery**, `/api/meta` shows `local snapshot (BigQuery read failed: Forbidden)`, or the agent says `BigQuery rejected the query (Forbidden)` | The site's service account cannot read the dataset (sites deployed before this fix). Re-run `SKIP_BIGQUERY=1 bash deploy/deploy.sh` with the current files (this also lets the site log its runs to its own dataset). Quick read-only fix in place: `P=$(gcloud config get-value project); gcloud projects add-iam-policy-binding $P --member serviceAccount:agri-sus-run@$P.iam.gserviceaccount.com --role roles/bigquery.dataViewer --condition=None -q`, wait a minute, then restart the site: `gcloud run services update agri-sus --region us-central1 --update-env-vars RESTARTED_AT=$(date +%s)` |
| `/api/meta` shows `"reason": "BigQuery ML factors not available (BigQuery: NotFound)"` | The BigQuery ML training in step 4/7 did not finish. Run `bash deploy/deploy.sh` again (without `SKIP_BIGQUERY`) and read the red error in step 4/7. |
| Step 3/7 printed `could not create the Firestore database - policies and chats will be kept in the Cloud Storage bucket instead` | Harmless: everything is kept in the bucket instead (a little slower to list). To use Firestore, create it once in the console (**Firestore → Create database → Native mode**, database ID `(default)`), then run `SKIP_BIGQUERY=1 bash deploy/deploy.sh` again. |
| Step 3/7 printed `could not create a Cloud Storage bucket - saved policies will not survive a restart` | Bucket names are shared by everyone on Google Cloud. Pick another name: `STORE_BUCKET=agrisus-store-$RANDOM SKIP_BIGQUERY=1 bash deploy/deploy.sh`. |
| The **📁 Policies** window says *Stored on local disk of this server only* | The site was deployed without storage (an older version, or step 3/7 failed). Run `SKIP_BIGQUERY=1 bash deploy/deploy.sh` again and read the yellow lines in step 3/7. |
| Saving or opening a policy says *The policy library is unavailable right now* | The service account lost access to Firestore or the bucket. Run `SKIP_BIGQUERY=1 bash deploy/deploy.sh` again (it re-grants access), wait a minute and retry. |
| The agent says `Gemini unavailable … switching to offline analyst`, or its model label shows `offline-rule-router` | Gemini models available to your project differ by region and date. Set one that your project has: `gcloud run services update agri-sus --region us-central1 --update-env-vars GEMINI_AGENT_MODEL=gemini-2.5-flash`. If Vertex AI Gemini is not available in your project at all, see *Use a Gemini API key instead of Vertex AI*. If the message ends with `The Gemini API key was rejected`, the key is wrong: replace it as described there. |
| The co-pilot shows `Error: Failed to fetch` or `network error`, or never finishes | Sites deployed before this fix: Gemini 3 thought for about a minute per step, so a full answer ran into Cloud Run's request time limit. Update the site with the current files: `SKIP_BIGQUERY=1 bash deploy/deploy.sh` (see *Updating the site after a code change*). |
| The co-pilot's activity feed says *The model was slow to respond - retrying with a faster setting* (or *rate-limited* / *overloaded*) | Nothing to do: a stalled or refused Gemini call was retried on the next model and the answer carries on. If it shows on most questions, make Gemini think less per step: `gcloud run services update agri-sus --region us-central1 --update-env-vars GEMINI_THINKING_BUDGET=512`. |
| The co-pilot says *Gemini is not responding - summarising what I found* or *answering with the built-in analyst* | Every model in the chain stalled or failed, twice. The answer was written from the steps already taken, or by the offline analyst when there were none. Usually temporary; if it persists, check the model as in the `Gemini unavailable` row above. |
| The co-pilot shows *The connection to this answer was lost. If it finished, it is in 💬 Chats.* | The server that was writing the answer restarted (for example during a redeploy). Open **💬 Chats**: finished answers are saved there. If it is not there, ask again. |
| The co-pilot or the document window says *You already have answers in progress* (or *documents being written*), or *The AI features are very busy right now* | Each visitor can have 2 answers and 2 documents in progress at a time (and each server 40 and 8). Wait for one to finish, or press **■ Stop**, then try again. |
| The policy document says *Deterministic template* or *Gemini could not write this version … the template was used* | Gemini was not reached, or stalled on every model (the writer gives up after 8 minutes in total), so the document was assembled from the same numbers without Gemini. Check `/api/meta` (see the `Gemini unavailable` row above), then click **↻ Regenerate…** → **Write version N**. |
| The policy document says *Gemini wrote most of this version, but the model stopped part-way; sections … come from the template* | Gemini stalled near the end, so its draft was kept and only the missing sections were filled from the same numbers. Click **↻ Regenerate…** → **Write version N** for a full Gemini version. |
| The document window says *This document was already being written, so your changes were not applied* | Someone else (or the co-pilot) was writing the same document: the window shows that job's progress and its result. Submit your changes again once it is ready. |
| Writing a policy document says *Too many requests* | Each visitor may write 6 new versions per 10 minutes (opening saved documents is unlimited). Wait a few minutes and try again. |
| Cloud Shell disconnected in the middle | Open Cloud Shell again, `cd ~/agrisus`, run `bash deploy/deploy.sh` again. Every step is safe to repeat. |
| Build failed | Run the script again (network hiccups happen). If it fails twice, open the build log link printed above the error. |
| The first page load is slow | Normal for ~1 minute after a deploy or after scaling from zero: the optimizer pre-solves the scenarios. |

Logs of the running site: **Cloud Run → agri-sus → Logs** (the script prints the direct link).

---

## Delete everything

```bash
bash deploy/teardown.sh
```

Type `DELETE` to confirm. It removes the Cloud Run service, the container images, the BigQuery dataset (including the
BigQuery ML models, which also removes them from the Vertex AI Model Registry), the storage bucket with the saved
policies, chats and cached plans, the app's Firestore collections, and the service account. The Firestore database
itself is left in place (it is free and other apps may use it).
To be completely sure nothing is billed, you can instead shut down the whole project:
**IAM & Admin → Settings → Shut down**.

---

## Guided version inside Cloud Shell

The same steps are available as a click-through tutorial panel in Cloud Shell:

```bash
teachme docs/DEPLOY_TUTORIAL.md
```

Or, once the code is on GitHub, put this button in the README (replace `YOUR_GITHUB_USER`). It clones the repository
into Cloud Shell and opens the tutorial (Cloud Shell opens such links in a temporary session — the script asks you
to sign in if needed):

```markdown
[![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/YOUR_GITHUB_USER/agrisus&cloudshell_git_branch=main&cloudshell_tutorial=docs/DEPLOY_TUTORIAL.md&show=ide%2Cterminal)
```
