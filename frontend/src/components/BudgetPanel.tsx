import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { SimResult } from '../api'
import { fmt } from '../api'

const INSTR: { id: 'dbt' | 'seed_kits' | 'price_support'; label: string; color: string }[] = [
  { id: 'dbt', label: 'Income-parity transfers (DBT)', color: '#7ee08a' },
  { id: 'seed_kits', label: 'Seed & FPO kits', color: '#4fb3ff' },
  { id: 'price_support', label: 'Assured price (PDP)', color: '#f2c14e' },
]
const inr = (v: number) => `${v < 0 ? '−' : ''}₹${fmt(Math.abs(v), 0)} cr`
const TIER_HELP: Record<string, string> = {
  'Tier 1': 'Each rupee returns 10× or more to the exchequer and the economy: fund first',
  'Tier 2': 'Returns more than it costs: fund while budget lasts',
  'Tier 3': 'Mostly private benefit (farm income, aquifer security): the first districts to lose funding when the budget shrinks',
  'Self-financing': 'Shifts with no public outlay (better margins alone) that still save forex, power or urea',
  'No outlay': 'No public outlay and no net public return in 2030: most keep today’s mix, a few shift crops on margins alone',
}

export default function BudgetPanel({ result, year, onMap }: { result: SimResult | null; year: number; onMap: () => void }) {
  const pm = result?.policy_mix
  const k = result?.kpis.find((x) => x.year === year)
  if (!pm || !k || !k.fiscal_ledger) return <div className="sus skeleton" />
  const L = k.fiscal_ledger
  const last = pm.budget_path_cr[pm.budget_path_cr.length - 1]
  const fixed = pm.mode === 'fixed'
  const data = pm.budget_path_cr.map((r) => ({ year: r.year, dbt: r.dbt, seed_kits: r.seed_kits, price_support: r.price_support, budget: fixed ? r.budget_cr : r.outlay_cr }))
  const ret = L.forex_saved_cr + L.power_subsidy_saved_cr + L.urea_subsidy_saved_cr
  const a = pm.assumptions
  const target = Number(result?.params.import_target_pct ?? 0)
  return (
    <div className="budget">
      <div className="bcard">
        <div className="sus-title">Policy mix · {fixed ? `fixed budget ₹${fmt(last.budget_cr, 0)} cr/yr` : 'least cost to hit the target'}</div>
        {!fixed && (
          <p className={`small ${pm.target_met ? 'good' : 'warn'}`}>
            {pm.target_met
              ? `Least cost to cut the 2030 import bill ${target}%: ₹${fmt(pm.min_budget_cr ?? last.outlay_cr, 0)} cr in ${last.year}.`
              : `Target unreachable: $${fmt(pm.import_target_shortfall_usd_bn, 2)} bn short even with ₹${fmt(last.outlay_cr, 0)} cr — the guardrails, not money, bind.`}
          </p>
        )}
        <ResponsiveContainer width="100%" height={150}>
          <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -6 }}>
            <CartesianGrid stroke="#ffffff10" />
            <XAxis dataKey="year" stroke="#9fb3a8" fontSize={10} />
            <YAxis stroke="#9fb3a8" fontSize={10} tickFormatter={(v) => fmt(Number(v), 0)} />
            <Tooltip contentStyle={{ background: '#13241d', border: '1px solid #2b4a3c', fontSize: 11 }} formatter={(v, n) => [`₹${fmt(Number(v), 0)} cr`, n === 'budget' ? (fixed ? 'Budget' : 'Least-cost outlay') : INSTR.find((i) => i.id === n)?.label ?? String(n)]} />
            {INSTR.map((i) => <Bar key={i.id} dataKey={i.id} stackId="o" fill={i.color} isAnimationActive={false} />)}
            {fixed && <Line dataKey="budget" stroke="#ffffff" strokeDasharray="4 3" dot={false} isAnimationActive={false} />}
          </ComposedChart>
        </ResponsiveContainer>
        <div className="mix-legend">
          {INSTR.map((i) => {
            const on = pm.instruments.find((x) => x.id === i.id)?.enabled
            return <span key={i.id} className={on ? '' : 'off'}><i style={{ background: i.color }} />{i.label}{on ? ` ₹${fmt(k.outlay_by_instrument_cr?.[i.id] ?? 0, 0)} cr` : ' (off)'}</span>
          })}
        </div>
        <div className="muted small">{year}: kits on {fmt(k.kit_area_mha ?? 0, 2)} Mha · price support on {fmt(k.price_support_area_mha ?? 0, 2)} Mha · {fixed ? 'dashed line = budget' : 'bars = budget the optimizer chose'}</div>
      </div>

      <div className="bcard">
        <div className="sus-title">Fiscal ledger · {year}</div>
        <div className="ledger">
          <div><span>Public outlay</span><b>{inr(L.outlay_cr)}</b></div>
          <div title="Imports of pulses and edible oils avoided, at landed prices"><span>Forex saved</span><b className={L.forex_saved_cr < 0 ? 'bad' : 'good'}>{inr(L.forex_saved_cr)}</b></div>
          <div title={`Farm-power subsidy avoided on groundwater no longer pumped (₹${fmt(a.power_subsidy_inr_m3, 2)}/m³)`}><span>Power subsidy saved</span><b className={L.power_subsidy_saved_cr < 0 ? 'bad' : 'good'}>{inr(L.power_subsidy_saved_cr)}</b></div>
          <div title={`Urea subsidy avoided through legume nitrogen credit (₹${fmt(a.urea_subsidy_inr_t, 0)}/t)`}><span>Urea subsidy saved</span><b className={L.urea_subsidy_saved_cr < 0 ? 'bad' : 'good'}>{inr(L.urea_subsidy_saved_cr)}</b></div>
        </div>
        <div className="ledger-bars" aria-hidden>
          <div className="lb"><span>Outlay</span><div><i style={{ width: `${Math.min(100, (100 * L.outlay_cr) / Math.max(ret, L.outlay_cr, 1))}%`, background: '#ff8a7d' }} /></div></div>
          <div className="lb"><span>Returns</span><div><i style={{ width: `${Math.max(0, Math.min(100, (100 * ret) / Math.max(ret, L.outlay_cr, 1)))}%` }} /></div></div>
        </div>
        <div className="ledger-ratio">
          <div title="(forex + power + urea saved) ÷ outlay"><b>{L.public_return_ratio == null ? '–' : `${fmt(L.public_return_ratio, 1)}×`}</b><span>public return per ₹1</span></div>
          <div title="(power + urea subsidy saved) ÷ outlay: what comes straight back to the budget"><b>{L.fiscal_roi == null ? '–' : `${fmt(L.fiscal_roi, 2)}×`}</b><span>budget payback</span></div>
        </div>
      </div>

      <div className="bcard">
        <div className="sus-title">Where each rupee works hardest · MROPI 2030 <button className="link" onClick={onMap}>show on map</button></div>
        <table className="tiers">
          <thead><tr><th>Tier</th><th>Districts</th><th>Outlay</th><th>Returns</th><th>Ratio</th></tr></thead>
          <tbody>
            {pm.mropi_tiers.map((t) => (
              <tr key={t.tier} title={TIER_HELP[t.tier]}>
                <td><span className={`tier-dot t${t.tier.replace(/\D/g, '') || (t.tier === 'Self-financing' ? 'sf' : 'nc')}`} />{t.tier} <span className="muted">{pm.mropi_thresholds[t.tier] ?? ''}</span></td>
                <td>{t.districts}</td>
                <td>{inr(t.outlay_cr)}</td>
                <td>{inr(t.returns_cr)}</td>
                <td>{t.outlay_cr >= 1 ? `${fmt(t.returns_cr / t.outlay_cr, 1)}×` : t.tier === 'Self-financing' ? '∞' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">MROPI = public return (forex + power + urea subsidy saved) ÷ policy outlay in each district. Tier 3 districts are funded for farm income and aquifer security, not fiscal payback.</p>
      </div>
      <div className="muted small assumptions">
        Assumptions: seed kit ₹{fmt(a.kit_cost_inr_ha, 0)}/ha, +{fmt(a.kit_yield_uplift_pct, 0)}% pulse/oilseed yield · price support tops market prices up to MSP ({fmt(a.price_support_admin_pct, 0)}% admin cost) and removes {fmt(a.price_support_risk_relief_pct, 0)}% of the switching-risk premium · power subsidy ₹{fmt(a.power_subsidy_inr_m3, 1)}/m³ pumped · urea subsidy ₹{fmt(a.urea_subsidy_inr_t, 0)}/t · marginal cost of public funds {fmt(a.mcpf, 2)} · ₹{fmt(a.usd_inr, 1)}/$.
      </div>
    </div>
  )
}
