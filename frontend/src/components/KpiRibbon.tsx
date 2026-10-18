import type { Kpi } from '../api'
import { fmt } from '../api'

const Arrow = ({ good }: { good: boolean }) => <span className={good ? 'up' : 'down'}>{good ? '▲' : '▼'}</span>

export default function KpiRibbon({ k }: { k?: Kpi }) {
  if (!k) return <div className="kpis skeleton" />
  const gain = k.assi - k.assi_status_quo
  const cut = k.import_bill_status_quo_usd_bn > 0 ? (100 * k.import_saving_usd_bn) / k.import_bill_status_quo_usd_bn : 0
  const short = k.import_target_shortfall_usd_bn ?? Math.max(0, k.import_bill_usd_bn - k.import_target_usd_bn)
  return (
    <div className="kpis">
      <div className="kpi hero">
        <div className="kpi-label">Sustainability index · {k.year} <span className="info" title="ASSI, an absolute 0-100 score for India’s farm system: import self-reliance 30%, groundwater health 30%, soil health 20%, crop diversity 20%. Both futures are scored on the same yardstick.">ⓘ</span></div>
        <div className="kpi-vs">
          <span className="vs-sq" title="Do nothing: today’s crop pattern continues">{fmt(k.assi_status_quo, 1)}</span>
          <span className="vs-arrow">→</span>
          <span className="vs-plan" title="Agri Sus plan">{fmt(k.assi, 1)}</span>
        </div>
        <div className="kpi-sub">do nothing → Agri Sus · <b className={gain >= 0 ? 'up' : 'down'}>{gain >= 0 ? '+' : ''}{fmt(gain, 1)} pts</b></div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Import bill · pulses + edible oil <span className="info" title="What India would spend importing pulses and edible oils in that year. The plan grows more of them at home.">ⓘ</span></div>
        <div className="kpi-value">${fmt(k.import_bill_usd_bn, 1)}<small>bn vs ${fmt(k.import_bill_status_quo_usd_bn, 1)}bn</small></div>
        <div className="kpi-sub"><Arrow good /> saves ${fmt(k.import_saving_usd_bn, 1)}bn (−{fmt(cut, 0)}%) · pulses {fmt(k.pulses_ssr_status_quo_pct, 0)}→{fmt(k.pulses_ssr_pct, 0)}% · oils {fmt(k.edible_oils_ssr_status_quo_pct, 0)}→{fmt(k.edible_oils_ssr_pct, 0)}%{short > 0.05 ? <span className="warn" title="The import-cut target is a soft goal; the gap shows how far the guardrails let the plan go."> · target missed by ${fmt(short, 1)}bn</span> : <span className="up"> · target met</span>}</div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Groundwater pumped <span className="info" title="Irrigation groundwater draft. 1 BCM = 1 billion m³ ≈ the yearly domestic use of ~18 million people.">ⓘ</span></div>
        <div className="kpi-value">{fmt(k.water_saved_bcm, 1)}<small>BCM/yr saved</small></div>
        <div className="kpi-sub"><Arrow good={k.districts_over_exploited <= k.districts_over_exploited_status_quo} /> over-exploited districts {k.districts_over_exploited_status_quo} → {k.districts_over_exploited} · paddy {fmt(k.rice_area_status_quo_mha, 1)}→{fmt(k.rice_area_mha, 1)} Mha</div>
      </div>
      <div className={`kpi badge ${k.guardrail_income_ok ? 'ok' : 'warn'}`}>
        <div className="kpi-label">Farmer income <span className="info" title="Calibrated farm income after switching, including income-parity incentives, as % of today’s income. Guardrail: no district may fall below 100%.">ⓘ</span></div>
        <div className="kpi-value">{fmt(k.farmer_income_vs_today_pct, 1)}%<small>of today</small></div>
        <div className="kpi-sub">{k.guardrail_income_ok ? <span className="up">✓ no district below today</span> : <span className="warn">⚠ {k.districts_below_income_floor} districts below today</span>} · ₹{fmt(k.incentive_cr, 0)} cr incentives</div>
      </div>
      <div className={`kpi badge ${k.guardrail_cereal_ok ? 'ok' : 'warn'}`}>
        <div className="kpi-label">Food security <span className="info" title="Cereal self-sufficiency: home-grown rice + wheat + maize + millets ÷ national demand. A hard floor — food security is never traded away.">ⓘ</span></div>
        <div className="kpi-value">{fmt(k.cereal_ssr_pct, 1)}%<small>cereal SSR</small></div>
        <div className="kpi-sub">{k.guardrail_cereal_ok ? <span className="up">✓ floor held</span> : <span className="warn">⚠ below floor</span>} · rice surplus {fmt(k.rice_exportable_surplus_mt, 1)} Mt</div>
      </div>
    </div>
  )
}
