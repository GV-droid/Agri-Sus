import type { Kpi, Meta, SimResult, TradeCut } from '../api'
import { LEVER_LABEL } from './Controls'

const GW: Record<string, string> = { status_quo: 'no increase', cap80: 'over-exploited ≤ 80%', zero_depletion: 'all Safe by the end' }

const ROWS: { key: keyof Kpi; label: string; unit: string; nd: number; good?: 1 | -1 }[] = [
  { key: 'assi', label: 'Sustainability index', unit: '', nd: 1, good: 1 },
  { key: 'water_saved_bcm', label: 'Groundwater saved', unit: ' BCM', nd: 2, good: 1 },
  { key: 'import_saving_usd_bn', label: 'Import saving', unit: ' $bn', nd: 2, good: 1 },
  { key: 'area_shifted_mha', label: 'Area shifted', unit: ' Mha', nd: 2 },
  { key: 'cereal_ssr_pct', label: 'Cereal self-sufficiency', unit: ' pp', nd: 1 },
  { key: 'farmer_income_vs_today_pct', label: 'Farmer income', unit: ' pp', nd: 1, good: 1 },
  { key: 'incentive_cr', label: 'Incentives', unit: ' ₹cr', nd: 0 },
]

const val = (k: string, v: unknown) => {
  if (k === 'gw_mode') return GW[String(v)] ?? String(v)
  if (k === 'budget_mode') return v === 'min_budget' ? 'least cost' : 'fixed'
  if (k === 'horizon_years') return String(v ?? 4)  // the default 4-year plan is left implicit in the params
  if (k === 'instruments') return ((v as string[] | undefined) ?? []).join(', ') || 'none'
  if (k === 'trade_cuts') {
    const c = (v as TradeCut[] | undefined) ?? []
    return c.length ? c.map((x) => `−${x.pct}% ${x.product} (${x.country})`).join(', ') : 'none'
  }
  return v == null ? '–' : Number(v).toLocaleString('en-IN')
}

type Props = { prev: SimResult; cur: SimResult; year: number; labels?: Meta['scenarios']; onClose: () => void }

const shockYears = (r: SimResult) => [...new Set(((r.params.shocks ?? []) as { year: number }[]).map((x) => x.year))].sort()

/** What did the last change do? Compares the run on screen with the one it replaced, in the year being viewed. */
export default function RunDiff({ prev, cur, year, labels, onClose }: Props) {
  let a = prev.kpis.find((x) => x.year === year)
  let b = cur.kpis.find((x) => x.year === year)
  let when = String(year)
  if (!a || !b) { // a different plan horizon: compare the two final years
    a = prev.kpis[prev.kpis.length - 1]
    b = cur.kpis[cur.kpis.length - 1]
    if (!a || !b) return null
    when = `final year (${a.year} → ${b.year})`
  }
  const name = (id: string) => labels?.find((s) => s.id === id)?.label ?? (id === 'gemini_outlook' ? 'Gemini live outlook' : id)
  const changes: string[] = []
  if (prev.params.scenario !== cur.params.scenario) changes.push(`Scenario: ${name(prev.params.scenario)} → ${name(cur.params.scenario)}`)
  for (const [k, m] of Object.entries(LEVER_LABEL)) {
    if (JSON.stringify(prev.params[k]) !== JSON.stringify(cur.params[k])) changes.push(`${m.label}: ${val(k, prev.params[k])} → ${val(k, cur.params[k])}${['trade_cuts', 'instruments', 'gw_mode', 'budget_mode'].includes(k) ? '' : m.unit}`)
  }
  const deltas = ROWS.map((r) => ({ ...r, d: Number(b[r.key] ?? 0) - Number(a[r.key] ?? 0) }))
    .filter((r) => Math.abs(r.d) >= 0.5 * 10 ** -r.nd)
  if (!changes.length && !deltas.length) return null
  const scenarioChanged = prev.params.scenario !== cur.params.scenario
  const shocks = [...new Set([...shockYears(prev), ...shockYears(cur)])].filter((y) => y !== year)
  const shockInYear = [...shockYears(prev), ...shockYears(cur)].includes(year)
  const none = scenarioChanged
    ? `no change in ${year}${shocks.length ? ` — the climate shock hits in ${shocks.join(' & ')}; pick that year above` : ''}`
    : 'no change — this lever is not limiting the plan (see the tags in the left panel)'
  // the plan is one pathway, so preparing for a shock in one year shifts the whole path a little
  const pathNote = scenarioChanged && deltas.length > 0 && shocks.length > 0 && !shockInYear
    ? `one pathway: preparing for the ${shocks.join(' & ')} shock re-plans every year` : ''
  return (
    <div className="run-diff" role="status">
      <div className="rd-body">
        <span className="rd-title">Last change</span>
        <span className="rd-changes">{changes.length ? changes.join(' · ') : 'Same settings'}</span>
        <span className="rd-arrow">⇒ {when}:</span>
        {deltas.length ? deltas.map((r) => (
          <span key={r.key} className={`rd-chip ${r.good ? (r.d * r.good > 0 ? 'up' : 'down') : ''}`}>
            {r.label} {r.d > 0 ? '+' : '−'}{Math.abs(r.d).toLocaleString('en-IN', { maximumFractionDigits: r.nd, minimumFractionDigits: r.nd })}{r.unit}
          </span>
        )) : <span className="rd-none">{none}</span>}
        {pathNote && <span className="rd-none">({pathNote})</span>}
      </div>
      <button className="rd-close" onClick={onClose} aria-label="Dismiss" title="Dismiss">×</button>
    </div>
  )
}
