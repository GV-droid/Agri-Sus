import { useEffect, useState } from 'react'
import type { ImportCheck, SimResult, TradeDeps } from '../api'
import { CROP_LABEL, api, fmt } from '../api'

const GROUP_LABEL: Record<string, string> = { pulses: 'Pulses', edible_oils: 'Edible oils' }
const VERDICT: Record<string, string> = { likely: 'good', partly: 'warn', unlikely: 'bad' }

/** Will the plan's extra pulses and oilseeds actually displace imports? (deterministic check; no model call) */
function ImportCheckCard({ runId, onDocument }: { runId: string; onDocument?: () => void }) {
  const [ic, setIc] = useState<ImportCheck | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let current = true
    setIc(null)
    setErr(null)
    api.importCheck(runId).then((r) => current && setIc(r)).catch((e) => current && setErr((e as Error).message))
    return () => { current = false }
  }, [runId])
  if (err || (ic && !ic.available)) return null
  return (
    <div className="bcard import-check">
      <div className="sus-title">Will more production actually cut imports? · {ic ? `${ic.final_year} plan vs the FY${ic.trade_year} import basket` : '…'}
        {onDocument && <> · <button className="link" onClick={onDocument}>counter-measures in the policy document →</button></>}</div>
      {!ic && <div className="muted small">Checking…</div>}
      <div className="ic-groups">
        {ic?.groups.map((g) => (
          <div key={g.group} className="ic-group">
            <div><b>{g.label}</b>: imports cut <b className={VERDICT[g.verdict] ?? ''}>{g.verdict}</b> <span className="muted small">— {g.verdict_reason}</span></div>
            <div className="small">The optimizer assumes a {fmt(g.plan_cut_mt, 2)} Mt cut{g.low_case_cut_mt != null && <> · only {fmt(g.low_case_cut_mt, 2)} Mt if just the products India imports displace imports</>}
              {g.direct_match_share_pct != null && <> · {fmt(g.direct_match_share_pct, 0)}% of the extra output matches the import basket</>}
              {g.weighted_cost_ratio != null && <> · domestic cost <b className={g.weighted_cost_ratio > 1.05 ? 'bad' : ''}>{fmt(g.weighted_cost_ratio, 2)}×</b> the landed import price ({g.duty_basis})</>}</div>
            <ul className="small">
              {g.risks.filter((r) => r.severity === 'high').slice(0, 3).map((r) => <li key={r.code} title={r.evidence}><span className="warn">⚠</span> {r.title} → <i>{r.clause_title}</i></li>)}
            </ul>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function TradePanel({ result, onShock, running, onDocument }: { result: SimResult | null; onShock: (country: string, product: string) => void; running: boolean; onDocument?: () => void }) {
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
      {result && <ImportCheckCard runId={result.run_id} onDocument={onDocument} />}
    </div>
  )
}
