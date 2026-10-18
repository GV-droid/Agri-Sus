import { useEffect, useState } from 'react'
import type { Candidates, Trace } from '../api'
import { CROP_LABEL, PILLAR_HELP, PILLAR_LABEL, api, fmt } from '../api'

const COLORS: Record<string, string> = {
  rice: '#4fb3ff', wheat: '#f2c14e', maize: '#f29e4e', bajra: '#c9a66b', jowar: '#b38b59', ragi: '#a3785a', tur: '#7ee08a', gram: '#5cc46a',
  urad: '#3fa45a', moong: '#9be89f', lentil: '#2f8f4f', groundnut: '#d98c5f', soybean: '#c7d36f', rapeseed_mustard: '#e8e26a',
  sunflower: '#ffd23f', sesamum: '#e0b080', cotton: '#e6e6e6', sugarcane: '#b07ad9',
}
const DISTRICT_PILLAR_LABEL: Record<string, string> = { ...PILLAR_LABEL, soil: 'Soil (legume share)' }
const DISTRICT_PILLAR_HELP: Record<string, string> = {
  ...PILLAR_HELP,
  water: 'Groundwater stage of extraction: 100 when pumping ≤ 60% of recharge, ~71 at 100%, 0 at 200%. Districts CGWB has not assessed score neutral.',
}

const DECISION_CLS: Record<string, string> = { Expand: 'good', Keep: '', Reduce: 'bad', Fixed: 'muted', 'Not chosen': 'muted', 'Blocked (water)': 'bad' }
const FLAG_ICON: Record<string, string> = { good: '✓', info: 'ℹ', warn: '⚠', block: '⛔' }
const seasonLabel = (season: string, y: number) => (season === 'Rabi' ? `Rabi ${y}-${String((y + 1) % 100).padStart(2, '0')}` : `${season} ${y}`)

function NextCrop({ code, runId, year }: { code: number; runId?: string; year: number }) {
  const [c, setC] = useState<Candidates | null>(null)
  const [err, setErr] = useState(false)
  const [all, setAll] = useState(false)
  useEffect(() => {
    let alive = true
    setErr(false)
    api.candidates(code, runId, year).then((x) => { if (alive) setC(x) }).catch(() => { if (alive) setErr(true) })
    return () => { alive = false }
  }, [code, runId, year])
  if (err) return <p className="muted small">Crop candidates unavailable for this run.</p>
  if (!c || c.district_code !== code || c.year !== year) return <p className="muted small">Scoring candidate crops…</p>
  const cl = c.climate
  return (
    <div className="nextcrop">
      <div className="muted small">
        {year} climate: terminal heat {cl.terminal_heat_days_delta >= 0 ? '+' : ''}{fmt(cl.terminal_heat_days_delta, 1)} days
        {cl.kharif_rain_ratio != null && <> · Kharif rain {fmt(100 * cl.kharif_rain_ratio, 0)}% of normal</>}
        {cl.rabi_tmax_delta_c != null && <> · Rabi Tmax {cl.rabi_tmax_delta_c >= 0 ? '+' : ''}{fmt(cl.rabi_tmax_delta_c, 1)} °C</>}
        {c.groundwater_cap && <> · groundwater <b className={c.groundwater_cap === 'above cap' ? 'bad' : c.groundwater_cap === 'at cap' ? 'warn' : 'good'}>{c.groundwater_cap}</b></>}
      </div>
      {c.seasons.map((s) => {
        const rows = [...s.candidates].sort((a, b) => b.plan_ha - a.plan_ha || b.change_ha - a.change_ha)
        return (
          <div key={s.season} className="nc-season">
            <div className="nc-head">
              <span>{seasonLabel(s.season, year)} <span className="muted">after {s.preceding.crop ? `${s.preceding.crop.name} (${s.preceding.label}, ${fmt(s.preceding.crop.share_pct, 0)}%)` : s.preceding.label}</span></span>
            </div>
            {s.recommended ? (
              <div className="nc-rec">
                <b className="good">→ {s.recommended.name}</b> <span className="muted small">— {s.recommended.why}</span>
                <div className="nc-flags">
                  {s.recommended.positives.map((x) => <span key={x} className="flag good">✓ {x}</span>)}
                  {s.recommended.cautions.map((x) => <span key={x} className="flag warn">⚠ {x}</span>)}
                </div>
              </div>
            ) : <div className="muted small">Keep today’s mix: no crop gains area this season.</div>}
            {s.closest_alternative && <div className="small muted nc-alt">✗ {s.closest_alternative.name}: {s.closest_alternative.why}</div>}
            <table className="nc-table">
              <thead><tr><th>Crop</th><th>Decision</th><th title="Hectares today → plan">Area (ha)</th><th title="Climate-adjusted yield for this year">Yield kg/ha</th><th title="Irrigation water need">Water</th><th title="Net margin at expected prices">Margin ₹/ha</th><th /></tr></thead>
              <tbody>
                {rows.slice(0, all ? rows.length : 5).map((r) => (
                  <tr key={r.crop}>
                    <td>{r.name}</td>
                    <td className={DECISION_CLS[r.decision] ?? ''}>{r.decision}</td>
                    <td>{fmt(r.baseline_ha, 0)} → <b>{fmt(r.plan_ha, 0)}</b></td>
                    <td title={`${r.yield_source}; climate factor ${fmt(r.climate_factor, 3)}`}>{fmt(r.predicted_yield_kg_ha, 0)}</td>
                    <td className={r.water_ok ? '' : 'bad'} title={`CWAR ${fmt(r.cwar, 2)}`}>{fmt(r.irrigation_need_mm, 0)} mm</td>
                    <td className={r.net_margin_inr_ha < 0 ? 'bad' : ''}>{fmt(r.net_margin_inr_ha, 0)}</td>
                    <td className="nc-ic">{r.flags.map((f) => <span key={f.code} className={`flag-ic ${f.level}`} title={f.text}>{FLAG_ICON[f.level]}</span>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      })}
      <button className="link" onClick={() => setAll(!all)}>{all ? 'Show top 5 crops' : 'Show every candidate crop'}</button>
      <div className="muted small">{c.method}</div>
    </div>
  )
}

export default function DistrictCard({ t, code, runId, year, onClose, onAsk }: { t: Trace | null; code: number | null; runId?: string; year: number; onClose: () => void; onAsk: (q: string) => void }) {
  const [tab, setTab] = useState<'plan' | 'next'>('plan')
  if (!t) return null
  const tr = t.trace
  const y = tr.yearly[String(year)]
  const c = tr.context
  const stage = Number(c.gw_stage_pct)
  const P = y?.score_pillars as Record<string, number> | undefined
  const S = y?.score_pillars_status_quo as Record<string, number> | undefined
  const gain = y ? y.sustainability_score - y.sustainability_score_status_quo : 0
  const ys = [...new Set(tr.rotation_timeline.map((s) => s.year).filter((v) => v > 0))].sort((a, b) => a - b)
  const yLast = tr.final_year ?? ys[ys.length - 1]
  const rows = tr.rotation_timeline.filter((s) => s.year === 0 || s.year === year || s.year === ys[0] || s.year === yLast)
  return (
    <div className="district-card" role="dialog" aria-label={`${tr.district_name} district`}>
      <div className="dc-head">
        <div>
          <h2>{tr.district_name}</h2>
          <div className="muted">{tr.state_name} · groundwater <b className={stage > 100 ? 'bad' : stage > 70 ? 'warn' : 'good'}>{c.gw_category}{Number.isFinite(stage) ? ` ${fmt(stage, 0)}%` : ''}</b> · irrigated {fmt(Number(c.irrigation_coverage_pct), 0)}% · soil N {fmt(Number(c.soil_n_kg_ha), 0)} kg/ha</div>
        </div>
        <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
      </div>

      {y && (
        <div className="dc-score">
          <div className="dc-score-head">
            <span>Sustainability score · {year}</span>
            <span className="dc-vs"><span className="vs-sq">{fmt(y.sustainability_score_status_quo, 0)}</span> → <span className="vs-plan">{fmt(y.sustainability_score, 0)}</span><small>/100</small>
              <b className={gain > 0.5 ? 'up' : gain < -0.5 ? 'down' : 'muted'}> {gain >= 0 ? '+' : ''}{fmt(gain, 1)}</b></span>
          </div>
          <div className="dc-legend"><i className="sq" /> Do nothing <i /> Agri Sus plan</div>
          {P && S && Object.keys(P).map((id) => (
            <div key={id} className="pillar compact" title={DISTRICT_PILLAR_HELP[id]}>
              <div className="pillar-head"><span>{DISTRICT_PILLAR_LABEL[id]}</span><span><span className="muted">{fmt(S[id], 0)} →</span> <b>{fmt(P[id], 0)}</b></span></div>
              <div className="pillar-bar sq"><div className="pillar-fill" style={{ width: `${S[id]}%` }} /></div>
              <div className="pillar-bar"><div className="pillar-fill" style={{ width: `${P[id]}%` }} /></div>
            </div>
          ))}
          <div className="dc-stats">
            <span title="Farm income incl. incentives vs today. Guardrail: ≥ 100%">Farmer income <b className={(y.income_vs_today_pct ?? 100) >= 99.95 ? 'good' : 'bad'}>{fmt(y.income_vs_today_pct, 1)}%</b> of today</span>
            <span title={y.outlay_cr ? `Transfers ₹${fmt(y.outlay_cr.dbt, 1)} cr · seed kits ₹${fmt(y.outlay_cr.seed_kits, 1)} cr · price support ₹${fmt(y.outlay_cr.price_support, 1)} cr` : undefined}>Outlay <b>₹{fmt(y.incentive_cr, 1)} cr</b></span>
            {y.mropi_tier && y.mropi_tier !== 'No outlay' && <span title="Public return (forex + power + urea subsidy saved) per rupee of outlay">Return <b>{y.mropi != null ? `${fmt(y.mropi, 1)}×` : 'no outlay'}</b> · {y.mropi_tier}</span>}
            {y.water_assessed && <span>GW use {fmt(y.gw_stage_status_quo_pct, 0)}% → <b>{fmt(y.gw_stage_pct, 0)}%</b></span>}
            {y.water_saved_mcm != null && y.water_saved_mcm > 0.5 && <span>Water saved <b>{fmt(y.water_saved_mcm, 0)} MCM/yr</b></span>}
            <span>Paddy <b>{y.rice_area_change_pct == null ? '–' : `${y.rice_area_change_pct > 0 ? '+' : ''}${fmt(y.rice_area_change_pct, 1)}%`}</b></span>
            <span>Legumes {fmt(y.legume_share_status_quo_pct, 0)}% → <b>{fmt(y.legume_share_pct, 0)}%</b> of crops</span>
          </div>
        </div>
      )}

      <div className="dc-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'plan'} className={tab === 'plan' ? 'active' : ''} onClick={() => setTab('plan')}>Plan & why</button>
        <button role="tab" aria-selected={tab === 'next'} className={tab === 'next' ? 'active' : ''} onClick={() => setTab('next')}>🌱 Next crop · {year}</button>
      </div>
      {tab === 'next' && code != null && <NextCrop code={code} runId={runId} year={year} />}
      {tab === 'plan' && <>
      <h3 className="sub">Crop mix: today → plan <span className="muted">(share of season area)</span></h3>
      <div className="timeline">
        {rows.map((s) => {
          const tot = s.total_ha || s.top_crops.reduce((a, b) => a + b.area_ha, 0) || 1
          const other = Math.max(0, tot - s.top_crops.reduce((a, b) => a + b.area_ha, 0))
          return (
            <div key={`${s.year}${s.season}`} className={`tl-row ${s.year === 0 ? 'today' : ''}`}>
              <span className="tl-label">{s.year === 0 ? 'Today' : s.year} {s.season[0]}</span>
              <div className="tl-bar">
                {s.top_crops.map((cr) => (
                  <div key={cr.crop} style={{ width: `${(100 * cr.area_ha) / tot}%`, background: COLORS[cr.crop] ?? '#888' }} title={`${CROP_LABEL[cr.crop] ?? cr.crop}: ${fmt(cr.area_ha, 0)} ha (${fmt((100 * cr.area_ha) / tot, 0)}%)`}>
                    {(100 * cr.area_ha) / tot > 13 ? CROP_LABEL[cr.crop] ?? cr.crop : ''}
                  </div>
                ))}
                {other / tot > 0.005 && <div style={{ width: `${(100 * other) / tot}%`, background: '#3a4a43' }} title={`Other crops: ${fmt(other, 0)} ha`} />}
              </div>
            </div>
          )
        })}
      </div>

      <h3 className="sub">Why — decision trace for {yLast}</h3>
      <ul className="moves">
        {tr.moves_2030.slice(0, 6).map((m) => (
          <li key={m.crop + m.season}>
            <b className={m.change_ha >= 0 ? 'good' : 'bad'}>{m.change_ha >= 0 ? '+' : ''}{fmt(m.change_ha, 0)} ha</b> {CROP_LABEL[m.crop] ?? m.crop} ({m.season}) — {m.reasons.join('; ') || 'rebalanced for national targets'}
          </li>
        ))}
        {tr.moves_2030.length === 0 && <li className="muted">No significant change: the current crop mix already fits the guardrails here, or switching would cost more than the budget allows.</li>}
        {tr.rejected_alternatives.slice(0, 2).map((r) => (
          <li key={'r' + r.crop + r.season} className="muted">✗ {CROP_LABEL[r.crop] ?? r.crop} ({r.season}) not chosen — {r.reasons.join('; ')}</li>
        ))}
      </ul>
      </>}
      <button className="link" onClick={() => onAsk(`Explain the plan for ${tr.district_name} district and what farmers there need to make the switch.`)}>✦ Ask the Co-pilot about {tr.district_name}</button>
    </div>
  )
}
