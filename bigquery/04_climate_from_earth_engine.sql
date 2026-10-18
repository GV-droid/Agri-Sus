-- Climate projections from the Google Earth Engine pipeline (scripts/gee_climate.py), same delta method and
-- columns as the bundled climate_projection_district_year table, so the app can switch with
-- CLIMATE_TABLE=climate_projection_ee (deploy/deploy.sh does this when USE_EARTH_ENGINE=1).
--   observed climatology : ee_climate_history  (CHIRPS daily rain + ERA5-Land daily temperature, 2005-2024 means)
--   change signal        : ee_cmip6_*          (NASA NEX-GDDP-CMIP6, SSP2-4.5 2026-30 vs historical 2005-13,
--                                               ratio for rain/ET0, delta for temperature and day counts)
--   per model 2026-30 window mean -> ensemble mean, applied to every plan year, ratios clipped to 0.75-1.3.
-- Run by: python deploy/load_bigquery.py --with-ee

CREATE OR REPLACE TABLE `${PROJECT}.${DATASET}.climate_projection_ee`
CLUSTER BY district_code
OPTIONS (description = 'District x year climate projection (SSP2-4.5) built from Google Earth Engine CHIRPS, ERA5-Land and NEX-GDDP-CMIP6') AS
WITH hist AS (
  SELECT * FROM `${PROJECT}.${DATASET}.ee_climate_history` WHERE agri_year BETWEEN 2005 AND 2024
),
clim AS (
  SELECT district_code,
    AVG(kharif_rain_mm) AS clim_kharif_rain_mm,
    AVG(kharif_tmax_c) AS clim_kharif_tmax_c,
    AVG(kharif_max_dry_spell_days) AS clim_kharif_max_dry_spell_days,
    AVG(kharif_heavy_rain_days) AS clim_kharif_heavy_rain_days,
    AVG(kharif_et0_mm) AS clim_kharif_et0_mm,
    AVG(rabi_rain_mm) AS clim_rabi_rain_mm,
    AVG(rabi_tmax_c) AS clim_rabi_tmax_c,
    AVG(rabi_tmin_c) AS clim_rabi_tmin_c,
    AVG(terminal_heat_days) AS clim_terminal_heat_days,
    AVG(harvest_extreme_rain_days) AS clim_harvest_extreme_rain_days,
    AVG(rabi_et0_mm) AS clim_rabi_et0_mm,
    AVG(CASE WHEN kharif_max_dry_spell_days >= 14 THEN 1 ELSE 0 END) AS clim_kharif_dry_spell_prob
  FROM hist GROUP BY district_code
),
-- export mode writes one table per model x period, local mode one ee_cmip6_all table: keep a single copy of each row
cm_all AS (
  SELECT t.*, _TABLE_SUFFIX AS sfx FROM `${PROJECT}.${DATASET}.ee_cmip6_*` AS t
),
cm AS (
  SELECT * FROM (
    SELECT cm_all.*, ROW_NUMBER() OVER (PARTITION BY district_code, model, period, agri_year ORDER BY sfx) AS rn
    FROM cm_all
  ) WHERE rn = 1
),
base AS (
  SELECT district_code, model,
    AVG(kharif_rain_mm) AS kharif_rain_mm,
    AVG(kharif_tmax_c) AS kharif_tmax_c,
    AVG(kharif_max_dry_spell_days) AS kharif_max_dry_spell_days,
    AVG(kharif_heavy_rain_days) AS kharif_heavy_rain_days,
    AVG(kharif_et0_mm) AS kharif_et0_mm,
    AVG(rabi_rain_mm) AS rabi_rain_mm,
    AVG(rabi_tmax_c) AS rabi_tmax_c,
    AVG(rabi_tmin_c) AS rabi_tmin_c,
    AVG(terminal_heat_days) AS terminal_heat_days,
    AVG(harvest_extreme_rain_days) AS harvest_extreme_rain_days,
    AVG(rabi_et0_mm) AS rabi_et0_mm
  FROM cm WHERE period = 'baseline' GROUP BY district_code, model
),
fut AS (
  SELECT f.district_code, f.model, f.agri_year,
    f.kharif_rain_mm / NULLIF(b.kharif_rain_mm, 0) AS kharif_rain_mm_ratio,
    f.kharif_tmax_c - b.kharif_tmax_c AS kharif_tmax_c_delta,
    f.kharif_max_dry_spell_days - b.kharif_max_dry_spell_days AS kharif_max_dry_spell_days_delta,
    f.kharif_heavy_rain_days - b.kharif_heavy_rain_days AS kharif_heavy_rain_days_delta,
    f.kharif_et0_mm / NULLIF(b.kharif_et0_mm, 0) AS kharif_et0_mm_ratio,
    f.rabi_rain_mm / NULLIF(b.rabi_rain_mm, 0) AS rabi_rain_mm_ratio,
    f.rabi_tmax_c - b.rabi_tmax_c AS rabi_tmax_c_delta,
    f.rabi_tmin_c - b.rabi_tmin_c AS rabi_tmin_c_delta,
    f.terminal_heat_days - b.terminal_heat_days AS terminal_heat_days_delta,
    f.harvest_extreme_rain_days - b.harvest_extreme_rain_days AS harvest_extreme_rain_days_delta,
    f.rabi_et0_mm / NULLIF(b.rabi_et0_mm, 0) AS rabi_et0_mm_ratio
  FROM cm AS f LEFT JOIN base AS b ON b.district_code = f.district_code AND b.model = f.model
  WHERE f.period = 'future' AND f.agri_year BETWEEN 2026 AND 2030
),
per_model AS (
  SELECT district_code, model,
    AVG(kharif_rain_mm_ratio) AS kharif_rain_mm_ratio,
    AVG(kharif_tmax_c_delta) AS kharif_tmax_c_delta,
    AVG(kharif_max_dry_spell_days_delta) AS kharif_max_dry_spell_days_delta,
    AVG(kharif_heavy_rain_days_delta) AS kharif_heavy_rain_days_delta,
    AVG(kharif_et0_mm_ratio) AS kharif_et0_mm_ratio,
    AVG(rabi_rain_mm_ratio) AS rabi_rain_mm_ratio,
    AVG(rabi_tmax_c_delta) AS rabi_tmax_c_delta,
    AVG(rabi_tmin_c_delta) AS rabi_tmin_c_delta,
    AVG(terminal_heat_days_delta) AS terminal_heat_days_delta,
    AVG(harvest_extreme_rain_days_delta) AS harvest_extreme_rain_days_delta,
    AVG(rabi_et0_mm_ratio) AS rabi_et0_mm_ratio
  FROM fut GROUP BY district_code, model
),
ens AS (
  SELECT district_code,
    AVG(kharif_rain_mm_ratio) AS kharif_rain_mm_ratio,
    AVG(kharif_tmax_c_delta) AS kharif_tmax_c_delta,
    AVG(kharif_max_dry_spell_days_delta) AS kharif_max_dry_spell_days_delta,
    AVG(kharif_heavy_rain_days_delta) AS kharif_heavy_rain_days_delta,
    AVG(kharif_et0_mm_ratio) AS kharif_et0_mm_ratio,
    AVG(rabi_rain_mm_ratio) AS rabi_rain_mm_ratio,
    AVG(rabi_tmax_c_delta) AS rabi_tmax_c_delta,
    AVG(rabi_tmin_c_delta) AS rabi_tmin_c_delta,
    AVG(terminal_heat_days_delta) AS terminal_heat_days_delta,
    AVG(harvest_extreme_rain_days_delta) AS harvest_extreme_rain_days_delta,
    AVG(rabi_et0_mm_ratio) AS rabi_et0_mm_ratio,
    COUNT(*) AS n_models
  FROM per_model GROUP BY district_code
),
yrs AS (
  SELECT 2026 AS agri_year UNION ALL SELECT 2027 UNION ALL SELECT 2028 UNION ALL SELECT 2029 UNION ALL SELECT 2030
)
SELECT
  c.district_code, y.agri_year,
  e.kharif_rain_mm_ratio,
  e.kharif_tmax_c_delta,
  e.kharif_max_dry_spell_days_delta,
  e.kharif_heavy_rain_days_delta,
  e.kharif_et0_mm_ratio,
  e.rabi_rain_mm_ratio,
  e.rabi_tmax_c_delta,
  e.rabi_tmin_c_delta,
  e.terminal_heat_days_delta,
  e.harvest_extreme_rain_days_delta,
  e.rabi_et0_mm_ratio,
  e.n_models,
  c.clim_kharif_rain_mm,
  c.clim_kharif_tmax_c,
  c.clim_kharif_max_dry_spell_days,
  c.clim_kharif_heavy_rain_days,
  c.clim_kharif_et0_mm,
  c.clim_rabi_rain_mm,
  c.clim_rabi_tmax_c,
  c.clim_rabi_tmin_c,
  c.clim_terminal_heat_days,
  c.clim_harvest_extreme_rain_days,
  c.clim_rabi_et0_mm,
  c.clim_kharif_dry_spell_prob,
  ROUND(c.clim_kharif_rain_mm * CASE WHEN e.kharif_rain_mm_ratio IS NULL THEN 1.0 ELSE LEAST(GREATEST(e.kharif_rain_mm_ratio, 0.75), 1.3) END, 1) AS proj_kharif_rain_mm,
  GREATEST(ROUND(c.clim_kharif_tmax_c + COALESCE(e.kharif_tmax_c_delta, 0), 2), 0) AS proj_kharif_tmax_c,
  GREATEST(ROUND(c.clim_kharif_max_dry_spell_days + COALESCE(e.kharif_max_dry_spell_days_delta, 0), 2), 0) AS proj_kharif_max_dry_spell_days,
  GREATEST(ROUND(c.clim_kharif_heavy_rain_days + COALESCE(e.kharif_heavy_rain_days_delta, 0), 2), 0) AS proj_kharif_heavy_rain_days,
  ROUND(c.clim_kharif_et0_mm * CASE WHEN e.kharif_et0_mm_ratio IS NULL THEN 1.0 ELSE LEAST(GREATEST(e.kharif_et0_mm_ratio, 0.75), 1.3) END, 1) AS proj_kharif_et0_mm,
  ROUND(c.clim_rabi_rain_mm * CASE WHEN e.rabi_rain_mm_ratio IS NULL THEN 1.0 ELSE LEAST(GREATEST(e.rabi_rain_mm_ratio, 0.75), 1.3) END, 1) AS proj_rabi_rain_mm,
  GREATEST(ROUND(c.clim_rabi_tmax_c + COALESCE(e.rabi_tmax_c_delta, 0), 2), 0) AS proj_rabi_tmax_c,
  GREATEST(ROUND(c.clim_rabi_tmin_c + COALESCE(e.rabi_tmin_c_delta, 0), 2), 0) AS proj_rabi_tmin_c,
  GREATEST(ROUND(c.clim_terminal_heat_days + COALESCE(e.terminal_heat_days_delta, 0), 2), 0) AS proj_terminal_heat_days,
  GREATEST(ROUND(c.clim_harvest_extreme_rain_days + COALESCE(e.harvest_extreme_rain_days_delta, 0), 2), 0) AS proj_harvest_extreme_rain_days,
  ROUND(c.clim_rabi_et0_mm * CASE WHEN e.rabi_et0_mm_ratio IS NULL THEN 1.0 ELSE LEAST(GREATEST(e.rabi_et0_mm_ratio, 0.75), 1.3) END, 1) AS proj_rabi_et0_mm,
  ROUND(LEAST(GREATEST(c.clim_kharif_dry_spell_prob + COALESCE(e.kharif_max_dry_spell_days_delta, 0) / 20, 0), 1), 3)
    AS proj_kharif_dry_spell_prob,
  'SSP2-4.5 (NEX-GDDP-CMIP6 via Google Earth Engine)' AS scenario
FROM clim AS c
CROSS JOIN yrs AS y
LEFT JOIN ens AS e ON e.district_code = c.district_code;

-- Quick comparison with the bundled projection (NASA POWER climatology + 2-model CMIP6 subset)
SELECT
  COUNT(*) AS districts,
  ROUND(AVG(e.proj_kharif_rain_mm - b.proj_kharif_rain_mm), 1) AS mean_diff_kharif_rain_mm,
  ROUND(AVG(e.proj_rabi_tmax_c - b.proj_rabi_tmax_c), 2) AS mean_diff_rabi_tmax_c,
  ROUND(CORR(e.proj_kharif_rain_mm, b.proj_kharif_rain_mm), 3) AS corr_kharif_rain,
  ROUND(CORR(e.proj_rabi_tmax_c, b.proj_rabi_tmax_c), 3) AS corr_rabi_tmax
FROM `${PROJECT}.${DATASET}.climate_projection_ee` AS e
JOIN `${PROJECT}.${DATASET}.climate_projection_district_year` AS b USING (district_code, agri_year)
WHERE e.agri_year = 2030;
