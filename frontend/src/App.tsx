import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import type { FeatureCollection, Meta, SimResult, Trace } from './api'
import Controls, { DEFAULT_LEVERS, leversFrom } from './components/Controls'
import type { Levers } from './components/Controls'
import KpiRibbon from './components/KpiRibbon'
import MapView, { METRICS } from './components/MapView'
import type { Metric, Mode } from './components/MapView'
import DistrictCard from './components/DistrictCard'
import Copilot from './components/Copilot'
import Trajectory from './components/Trajectory'
import SustainabilityPanel from './components/SustainabilityPanel'
import StateBoard from './components/StateBoard'
import BriefModal from './components/BriefModal'
import Tour from './components/Tour'
import RunDiff from './components/RunDiff'
import BudgetPanel from './components/BudgetPanel'
import TradePanel from './components/TradePanel'

type Tab = 'sustainability' | 'budget' | 'trade' | 'states' | 'trajectory' | 'guardrails'
const TABS: [Tab, string][] = [['sustainability', '🌍 Sustainability forecast'], ['budget', '💰 Budget & returns'], ['trade', '🚢 Trade shocks'], ['states', '🏆 State leaderboard'], ['trajectory', '📈 Trajectory'], ['guardrails', '⚖ Binding guardrails']]

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [geo, setGeo] = useState<FeatureCollection | null>(null)
  const [scenario, setScenario] = useState('baseline')
  const [levers, setLevers] = useState<Levers>(DEFAULT_LEVERS)
  const [result, setResult] = useState<SimResult | null>(null)
  const [prev, setPrev] = useState<SimResult | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [year, setYear] = useState(2030)
  const [metric, setMetric] = useState<Metric>('sustainability_score')
  const [selected, setSelected] = useState<number | null>(null)
  const [trace, setTrace] = useState<Trace | null>(null)
  const [pendingQ, setPendingQ] = useState<string | null>(null)
  const [showInfo, setShowInfo] = useState(false)
  const [tab, setTab] = useState<Tab>('sustainability')
  const [focus, setFocus] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('plan')
  const [playing, setPlaying] = useState(false)
  const [showBrief, setShowBrief] = useState(false)
  const [showTour, setShowTour] = useState(() => !localStorage.getItem('agrisus_tour_v3') && !new URLSearchParams(location.search).has('notour'))
  const timer = useRef<number | null>(null)
  const years = meta?.plan_years ?? [2027, 2028, 2029, 2030]
  const states = useMemo(() => [...new Set((result?.districts ?? []).map((d) => d.state_name))].sort(), [result?.districts])

  const closeTour = () => { localStorage.setItem('agrisus_tour_v3', '1'); setShowTour(false) }
  const play = () => {
    if (playing) { if (timer.current) window.clearInterval(timer.current); setPlaying(false); return }
    setPlaying(true)
    setMode('plan')
    let i = 0
    setYear(years[0])
    timer.current = window.setInterval(() => {
      i += 1
      if (i >= years.length) { if (timer.current) window.clearInterval(timer.current); setPlaying(false); return }
      setYear(years[i])
    }, 1300)
  }
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current) }, [])

  // every new result remembers the one it replaced, so the UI can say what the last change did
  const shown = useRef<SimResult | null>(null)
  // the district card sits just below the map toolbar, whose height depends on how its controls wrap
  const tools = useRef<HTMLDivElement>(null)
  const [toolsH, setToolsH] = useState(66)
  useEffect(() => {
    const el = tools.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setToolsH(Math.round(e.contentRect.height)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const show = useCallback((r: SimResult) => {
    const old = shown.current
    if (old && old.run_id !== r.run_id) setPrev(old)
    shown.current = r
    setResult(r)
    setLevers(leversFrom(r.params))
  }, [])

  const run = useCallback(async (sc = scenario, lv = levers) => {
    setRunning(true)
    setError(null)
    try {
      show(await api.simulate(sc, lv))
    } catch (e) {
      setError((e as Error).message)
    }
    setRunning(false)
  }, [scenario, levers, show])

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(String(e)))
    api.geo().then(setGeo)
    run('baseline', DEFAULT_LEVERS)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (selected == null) { setTrace(null); return }
    let alive = true
    api.district(selected, result?.run_id).then((t) => { if (alive) setTrace(t) }).catch(() => { if (alive) setTrace(null) })
    return () => { alive = false }
  }, [selected, result?.run_id])

  // a scenario may preset a lever (e.g. the zero-depletion mandate sets the groundwater guardrail); leaving it restores the default
  const preset = (id: string) => meta?.scenarios.find((x) => x.id === id)?.levers ?? {}
  const chooseScenario = (s: string) => {
    const lv = { ...levers } as Record<string, unknown>
    for (const key of Object.keys(preset(scenario))) if (key in DEFAULT_LEVERS) lv[key] = DEFAULT_LEVERS[key as keyof Levers]
    Object.assign(lv, preset(s))
    const fy = meta?.scenarios.find((x) => x.id === s)?.focus_year
    if (fy) setYear(fy)
    setScenario(s)
    setLevers(lv as Levers)
    run(s, lv as Levers)
  }
  // "what if this supplier stops?" from the trade tab: add a 100% cut to the current levers and re-plan
  const onShock = (country: string, product: string) => {
    const max = meta?.trade?.max_cuts ?? 3
    const cuts = [...levers.trade_cuts.filter((c) => !(c.country === country && c.product === product)), { country, product, pct: 100 }].slice(-max)
    const lv = { ...levers, trade_cuts: cuts }
    setLevers(lv)
    run(scenario, lv)
  }
  const onSimulation = useCallback((id: string) => {
    api.run(id).then((r) => { show(r); setScenario(r.params.scenario) }).catch(() => undefined)
  }, [show])
  const dirty = !!result && JSON.stringify(levers) !== JSON.stringify(leversFrom(result.params))

  const k = result?.kpis.find((x) => x.year === year)
  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">🌱</span>
          <div>
            <h1>Agri Sus</h1>
            <span className="tag">AI policy simulator for every district in India · what if farmers switched crops — compared with doing nothing</span>
          </div>
        </div>
        <div className="head-right">
          <span className="pill">{meta?.data_backend === 'BigQuery' ? 'BigQuery' : 'Data snapshot'}</span>
          <span className={`pill ${meta?.gemini.enabled ? 'live' : ''}`}>{meta?.gemini.enabled ? 'Gemini on Vertex AI' : 'Gemini offline'}</span>
          <button className="primary" onClick={() => setShowBrief(true)} disabled={!result}>✦ Policy brief</button>
          <button className="ghost" onClick={() => setShowTour(true)}>How it works</button>
          <button className="ghost" onClick={() => setShowInfo(true)}>Method & data</button>
        </div>
      </header>
      <main>
        <Controls meta={meta} scenario={scenario} setScenario={chooseScenario} levers={levers} setLevers={setLevers} onRun={() => run()} running={running}
          status={result?.lever_status} dirty={dirty} />
        <section className="center">
          {/* first screen: KPIs + last-change strip + map; the map takes whatever height is left so India always fits */}
          <div className="stage">
          <KpiRibbon k={k} />
          {error && <div className="error">{error}</div>}
          {prev && result && !running && <RunDiff prev={prev} cur={result} year={year} labels={meta?.scenarios} onClose={() => setPrev(null)} />}
          <div className="map-wrap" style={{ ['--tools-h' as string]: `${toolsH}px` }}>
            <MapView geo={geo} districts={result?.districts ?? []} year={year} metric={metric} mode={mode} setMode={setMode} kpi={trace ? undefined : k}
              selected={selected} onSelect={setSelected} focusState={focus} topInset={toolsH + 10} />
            <div className="map-tools" ref={tools}>
              <select value={metric} onChange={(e) => setMetric(e.target.value as Metric)} aria-label="Map layer">
                {Object.entries(METRICS).map(([id, m]) => <option key={id} value={id}>{m.label}</option>)}
              </select>
              <select value={focus ?? ''} onChange={(e) => setFocus(e.target.value || null)} aria-label="Zoom to state">
                <option value="">All India</option>
                {states.map((st) => <option key={st} value={st}>{st}</option>)}
              </select>
              <div className="scrubber">
                <button className={`play ${playing ? 'active' : ''}`} onClick={play} title="Animate 2027 → 2030">{playing ? '❚❚' : '▶'}</button>
                {years.map((y) => (
                  <button key={y} className={y === year ? 'active' : ''} onClick={() => setYear(y)}>{y}</button>
                ))}
              </div>
              {METRICS[metric].sq ? (
                <div className="scrubber compare" role="group" aria-label="Compare">
                  <button className={mode === 'sq' ? 'active sq' : ''} onClick={() => setMode('sq')} title="2027-30 if today’s crop pattern continues">Do nothing</button>
                  <button className={mode === 'plan' ? 'active' : ''} onClick={() => setMode('plan')} title="2027-30 under the optimised Agri Sus plan">Agri Sus plan</button>
                  <button className={mode === 'diff' ? 'active diff' : ''} onClick={() => setMode('diff')} title="Plan minus do nothing: where the plan makes a difference">Difference</button>
                </div>
              ) : <span className="badge-change">Change vs doing nothing</span>}
              {running && <span className="spinner">Optimising {result ? `${Math.round(result.solver.variables / 1000)}k` : '110k'} variables across India…</span>}
            </div>
            {trace && <DistrictCard t={trace} code={selected} runId={result?.run_id} year={year} onClose={() => setSelected(null)} onAsk={setPendingQ} />}
          </div>
          </div>
          <div className="bottom">
            <div className="tabs" role="tablist">
              {TABS.map(([id, l]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{l}</button>)}
            </div>
            <div className="tab-body">
              {tab === 'sustainability' && <SustainabilityPanel result={result} year={year} />}
              {tab === 'budget' && <BudgetPanel result={result} year={year} onMap={() => { setMetric('mropi'); setYear(2030) }} />}
              {tab === 'trade' && <TradePanel result={result} onShock={onShock} running={running} />}
              {tab === 'states' && <StateBoard rows={result?.states ?? []} focus={focus} onFocus={(st) => { setFocus(st); if (st) setMetric('sustainability_score') }} />}
              {tab === 'trajectory' && <Trajectory kpis={result?.kpis ?? []} />}
              {tab === 'guardrails' && (
                <div className="binding">
                  <h4>Binding guardrails (shadow prices) <span className="muted">— which rule is “costing” the most, and how much one more unit of it would be worth</span></h4>
                  <ul>
                    {(result?.binding_constraints ?? []).filter((b) => b.year === year || b.year === 2030).slice(0, 8).map((b, i) => (
                      <li key={i}><b>{b.constraint}</b><br /><span className="muted">{b.shadow_price}</span></li>
                    ))}
                  </ul>
                  {result && (
                    <div className="muted small">
                      {result.solver.engine} · {result.solver.variables.toLocaleString()} vars · {result.solver.seconds}s · GW caps binding in {result.constraint_summary.district_gw_caps_binding} district-years · {result.constraint_summary.crop_district_expansions_blocked_by_cwar} expansions blocked by CWAR
                    </div>
                  )}
                </div>
              )}
              {result?.outlook && tab === 'sustainability' && (
                <div className="outlook small"><b>Gemini outlook:</b> {result.outlook.rationale}</div>
              )}
            </div>
          </div>
        </section>
        <Copilot meta={meta} onSimulation={onSimulation} pending={pendingQ} clearPending={() => setPendingQ(null)} />
      </main>
      {showBrief && <BriefModal runId={result?.run_id} state={focus} languages={meta?.languages ?? { en: 'English' }} onClose={() => setShowBrief(false)} />}
      {showTour && <Tour onClose={closeTour} />}
      {showInfo && (
        <div className="modal" onClick={() => setShowInfo(false)}>
          <div className="modal-body" onClick={(e) => e.stopPropagation()}>
            <h2>Method & data</h2>
            <p><b>Optimizer.</b> Google OR-Tools linear program (COIN-OR CLP engine) over {meta?.n_districts ?? 752} districts in every state and UT of India × 18 crops × Kharif/Rabi × 2027-2030 (~{result ? Math.round(result.solver.variables / 1000) : 129}k variables). It maximises calibrated farmer welfare + soil-nitrogen credit − ¼ × import bill − groundwater cost − 1.25 × policy outlay (marginal cost of public funds), subject to: cereal self-sufficiency floor, rice-export retention, a yearly cap on land changing crop, flexibility bounds (rice/wheat may fall ≤ 15 %/yr, other crops ≤ 5 %/yr), national growth caps (≤ 5 %/yr per crop by default — the “Seed & market scale-up” lever), farmer income never below today in any district (paid from one annual budget), groundwater caps from CGWB GWRA 2024-25, a soft legume-rotation target and the CWAR water rule. The plan is one coherent pathway: the LP chooses each district’s 2030 crop mix and every year moves a quarter of the way there (25 → 50 → 75 → 100 %), so no farmer is asked to switch and then switch back — yet every year must satisfy its own climate, water, food-security and income constraints. The tags next to each lever are read from the LP’s dual values: “limiting” means the lever is binding, “not limiting” means moving it will not change the plan. Cotton and sugarcane are held fixed.</p>
            <p><b>Policy instruments & budget.</b> Three instruments share one annual budget and the LP picks the mix district by district: income-parity transfers per hectare switched (DBT, like Haryana’s <i>Mera Pani Meri Virasat</i>); seed & FPO kits (₹4,000/ha certified seed, bio-inputs and aggregation, +10 % pulse/oilseed yield); and an assured price (price-deficiency payment up to MSP, PM-AASHA style) that also removes half of the switching-risk premium. Any instrument can be switched off. In <i>Least cost to hit target</i> mode the budget itself becomes a decision variable: the optimizer finds the smallest yearly outlay that meets the import-bill target — or says how far the guardrails leave it short, however much money is offered.</p>
            <p><b>Fiscal ledger & MROPI.</b> Every rupee is tracked against what it returns: forex saved on avoided pulse and edible-oil imports, farm-power subsidy saved on groundwater no longer pumped (₹1.5/m³) and urea subsidy saved through legume nitrogen credit (₹32,000/t). MROPI = those returns ÷ outlay in each district: Tier 1 ≥ 10× (fund first), Tier 2 1-10×, Tier 3 &lt; 1× (funded for farm income and aquifer security; first to lose money when the budget shrinks); districts that save public money with no outlay are self-financing. The district × year ledger is written to BigQuery table <code>policy_budget_ledger</code> for audit.</p>
            <p><b>Trade shocks.</b> Country-wise imports of pulses and edible oils come from the India Data Portal’s DGCI&S import statistics (country × HS-8 code, FY2014-15 to 2025-26). “What if Indonesia stops palm oil?” forces the plan to replace that volume from Indian fields every year; the result shows the crops and districts that fill the gap, the shortfall if guardrails bind, and the remaining alternative suppliers.</p>
            <p><b>Next crop.</b> For a district and year the card scores every candidate crop for each season: what was sown before (rotation), climate-adjusted yield, irrigation need versus water availability (CWAR), margin, nitrogen credit, terminal heat and rice-fallow fit — and, for crops the plan does not choose, the LP reduced cost: what forcing one hectare would cost the plan, including the groundwater-cap penalty.</p>
            <p><b>Calibration.</b> Positive Mathematical Programming: with zero incentive the model reproduces today’s crop map in every district. Switching costs (risk, learning, markets) are inferred from the margin gap to each district’s best alternative plus a 15 % adoption premium. That is why moving Punjab paddy costs ~₹60k per hectare in the default plan — about 3.5× the ₹17.3k/ha (₹7,000/acre) of Haryana’s <i>Mera Pani Meri Virasat</i>, consistent with its low uptake.</p>
            <p><b>Yields.</b> 2020-24 district baselines (DES APY) × state trend × climate response (rainfall deficit/excess × rainfed share, crop heat sensitivity, terminal heat, dry spells) calibrated on 2001-2019 and driven by NASA NEX-GDDP-CMIP6 SSP2-4.5 projections. Trained on 76k district-crop-years across India. Hold-out 2020-24 WMAPE {String(meta?.yield_model?.wmape_pct_model ?? '')}% vs persistence {String(meta?.yield_model?.wmape_pct_persistence ?? '')}% — used for stress tests, not point forecasts. On Google Cloud a BigQuery ML boosted-tree challenger replaces these climate factors only if it beats the prior’s BigQuery ML twin on the same hold-out. Active now: <b>{meta?.yield_engine?.active === 'bqml_boosted_tree' ? 'BigQuery ML boosted tree' : 'agronomic prior'}</b>{meta?.yield_engine?.reason ? ` (${meta.yield_engine.reason})` : ''}. {meta?.yield_rows_repaired ? `${meta.yield_rows_repaired} district-crop baselines with unit errors in the source (e.g. groundnut reported at 132 t/ha) were replaced by state medians.` : ''}</p>
            <p><b>Gemini.</b> {meta?.gemini.agent_model} via {meta?.gemini.backend}: function-calling agent (optimizer, BigQuery SQL, yield model, Google Search grounding) and a grounded 2027-30 outlook (ENSO, heat, import prices) that drives the “Gemini live outlook” scenario.</p>
            <p><b>Sustainability.</b> ASSI is an <i>absolute</i> 0-100 index, scored on the same yardstick for “do nothing” and for the plan: import self-reliance 30 % (pulses & edible-oil self-sufficiency), groundwater health 30 % (draft-weighted CGWB stage), soil health 20 % (legume share of cropped area vs a one-in-three rotation) and crop diversity 20 %. District scores use water 40 %, soil 30 %, diversity 30 %. Farmer income and cereal security are hard guardrails, never traded off. Stress tests re-score the fixed plan under unplanned El Niño, heatwave and back-to-back weak monsoons.</p>
            <p><b>Data.</b> India Data Portal (APY, Agri-census crop & non-crop, Soil Health Card, CGWB wells, land-use/irrigation, MSP, cost of cultivation, IMD rainfall, PMKSY, DGCI&S country-wise import trade), CGWB IN-GRES GWRA 2022-25, DGCIS TradeStat 2024-25, NASA POWER & NEX-GDDP-CMIP6, FAO-56, ICAR. Stored in BigQuery dataset <code>agri_sus_mvp</code>.</p>
            <p><b>Google Cloud.</b> Cloud Run serves the app; Gemini on Vertex AI runs the co-pilot and the Search-grounded outlook; BigQuery holds the data, every run and the fiscal ledger; a BigQuery ML boosted-tree challenger, registered in the Vertex AI Model Registry, competes with the yield prior on hold-out error; Earth Engine can rebuild the district climate features from CHIRPS, ERA5-Land and CMIP6 grids; Cloud Build and Artifact Registry ship the container. Live now: data from {meta?.data_backend ?? '…'}; climate from {meta?.climate_source ?? '…'}.</p>
            <button className="primary" onClick={() => setShowInfo(false)}>Close</button>
          </div>
        </div>
      )}
    </div>
  )
}
