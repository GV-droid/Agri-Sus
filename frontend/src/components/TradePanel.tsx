import { useEffect, useState } from 'react'
import type { SimResult, TradeDeps } from '../api'
import { CROP_LABEL, api, fmt } from '../api'

const GROUP_LABEL: Record<string, string> = { pulses: 'Pulses', edible_oils: 'Edible oils' }

export default function TradePanel({ result, onShock, running }: { result: SimResult | null; onShock: (country: string, product: string) => void; running: boolean }) {
  const [deps, setDeps] = useState<TradeDeps | null>(null)
  const [err, setErr] = useState(false)
  useEffect(() => { api.trade().then(setDeps).catch(() => setErr(true)) }, [])
  const tr = result?.trade
  const cut = new Set((tr?.cuts ?? []).map((c) => `${c.country}|${c.product}`))
  return (
    <div className="trade">
      <div className="bcard trade-result">
        <div className="sus-title">Trade-shock stress test</div>
        {!tr && <p className="small muted">No supplier shock in this run. Pick a supplier on the right (or under “Trade shock” in the levers) to ask: <i>what if they stop selling to India?</i> The optimizer must then replace that volume from Indian fields within every guardrail.</p>}
        {tr?.cuts.map((c) => (
          <div key={c.country + c.product} className="shock">
            <div><b>−{fmt(c.pct, 0)}% {c.product} from {c.country}</b> <span className="muted small">= {fmt(c.volume_kt, 0)} kt · ${fmt(c.value_usd_mn, 0)} mn · {fmt(c.share_of_product_pct ?? 0, 0)}% of India’s {c.product} imports (FY{c.fiscal_year})</span></div>
            {!!c.alternatives?.length && <div className="small muted">Other suppliers: {c.alternatives.map((a) => `${a.country} ${fmt(a.share_pct, 0)}%`).join(' · ')}</div>}
          </div>
        ))}
        {tr?.groups.map((g) => {
          const last = g.years[g.years.length - 1]
          return (
            <div key={g.group} className="shock-group">
              <div className="small"><b className={g.met ? 'good' : 'warn'}>{g.met ? '✓ replaced at home' : '⚠ shortfall'}</b> · {GROUP_LABEL[g.group] ?? g.group}: {fmt(last.required_cut_kt, 0)} kt must be replaced; the plan cuts {fmt(last.achieved_cut_kt, 0)} kt of imports in {last.year}{!g.met && <> — <b className="warn">{fmt(last.shortfall_kt, 0)} kt short</b> (guardrails bind)</>}</div>
              <div className="shock-years">
                {g.years.map((y) => {
                  const pct = y.required_cut_kt > 0 ? Math.min(100, (100 * y.achieved_cut_kt) / y.required_cut_kt) : 100
                  return <div key={y.year} title={`${y.year}: need ${fmt(y.required_cut_kt, 0)} kt, plan cuts ${fmt(y.achieved_cut_kt, 0)} kt`}><span>{y.year}</span><div className="pbar"><i className={y.shortfall_kt >= 1 ? 'short' : ''} style={{ width: `${pct}%` }} /></div></div>
                })}
              </div>
              {!!g.substitute_crops.length && <div className="small">Extra output from: {g.substitute_crops.slice(0, 5).map((c) => <span key={c.crop} className="chip-s">{CROP_LABEL[c.crop] ?? c.crop} +{fmt(c.extra_kt, 0)} kt</span>)}</div>}
              {!!g.substitute_districts.length && <div className="small muted">Top districts: {g.substitute_districts.slice(0, 5).map((d) => `${d.district_name} (${d.state_name}) +${fmt(d.extra_kt, 1)} kt`).join(' · ')}</div>}
            </div>
          )
        })}
      </div>
      <div className="bcard trade-deps">
        <div className="sus-title">Who India buys from · FY{deps?.fiscal_year ?? '…'} <span className="muted">(DGCI&S via India Data Portal)</span></div>
        {err && <p className="small bad">Trade data unavailable.</p>}
        <div className="deps-list">
          {deps?.products.map((p) => (
            <div key={p.product} className="dep">
              <div className="dep-head"><b>{p.product}</b><span className="muted small">{fmt(p.total_kt, 0)} kt · ${fmt(p.total_usd_mn, 0)} mn · concentration {p.hhi >= 0.25 ? <b className="warn">high</b> : p.hhi >= 0.15 ? 'moderate' : 'low'} (HHI {fmt(p.hhi, 2)})</span></div>
              {p.suppliers.slice(0, 3).map((s) => (
                <div key={s.country} className="sup">
                  <span className="sup-name">{s.country}</span>
                  <div className="pbar"><i style={{ width: `${s.share_pct}%` }} /></div>
                  <span className="small">{fmt(s.share_pct, 0)}%</span>
                  <button className="link" disabled={running || cut.has(`${s.country}|${p.product}`)} onClick={() => onShock(s.country, p.product)} title={`Re-plan as if ${s.country} stopped selling ${p.product} to India`}>
                    {cut.has(`${s.country}|${p.product}`) ? 'in this run' : 'what if it stops?'}
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
