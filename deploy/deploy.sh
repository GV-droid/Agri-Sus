#!/usr/bin/env bash
# Agri Sus - one-command deploy to Google Cloud. Run it in Google Cloud Shell from the project folder:
#
#     bash deploy/deploy.sh
#
# It is safe to re-run (every step is idempotent). What it does:
#   1. picks your project and enables the APIs (Cloud Run, Cloud Build, Artifact Registry, Vertex AI, BigQuery,
#      Firestore, Cloud Storage)
#   2. creates a least-privilege service account for the app
#   3. creates the free-tier stores for saved policies, co-pilot chats and solved plans: a Firestore database and a
#      Cloud Storage bucket (so they survive restarts and are shared by every Cloud Run instance)
#   4. loads the data into BigQuery (partitioned + clustered tables, decision-trace and budget-ledger tables) and
#      trains the BigQuery ML yield models (linear + boosted tree), registered in the Vertex AI Model Registry
#   5. optional: Google Earth Engine climate pipeline (CHIRPS, ERA5-Land, NEX-GDDP-CMIP6 -> BigQuery)
#   6. builds the container with Cloud Build and stores it in Artifact Registry
#   7. deploys it to Cloud Run (Gemini on Vertex AI with Google Search grounding) and prints the public URL
#
# Optional settings (environment variables): PROJECT_ID, REGION (us-central1), SERVICE (agri-sus),
#   BQ_DATASET (agri_sus_mvp), BQ_LOCATION (US), USE_EARTH_ENGINE (ask | 1 | 0), GEMINI_AGENT_MODEL,
#   YIELD_MODEL (auto | prior | bqml), MIN_INSTANCES (1 = no cold starts, 0 = scale to zero and pay nothing idle),
#   SKIP_BIGQUERY=1 (code-only redeploy: keep the BigQuery tables and models from the previous run),
#   STORE_BUCKET (<project>-agri-sus-store), FIRESTORE_LOCATION (same as REGION)
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m[x] %s\033[0m\n' "$*"; exit 1; }
interactive() { [ -t 0 ] && [ -t 1 ]; }

command -v gcloud >/dev/null || die "gcloud was not found. Open Google Cloud Shell (https://shell.cloud.google.com) and run this script there."
[ -f Dockerfile ] && [ -d data/processed/app ] || die "Run this from the Agri Sus project folder (the one with the Dockerfile)."
# a temporary Cloud Shell session (e.g. opened from an "Open in Cloud Shell" link) starts signed out
if [ -z "$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)" ]; then
  interactive || die "You are not signed in. Run: gcloud auth login --update-adc   and then re-run this script."
  warn "You are not signed in to Google Cloud in this terminal - follow the link below to sign in."
  gcloud auth login --update-adc || die "Sign-in failed. Run: gcloud auth login --update-adc"
fi

# ---------------------------------------------------------------------------------------------- settings
PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
if [ -z "$PROJECT_ID" ] && interactive; then
  echo "Your Google Cloud projects:"
  gcloud projects list --format='table(projectId,name)' 2>/dev/null || true
  read -r -p "Type the PROJECT ID to deploy into: " PROJECT_ID
fi
[ -n "$PROJECT_ID" ] || die "No project selected. Run: PROJECT_ID=your-project-id bash deploy/deploy.sh"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-agri-sus}"
DATASET="${BQ_DATASET:-agri_sus_mvp}"
BQ_LOCATION="${BQ_LOCATION:-US}"
REPO="${AR_REPO:-agri-sus}"
MODEL="${GEMINI_AGENT_MODEL:-gemini-3.8-flash}"
SA_NAME="agri-sus-run"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
USE_EE="${USE_EARTH_ENGINE:-ask}"
BUCKET="${STORE_BUCKET:-${PROJECT_ID}-agri-sus-store}"

echo "Agri Sus will be deployed with:"
note "project     : $PROJECT_ID"
note "region      : $REGION   (Cloud Run + Artifact Registry)"
note "BigQuery    : ${PROJECT_ID}.${DATASET}  (location $BQ_LOCATION)"
note "Gemini      : $MODEL on Vertex AI (falls back to newer/older Flash models automatically)"
note "Storage     : Firestore (default) database + gs://${BUCKET}  (saved policies, chats, solved plans; free tier)"
if interactive; then
  read -r -p "Continue? [Y/n] " ok
  case "${ok:-y}" in [nN]*) die "Cancelled." ;; esac
fi
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1

billing="$(gcloud billing projects describe "$PROJECT_ID" --quiet --format='value(billingEnabled)' 2>/dev/null || echo unknown)"
if [ "$billing" = "False" ]; then
  die "Billing is not enabled for $PROJECT_ID. Link a billing account (free trial works): https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT_ID"
fi

# ---------------------------------------------------------------------------------------------- 1. APIs
step "1/7 Enabling Google Cloud APIs (about 1-2 minutes the first time)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com bigquery.googleapis.com bigquerystorage.googleapis.com iam.googleapis.com \
  cloudresourcemanager.googleapis.com firestore.googleapis.com storage.googleapis.com --project "$PROJECT_ID"

# ---------------------------------------------------------------------------------------------- 2. identity
step "2/7 Service account for the app (least privilege)"
gcloud iam service-accounts describe "$SA" --project "$PROJECT_ID" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name "Agri Sus Cloud Run" --project "$PROJECT_ID"
# project level only: call Vertex AI (Gemini) and run BigQuery jobs; data access is granted on the dataset (step 3)
for ROLE in roles/aiplatform.user roles/bigquery.jobUser; do
  for i in 1 2 3 4 5; do  # a brand-new service account can take a few seconds to become visible to IAM
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" --role "$ROLE" \
      --condition=None -q >/dev/null 2>&1 && break
    [ "$i" = 5 ] && die "Could not grant $ROLE to $SA (do you have Owner on the project?)"
    sleep 10
  done
done
note "granted Vertex AI user + BigQuery job user to $SA"
# Cloud Build's default build identity in new projects is the Compute Engine default service account
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
if gcloud iam service-accounts describe "$BUILD_SA" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${BUILD_SA}" \
    --role roles/cloudbuild.builds.builder --condition=None -q >/dev/null 2>&1 || \
    warn "could not grant the Cloud Build role to $BUILD_SA (the build may still work)"
fi

# ---------------------------------------------------------------------------------------------- 3. storage
# Saved policies, co-pilot chat sessions and solved plans must survive restarts and be shared by every instance:
# metadata in Firestore, run snapshots in Cloud Storage. Both stay inside the always-free tier for this app
# (Firestore: 1 GiB + 50k reads/20k writes a day on the (default) database; Cloud Storage: 5 GB in us-central1,
# us-east1 or us-west1). Cached plans under runs/ are deleted automatically after 30 days; saved policies are kept.
step "3/7 Storage for saved policies, chats and solved plans (Firestore + Cloud Storage, free tier)"
STORE_OK=1
if gcloud firestore databases describe --database="(default)" --project "$PROJECT_ID" >/dev/null 2>&1; then
  note "Firestore (default) database already exists"
else
  FS_LOC="${FIRESTORE_LOCATION:-$REGION}"
  gcloud firestore databases create --database="(default)" --location="$FS_LOC" --type=firestore-native \
    --project "$PROJECT_ID" >/dev/null 2>&1 || \
  gcloud firestore databases create --database="(default)" --location=nam5 --type=firestore-native \
    --project "$PROJECT_ID" >/dev/null 2>&1 || \
    warn "could not create the Firestore database - policies and chats will be kept in the Cloud Storage bucket instead"
  gcloud firestore databases describe --database="(default)" --project "$PROJECT_ID" >/dev/null 2>&1 && \
    note "created the Firestore (default) database"
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" --role roles/datastore.user \
  --condition=None -q >/dev/null 2>&1 || warn "could not grant Firestore access (roles/datastore.user) to $SA"
if ! gcloud storage buckets describe "gs://${BUCKET}" --project "$PROJECT_ID" >/dev/null 2>&1; then
  mk() { gcloud storage buckets create "gs://$1" --project "$PROJECT_ID" --location "$REGION" \
           --uniform-bucket-level-access --public-access-prevention "${@:2}" >/dev/null 2>&1; }
  # no soft-delete copies: deleted snapshots should not keep using storage for a week
  if mk "$BUCKET" --soft-delete-duration=0 || mk "$BUCKET"; then
    note "created gs://${BUCKET}"
  elif [ -z "${STORE_BUCKET:-}" ] && { BUCKET="${BUCKET}-${PROJECT_NUMBER}"; mk "$BUCKET" --soft-delete-duration=0 || mk "$BUCKET"; }; then
    note "created gs://${BUCKET}"
  else
    STORE_OK=0
    warn "could not create a Cloud Storage bucket - saved policies will not survive a restart."
    warn "Create one in the console (Cloud Storage > Buckets) and re-run: STORE_BUCKET=<name> bash deploy/deploy.sh"
  fi
fi
if [ "$STORE_OK" = "1" ]; then
  LIFECYCLE="$(mktemp)"
  printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30,"matchesPrefix":["runs/"]}}]}' > "$LIFECYCLE"
  gcloud storage buckets update "gs://${BUCKET}" --lifecycle-file="$LIFECYCLE" --project "$PROJECT_ID" >/dev/null 2>&1 || \
    warn "could not set the 30-day clean-up rule for cached plans on gs://${BUCKET}"
  rm -f "$LIFECYCLE"
  for i in 1 2 3; do
    gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --member "serviceAccount:${SA}" \
      --role roles/storage.objectUser --project "$PROJECT_ID" >/dev/null 2>&1 && break
    [ "$i" = 3 ] && { STORE_OK=0; warn "could not give $SA access to gs://${BUCKET}"; }
    sleep 5
  done
  [ "$STORE_OK" = "1" ] && note "the app may read and write objects in gs://${BUCKET} (and nothing else in Cloud Storage)"
fi

# ---------------------------------------------------------------------------------------------- 4. BigQuery
step "4/7 Loading data into BigQuery and training BigQuery ML models (about 5-10 minutes)"
PY=python3
if [ ! -x .deploy-venv/bin/python ]; then python3 -m venv .deploy-venv >/dev/null 2>&1 || true; fi
[ -x .deploy-venv/bin/python ] && PY=.deploy-venv/bin/python
"$PY" -m pip install --quiet --disable-pip-version-check -r scripts/requirements-gcp.txt 2>/dev/null || \
  "$PY" -m pip install --quiet --disable-pip-version-check --user --break-system-packages -r scripts/requirements-gcp.txt
BQ_OK=1
if [ "${SKIP_BIGQUERY:-0}" = "1" ]; then
  note "skipped (SKIP_BIGQUERY=1) - keeping the tables and models from the previous run"
elif ! "$PY" deploy/load_bigquery.py --project "$PROJECT_ID" --dataset "$DATASET" --location "$BQ_LOCATION"; then
  BQ_OK=0
  warn "The BigQuery step did not finish. The app still works: it falls back to the data bundled in the container."
  warn "Fix the error above and re-run this script, or run: $PY deploy/load_bigquery.py --project $PROJECT_ID"
fi
# dataset-scoped access for the app: read the tables, write decision traces and the budget ledger
# (bq add-iam-policy-binding does not support datasets, so the dataset's access list is edited instead)
if ! "$PY" deploy/grant_dataset_access.py --project "$PROJECT_ID" --dataset "$DATASET" --member "$SA"; then
  # read-only fallback: never write access to every dataset in the project
  warn "could not add $SA to the dataset's access list - granting BigQuery Data Viewer on the project instead"
  warn "(the app reads its tables, but decision traces and the budget ledger will not be logged to BigQuery)"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" \
    --role roles/bigquery.dataViewer --condition=None -q >/dev/null 2>&1 || \
    warn "could not grant BigQuery data access to $SA - the site will run on its bundled data snapshot"
fi

# ---------------------------------------------------------------------------------------------- 5. Earth Engine
CLIMATE_TABLE="climate_projection_district_year"
if [ "$USE_EE" = "ask" ]; then
  USE_EE=0
  if interactive; then
    echo
    echo "Optional: recompute the climate inputs with Google Earth Engine (CHIRPS rainfall, ERA5-Land temperature and"
    echo "NASA NEX-GDDP-CMIP6 projections, averaged over each of the 785 district boundaries). Needs a one-time free"
    echo "registration of the project for Earth Engine and takes 1-2 hours. The app works without it (bundled tables)."
    read -r -p "Run the Earth Engine pipeline now? [y/N] " ans
    case "${ans:-n}" in [yY]*) USE_EE=1 ;; esac
  fi
fi
if [ "$USE_EE" = "1" ] && [ "$BQ_OK" = "1" ]; then
  step "5/7 Google Earth Engine climate pipeline (1-2 hours; safe to re-run if Cloud Shell disconnects)"
  gcloud services enable earthengine.googleapis.com --project "$PROJECT_ID"
  note "If this is the first time, register the project (free for non-commercial use):"
  note "  https://code.earthengine.google.com/register?project=${PROJECT_ID}"
  if interactive; then read -r -p "    Press Enter once the project is registered for Earth Engine... " _; fi
  if "$PY" scripts/gee_climate.py --project "$PROJECT_ID" --dataset "$DATASET" --mode export --skip-existing && \
     "$PY" deploy/load_bigquery.py --project "$PROJECT_ID" --dataset "$DATASET" --location "$BQ_LOCATION" --sql-only --with-ee; then
    CLIMATE_TABLE="climate_projection_ee"
    note "the app will use the Earth Engine climate table ${DATASET}.${CLIMATE_TABLE}"
  else
    warn "Earth Engine step failed - keeping the bundled climate tables. Re-run with: USE_EARTH_ENGINE=1 bash deploy/deploy.sh"
  fi
else
  step "5/7 Earth Engine climate pipeline skipped (run later with: USE_EARTH_ENGINE=1 bash deploy/deploy.sh)"
  # keep using Earth Engine climate if an earlier run already produced it
  if bq show "${PROJECT_ID}:${DATASET}.climate_projection_ee" >/dev/null 2>&1; then
    CLIMATE_TABLE="climate_projection_ee"
    note "found ${DATASET}.climate_projection_ee from an earlier run - using it"
  fi
fi

# ---------------------------------------------------------------------------------------------- 6. build
step "6/7 Building the container with Cloud Build (about 6-10 minutes)"
gcloud artifacts repositories describe "$REPO" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" \
    --project "$PROJECT_ID" --description "Agri Sus container images"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:$(date +%Y%m%d-%H%M%S)"
gcloud builds submit --project "$PROJECT_ID" --tag "$IMAGE" --machine-type e2-highcpu-8 --timeout 1800s .

# ---------------------------------------------------------------------------------------------- 7. deploy
step "7/7 Deploying to Cloud Run"
ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${GEMINI_LOCATION:-global},GOOGLE_GENAI_USE_VERTEXAI=true"
ENV_VARS="${ENV_VARS},DATA_BACKEND=bigquery,BQ_DATASET=${DATASET},CLIMATE_TABLE=${CLIMATE_TABLE},YIELD_MODEL=${YIELD_MODEL:-auto}"
ENV_VARS="${ENV_VARS},GEMINI_AGENT_MODEL=${MODEL},GEMINI_FORECAST_MODEL=${GEMINI_FORECAST_MODEL:-$MODEL}"
ENV_VARS="${ENV_VARS},GEMINI_THINKING_BUDGET=${GEMINI_THINKING_BUDGET:-1024},GEMINI_TIMEOUT_S=${GEMINI_TIMEOUT_S:-90},AGENT_DEADLINE_S=${AGENT_DEADLINE_S:-240}"
ENV_VARS="${ENV_VARS},GEMINI_MAX_REQUESTS_PER_10MIN=${GEMINI_MAX_REQUESTS_PER_10MIN:-200},API_DOCS=${API_DOCS:-false}"
[ "$STORE_OK" = "1" ] && ENV_VARS="${ENV_VARS},STORE_BUCKET=${BUCKET}"
# --no-cpu-throttling: the app pre-solves the scenarios in the background after start-up, and co-pilot answers and
#   policy documents run as background jobs that finish (and are saved) even if the browser disconnects
# --session-affinity: a visitor keeps talking to the instance that holds their simulation results and reconnects to
#   the instance writing their answer
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --project "$PROJECT_ID" \
  --service-account "$SA" --allow-unauthenticated \
  --memory 4Gi --cpu 2 --no-cpu-throttling --cpu-boost --concurrency 10 --timeout 600 --session-affinity \
  --min-instances "${MIN_INSTANCES:-1}" --max-instances 5 \
  --set-env-vars "$ENV_VARS"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
step "Done! Agri Sus is live at:  $URL"
note "The first page load can take ~1 minute while the optimizer pre-solves the scenarios."
note "Health check : curl ${URL}/api/health"
note "BigQuery     : https://console.cloud.google.com/bigquery?project=${PROJECT_ID}&ws=!1m4!1m3!3m2!1s${PROJECT_ID}!2s${DATASET}"
note "Vertex AI    : https://console.cloud.google.com/vertex-ai/models?project=${PROJECT_ID}  (BigQuery ML models)"
note "Logs         : https://console.cloud.google.com/run/detail/${REGION}/${SERVICE}/logs?project=${PROJECT_ID}"
note "Saved data   : https://console.cloud.google.com/firestore/databases/-default-/data?project=${PROJECT_ID}  and  gs://${BUCKET}"
note "Costs: with MIN_INSTANCES=1 one instance stays warm (no cold starts). To pay nothing when idle:"
note "  gcloud run services update $SERVICE --region $REGION --min-instances 0"
note "To delete everything later: bash deploy/teardown.sh"
