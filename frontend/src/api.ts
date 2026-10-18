export type Kpi = {
  year: number
  assi: number
  assi_status_quo: number
  import_bill_usd_bn: number
  import_bill_status_quo_usd_bn: number
  import_saving_usd_bn: number
  pulses_ssr_pct: number
  pulses_ssr_status_quo_pct: number
  edible_oils_ssr_pct: number
  edible_oils_ssr_status_quo_pct: number
  cereal_ssr_pct: number
  cereal_ssr_status_quo_pct: number
  rice_ssr_pct: number
  wheat_ssr_pct: number
  rice_exportable_surplus_mt: number
  gw_draft_bcm: number
  gw_draft_status_quo_bcm: number
  water_saved_bcm: number
  districts_over_exploited: number
  districts_over_exploited_status_quo: number
  farmer_margin_cr: number
  farmer_margin_status_quo_cr: number
  farmer_income_vs_today_pct: number
  farmer_welfare_change_cr: number
  farmer_accounting_margin_change_cr: number
  districts_below_income_floor: number
  guardrail_income_ok: boolean
  guardrail_cereal_ok: boolean
  import_target_usd_bn: number
  import_target_shortfall_usd_bn?: number
  water_index: number
  water_index_status_quo: number
  legume_share_gca_pct: number
  legume_share_gca_status_quo_pct: number
  diversity_index: number
  diversity_index_status_quo: number
  incentive_cr: number
  n_credit_kt: number
  urea_saved_kt: number
  legume_area_mha: number
  rice_area_mha: number
  rice_area_status_quo_mha: number
  area_shifted_mha: number
  assi_pillars: Pillars
  assi_pillars_status_quo: Pillars
  budget_cr?: number
  outlay_by_instrument_cr?: Mix
  kit_area_mha?: number
  price_support_area_mha?: number
  fiscal_ledger?: Ledger
  pulses_imports_mt?: number
  edible_oils_imports_mt?: number
}

export type Mix = { dbt: number; seed_kits: number; price_support: number }
export type Ledger = {
  outlay_cr: number
  forex_saved_cr: number
  power_subsidy_saved_cr: number
  urea_subsidy_saved_cr: number
  fiscal_return_cr: number
  fiscal_roi: number | null
  public_return_ratio: number | null
}
export type Tier = { tier: string; districts: number; outlay_cr: number; returns_cr: number }
export type PolicyMix = {
  mode: 'fixed' | 'min_budget'
  instruments: { id: keyof Mix; label: string; enabled: boolean; outlay_2030_cr: number }[]
  budget_path_cr: ({ year: number; budget_cr: number; outlay_cr: number } & Mix)[]
  min_budget_cr: number | null
  target_met: boolean
  import_target_shortfall_usd_bn: number
  assumptions: Record<string, number>
  mropi_tiers: Tier[]
  mropi_thresholds: Record<string, string>
}
export type TradeCut = { country: string; product: string; pct: number }
export type TradeOut = {
  cuts: (TradeCut & { alpha3: string; group: string; fiscal_year: string; base_qty_kt: number; base_value_usd_mn: number; share_of_product_pct: number | null; volume_kt: number; value_usd_mn: number; alternatives?: { country: string; qty_kt: number; share_pct: number }[] })[]
  groups: { group: string; volume_kt: number; met: boolean; years: { year: number; required_cut_kt: number; achieved_cut_kt: number; shortfall_kt: number }[]; substitute_crops: { crop: string; extra_kt: number }[]; substitute_districts: { district_code: number; district_name: string; state_name: string; extra_kt: number }[] }[]
}
export type TradeDeps = {
  fiscal_year: string | null
  source?: string
  products: { product: string; group: string; total_kt: number; total_usd_mn: number; hhi: number; suppliers: { country: string; alpha3: string; qty_kt: number; value_usd_mn: number; share_pct: number }[]; history_kt: { fiscal_year: string; qty_kt: number }[] }[]
}
export type Flag = { code: string; level: 'good' | 'info' | 'warn' | 'block'; text: string }
export type Candidate = {
  crop: string; name: string; category: string; family: string; decision: string; baseline_ha: number; plan_ha: number; change_ha: number
  plan_share_pct: number; baseline_yield_kg_ha: number | null; predicted_yield_kg_ha: number; climate_factor: number; yield_source: string
  irrigation_need_mm: number; cwar: number; water_ok: boolean; price_inr_qtl: number; net_margin_inr_ha: number; n_credit_kg_ha: number
  kit_ha: number; price_support_ha: number; reduced_cost_inr_ha: number | null; groundwater_cap_cost_inr_ha: number | null; flags: Flag[]
}
export type CropRef = { crop: string; name: string; area_ha: number; share_pct: number } | null
export type Candidates = {
  district_code: number; district_name: string; state_name: string; year: number; groundwater_cap: string | null
  climate: { terminal_heat_days_delta: number; kharif_rain_ratio: number | null; rabi_rain_ratio: number | null; rabi_tmax_delta_c: number | null; scenario: string | null }
  seasons: {
    season: string
    preceding: { season: string; year: number; label: string; crop: CropRef }
    plan_main_crop: CropRef
    today_main_crop: CropRef
    recommended: { crop: string; name: string; why: string; positives: string[]; cautions: string[] } | null
    closest_alternative: { crop: string; name: string; cost_to_plan_inr_ha: number; groundwater_cap_cost_inr_ha: number | null; why: string } | null
    candidates: Candidate[]
  }[]
  method: string
}

export type Pillars = { self_reliance: number; water: number; soil: number; diversity: number }
export type DistrictPillars = { water: number; soil: number; diversity: number }

export type StateRow = {
  state_name: string
  districts: number
  score: number
  score_status_quo: number
  score_gain: number
  water_saved_mcm: number
  rice_area_change_ha: number
  legume_area_change_ha: number
  incentive_cr: number
  area_shifted_ha: number
  outlay_cr?: Mix
  returns_cr?: number
  mropi?: number | null
}

export type StressTest = {
  id: string
  label: string
  min_assi: number
  plan_beats_status_quo: boolean
  years: { year: number; assi: number; assi_status_quo: number; cereal_ssr_pct: number; import_bill_usd_bn: number }[]
}

export type DistrictYear = {
  gw_stage_pct: number | null
  gw_stage_status_quo_pct: number | null
  legume_share_pct: number | null
  legume_share_status_quo_pct: number | null
  rice_share_pct: number | null
  rice_share_status_quo_pct: number | null
  rice_area_change_pct: number | null
  margin_change_pct: number | null
  income_vs_today_pct: number | null
  water_assessed: boolean
  incentive_cr: number
  area_shifted_ha: number
  sustainability_score: number
  sustainability_score_status_quo: number
  score_pillars: DistrictPillars
  score_pillars_status_quo: DistrictPillars
  water_saved_mcm: number
  rice_area_change_ha: number
  legume_area_change_ha: number
  outlay_cr?: Mix
  returns_cr?: { forex: number; power_subsidy: number; urea_subsidy: number }
  kit_ha?: number
  price_support_ha?: number
  mropi?: number | null
  mropi_tier?: string
  gw_cap?: string | null
}

export type DistrictResult = {
  district_code: number
  district_name: string
  state_name: string
  years: Record<string, DistrictYear>
}

export type SimResult = {
  run_id: string
  params: Record<string, unknown> & { scenario: string }
  solver: { engine: string; status: string; variables: number; constraints: number; seconds: number }
  kpis: Kpi[]
  binding_constraints: { constraint: string; year: number; shadow_price: string }[]
  constraint_summary: Record<string, number>
  lever_status?: Record<string, LeverStatus>
  districts: DistrictResult[]
  states: StateRow[]
  robustness: StressTest[]
  assi_weights: Record<string, number>
  district_score_weights: Record<string, number>
  outlook?: Outlook
  policy_mix?: PolicyMix
  trade?: TradeOut | null
}

export type LeverStatus = { state: 'binding' | 'slack' | 'missed' | 'active' | 'inactive'; note: string }

export type Outlook = {
  years: { year: number; enso_phase: string; kharif_rain_pct_vs_normal: number; rabi_tmax_anomaly_c: number; confidence: string }[]
  import_price_multipliers: Record<string, number>
  rationale: string
  citations: { title: string; uri: string }[]
  model: string
}

export type Meta = {
  plan_years: number[]
  scenarios: { id: string; label: string; description: string; levers?: Record<string, unknown>; focus_year?: number | null }[]
  gemini: { enabled: boolean; agent_model: string; forecast_model: string; backend: string }
  data_backend: string
  yield_model: Record<string, unknown>
  yield_engine?: { active: string; mode: string; reason?: string; holdout_wmape_pct?: Record<string, number> }
  climate_source?: string
  states: string[]
  n_districts: number
  yield_rows_repaired?: number
  languages: Record<string, string>
  budget_modes?: { id: string; label: string }[]
  instruments?: { id: keyof Mix; label: string }[]
  trade?: { fiscal_year: string | null; max_cuts: number; products: { product: string; group: string; total_kt: number; suppliers: string[] }[] }
}

export type Trace = {
  profile: Record<string, unknown> & { top_crops_2020_24: { crop: string; season: string; area_ha: number; yield_kg_ha: number }[] }
  trace: {
    district_name: string
    state_name: string
    context: Record<string, number | string>
    moves_2030: { crop: string; season: string; baseline_ha: number; plan_2030_ha: number; change_ha: number; yield_kg_ha: number; net_margin_inr_ha: number; cwar: number; reasons: string[] }[]
    rejected_alternatives: { crop: string; season: string; reasons: string[] }[]
    rotation_timeline: { year: number; season: string; total_ha: number; top_crops: { crop: string; area_ha: number }[] }[]
    yearly: Record<string, DistrictYear>
  }
}

export type AgentEvent =
  | { type: 'model'; model: string; backend: string }
  | { type: 'tool_call'; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; summary: string }
  | { type: 'text'; text: string }
  | { type: 'citations'; citations: { title: string; uri: string }[] }
  | { type: 'simulation'; run_id: string; scenario?: string }
  | { type: 'error'; text: string }
  | { type: 'done' }

async function j<T>(r: Promise<Response>): Promise<T> {
  const res = await r
  if (!res.ok) throw new Error((await res.text()) || res.statusText)
  return res.json()
}

export const api = {
  meta: () => j<Meta>(fetch('/api/meta')),
  geo: () => j<FeatureCollection>(fetch('/api/geo')),
  simulate: (scenario: string, overrides: Record<string, unknown>) =>
    j<SimResult>(fetch('/api/simulate', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ scenario, overrides }) })),
  run: (id: string) => j<SimResult>(fetch(`/api/run/${id}`)),
  district: (code: number, runId?: string) => j<Trace>(fetch(`/api/district/${code}${runId ? `?run_id=${runId}` : ''}`)),
  candidates: (code: number, runId: string | undefined, year: number) =>
    j<Candidates>(fetch(`/api/district/${code}/candidates?year=${year}${runId ? `&run_id=${runId}` : ''}`)),
  trade: () => j<TradeDeps>(fetch('/api/trade')),
  brief: (runId: string | undefined, state: string | null, language: string) =>
    j<{ markdown: string; model: string; scope: string }>(fetch('/api/brief', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ run_id: runId, state, language }) })),
  async *chat(messages: { role: string; content: string }[], language = 'en'): AsyncGenerator<AgentEvent> {
    const res = await fetch('/api/agent/chat', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ messages, language }) })
    if (!res.ok || !res.body) {
      yield { type: 'error', text: await res.text() }
      return
    }
    const reader = res.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let i
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i)
        buf = buf.slice(i + 2)
        if (chunk.startsWith('data: ')) yield JSON.parse(chunk.slice(6)) as AgentEvent
      }
    }
  },
}

export const fmt = (v: number | null | undefined, d = 1) => (v == null || Number.isNaN(v) ? '–' : v.toLocaleString('en-IN', { maximumFractionDigits: d, minimumFractionDigits: 0 }))
export const CROP_LABEL: Record<string, string> = {
  rice: 'Paddy', wheat: 'Wheat', maize: 'Maize', bajra: 'Bajra', jowar: 'Jowar', ragi: 'Ragi', tur: 'Tur', gram: 'Chana',
  urad: 'Urad', moong: 'Moong', lentil: 'Masur', groundnut: 'Groundnut', soybean: 'Soybean', rapeseed_mustard: 'Mustard',
  sunflower: 'Sunflower', sesamum: 'Sesame', cotton: 'Cotton', sugarcane: 'Sugarcane',
}

export const PILLAR_LABEL: Record<string, string> = {
  self_reliance: 'Import self-reliance', water: 'Groundwater health', soil: 'Soil health', diversity: 'Crop diversity',
}
export const PILLAR_HELP: Record<string, string> = {
  self_reliance: 'Share of India’s pulses and edible-oil demand grown at home (average of the two, each capped at 100%)',
  water: 'Groundwater safety of farm districts: 100 when pumping is ≤ 60% of recharge, ~71 at 100% (over-exploited), 0 at 200%',
  soil: 'Legumes (pulses, groundnut, soybean) as a share of cropped area; one legume in every three crops = 100',
  diversity: 'How varied each district’s crop mix is (1 − Herfindahl index); a single-crop district scores 0',
}

export type Feature = { type: 'Feature'; properties: { district_code: number; district_name: string; state_name: string; data_code: number | null; data_note?: string }; geometry: unknown }
export type FeatureCollection = { type: 'FeatureCollection'; features: Feature[] }
