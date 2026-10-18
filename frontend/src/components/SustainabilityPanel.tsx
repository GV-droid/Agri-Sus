import { Area, ComposedChart, CartesianGrid, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Kpi, SimResult } from '../api'
import { PILLAR_HELP, PILLAR_LABEL, fmt } from '../api'

const PLAN = '#7ee08a'
const SQ = '#c3cec8'

function Gauge({ value, base }: { value: number; base: number }) {
  const r = 50
  const pt = (v: number, rr: number) => {
    const a = Math.PI * (1 - Math.max(0, Math.min(100, v)) / 100)
    return [60 + rr * Math.cos(a), 62 - rr * Math.sin(a)]
  }
  const [x, y] = pt(value, r)
  const [bx, by] = pt(base, r - 11)
  return (
    <svg viewBox="0 0 120 80" className="gauge" role="img" aria-label={`Sustainability index: plan ${value}, do nothing ${base}, of 100`}>
      <defs>
        <linearGradient id="g-arc" x1="0" x2="1">
          <stop offset="0" stopColor="#ff6b5b" />
          <stop offset=".5" stopColor="#f2c14e" />
          <stop offset="1" stopColor={PLAN} />
        </linearGradient>
      </defs>
      <path d={`M10 62 A ${r} ${r} 0 0 1 110 62`} stroke="#ffffff14" strokeWidth="9" fill="none" strokeLinecap="round" />
      <path d={`M10 62 A ${r} ${r} 0 0 1 ${x} ${y}`} stroke="url(#g-arc)" strokeWidth="9" fill="none" strokeLinecap="round" />
      <path d={`M21 62 A ${r - 11} ${r - 11} 0 0 1 99 62`} stroke="#ffffff0d" strokeWidth="5" fill="none" strokeLinecap="round" />
      <path d={`M21 62 A ${r - 11} ${r - 11} 0 0 1 ${bx} ${by}`} stroke={SQ} strokeWidth="5" fill="none" strokeLinecap="round" opacity=".75" />
      <text x="60" y="52" textAnchor="middle" fontSize="20" fontWeight="700" fill={PLAN}>{fmt(value, 1)}</text>
      <text x="60" y="62" textAnchor="middle" fontSize="7" fill="#cfe0d6">Agri Sus plan</text>
      <text x="60" y="76" textAnchor="middle" fontSize="7.5" fill={SQ}>do nothing {fmt(base, 1)} · gap {value - base >= 0 ? '+' : ''}{fmt(value - base, 1)}</text>
    </svg>
  )
}

function narrative(k: Kpi, first: Kpi): string {
  const sq = k.assi_pillars_status_quo as Record<string, number>
  const gains = Object.entries(k.assi_pillars).map(([id, v]) => [id, v - sq[id]] as const).sort((a, b) => b[1] - a[1])
  const top = gains.filter((g) => g[1] > 1).slice(0, 2).map((g) => PILLAR_LABEL[g[0]].toLowerCase())
  const drift = k.assi_status_quo - first.assi_status_quo
  const sqLine = Math.abs(drift) < 0.3
    ? `Doing nothing, India’s farm sustainability index stays at about ${fmt(k.assi_status_quo, 1)}.`
    : `Doing nothing, the index ${drift < 0 ? 'slips' : 'creeps'} from ${fmt(first.assi_status_quo, 1)} (${first.year}) to ${fmt(k.assi_status_quo, 1)} (${k.year})${drift < 0 ? ' as aquifers keep falling' : ''}.`
  return `${sqLine} With the Agri Sus plan it reaches ${fmt(k.assi, 1)} by ${k.year} — ${fmt(k.assi - k.assi_status_quo, 1)} points better. ` +
    (top.length ? `Most of the gain comes from ${top.join(' and ')}. ` : '') +
    `Farmers earn ${fmt(k.farmer_income_vs_today_pct, 1)}% of today’s income and cereal self-sufficiency stays at ${fmt(k.cereal_ssr_pct, 0)}%.`
}

export default function SustainabilityPanel({ result, year }: { result: SimResult | null; year: number }) {
  if (!result) return <div className="sus skeleton" />
  const k = result.kpis.find((x) => x.year === year) ?? result.kpis[result.kpis.length - 1]
  const first = result.kpis[0]
  const vals: number[] = []
  const data = result.kpis.map((x) => {
    const plan = result.robustness.map((r) => r.years.find((y) => y.year === x.year)?.assi ?? x.assi)
    const sq = result.robustness.map((r) => r.years.find((y) => y.year === x.year)?.assi_status_quo ?? x.assi_status_quo)
    const pr: [number, number] = [Math.min(x.assi, ...plan), Math.max(x.assi, ...plan)]
    const sr: [number, number] = [Math.min(x.assi_status_quo, ...sq), Math.max(x.assi_status_quo, ...sq)]
    vals.push(...pr, ...sr)
    return { year: x.year, 'Agri Sus plan': x.assi, 'Do nothing': x.assi_status_quo, 'Plan under shocks': pr, 'Do nothing under shocks': sr }
  })
  const lo = Math.floor((Math.min(...vals) - 2) / 5) * 5
  const hi = Math.ceil((Math.max(...vals) + 2) / 5) * 5
  const w = result.assi_weights ?? {}
  const short = k.import_target_shortfall_usd_bn ?? 0
  return (
    <div className="sus">
      <div className="sus-left">
        <div className="sus-title">Sustainability index <span className="muted">(ASSI) · {k.year}</span></div>
        <Gauge value={k.assi} base={k.assi_status_quo} />
        <p className="sus-story">{narrative(k, first)}</p>
      </div>
      <div className="sus-mid">
        <div className="sus-title">What drives the score <span className="muted">· do nothing vs plan</span></div>
        {Object.entries(k.assi_pillars).map(([id, v]) => {
          const sq = (k.assi_pillars_status_quo as Record<string, number>)[id]
          const d = v - sq
          return (
            <div key={id} className="pillar" title={PILLAR_HELP[id]}>
              <div className="pillar-head">
                <span>{PILLAR_LABEL[id]} <span className="muted">· {Math.round((w[id] ?? 0) * 100)}%</span></span>
                <span><span className="muted">{fmt(sq, 0)} →</span> <b className={d > 0.5 ? 'up' : d < -0.5 ? 'down' : ''}>{fmt(v, 0)}</b></span>
              </div>
              <div className="pillar-bar sq"><div className="pillar-fill" style={{ width: `${sq}%` }} /></div>
              <div className="pillar-bar"><div className="pillar-fill" style={{ width: `${v}%` }} /></div>
            </div>
          )
        })}
        <div className="guards">
          <span className={`guard ${k.guardrail_income_ok ? 'ok' : 'warn'}`} title="No district’s farm income (incl. incentives) may fall below today">{k.guardrail_income_ok ? '✓' : '⚠'} Farmer income {fmt(k.farmer_income_vs_today_pct, 1)}% of today</span>
          <span className={`guard ${k.guardrail_cereal_ok ? 'ok' : 'warn'}`} title="Rice + wheat + maize + millets production ÷ demand">{k.guardrail_cereal_ok ? '✓' : '⚠'} Cereals {fmt(k.cereal_ssr_pct, 1)}% self-sufficient</span>
          <span className={`guard ${short > 0.05 ? 'soft' : 'ok'}`} title="Soft goal: cut the pulses + edible-oil import bill vs doing nothing">{short > 0.05 ? `◐ Import-cut goal: $${fmt(short, 1)}bn short` : '✓ Import-cut goal met'}</span>
        </div>
      </div>
      <div className="sus-right">
        <div className="sus-title">Forecast {first.year} → {result.kpis[result.kpis.length - 1].year} <span className="muted">· shaded = range under unplanned climate shocks</span></div>
        <ResponsiveContainer width="100%" height={150}>
          <ComposedChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: -20 }}>
            <CartesianGrid stroke="#ffffff12" />
            <XAxis dataKey="year" stroke="#9fb3a8" fontSize={11} />
            <YAxis domain={[lo, hi]} stroke="#9fb3a8" fontSize={11} allowDecimals={false} />
            <Tooltip contentStyle={{ background: '#13241d', border: '1px solid #2b4a3c', fontSize: 12 }}
              formatter={(v) => (Array.isArray(v) ? `${fmt(Number(v[0]), 1)} – ${fmt(Number(v[1]), 1)}` : fmt(Number(v), 1))} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Area dataKey="Do nothing under shocks" stroke={SQ} strokeOpacity={0.35} fill={SQ} fillOpacity={0.18} legendType="none" isAnimationActive={false} />
            <Area dataKey="Plan under shocks" stroke={PLAN} strokeOpacity={0.4} fill={PLAN} fillOpacity={0.22} legendType="none" isAnimationActive={false} />
            <Line dataKey="Do nothing" stroke={SQ} strokeWidth={2.5} dot={{ r: 3 }} isAnimationActive={false} />
            <Line dataKey="Agri Sus plan" stroke={PLAN} strokeWidth={3} dot={{ r: 3.5 }} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
        <div className="stress">
          {result.robustness.map((r) => (
            <span key={r.id} className={`stress-chip ${r.plan_beats_status_quo ? 'ok' : 'warn'}`} title="The plan is NOT re-optimised: this shows how the fixed plan copes with a shock it didn’t foresee, against doing nothing under the same shock">
              {r.plan_beats_status_quo ? '✓' : '⚠'} {r.label}: plan still ahead{r.plan_beats_status_quo ? '' : '? no'} · worst {fmt(r.min_assi, 1)}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
