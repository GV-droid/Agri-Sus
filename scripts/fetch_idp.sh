#!/usr/bin/env bash
# Download the full (all-India) India Data Portal resources used by AgriSus.
# They are served by an open CKAN backend: https://ckan.indiadataportal.com/api/3/action/
# Usage: bash scripts/fetch_idp.sh [all|core|trade]      (default: all)
#   core  - agriculture, soil, groundwater, irrigation, land use, MSP           -> data/raw/
#   trade - India's imports by partner country (Import Trade Statistics, ~1 GB) -> data/raw/trade_idp/
set -euo pipefail
SECTION="${1:-all}"
RAW="$(cd "$(dirname "$0")/../data" && pwd)/raw"
mkdir -p "$RAW/trade_idp"

API="https://ckan.indiadataportal.com/api/3/action/resource_show?id="
CORE=(
  "apy_district.csv a000ce51-a257-42e3-8ab7-3cd212966f88"                   # APY district 1997-2023 (DES)
  "apy_upag.csv 9668b3f8-8e23-45a5-b290-4193cbacc670"                       # APY district 2013-2025 (UPAG)
  "agcensus_crop_district.csv 3ac73780-08c2-4e19-aa81-d8d68268fef8"         # Ag Census crop 2010-11
  "agcensus_noncrop_district.csv 19c29a41-65c0-4703-a14b-2833ba28b023"      # Ag Census non-crop 2010-11, 2015-16
  "soil_health_card_village.csv 66860e1a-113a-4ca8-94aa-2418bd462d28"       # SHC village nutrients 2023-25 (~1.1 GB)
  "groundwater_cgwb_wells.csv 5ddef175-b6e3-4a97-b37e-536c3c83001f"         # CGWB well levels 2013-2023
  "groundwater_statewb_wells.csv 7eb15e93-d149-4d78-9c5f-143f2a47ac72"      # State GW boards well levels
  "irrigation_sources_district.csv c737f9df-950e-435c-a5ae-713f59f08cfc"    # Sources of irrigation 1998-2024
  "irrigation_cropwise_district.csv 9b7efbbe-95cb-4ce9-81a1-aa91a62cdad7"   # Crop-wise irrigated area 1998-2009
  "land_use_classification_district.csv f51705fb-52bd-4066-8040-06afdd854461" # Land use 1998-2024
  "msp.csv 232198e1-3a4f-4b28-8c61-c6f72d784311"                            # MSP 1998-2027
)
# Import Trade Statistics (DGCI&S via India Data Portal): HS8 x partner country x month, FY 2017-18 onwards.
# Consumed by scripts/build_trade_idp.py -> data/processed/app/trade_bilateral_dependencies.csv
TRADE=(
  "trade_idp/imports-from-american-countries.csv 615d33b9-fafe-4c18-a05d-672bbe26550e"
  "trade_idp/imports-from-asian-countries.csv 31e441c8-5767-4209-a7ef-dca32aff64a8"
  "trade_idp/imports-from-european-countries.csv d2e5ed6a-b4b6-4bf9-b52c-b5b943d051db"
  "trade_idp/imports-from-african-countries.csv 931e4706-1c0e-4997-aca3-232919440b90"
  "trade_idp/imports-from-oceanic-countries.csv a0a660b5-8b2a-43aa-84bb-8f49bdb5c2e4"
)

case "$SECTION" in
  all)   LIST=("${CORE[@]}" "${TRADE[@]}") ;;
  core)  LIST=("${CORE[@]}") ;;
  trade) LIST=("${TRADE[@]}") ;;
  *) echo "usage: bash scripts/fetch_idp.sh [all|core|trade]" >&2; exit 2 ;;
esac

cd "$RAW"
pids=()
for pair in "${LIST[@]}"; do
  read -r file id <<<"$pair"
  url=$(curl -s --retry 3 "${API}${id}" | python3 -c "import json,sys;print(json.load(sys.stdin)['result']['url'])")
  echo "-> $file"
  curl -sSL --retry 3 --fail -o "$file" "$url" &
  pids+=($!)
done
fail=0
for p in "${pids[@]}"; do wait "$p" || fail=1; done
ls -lh . trade_idp
if [ "$fail" -ne 0 ]; then echo "Some downloads failed - re-run the script." >&2; exit 1; fi
if [ "$SECTION" != "core" ]; then
  echo "Next: python scripts/build_trade_idp.py   (rebuilds trade_bilateral_dependencies.csv)"
fi
