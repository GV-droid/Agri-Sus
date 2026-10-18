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
export type PlanTotals = {
  years: number
  first_year: number
  last_year: number
  budget_cr: number
  outlay_cr: number
  by_instrument_cr: Mix
  forex_saved_cr: number
  power_subsidy_saved_cr: number
  urea_subsidy_saved_cr: number
  returns_cr: number
  public_return_ratio: number | null
  fiscal_roi: number | null
}
export type PolicyMix = {
  mode: 'fixed' | 'min_budget'
  instruments: { id: keyof Mix; label: string; enabled: boolean; outlay_2030_cr: number }[]
  budget_path_cr: ({ year: number; budget_cr: number; outlay_cr: number } & Mix)[]
  totals?: PlanTotals
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

export type PolicyRef = { id: string; name: string; note: string; source: string; created_at: string; deleted: boolean; scenario_label: string }
export type SimResult = {
  run_id: string
  plan_years?: number[]
  policy?: PolicyRef
  params_run_id?: string
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
  horizon?: { min: number; max: number; default: number; start: number }
  store?: StoreStatus
}

export type StoreStatus = { documents: string; snapshots: string; shared: boolean; durable: boolean }
export type PolicySummary = {
  first_year: number; end_year: number; assi: number; assi_status_quo: number; import_saving_usd_bn: number; water_saved_bcm: number
  farmer_income_vs_today_pct: number; budget_mode: string; target_met: boolean; outlay_end_year_cr: number; outlay_total_cr: number
  returns_total_cr: number | null; districts: number
}
export type PolicyMeta = {
  id: string; name: string; note: string; source: 'user' | 'copilot'; created_at: string; updated_at: string; deleted: boolean
  deleted_at: string | null; scenario: string; scenario_label: string; run_id: string; model_version: string
  levers: Record<string, unknown>; summary: PolicySummary
}
export type ChatSummary = { id: string; title: string; created_at: string; updated_at: string; n_messages: number; language: string }
export type DocRef = { run_id: string; scope: string; language: string; title?: string; version?: number; policy_name?: string | null }
/** One step of the co-pilot's activity feed, as saved with the answer (plain language, no tool names). */
export type Activity = { text: string; ok?: boolean; detail?: string; kind?: string }
export type ChatMessage = {
  role: 'user' | 'assistant'; content: string; at?: string; citations?: { title: string; uri: string }[]
  policies?: { id: string; name: string }[]; simulations?: string[]; documents?: DocRef[]; model?: string | null
  turn?: string; activity?: Activity[]; elapsed_s?: number
}

export type ImportRisk = {
  code: string; group: string; severity: 'high' | 'medium' | 'low'; title: string; evidence: string; quantified: boolean
  clause_title: string; clause: string
}
export type ImportGroup = {
  group: string; label: string; verdict: 'likely' | 'partly' | 'unlikely' | 'n/a'; verdict_reason: string
  imports_2024_25_mt: number; imports_no_policy_mt: number; imports_plan_mt: number; plan_cut_mt: number
  low_case_cut_mt: number | null; expansion_kt: number; direct_match_share_pct: number | null
  weighted_cost_ratio: number | null; support_needed_cr: number | null; plan_price_support_cr: number | null; duty_basis: string
  cheap_substitutes: { product: string; competes_with: string; share_pct: number; landed_inr_t: number; msp_inr_t: number }[]
  suppliers: { top: { country: string; share_pct: number }[]; hhi: number; preferential_any_pct: number }
  risks: ImportRisk[]
}
export type ImportCheck = {
  available: boolean; reason?: string; scope: string; final_year: number; trade_year: string; groups: ImportGroup[]; method: string
  trade_policy?: { as_of?: string; typical_effective_duty_pct?: Record<string, number | null> }
}
export type DocSection = { id: string; title: string; words: number; from_template: boolean }
export type DocVersion = { version: number; created_at: string; generator: string; model?: string | null; source?: string; instructions?: string | null; degraded?: boolean }
export type PolicyDocument = {
  id: string; run_id: string; policy_id: string | null; policy_name: string | null; scope: string; language: string; version: number
  created_at: string; updated_at: string; generator: 'gemini' | 'template'; model: string; degraded: boolean; degraded_reason: string | null
  title: string; markdown: string; sections: DocSection[]; words: number; citations: { title: string; uri: string }[]
  research: { schemes: { verified: boolean; source: string; schemes: unknown[] }; trade_policy: unknown; fetched_at: string; reused_from_version: number | null }
  import_check: ImportCheck; clauses: { number: string; group_label: string; title: string; clause_title: string; severity: string }[]
  instructions: string | null; source: string; history: DocVersion[]; elapsed_s: number
}
export type DocStep = { type: 'step'; id: string; label: string; status: 'running' | 'done' | 'failed' | 'skipped'; detail?: string }
export type DocEvent = (DocStep | { type: 'document'; document: PolicyDocument; reused: boolean } | { type: 'error'; code?: number; message: string }
  | { type: 'job'; id: string; kind: string } | { type: 'notice'; text: string } | Lost | { type: 'done' }) & { seq?: number }
export type ChatSession = ChatSummary & { messages: ChatMessage[] }

export type Trace = {
  profile: Record<string, unknown> & { top_crops_2020_24: { crop: string; season: string; area_ha: number; yield_kg_ha: number }[] }
  trace: {
    district_name: string
    state_name: string
    context: Record<string, number | string>
    final_year?: number
    plan_years?: number[]
    moves_2030: { crop: string; season: string; baseline_ha: number; plan_2030_ha: number; change_ha: number; yield_kg_ha: number; net_margin_inr_ha: number; cwar: number; reasons: string[] }[]
    rejected_alternatives: { crop: string; season: string; reasons: string[] }[]
    rotation_timeline: { year: number; season: string; total_ha: number; top_crops: { crop: string; area_ha: number }[] }[]
    yearly: Record<string, DistrictYear>
  }
}

/** The stream of a background job was cut and the job is no longer on the server (a restart, or another instance
 * answered the reconnect): its result is in the saved chat or document, so the caller reloads that instead. */
export type Lost = { type: 'lost'; job: string }
export type AgentEvent = (
  | { type: 'job'; id: string; kind: string }
  | { type: 'model'; model: string; backend: string }
  | { type: 'status'; phase: 'thinking' | 'plan' | 'retry' | 'wrapup' | 'scope' | 'fallback' | 'stopped'; text: string }
  | { type: 'tool_call'; id?: string | null; name: string; label?: string; eta_s?: number }
  | { type: 'progress'; id: string; text: string; eta_s?: number }
  | { type: 'tool_result'; id?: string | null; name: string; summary: string; ok?: boolean }
  | { type: 'text'; text: string }
  | { type: 'text_reset' }
  | { type: 'citations'; citations: { title: string; uri: string }[] }
  | { type: 'simulation'; run_id: string; scenario?: string }
  | { type: 'policy'; id: string; name: string; run_id: string; created?: boolean }
  | ({ type: 'document'; created?: boolean } & DocRef)
  | { type: 'doc_job'; id: string; run_id: string; scope: string; language: string }
  | { type: 'session'; id: string; saved?: boolean }
  | { type: 'error'; text: string }
  | Lost
  | { type: 'done' }) & { seq?: number }

/** The API's error text: FastAPI's {"detail": ...} message, else the raw body. */
async function errText(res: Response): Promise<string> {
  const t = await res.text()
  try {
    const d = JSON.parse(t).detail
    if (typeof d === 'string') return d
    if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg)
  } catch { /* not JSON */ }
  return t || res.statusText
}

async function j<T>(r: Promise<Response>): Promise<T> {
  const res = await r
  if (!res.ok) throw new Error(await errText(res))
  return res.json()
}

/** Server-Sent Events: one JSON object per `data:` line (heartbeat comments are skipped). After the `done` event the
 * response is read to its end (the server closes it straight away) instead of being cut off, so a finished answer
 * never shows up as an aborted request; a stream that is left early is cancelled. */
async function* sse<T extends { type: string }>(body: ReadableStream<Uint8Array>): AsyncGenerator<T> {
  const reader = body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  let ended = false
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) { ended = true; break }
      buf += dec.decode(value, { stream: true })
      let i
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i)
        buf = buf.slice(i + 2)
        if (!chunk.startsWith('data: ')) continue
        const ev = JSON.parse(chunk.slice(6)) as T
        if (ev.type === 'done') ended = await drain(reader)
        yield ev
      }
    }
  } finally {
    if (!ended) reader.cancel().catch(() => undefined)
  }
}

/** Reads what is left of a response, for at most `ms`: true if it ended by itself. */
async function drain(reader: ReadableStreamDefaultReader<Uint8Array>, ms = 2000): Promise<boolean> {
  let timer = 0
  const late = new Promise<null>((resolve) => { timer = window.setTimeout(() => resolve(null), ms) })
  try {
    for (;;) {
      const r = await Promise.race([reader.read(), late])
      if (!r) return false
      if (r.done) return true
    }
  } catch {
    return false
  } finally {
    window.clearTimeout(timer)
  }
}

const pause = (ms: number, signal?: AbortSignal) => new Promise<void>((resolve) => {
  const t = window.setTimeout(resolve, ms)
  signal?.addEventListener('abort', () => { window.clearTimeout(t); resolve() }, { once: true })
})
const RESUME_TRIES = 7

/** The live events of a background job (a co-pilot answer, a policy document) that survive a dropped connection:
 * the server keeps working, and this reconnects with ?after=<last event seen> (backing off 1-8 s) and carries on
 * where it stopped, without repeating or losing an event. Ends after the job's 'done' event; yields `lost` if the
 * job is no longer on the server. `open()` makes the first request (the POST that starts the job). */
async function* jobStream<T extends { type: string; seq?: number }>(
  open: () => Promise<Response>, onError: (text: string, status: number) => T, signal?: AbortSignal, jobId?: string,
): AsyncGenerator<T | Lost> {
  let job = jobId ?? null
  let last = 0
  let tries = 0
  let res: Response | null = null
  try { res = await open() } catch { if (signal?.aborted) return }
  for (;;) {
    if (res && (!res.ok || !res.body)) {
      if (res.status === 404 && job) { yield { type: 'lost', job }; return }
      if (!job || (res.status < 500 && res.status !== 429)) { yield onError(await errText(res), res.status); return }
    } else if (res?.body) {
      try {
        for await (const ev of sse<T>(res.body)) {
          tries = 0
          if (ev.type === 'job' && !job) job = (ev as unknown as { id: string }).id
          if (typeof ev.seq === 'number') {
            if (ev.seq <= last) continue  // already shown before the reconnect
            last = ev.seq
          }
          yield ev
          if (ev.type === 'done') return
        }
      } catch { /* the connection dropped: resume below */ }
    }
    if (signal?.aborted) return
    if (!job || tries >= RESUME_TRIES) {
      yield onError('The connection to the server was lost. Your request may still finish - it is saved automatically.', 0)
      return
    }
    await pause(Math.min(8000, 1000 * 2 ** tries++), signal)
    if (signal?.aborted) return
    try { res = await fetch(`/api/jobs/${job}/events?after=${last}`, { signal }) } catch { res = null }
  }
}

const docQuery = (state: string | null | undefined, language: string) => {
  const q = new URLSearchParams({ language })
  if (state) q.set('state', state)
  return q.toString()
}

const JSON_HEADERS = { 'content-type': 'application/json' }
const send = (method: string, body?: unknown): RequestInit => ({ method, headers: JSON_HEADERS, body: body === undefined ? undefined : JSON.stringify(body) })

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
  solveEstimate: () => j<{ seconds: number }>(fetch('/api/solve-estimate')),
  policies: {
    list: () => j<{ policies: PolicyMeta[]; max: number; store: StoreStatus }>(fetch('/api/policies')),
    save: (runId: string, name: string, note: string) => j<PolicyMeta>(fetch('/api/policies', send('POST', { run_id: runId, name, note }))),
    update: (id: string, patch: { name?: string; note?: string; deleted?: boolean }) => j<PolicyMeta>(fetch(`/api/policies/${id}`, send('PATCH', patch))),
    remove: (id: string, purge = false) => j<PolicyMeta | { id: string; purged: boolean }>(fetch(`/api/policies/${id}${purge ? '?purge=true' : ''}`, send('DELETE'))),
  },
  chats: {
    list: () => j<{ chats: ChatSummary[]; store: StoreStatus }>(fetch('/api/chats')),
    get: (id: string) => j<ChatSession>(fetch(`/api/chats/${id}`)),
    remove: (id: string) => j<{ id: string; deleted: boolean }>(fetch(`/api/chats/${id}`, send('DELETE'))),
  },
  documents: {
    /** The saved document (null if none has been written yet) - never writes one. */
    get: (runId: string, state: string | null | undefined, language: string) =>
      j<{ document: PolicyDocument | null; busy: boolean; job: { id: string } | null; scope: string; language: string }>(fetch(`/api/documents/${runId}?${docQuery(state, language)}`)),
    /** Write it once (or return the saved one); regenerate writes a new version. Streams step progress; the writing
     * goes on (and is saved) on the server if the connection drops or the dialog closes. */
    generate: (body: { run_id: string; state?: string | null; language: string; regenerate?: boolean; instructions?: string; refresh_research?: boolean }, signal?: AbortSignal) =>
      jobStream<DocEvent>(() => fetch('/api/documents', { ...send('POST', body), signal }),
        (message, code) => ({ type: 'error', code, message }), signal),
    /** Follow a document that is already being written (by another window or by the co-pilot). */
    follow: (jobId: string, signal?: AbortSignal) =>
      jobStream<DocEvent>(() => fetch(`/api/jobs/${jobId}/events?after=0`, { signal }),
        (message, code) => ({ type: 'error', code, message }), signal, jobId),
  },
  jobs: {
    /** Replay a co-pilot answer from its first event and follow it live (after a page reload). */
    follow: (jobId: string, signal?: AbortSignal) =>
      jobStream<AgentEvent>(() => fetch(`/api/jobs/${jobId}/events?after=0`, { signal }), (text) => ({ type: 'error', text }), signal, jobId),
    /** The Stop button: the server stops the answer and saves what was said so far. */
    cancel: (jobId: string) => j<{ id: string; status: string; stopping: boolean }>(fetch(`/api/jobs/${jobId}/cancel`, send('POST'))),
  },
  importCheck: (runId: string, state?: string | null) =>
    j<ImportCheck>(fetch(`/api/import-check/${runId}${state ? `?state=${encodeURIComponent(state)}` : ''}`)),
  /** Ask the co-pilot. The answer runs as a job on the server: the stream resumes by itself after a dropped
   * connection, and the finished answer is saved to the chat session either way. */
  chat(messages: { role: string; content: string }[], language = 'en', sessionId?: string | null, runId?: string | null, signal?: AbortSignal): AsyncGenerator<AgentEvent> {
    const body = { messages, language, session_id: sessionId || undefined, context: runId ? { run_id: runId } : undefined }
    return jobStream<AgentEvent>(() => fetch('/api/agent/chat', { ...send('POST', body), signal }), (text) => ({ type: 'error', text }), signal)
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
