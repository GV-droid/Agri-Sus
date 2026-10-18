-- BigQuery ML yield models, both registered in the Vertex AI Model Registry (model_registry='VERTEX_AI').
--   yield_climate_response : LINEAR_REG twin of backend/app/yield_model.py (the agronomic prior = champion)
--   yield_boosted_tree     : BOOSTED_TREE_REGRESSOR challenger (XGBoost in BigQuery) with crop, region, soil,
--                            irrigation and weather anomalies, so it can learn non-linear interactions
-- Target: log(yield / trailing-5-year mean yield). Training 2004-2019, hold-out 2020-2024 (CUSTOM split).
-- The app promotes the challenger only if it beats the champion on hold-out WMAPE (YIELD_MODEL=auto).

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.yield_features` AS
SELECT
  district_code, state_name, crop, season, agri_year, area_ha, yield_kg_ha, trailing5_yield_kg_ha,
  LN(yield_ratio) AS log_yield_ratio,
  LEAST(GREATEST(IFNULL(season_rain_anom_pct, 0), -80), 150) / 100 AS rain_anom,
  1 - LEAST(GREATEST(IFNULL(irrigation_coverage_pct, 50) / 100, 0), 1) AS rainfed,
  LEAST(LEAST(GREATEST(IFNULL(season_rain_anom_pct, 0), -80), 150) / 100, 0)
    * (1 - LEAST(GREATEST(IFNULL(irrigation_coverage_pct, 50) / 100, 0), 1)) AS rain_deficit_x_rainfed,
  LEAST(GREATEST(LEAST(GREATEST(IFNULL(season_rain_anom_pct, 0), -80), 150) / 100, 0), 0.3)
    * (1 - LEAST(GREATEST(IFNULL(irrigation_coverage_pct, 50) / 100, 0), 1)) AS rain_surplus_x_rainfed,
  GREATEST(LEAST(GREATEST(IFNULL(season_rain_anom_pct, 0), -80), 150) / 100 - 0.5, 0)
    * (1 - LEAST(GREATEST(IFNULL(irrigation_coverage_pct, 50) / 100, 0), 1)) AS rain_excess_x_rainfed,
  IFNULL(season_tmax_anom_c, 0) * CASE crop WHEN 'wheat' THEN -0.05 WHEN 'rice' THEN -0.03 ELSE -0.03 END
    AS tmax_anom_c_x_sensitivity,
  IF(crop = 'wheat', IFNULL(heat_days_anom, 0), 0) AS wheat_terminal_heat_days,
  IF(season = 'Kharif' AND IFNULL(dry_spell_days, 0) >= 14,
     1 - LEAST(GREATEST(IFNULL(irrigation_coverage_pct, 50) / 100, 0), 1), 0) AS kharif_dry_spell14_x_rainfed,
  LEAST(IFNULL(extreme_rain_days, 0), 10) AS extreme_rain_days,
  -- raw drivers for the boosted tree (same names are rebuilt for 2026-30 in yield_climate_factor_bqml below)
  IFNULL(season_tmax_anom_c, 0) AS tmax_anom_c,
  IF(season = 'Kharif', 0, IFNULL(heat_days_anom, 0)) AS heat_anom_days,
  IF(season = 'Kharif', IFNULL(dry_spell_days, 0), 0) AS dry_spell_days,
  groundwater_share, soil_n_kg_ha_est, soil_oc_pct_est, lat, lon
FROM `${PROJECT}.${DATASET}.yield_training`
WHERE crop NOT IN ('sugarcane', 'cotton') AND yield_ratio IS NOT NULL AND yield_ratio > 0;

-- 1. Champion: linear twin of the agronomic prior
CREATE OR REPLACE MODEL `${PROJECT}.${DATASET}.yield_climate_response`
OPTIONS (
  model_type = 'LINEAR_REG',
  input_label_cols = ['log_yield_ratio'],
  l2_reg = 1.0,
  data_split_method = 'CUSTOM',
  data_split_col = 'is_eval',
  model_registry = 'VERTEX_AI',
  vertex_ai_model_id = 'agrisus_yield_climate_response'
) AS
SELECT
  rain_deficit_x_rainfed, rain_surplus_x_rainfed, rain_excess_x_rainfed, tmax_anom_c_x_sensitivity,
  wheat_terminal_heat_days, kharif_dry_spell14_x_rainfed, extreme_rain_days, log_yield_ratio,
  agri_year >= 2020 AS is_eval
FROM `${PROJECT}.${DATASET}.yield_features`;

-- 2. Challenger: gradient-boosted trees (early stopping on the 2020-24 hold-out)
CREATE OR REPLACE MODEL `${PROJECT}.${DATASET}.yield_boosted_tree`
OPTIONS (
  model_type = 'BOOSTED_TREE_REGRESSOR',
  input_label_cols = ['log_yield_ratio'],
  booster_type = 'GBTREE',
  max_iterations = 300,
  learn_rate = 0.05,
  max_tree_depth = 6,
  subsample = 0.8,
  colsample_bytree = 0.8,
  min_tree_child_weight = 20,
  l2_reg = 1.0,
  early_stop = TRUE,
  min_rel_progress = 0.001,
  data_split_method = 'CUSTOM',
  data_split_col = 'is_eval',
  enable_global_explain = TRUE,
  model_registry = 'VERTEX_AI',
  vertex_ai_model_id = 'agrisus_yield_boosted_tree'
) AS
SELECT
  crop, season, state_name, rain_anom, rainfed, tmax_anom_c, heat_anom_days, dry_spell_days, extreme_rain_days,
  groundwater_share, soil_n_kg_ha_est, soil_oc_pct_est, lat, lon, log_yield_ratio,
  agri_year >= 2020 AS is_eval
FROM `${PROJECT}.${DATASET}.yield_features`;

-- 3. Hold-out (2020-24) comparison: area-weighted MAPE of the implied yield, champion vs challenger vs persistence
CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.yield_model_comparison` AS
SELECT 'bqml_boosted_tree' AS model, 'BigQuery ML BOOSTED_TREE_REGRESSOR (challenger)' AS label,
  ROUND(100 * SUM(area_ha * ABS(SAFE_DIVIDE(trailing5_yield_kg_ha * EXP(predicted_log_yield_ratio), yield_kg_ha) - 1))
        / SUM(area_ha), 2) AS holdout_wmape_pct,
  COUNT(*) AS holdout_rows, CURRENT_TIMESTAMP() AS evaluated_at
FROM ML.PREDICT(MODEL `${PROJECT}.${DATASET}.yield_boosted_tree`,
                (SELECT * FROM `${PROJECT}.${DATASET}.yield_features` WHERE agri_year >= 2020))
UNION ALL
SELECT 'bqml_linear_prior_twin', 'BigQuery ML LINEAR_REG twin of the agronomic prior (champion)',
  ROUND(100 * SUM(area_ha * ABS(SAFE_DIVIDE(trailing5_yield_kg_ha * EXP(predicted_log_yield_ratio), yield_kg_ha) - 1))
        / SUM(area_ha), 2),
  COUNT(*), CURRENT_TIMESTAMP()
FROM ML.PREDICT(MODEL `${PROJECT}.${DATASET}.yield_climate_response`,
                (SELECT * FROM `${PROJECT}.${DATASET}.yield_features` WHERE agri_year >= 2020))
UNION ALL
SELECT 'persistence', 'Trailing 5-year mean yield (no climate signal)',
  ROUND(100 * SUM(area_ha * ABS(SAFE_DIVIDE(trailing5_yield_kg_ha, yield_kg_ha) - 1)) / SUM(area_ha), 2),
  COUNT(*), CURRENT_TIMESTAMP()
FROM `${PROJECT}.${DATASET}.yield_features` WHERE agri_year >= 2020;

SELECT model, holdout_wmape_pct, holdout_rows FROM `${PROJECT}.${DATASET}.yield_model_comparison` ORDER BY holdout_wmape_pct;

-- 4. What drives the challenger (gain-based importance)
SELECT feature, ROUND(importance_gain, 3) AS importance_gain
FROM ML.FEATURE_IMPORTANCE(MODEL `${PROJECT}.${DATASET}.yield_boosted_tree`) ORDER BY importance_gain DESC LIMIT 10;

-- 5. 2026-30 climate factor per district x crop x season from the challenger, as a counterfactual:
--    exp(prediction under the projected climate - prediction under the district's normal climate), so trend and
--    location effects cancel and only the climate signal remains (clipped to 0.4-1.3 like the prior).
CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.yield_climate_factor_bqml`
CLUSTER BY district_code AS
WITH plan AS (
  SELECT
    c.district_code, c.state_name, c.crop, c.season, p.agri_year,
    1 - LEAST(GREATEST(IFNULL(x.irrigation_coverage_pct, 50) / 100, 0), 1) AS rainfed,
    x.groundwater_share, x.soil_n_kg_ha_est, x.soil_oc_pct_est, x.lat, x.lon,
    LEAST(GREATEST(100 * (SAFE_DIVIDE(IF(c.season = 'Kharif', p.proj_kharif_rain_mm, p.proj_rabi_rain_mm),
                                      IF(c.season = 'Kharif', p.clim_kharif_rain_mm, p.clim_rabi_rain_mm)) - 1),
                   -80), 150) / 100 AS rain_anom_proj,
    IF(c.season = 'Kharif', p.kharif_tmax_c_delta, p.rabi_tmax_c_delta) AS tmax_proj,
    IF(c.season = 'Kharif', 0, p.terminal_heat_days_delta) AS heat_proj,
    IF(c.season = 'Kharif', p.proj_kharif_max_dry_spell_days, 0) AS dry_proj,
    IF(c.season = 'Kharif', p.clim_kharif_max_dry_spell_days, 0) AS dry_norm,
    LEAST(IF(c.season = 'Kharif', p.proj_kharif_heavy_rain_days, p.proj_harvest_extreme_rain_days), 10) AS xr_proj,
    LEAST(IF(c.season = 'Kharif', p.clim_kharif_heavy_rain_days, p.clim_harvest_extreme_rain_days), 10) AS xr_norm
  FROM `${PROJECT}.${DATASET}.district_crop_candidates` c
  JOIN `${PROJECT}.${DATASET}.district_context` x USING (district_code)
  JOIN `${PROJECT}.${DATASET}.${CLIMATE_TABLE}` p USING (district_code)
  WHERE c.crop NOT IN ('sugarcane', 'cotton')
),
scored AS (
  SELECT * FROM ML.PREDICT(MODEL `${PROJECT}.${DATASET}.yield_boosted_tree`, (
    SELECT 'projected' AS variant, district_code, state_name, crop, season, agri_year, rainfed, groundwater_share,
      soil_n_kg_ha_est, soil_oc_pct_est, lat, lon, IFNULL(rain_anom_proj, 0) AS rain_anom,
      IFNULL(tmax_proj, 0) AS tmax_anom_c, IFNULL(heat_proj, 0) AS heat_anom_days, IFNULL(dry_proj, 0) AS dry_spell_days,
      IFNULL(xr_proj, 0) AS extreme_rain_days
    FROM plan
    UNION ALL
    SELECT 'normal', district_code, state_name, crop, season, agri_year, rainfed, groundwater_share,
      soil_n_kg_ha_est, soil_oc_pct_est, lat, lon, 0, 0, 0, IFNULL(dry_norm, 0), IFNULL(xr_norm, 0)
    FROM plan))
)
SELECT district_code, crop, season, agri_year,
  ROUND(LEAST(GREATEST(EXP(SUM(IF(variant = 'projected', predicted_log_yield_ratio, -predicted_log_yield_ratio))),
                       0.4), 1.3), 4) AS climate_factor
FROM scored
GROUP BY district_code, crop, season, agri_year;

SELECT season, ROUND(AVG(climate_factor), 4) AS mean_factor, ROUND(MIN(climate_factor), 3) AS min_factor,
       ROUND(MAX(climate_factor), 3) AS max_factor, COUNT(*) AS n_rows
FROM `${PROJECT}.${DATASET}.yield_climate_factor_bqml` WHERE agri_year = 2030 GROUP BY season;
