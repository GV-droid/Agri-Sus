# Model card: Agri Sus yield response model (and the policy, fiscal and trade models around it)

**Purpose:** scale the 5-year baseline district yields under the 2027–30 climate scenarios (CMIP6 deltas and user or Gemini shocks), so the optimizer can stress-test crop plans.
**Not for:** point forecasts of district yield in any one season.

## Form
yield = trend-adjusted 5-yr baseline × climate_factor, where climate_factor = clip(1 + λ·Σ βₖ·xₖ, 0.4, 1.3)

| Term xₖ | Prior βₖ | Basis |
|---|---|---|
| rain deficit × rainfed share | +0.35 per unit deficit | Rainfed yield elasticity to monsoon deficit (ICRISAT / Economic Survey 2017-18, ch. 6) |
| rain surplus (≤30%) × rainfed | +0.05 | Diminishing returns to surplus rain |
| rain excess (>50%) × rainfed | −0.10 | Waterlogging and flood losses |
| Tmax anomaly × crop sensitivity | wheat −5%/°C, rice −3%/°C, others −3%/°C | Lobell et al. 2012 (wheat, terminal heat); Asseng et al. 2015; Zhao et al. 2017 PNAS |
| wheat terminal heat days (>35 °C) | −0.5%/day | Lobell et al. 2012 |
| Kharif 14-day dry spell × rainfed | −5% | IMD/ICAR dry-spell studies |
| extreme rain days | −1%/day | Flood damage literature |

λ is a single shrinkage factor, calibrated by least squares on the detrended log-yield anomalies:

- λ = 0.455 when fitted on 2001–2019;
- λ = 0.362 when fitted on all years (the deployed value).

## Data
- UPAG/APY district yields 2001–2024: 76,827 training rows and 28,542 holdout rows, covering every state and UT.
- IMD gridded rainfall and NASA POWER temperature.
- Irrigation coverage from the Land Use Statistics.
- **Cleaning before the optimizer:** 157 district baseline yields are repaired (`backend/app/data.py`, `clean_yields`). The source has unit errors (for example, groundnut at 132 t/ha and moong at 5 t/ha) and zero yields.
  - Values above 1.6× a crop-specific agronomic ceiling are treated as missing and imputed from the state median, or the national median if there is no state value.
  - Remaining values are clipped to the range from 15% of the national median up to the ceiling.
  - The count is reported by `/api/meta` as `yield_rows_repaired`.

## Evaluation (holdout 2020-21 … 2024-25)

| | Model WMAPE | Persistence WMAPE |
|---|---|---|
| All rows | 18.23% | 17.92% |
| Climate-shock rows (n = 2,740) | 18.48% | 17.61% |

The model **does not beat persistence** on point accuracy. At district level, rainfall correlates only weakly with the yield anomaly (|r| < 0.1), because noise from seed, pests, prices and reporting dominates. The model is therefore used only for *directional, literature-consistent* shock responses.

Rejected alternatives:

- HistGradientBoosting: worse than persistence on the holdout.
- Unconstrained Huber regression: coefficients flipped sign between fits.

## BigQuery ML: champion and challenger
`bigquery/02_bqml_yield_model.sql` trains two models on the same 2004–19 training rows. It holds out 2020–24 (`data_split_method = 'CUSTOM'`), and both models are registered in the **Vertex AI Model Registry** (`MODEL_REGISTRY = 'VERTEX_AI'`).

| Model | Type | Inputs | Role |
|---|---|---|---|
| `yield_climate_response` | `LINEAR_REG` | The prior's engineered terms (table above) | Champion: an unconstrained twin of the agronomic prior |
| `yield_boosted_tree` | `BOOSTED_TREE_REGRESSOR` (300 rounds max, depth 6, early stopping, global explain) | Crop, season, state, rain anomaly, rainfed share, Tmax anomaly, heat days, dry spells, extreme rain, groundwater share, soil N and OC, lat/lon | Challenger: can learn non-linear crop × region × weather interactions |

- **Hold-out comparison.** `yield_model_comparison` stores the area-weighted MAPE of the implied yield for the challenger, the champion and persistence on 2020–24. `ML.FEATURE_IMPORTANCE` lists what drives the challenger.
- **Climate factor.** The challenger's 2026–30 factor per district × crop × season × year is a counterfactual: exp(prediction under the projected climate − prediction under the district's normal climate). Trend and location effects cancel, so only the climate signal remains. It is clipped to 0.4–1.3 like the prior, and stored in `yield_climate_factor_bqml`.
- **Promotion rule** (`backend/app/data.py`). With `YIELD_MODEL=auto` (the default), the app uses the boosted-tree factors only if the challenger's hold-out WMAPE is lower than the champion's. Otherwise the agronomic prior stays.
  - `YIELD_MODEL=prior` or `YIELD_MODEL=bqml` forces either engine.
  - Crops and districts the challenger does not cover (sugarcane, cotton) keep the prior.
  - What-if shocks always act through the prior's marginal response on top of the active factor.
- **Reporting.** `/api/meta` → `yield_engine` reports the active engine, the hold-out WMAPEs and the reason. On the offline snapshot it reports `agronomic_prior` ("BigQuery ML factors not available").

## Climate inputs
- **Bundled snapshot.** IMD gridded rainfall and NASA POWER temperature give the history. NEX-GDDP-CMIP6 SSP2-4.5 gives the 2026–30 delta, with the rain ratio clipped to 0.75–1.3.
- **Google Earth Engine (optional).** `scripts/gee_climate.py` computes the same indicators from CHIRPS daily rain, ERA5-Land daily temperature and NASA NEX-GDDP-CMIP6 (SSP2-4.5), averaged over each whole district polygon rather than sampled at its centroid, for Kharif and Rabi. `bigquery/04_climate_from_earth_engine.sql` turns the result into `climate_projection_ee`, with the same columns as the bundled table. The deploy script then re-runs the BigQuery ML step with `CLIMATE_TABLE=climate_projection_ee` and passes the same setting to Cloud Run, so both the app and BigQuery ML use it. `/api/meta` → `climate_source` says which source is live.

## Next-crop ranking
For a district and plan year, every candidate crop in each season is scored on four things:

- **Predicted yield:** the district baseline × the active climate factor for that year.
- **Water feasibility:** CWAR, the irrigation need ÷ (safe groundwater + canal supply).
- **Net margin** at expected prices.
- **Rotation fit** after the preceding season's dominant crop: cereal after cereal, a late-sown wheat after long-duration paddy, terminal heat, a drier season, or the legume nitrogen credit.

The decision (expand / keep / reduce) and the recommended crop come from the LP plan, not from a separate ranking. The closest rejected alternative is explained by its reduced cost: the cost to the plan of forcing one hectare of it.

## Policy instruments, fiscal ledger and MROPI
One annual budget funds three instruments. The LP chooses the mix per district and year, and every instrument costs society the marginal cost of public funds.

| Parameter | Value | Basis |
|---|---|---|
| Income-parity transfer (DBT) | As needed per district | Keeps every district's farm income at or above today, like Haryana's *Mera Pani Meri Virasat* (₹7,000/acre) |
| Seed & FPO kit | ₹4,000/ha per season, +10% yield on pulses/oilseeds | Certified seed, rhizobium/PSB, micronutrients, FPO aggregation. NFSM cluster demonstrations report +20–40%, so +10% is conservative |
| Price-deficiency payment | Top-up to MSP, +3% admin cost; removes 50% of the switching-risk premium | PM-AASHA / Bhavantar-style payments |
| Marginal cost of public funds | 1.25 | Conventional cost-benefit value for the extra cost of raising ₹1 of tax (an assumption) |
| Farm-power subsidy avoided | ₹1.5 per m³ not pumped | ~0.3 kWh/m³ × ~₹5/kWh unrecovered cost |
| Urea subsidy avoided | ₹32,000/t (urea 46% N) | FY 2024-25: ~₹1.19 lakh cr over ~37 Mt sold |
| Exchange rate | ₹84/$ | RBI reference rate, FY 2024-25 average |

- **Fiscal ledger.** Outlay is set against three returns:
  - forex on pulse and edible-oil imports avoided, at landed prices;
  - farm-power subsidy on groundwater no longer pumped;
  - urea subsidy saved through legume nitrogen.
- **Two summary ratios.** The *public return* ratio is all three returns ÷ outlay. *Budget payback* counts only power + urea ÷ outlay, because forex is an economy-wide gain, not a fiscal receipt.
- **MROPI.** The public return per rupee of outlay in each district:
  - Tier 1: ≥ 10×;
  - Tier 2: 1–10×;
  - Tier 3: < 1×. These districts are funded for farm income and aquifer security, not payback;
  - Self-financing: no outlay, positive return;
  - No outlay: no public money spent and no net public return.
- **Least-cost mode.** The LP minimises the budget subject to the import-bill target as a hard constraint, plus every guardrail.

## Trade data
Country-wise imports come from the India Data Portal's *Import Trade Statistics* (DGCI&S monthly, country × HS8). `scripts/build_trade_idp.py` aggregates them to fiscal year × country × product for pulses (HS 0713, by type) and edible oils (HS 1507, 1508 and 1511–1515), then normalises the portal's changing units (INR lakh vs crore, tonnes vs kg) against the implied exchange rate and each product's landed-price band.

- The app uses the latest *complete* fiscal year (FY 2024-25; FY 2025-26 is partial).
- A supplier cut becomes a tonnage phased over 2027–30 (¼, ½, ¾, all) and enters the LP's commodity balance. Domestic production must replace it; any shortfall is reported with the remaining suppliers.
- **Supplier concentration** is the Herfindahl index (0–1) of country shares.

## Risks
- Climate factors are clipped to the range 0.4–1.3.
- The CMIP6 rain ratios are clipped to 0.75–1.3.
- Results are scenario outputs for planners. They are not farm-level advice.
