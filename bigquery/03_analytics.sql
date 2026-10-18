-- Example analytics the Co-pilot can run (query_bigquery tool) and judges can reproduce.

-- 1. Over-exploited districts ranked by paddy area (water-intensive crop on depleting aquifers)
SELECT x.state_name, x.district_name, x.cgwb_stage_of_extraction_pct, ROUND(SUM(b.area_ha)) AS paddy_ha
FROM `${PROJECT}.${DATASET}.district_context` x
JOIN `${PROJECT}.${DATASET}.baseline_crop_area_district` b USING (district_code)
WHERE b.crop = 'rice' AND x.cgwb_category = 'Over-Exploited'
GROUP BY 1, 2, 3 ORDER BY paddy_ha DESC LIMIT 20;

-- 2. Latest optimizer runs: 2030 KPIs by scenario
SELECT scenario, JSON_VALUE(kpis_json, '$.assi') AS assi,
       JSON_VALUE(kpis_json, '$.import_saving_usd_bn') AS import_saving_usd_bn,
       JSON_VALUE(kpis_json, '$.water_saved_bcm') AS water_saved_bcm, created_at
FROM `${PROJECT}.${DATASET}.simulation_runs` WHERE agri_year = 2030 ORDER BY created_at DESC LIMIT 20;

-- 3. Largest district crop moves in the latest baseline run
SELECT district_code, crop, season, ROUND(plan_area_ha - baseline_area_ha) AS change_ha, cwar
FROM `${PROJECT}.${DATASET}.simulation_decision_traces`
WHERE run_id = (SELECT run_id FROM `${PROJECT}.${DATASET}.simulation_runs` WHERE scenario = 'baseline'
                ORDER BY created_at DESC LIMIT 1) AND agri_year = 2030
ORDER BY ABS(plan_area_ha - baseline_area_ha) DESC LIMIT 25;

-- 4. Where the money goes: 2030 outlay, public returns and MROPI by state (latest run of each scenario)
WITH latest AS (
  SELECT scenario, ARRAY_AGG(run_id ORDER BY created_at DESC LIMIT 1)[OFFSET(0)] AS run_id
  FROM `${PROJECT}.${DATASET}.simulation_runs` GROUP BY scenario
)
SELECT l.scenario, b.state_name, ROUND(SUM(b.total_outlay_cr), 1) AS outlay_cr,
       ROUND(SUM(b.import_value_saved_cr + b.power_subsidy_saved_cr + b.urea_subsidy_saved_cr), 1) AS returns_cr,
       ROUND(SAFE_DIVIDE(SUM(b.import_value_saved_cr + b.power_subsidy_saved_cr + b.urea_subsidy_saved_cr),
                         SUM(b.total_outlay_cr)), 2) AS mropi
FROM `${PROJECT}.${DATASET}.policy_budget_ledger` b JOIN latest l USING (run_id)
WHERE b.agri_year = 2030
GROUP BY 1, 2 ORDER BY outlay_cr DESC LIMIT 30;

-- 5. Spend by MROPI tier and instrument for the latest baseline run
SELECT tier, COUNT(DISTINCT district_code) AS districts, ROUND(SUM(dbt_cr), 1) AS dbt_cr,
       ROUND(SUM(seed_kit_cr), 1) AS seed_kit_cr, ROUND(SUM(price_deficiency_cr), 1) AS price_support_cr
FROM `${PROJECT}.${DATASET}.policy_budget_ledger`
WHERE agri_year = 2030 AND run_id = (SELECT run_id FROM `${PROJECT}.${DATASET}.simulation_runs`
                                     WHERE scenario = 'baseline' ORDER BY created_at DESC LIMIT 1)
GROUP BY tier ORDER BY tier;

-- 6. Most concentrated import dependencies (one supplier shock away from a shortfall)
SELECT commodity_group, product, imports_kt, hhi, top_suppliers
FROM `${PROJECT}.${DATASET}.trade_supplier_concentration` ORDER BY hhi DESC LIMIT 10;

-- 7. Champion vs challenger yield models (BigQuery ML, registered in Vertex AI Model Registry)
SELECT model, label, holdout_wmape_pct, holdout_rows FROM `${PROJECT}.${DATASET}.yield_model_comparison`
ORDER BY holdout_wmape_pct;
