#!/usr/bin/env bash
# Delete everything deploy/deploy.sh created (Cloud Run service, container images, BigQuery dataset incl. the
# BigQuery ML models, service account) so the project stops incurring charges. usage: bash deploy/teardown.sh
set -euo pipefail
PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
[ -n "$PROJECT_ID" ] || { echo "Set PROJECT_ID first: PROJECT_ID=my-project bash deploy/teardown.sh"; exit 1; }
REGION="${REGION:-us-central1}"; SERVICE="${SERVICE:-agri-sus}"; DATASET="${BQ_DATASET:-agri_sus_mvp}"; REPO="${AR_REPO:-agri-sus}"
echo "This permanently deletes from project $PROJECT_ID:"
echo "  Cloud Run service $SERVICE ($REGION), Artifact Registry repo $REPO, BigQuery dataset $DATASET, service account agri-sus-run"
echo "  and the Secret Manager secret gemini-api-key, if you created one"
read -r -p "Type DELETE to continue: " ok
[ "$ok" = "DELETE" ] || { echo "Cancelled."; exit 0; }
gcloud run services delete "$SERVICE" --region "$REGION" --project "$PROJECT_ID" -q || true
gcloud artifacts repositories delete "$REPO" --location "$REGION" --project "$PROJECT_ID" -q || true
bq rm -r -f -d "${PROJECT_ID}:${DATASET}" || true
gcloud iam service-accounts delete "agri-sus-run@${PROJECT_ID}.iam.gserviceaccount.com" --project "$PROJECT_ID" -q || true
gcloud secrets describe gemini-api-key --project "$PROJECT_ID" -q >/dev/null 2>&1 && \
  { gcloud secrets delete gemini-api-key --project "$PROJECT_ID" -q || true; }
echo "Done. (BigQuery ML models registered in Vertex AI are removed with the dataset.)"
