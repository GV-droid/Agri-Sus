-- Agri Sus BigQuery dataset (run after deploy/load_bigquery.py has loaded the snapshot tables).
-- Replace ${PROJECT} / ${DATASET} or run via deploy/load_bigquery.py which templates them.

-- Feature view used by the optimizer: one row per district x crop x season with water, soil and economics context.
CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.district_seasonal_features` AS
SELECT
  c.district_code, x.district_name, c.state_name, c.crop, c.season,
  c.baseline_area_ha, c.baseline_yield_kg_ha, c.yield_source,
  a.family, a.commodity_category, a.kc_avg, a.extra_water_mm, a.n_credit_next_crop_kg_ha, a.optimizer_crop,
  x.net_sown_area_ha, x.irrigation_coverage_pct, x.groundwater_share, x.canal_share, x.current_fallow_ha,
  x.soil_n_kg_ha_est, x.soil_oc_pct_est, x.cgwb_stage_of_extraction_pct, x.cgwb_category,
  x.safe_gw_allowance_mm_yr, x.current_gw_irrigation_mm_yr, x.canal_supply_mm_season, x.micro_irrigation_share,
  IF(c.season = 'Kharif', x.clim_kharif_rain_mm, x.clim_rabi_rain_mm) AS clim_season_rain_mm,
  m.msp_inr_per_qtl,
  COALESCE(cs.a2fl_inr_ha, cn.a2fl_inr_ha) AS cost_a2fl_inr_ha
FROM `${PROJECT}.${DATASET}.district_crop_candidates` c
JOIN `${PROJECT}.${DATASET}.district_context` x USING (district_code)
JOIN `${PROJECT}.${DATASET}.crop_agronomy` a USING (crop)
LEFT JOIN (SELECT crop, ANY_VALUE(msp_inr_per_qtl) msp_inr_per_qtl FROM `${PROJECT}.${DATASET}.msp_latest`
           WHERE crop IS NOT NULL GROUP BY crop) m USING (crop)
LEFT JOIN `${PROJECT}.${DATASET}.cost_of_cultivation_state_crop` cs ON cs.state_name = c.state_name AND cs.crop = c.crop
LEFT JOIN `${PROJECT}.${DATASET}.cost_of_cultivation_state_crop` cn ON cn.state_name = '_ALL_' AND cn.crop = c.crop;

-- Decision traces written by every optimizer run (Cloud Run service account needs roles/bigquery.dataEditor).
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.simulation_decision_traces` (
  run_id STRING, trace_id STRING, created_at TIMESTAMP, scenario STRING,
  district_code INT64, state_name STRING, agri_year INT64, season STRING, crop STRING,
  baseline_area_ha FLOAT64, plan_area_ha FLOAT64, predicted_yield_kg_ha FLOAT64, net_margin_inr_ha FLOAT64,
  cwar FLOAT64, reduced_cost FLOAT64, params_json STRING
) PARTITION BY DATE(created_at) CLUSTER BY scenario, district_code;

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.simulation_runs` (
  run_id STRING, created_at TIMESTAMP, scenario STRING, agri_year INT64, kpis_json STRING
) PARTITION BY DATE(created_at);

-- Fiscal ledger written by every optimizer run: district x plan-year outlay per instrument and the public returns
-- behind MROPI (Marginal Return On Public Investment = (forex + power-subsidy + urea-subsidy savings) / outlay).
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.policy_budget_ledger` (
  run_id STRING, created_at TIMESTAMP, scenario STRING, mode STRING, agri_year INT64,
  state_name STRING, district_code INT64, district_name STRING,
  dbt_cr FLOAT64, seed_kit_cr FLOAT64, price_deficiency_cr FLOAT64, total_outlay_cr FLOAT64,
  area_shifted_ha FLOAT64, water_saved_mcm FLOAT64, import_value_saved_cr FLOAT64,
  power_subsidy_saved_cr FLOAT64, urea_subsidy_saved_cr FLOAT64, mropi FLOAT64, tier STRING
) PARTITION BY DATE(created_at) CLUSTER BY scenario, state_name, tier
OPTIONS (description = 'Agri Sus policy budget ledger: instrument outlays and public returns per district, year and run');

-- Policy documents (sections A-H + annex) written by the document agent: one row per section per saved version, so
-- the co-pilot and analysts can query what every version of a policy document says. The app's load job also creates
-- this table on first use.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.policy_documents` (
  doc_id STRING, run_id STRING, policy_id STRING, policy_name STRING, scope STRING, language STRING, version INT64,
  created_at TIMESTAMP, generator STRING, model STRING, title STRING, section_id STRING, section_title STRING,
  section_order INT64, markdown STRING, instructions STRING, import_verdicts_json STRING
) PARTITION BY DATE(created_at) CLUSTER BY run_id, scope, language
OPTIONS (description = 'Agri Sus policy documents: one row per section of every saved document version');

-- Import dependence per product: supplier concentration (Herfindahl-Hirschman index, 0-1) in the latest complete
-- fiscal year (the in-progress year is partial)
CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.trade_supplier_concentration` AS
WITH latest AS (SELECT MAX(fiscal_year) AS fy FROM `${PROJECT}.${DATASET}.trade_bilateral_dependencies`
                WHERE fy_months_covered >= 12)
SELECT
  t.fiscal_year, t.commodity_group, t.product,
  ROUND(SUM(t.qty_t) / 1000, 1) AS imports_kt,
  ROUND(SUM(t.value_usd_mn), 1) AS imports_usd_mn,
  ROUND(SUM(POW(t.share_of_product_pct / 100, 2)), 3) AS hhi,
  ARRAY_AGG(STRUCT(t.country, t.share_of_product_pct) ORDER BY t.share_of_product_pct DESC LIMIT 3) AS top_suppliers
FROM `${PROJECT}.${DATASET}.trade_bilateral_dependencies` t, latest
WHERE t.fiscal_year = latest.fy
GROUP BY t.fiscal_year, t.commodity_group, t.product;
