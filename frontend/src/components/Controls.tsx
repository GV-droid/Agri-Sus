import { useState } from 'react'
import type { LeverStatus, Meta, TradeCut } from '../api'
import { fmt } from '../api'

export type Levers = {
  max_shift_pct: number
  cereal_floor_pct: number
  gw_mode: string
  import_target_pct: number
  incentive_budget_cr: number
  rice_export_retention_pct: number
  price_realisation: number
  crop_growth_cap_pct: number
  budget_mode: string
  instruments: string[]
  trade_cuts: TradeCut[]
  horizon_years: number
}

export const INSTRUMENT_IDS = ['dbt', 'seed_kits', 'price_support']
const INSTRUMENT_SHORT: Record<string, [string, string]> = {
  dbt: ['Income-parity transfers', 'Direct benefit transfer per hectare switched (like Haryana’s Mera Pani Meri Virasat)'],
  seed_kits: ['Seed & FPO kits', '₹4,000/ha certified-seed minikit, bio-inputs and FPO aggregation; +10% yield on pulses & oilseeds'],
  price_support: ['Assured price (PDP)', 'Price-deficiency payment up to MSP on diversified pulse & oilseed area (PM-AASHA style)'],
}

export const DEFAULT_LEVERS: Levers = {
  max_shift_pct: 12, cereal_floor_pct: 101.5, gw_mode: 'cap80', import_target_pct: 25, incentive_budget_cr: 6000,
  rice_export_retention_pct: 75, price_realisation: 0.9, crop_growth_cap_pct: 5,
  budget_mode: 'fixed', instruments: [...INSTRUMENT_IDS], trade_cuts: [], horizon_years: 4,
}

/** Levers shown by a finished run (so the sliders always describe the result on screen). */
export function leversFrom(params: Record<string, unknown>): Levers {
  const out: Levers = { ...DEFAULT_LEVERS, instruments: [...DEFAULT_LEVERS.instruments], trade_cuts: [] }
  for (const key of Object.keys(DEFAULT_LEVERS) as (keyof Levers)[]) {
    const v = params[key]
    if (key === 'gw_mode' || key === 'budget_mode') { if (typeof v === 'string') out[key] = v }
    else if (key === 'instruments') { if (Array.isArray(v)) out.instruments = INSTRUMENT_IDS.filter((x) => v.includes(x)) }
    else if (key === 'trade_cuts') {
      if (Array.isArray(v)) out.trade_cuts = v.map((c) => ({ country: String(c.country), product: String(c.product), pct: Number(c.pct) }))
    } else if (typeof v === 'number' && Number.isFinite(v)) (out[key] as number) = v
  }
  return out
}

const TAG: Record<LeverStatus['state'], [string, string]> = {
  binding: ['limiting', 'tag-binding'], slack: ['not limiting', 'tag-slack'], missed: ['not reached', 'tag-missed'], active: ['active', 'tag-active'],
  inactive: ['set by optimizer', 'tag-slack'],
}

function StatusTag({ s }: { s?: LeverStatus }) {
  if (!s) return null
  const [label, cls] = TAG[s.state] ?? [s.state, 'tag-slack']
  return <span className={`lever-tag ${cls}`} title={s.note}>{label}</span>
}

const PLAN_START = 2027
const SLIDERS: { key: keyof Levers; label: string; min: number; max: number; step: number; unit: string; help: string; show?: (v: number) => string }[] = [
  { key: 'horizon_years', label: 'Plan horizon', min: 2, max: 8, step: 1, unit: ' yrs', help: 'How many years the transition takes, from 2027. Targets, caps and the glide path all end in the final year; longer plans take longer to solve', show: (v) => `${v} yrs · ${PLAN_START}–${PLAN_START + v - 1}` },
  { key: 'max_shift_pct', label: 'Max land shift / year', min: 2, max: 20, step: 1, unit: '%', help: 'Guardrail B: share of a district’s season area that may change crop each year' },
  { key: 'cereal_floor_pct', label: 'Cereal self-sufficiency floor', min: 95, max: 108, step: 0.5, unit: '%', help: 'Guardrail A: national rice+wheat+maize production / demand' },
  { key: 'rice_export_retention_pct', label: 'Rice exports retained', min: 0, max: 100, step: 5, unit: '%', help: 'Share of 2024-25 rice exports (20.2 Mt – virtual water) kept feasible' },
  { key: 'import_target_pct', label: 'Import-bill cut target (final year)', min: 0, max: 50, step: 5, unit: '%', help: 'Pulses + edible oils in the final plan year, vs the bill if nothing changes' },
  { key: 'incentive_budget_cr', label: 'Annual policy budget', min: 0, max: 20000, step: 500, unit: ' ₹cr/yr', help: 'One budget for all enabled instruments (transfers, seed kits, price support); no district’s farm income may fall below today' },
  { key: 'price_realisation', label: 'MSP realisation (non-procured)', min: 0.7, max: 1, step: 0.05, unit: '×', help: '1.0 = assured procurement of pulses & oilseeds (PM-AASHA)' },
  { key: 'crop_growth_cap_pct', label: 'Seed & market scale-up', min: 2, max: 12, step: 1, unit: '%/yr', help: 'How fast national area of any crop can grow: certified seed, procurement and processing capacity (NMEO-Oilseeds, pulses mission)' },
]

export const LEVER_LABEL: Record<string, { label: string; unit: string }> = Object.fromEntries([
  ...SLIDERS.map((s) => [s.key, { label: s.label, unit: s.unit }]),
  ['gw_mode', { label: 'Groundwater guardrail', unit: '' }],
  ['budget_mode', { label: 'Budget mode', unit: '' }],
  ['instruments', { label: 'Policy instruments', unit: '' }],
  ['trade_cuts', { label: 'Trade shock', unit: '' }],
])

const short = (c: string) => c.replace('United Republic of Tanzania', 'Tanzania').replace('Russian Federation', 'Russia').replace('United States of America', 'USA')
  .replace('Kingdom of the Netherlands', 'Netherlands').replace('United Arab Emirates', 'UAE').replace('Papua New Guinea', 'PNG')

function TradePicker({ meta, cuts, onChange }: { meta: Meta | null; cuts: TradeCut[]; onChange: (c: TradeCut[]) => void }) {
  const products = meta?.trade?.products ?? []
  const [prods, setProds] = useState<string[]>([])
  const [ctrs, setCtrs] = useState<string[]>([])
  const [pct, setPct] = useState(30)
  const max = meta?.trade?.max_cuts ?? 8
  if (!products.length) return <p className="muted small">Bilateral trade data not loaded.</p>
  const chosen = products.filter((p) => prods.includes(p.product))
  const supplies = (country: string) => chosen.some((p) => p.suppliers.includes(country))
  const countries = [...new Set(chosen.flatMap((p) => p.suppliers))]
  const pairs = chosen.flatMap((p) => p.suppliers.filter((c) => ctrs.includes(c)).map((c) => ({ country: c, product: p.product, pct })))
  const same = (a: TradeCut, b: TradeCut) => a.country === b.country && a.product === b.product
  const kept = cuts.filter((c) => !pairs.some((x) => same(x, c)))
  const room = Math.max(0, max - kept.length)
  const toggle = (list: string[], v: string, set: (x: string[]) => void) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  const add = () => {
    if (!pairs.length) return
    onChange([...kept, ...pairs.slice(0, room)])
    setCtrs([])
  }
  const groups: [string, string][] = [['pulses', 'Pulses'], ['edible_oils', 'Edible oils']]
  return (
    <div className="trade-picker">
      {cuts.map((c) => (
        <div key={c.country + c.product} className="trade-cut">
          <span>−{fmt(c.pct, 0)}% <b>{c.product}</b> from <b>{short(c.country)}</b></span>
          <button className="link" aria-label={`Remove ${c.product} from ${c.country}`} onClick={() => onChange(cuts.filter((x) => x !== c))}>✕</button>
        </div>
      ))}
      {cuts.length > 1 && <button className="link small clear-cuts" onClick={() => onChange([])}>Clear all {cuts.length} shocks</button>}
      {groups.map(([g, gl]) => (
        <div key={g} className="chip-group" role="group" aria-label={`${gl} to cut`}>
          <span className="muted small">{gl}</span>
          {products.filter((p) => p.group === g).map((p) => (
            <button key={p.product} type="button" className={`chip mini ${prods.includes(p.product) ? 'active' : ''}`} aria-pressed={prods.includes(p.product)}
              title={`${fmt(p.total_kt, 0)} kt imported; top suppliers: ${p.suppliers.map(short).join(', ')}`} onClick={() => toggle(prods, p.product, setProds)}>{p.product}</button>
          ))}
        </div>
      ))}
      {chosen.length > 0 && (
        <div className="chip-group" role="group" aria-label="Supplier countries">
          <span className="muted small">From</span>
          {countries.map((c) => (
            <button key={c} type="button" className={`chip mini ${ctrs.includes(c) ? 'active' : ''}`} aria-pressed={ctrs.includes(c)} disabled={!supplies(c)}
              title={chosen.filter((p) => p.suppliers.includes(c)).map((p) => p.product).join(', ')} onClick={() => toggle(ctrs, c, setCtrs)}>{short(c)}</button>
          ))}
        </div>
      )}
      <div className="trade-row">
        <input type="range" min={10} max={100} step={10} value={pct} aria-label="Share of each supplier’s volume cut" onChange={(e) => setPct(Number(e.target.value))} />
        <b className="trade-pct">−{pct}%</b>
        <button className="ghost small-btn" onClick={add} disabled={!pairs.length || !room}
          title={pairs.length ? pairs.map((x) => `${x.product} from ${short(x.country)}`).join('\n') : 'Pick products, then the countries that stop supplying them'}>
          {pairs.length > 1 ? `Add ${Math.min(pairs.length, room)} shocks` : 'Add shock'}
        </button>
      </div>
      {pairs.length > room && <p className="muted small">Up to {max} shocks per plan: {pairs.length - room} of these do not fit.</p>}
    </div>
  )
}

// shown until /api/meta arrives so the panel does not jump when it does (the server's list replaces it)
const SCENARIO_PLACEHOLDER = [
  ['baseline', 'Baseline (CMIP6 SSP2-4.5)'], ['el_nino_2028', '2028 El Nino drought'], ['palm_oil_shock', 'Palm-oil price shock'],
  ['zero_gw_depletion', 'Zero groundwater depletion mandate'], ['indonesia_palm_halt', 'Indonesia halts palm-oil exports'],
  ['canada_lentil_halt', 'Canada lentil supply cut'], ['northern_heatwave', 'Wheat-belt Rabi heatwave (2029)'],
].map(([id, label]) => ({ id, label, description: '' }))

type Props = {
  meta: Meta | null
  scenario: string
  setScenario: (s: string) => void
  levers: Levers
  setLevers: (l: Levers) => void
  onRun: () => void
  running: boolean
  status?: Record<string, LeverStatus>
  dirty: boolean
  onSave: () => void
  canSave: boolean
}

export default function Controls({ meta, scenario, setScenario, levers, setLevers, onRun, running, status, dirty, onSave, canSave }: Props) {
  return (
    <aside className="panel controls">
      <h2 className="sec">Crisis scenario</h2>
      <div className="scenarios">
        {(meta?.scenarios ?? SCENARIO_PLACEHOLDER).map((s) => (
          <button key={s.id} className={`chip ${scenario === s.id ? 'active' : ''}`} title={s.description} disabled={!meta} onClick={() => setScenario(s.id)}>
            {s.label}
          </button>
        ))}
        <button className={`chip gemini ${scenario === 'gemini_outlook' ? 'active' : ''}`} disabled={!meta?.gemini.enabled}
          title={meta?.gemini.enabled ? 'Gemini + Google Search builds an ENSO/heat/price outlook for the plan years and feeds it to the optimizer' : 'Requires Vertex AI / Gemini credentials'}
          onClick={() => setScenario('gemini_outlook')}>
          ✦ Gemini live outlook
        </button>
      </div>
      <p className="muted small scen-desc">{meta ? meta.scenarios.find((s) => s.id === scenario)?.description ?? (scenario === 'gemini_outlook' ? 'Gemini-grounded forecast of monsoon, terminal heat and import prices for each plan year.' : '') : '\u00a0'}</p>

      <h2 className="sec">Groundwater guardrail <StatusTag s={dirty ? undefined : status?.gw_mode} /></h2>
      <select title={status?.gw_mode?.note} value={levers.gw_mode} onChange={(e) => setLevers({ ...levers, gw_mode: e.target.value })}>
        <option value="status_quo" title="No district pumps more than today">No increase over today</option>
        <option value="cap80" title="Over-exploited and critical districts brought to ≤ 80% of recharge by the final plan year">Cap over-exploited at 80%</option>
        <option value="zero_depletion" title="Every district at CGWB ‘Safe’ (≤ 70% of recharge) by the final plan year">All districts ‘Safe’ by the end</option>
      </select>

      <h2 className="sec">Budget <StatusTag s={dirty ? undefined : status?.budget_mode} /></h2>
      <div className="seg" role="group" aria-label="Budget mode" title={status?.budget_mode?.note}>
        <button className={levers.budget_mode === 'fixed' ? 'active' : ''} onClick={() => setLevers({ ...levers, budget_mode: 'fixed' })}
          title="Best plan within a fixed annual budget">Fixed budget</button>
        <button className={levers.budget_mode === 'min_budget' ? 'active' : ''} onClick={() => setLevers({ ...levers, budget_mode: 'min_budget' })}
          title="The optimizer finds the smallest budget that meets the import-bill target">Least cost to hit target</button>
      </div>
      <div className="instruments" title={status?.instruments?.note}>
        {INSTRUMENT_IDS.map((id) => (
          <label key={id} className="check" title={INSTRUMENT_SHORT[id][1]}>
            <input type="checkbox" checked={levers.instruments.includes(id)}
              onChange={(e) => setLevers({ ...levers, instruments: INSTRUMENT_IDS.filter((x) => (x === id ? e.target.checked : levers.instruments.includes(x))) })} />
            {INSTRUMENT_SHORT[id][0]}
          </label>
        ))}
      </div>

      <h2 className="sec">Policy levers</h2>
      <p className="muted small lever-help">{dirty ? 'Settings changed — press Run optimizer to re-plan.' : 'Tags show whether a lever limits the current plan; moving a “not limiting” lever will not change the result.'}</p>
      {SLIDERS.map((s) => {
        const off = s.key === 'incentive_budget_cr' && levers.budget_mode === 'min_budget'
        const lo = s.key === 'horizon_years' ? meta?.horizon?.min ?? s.min : s.min
        const hi = s.key === 'horizon_years' ? meta?.horizon?.max ?? s.max : s.max
        const v = levers[s.key] as number
        return (
          <label key={s.key} className={`slider ${off ? 'off' : ''}`} title={`${s.help}${status?.[s.key]?.note && !dirty ? `\n\nNow: ${status[s.key].note}` : ''}`}>
            <div className="slider-head"><span>{s.label} <StatusTag s={dirty ? undefined : status?.[s.key]} /></span><b>{off ? 'set by optimizer' : s.show ? s.show(v) : `${v}${s.unit}`}</b></div>
            <input type="range" min={lo} max={hi} step={s.step} value={v} disabled={off} aria-label={s.label}
              onChange={(e) => setLevers({ ...levers, [s.key]: Number(e.target.value) })} />
          </label>
        )
      })}

      <h2 className="sec">Trade shock <StatusTag s={dirty ? undefined : status?.trade_cuts} /></h2>
      <p className="muted small lever-help">Pick one or more products and the countries that stop supplying them ({meta?.trade?.fiscal_year ? `FY${meta.trade.fiscal_year}` : 'latest year'} DGCI&S volumes) — the plan must replace them at home.</p>
      <TradePicker meta={meta} cuts={levers.trade_cuts} onChange={(c) => setLevers({ ...levers, trade_cuts: c })} />
      <div className="row">
        <button className={`primary ${dirty && !running ? 'pulse' : ''}`} onClick={onRun} disabled={running}>{running ? 'Optimising…' : 'Run optimizer'}</button>
        <button className="ghost" onClick={() => setLevers(DEFAULT_LEVERS)}>Reset</button>
      </div>
      <button className="ghost save-btn" onClick={onSave} disabled={!canSave || running || dirty}
        title={dirty ? 'Run the optimizer first: a policy saves the plan on screen' : 'Save the levers and every result on screen to the shared policy library'}>💾 Save as policy</button>
      <p className="muted small">OR-Tools LP · {meta?.n_districts ?? 752} districts (all India) × 18 crops × 2 seasons × {levers.horizon_years} years</p>
    </aside>
  )
}
