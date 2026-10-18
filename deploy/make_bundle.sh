#!/usr/bin/env bash
# Make a small zip (~35 MB) of everything needed to deploy, for uploading to Cloud Shell (no GitHub needed).
# usage (on your computer, in the project folder): bash deploy/make_bundle.sh   ->  agrisus-deploy.zip
set -euo pipefail
cd "$(dirname "$0")/.."
rm -f agrisus-deploy.zip
zip -qr agrisus-deploy.zip Dockerfile .dockerignore .gcloudignore .env.example cloudbuild.yaml README.md \
  backend frontend bigquery deploy scripts docs data/processed/app data/processed/geo/districts_india.geojson \
  -x '*/node_modules/*' 'frontend/dist/*' '*/__pycache__/*' '*.pyc' '*.DS_Store' '.deploy-venv/*' '*/.env' '*/.env.*'
ls -lh agrisus-deploy.zip
