import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from 'recharts'
import type { Kpi } from '../api'
import { fmt } from '../api'

const PLAN = '#7ee08a'
const SQ = '#c3cec8'

type Spec = { title: string; unit: string; sq: (k: Kpi) => number; plan: (k: Kpi) => number; target?: (k: Kpi) => number; better: 'up' | 'down'; d?: number }
const SPECS: Spec[] = [
  { title: 'Import bill · pulses + edible oil', unit: '$bn', sq: (k) => k.import_bill_status_quo_usd_bn, plan: (k) => k.import_bill_usd_bn, target: (k) => k.import_target_usd_bn, better: 'down' },
  { title: 'Groundwater pumped for irrigation', unit: 'BCM', sq: (k) => k.gw_draft_status_quo_bcm, plan: (k) => k.gw_draft_bcm, better: 'down', d: 0 },
  { title: 'Pulses self-sufficiency', unit: '%', sq: (k) => k.pulses_ssr_status_quo_pct, plan: (k) => k.pulses_ssr_pct, better: 'up', d: 0 },
  { title: 'Edible-oil self-sufficiency', unit: '%', sq: (k) => k.edible_oils_ssr_status_quo_pct, plan: (k) => k.edible_oils_ssr_pct, better: 'up', d: 0 },
  { title: 'Paddy area', unit: 'Mha', sq: (k) => k.rice_area_status_quo_mha, plan: (k) => k.rice_area_mha, better: 'down' },
  { title: 'Legume share of cropped area', unit: '%', sq: (k) => k.legume_share_gca_status_quo_pct, plan: (k) => k.legume_share_gca_pct, better: 'up' },
]

function Mini({ s, kpis }: { s: Spec; kpis: Kpi[] }) {
  const data = kpis.map((k) => ({ year: k.year, 'Do nothing': s.sq(k), 'Agri Sus plan': s.plan(k), ...(s.target ? { Target: s.target(k) } : {}) }))
  const last = kpis[kpis.length - 1]
  const dv = s.plan(last) - s.sq(last)
  const good = s.better === 'up' ? dv >= 0 : dv <= 0
  return (
    <div className="mini">
      <div className="mini-head"><span>{s.title}</span><b className={good ? 'up' : 'down'}>{dv >= 0 ? '+' : ''}{fmt(dv, s.d ?? 1)} {s.unit}</b></div>
      <ResponsiveContainer width="100%" height={92}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -24 }}>
          <CartesianGrid stroke="#ffffff10" />
          <XAxis dataKey="year" stroke="#9fb3a8" fontSize={10} />
          <YAxis stroke="#9fb3a8" fontSize={10} domain={['auto', 'auto']} tickFormatter={(v) => fmt(Number(v), 0)} />
          <Tooltip contentStyle={{ background: '#13241d', border: '1px solid #2b4a3c', fontSize: 11 }} formatter={(v) => `${fmt(Number(v), 1)} ${s.unit}`} />
          <Line dataKey="Do nothing" stroke={SQ} strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} />
          <Line dataKey="Agri Sus plan" stroke={PLAN} strokeWidth={2.5} dot={{ r: 2.5 }} isAnimationActive={false} />
          {s.target && <Line dataKey="Target" stroke="#f2c14e" strokeDasharray="4 3" dot={false} isAnimationActive={false} />}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function Trajectory({ kpis }: { kpis: Kpi[] }) {
  if (!kpis.length) return <div className="sus skeleton" />
  return (
    <div>
      <div className="mini-legend"><i className="sq" /> Do nothing <i /> Agri Sus plan <i className="tg" /> Import-cut target · headline = plan − do nothing in {kpis[kpis.length - 1].year}</div>
      <div className="minis">{SPECS.map((s) => <Mini key={s.title} s={s} kpis={kpis} />)}</div>
    </div>
  )
}
