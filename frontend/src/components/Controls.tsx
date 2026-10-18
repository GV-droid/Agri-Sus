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
  budget_mode: 'fixed', instruments: [...INSTRUMENT_IDS], trade_cuts: [],
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

const SLIDERS: { key: keyof Levers; label: string; min: number; max: number; step: number; unit: string; help: string }[] = [
  { key: 'max_shift_pct', label: 'Max land shift / year', min: 2, max: 20, step: 1, unit: '%', help: 'Guardrail B: share of a district’s season area that may change crop each year' },
  { key: 'cereal_floor_pct', label: 'Cereal self-sufficiency floor', min: 95, max: 108, step: 0.5, unit: '%', help: 'Guardrail A: national rice+wheat+maize production / demand' },
  { key: 'rice_export_retention_pct', label: 'Rice exports retained', min: 0, max: 100, step: 5, unit: '%', help: 'Share of 2024-25 rice exports (20.2 Mt – virtual water) kept feasible' },
  { key: 'import_target_pct', label: '2030 import-bill cut target', min: 0, max: 50, step: 5, unit: '%', help: 'Pulses + edible oils, vs status-quo 2030 bill' },
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

function TradePicker({ meta, cuts, onChange }: { meta: Meta | null; cuts: TradeCut[]; onChange: (c: TradeCut[]) => void }) {
  const products = meta?.trade?.products ?? []
  const [product, setProduct] = useState('')
  const [country, setCountry] = useState('')
  const [pct, setPct] = useState(30)
  const prod = products.find((p) => p.product === (product || products[0]?.product))
  const ctry = country && prod?.suppliers.includes(country) ? country : prod?.suppliers[0] ?? ''
  const max = meta?.trade?.max_cuts ?? 3
  if (!products.length) return <p className="muted small">Bilateral trade data not loaded.</p>
  const add = () => {
    if (!prod || !ctry) return
    const rest = cuts.filter((c) => !(c.country === ctry && c.product === prod.product))
    onChange([...rest, { country: ctry, product: prod.product, pct }].slice(-max))
  }
  return (
    <div className="trade-picker">
      {cuts.map((c) => (
        <div key={c.country + c.product} className="trade-cut">
          <span>−{fmt(c.pct, 0)}% <b>{c.product}</b> from <b>{c.country}</b></span>
          <button className="link" aria-label={`Remove ${c.product} from ${c.country}`} onClick={() => onChange(cuts.filter((x) => x !== c))}>✕</button>
        </div>
      ))}
      <div className="trade-row">
        <select aria-label="Imported product" value={prod?.product ?? ''} onChange={(e) => { setProduct(e.target.value); setCountry('') }}>
          {products.map((p) => <option key={p.product} value={p.product}>{p.product} ({fmt(p.total_kt, 0)} kt)</option>)}
        </select>
        <select aria-label="Supplier country" value={ctry} onChange={(e) => setCountry(e.target.value)}>
          {prod?.suppliers.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="trade-row">
        <input type="range" min={10} max={100} step={10} value={pct} aria-label="Share of that supplier’s volume cut" onChange={(e) => setPct(Number(e.target.value))} />
        <b className="trade-pct">−{pct}%</b>
        <button className="ghost small-btn" onClick={add} disabled={!ctry || (cuts.length >= max && !cuts.some((c) => c.country === ctry && c.product === prod?.product))}>Add shock</button>
      </div>
    </div>
  )
}

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
}

export default function Controls({ meta, scenario, setScenario, levers, setLevers, onRun, running, status, dirty }: Props) {
  return (
    <aside className="panel controls">
      <h3>Crisis scenario</h3>
      <div className="scenarios">
        {meta?.scenarios.map((s) => (
          <button key={s.id} className={`chip ${scenario === s.id ? 'active' : ''}`} title={s.description} onClick={() => setScenario(s.id)}>
            {s.label}
          </button>
        ))}
        <button className={`chip gemini ${scenario === 'gemini_outlook' ? 'active' : ''}`} disabled={!meta?.gemini.enabled}
          title={meta?.gemini.enabled ? 'Gemini + Google Search builds a 2027-30 ENSO/heat/price outlook and feeds it to the optimizer' : 'Requires Vertex AI / Gemini credentials'}
          onClick={() => setScenario('gemini_outlook')}>
          ✦ Gemini live outlook
        </button>
      </div>
      <p className="muted small">{meta?.scenarios.find((s) => s.id === scenario)?.description ?? (scenario === 'gemini_outlook' ? 'Gemini-grounded forecast of monsoon, terminal heat and import prices for 2027-30.' : '')}</p>

      <h3>Groundwater guardrail <StatusTag s={dirty ? undefined : status?.gw_mode} /></h3>
      <select title={status?.gw_mode?.note} value={levers.gw_mode} onChange={(e) => setLevers({ ...levers, gw_mode: e.target.value })}>
        <option value="status_quo" title="No district pumps more than today">No increase over today</option>
        <option value="cap80" title="Over-exploited and critical districts brought to ≤ 80% of recharge by 2030">Cap over-exploited at 80%</option>
        <option value="zero_depletion" title="Every district at CGWB ‘Safe’ (≤ 70% of recharge) by 2030">All districts ‘Safe’ by 2030</option>
      </select>

      <h3>Budget <StatusTag s={dirty ? undefined : status?.budget_mode} /></h3>
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

      <h3>Policy levers</h3>
      <p className="muted small lever-help">{dirty ? 'Settings changed — press Run optimizer to re-plan.' : 'Tags show whether a lever limits the current plan; moving a “not limiting” lever will not change the result.'}</p>
      {SLIDERS.map((s) => {
        const off = s.key === 'incentive_budget_cr' && levers.budget_mode === 'min_budget'
        return (
          <label key={s.key} className={`slider ${off ? 'off' : ''}`} title={`${s.help}${status?.[s.key]?.note && !dirty ? `\n\nNow: ${status[s.key].note}` : ''}`}>
            <div className="slider-head"><span>{s.label} <StatusTag s={dirty ? undefined : status?.[s.key]} /></span><b>{off ? 'set by optimizer' : `${levers[s.key]}${s.unit}`}</b></div>
            <input type="range" min={s.min} max={s.max} step={s.step} value={levers[s.key] as number} disabled={off}
              onChange={(e) => setLevers({ ...levers, [s.key]: Number(e.target.value) })} />
          </label>
        )
      })}

      <h3>Trade shock <StatusTag s={dirty ? undefined : status?.trade_cuts} /></h3>
      <p className="muted small lever-help">Cut imports from one supplier ({meta?.trade?.fiscal_year ? `FY${meta.trade.fiscal_year}` : 'latest year'} DGCI&S volumes) — the plan must replace them at home.</p>
      <TradePicker meta={meta} cuts={levers.trade_cuts} onChange={(c) => setLevers({ ...levers, trade_cuts: c })} />
      <div className="row">
        <button className={`primary ${dirty && !running ? 'pulse' : ''}`} onClick={onRun} disabled={running}>{running ? 'Optimising…' : 'Run optimizer'}</button>
        <button className="ghost" onClick={() => setLevers(DEFAULT_LEVERS)}>Reset</button>
      </div>
      <p className="muted small">OR-Tools LP · {meta?.n_districts ?? 752} districts (all India) × 18 crops × 2 seasons × 4 years</p>
    </aside>
  )
}
