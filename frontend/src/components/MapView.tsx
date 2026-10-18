import { useEffect, useMemo, useRef, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { FlyToInterpolator, WebMercatorViewport } from '@deck.gl/core'
import { GeoJsonLayer } from '@deck.gl/layers'
import { Map } from 'react-map-gl/maplibre'
import type { DistrictResult, Feature, FeatureCollection, Kpi } from '../api'
import { fmt } from '../api'

export type Metric = 'sustainability_score' | 'gw_stage_pct' | 'legume_share_pct' | 'rice_share_pct' | 'water_saved_mcm' | 'rice_area_change_pct' | 'income_vs_today_pct' | 'incentive_cr' | 'mropi'
export type Mode = 'sq' | 'plan' | 'diff'
type Ramp = 'score' | 'water' | 'diverge' | 'green' | 'gold' | 'blue' | 'paddy'
type Scale = { label: string; unit: string; domain: [number, number]; ramp: Ramp; help: string; decimals?: number; log?: boolean }
type Cfg = Scale & { sq?: string; diff?: Scale }

export const METRICS: Record<Metric, Cfg> = {
  sustainability_score: {
    label: 'District sustainability score', unit: '/100', domain: [20, 95], ramp: 'score', sq: 'sustainability_score_status_quo', decimals: 0,
    help: 'Groundwater health 40% + soil (legume share) 30% + crop diversity 30%. Same yardstick for both views.',
    diff: { label: 'Score gain: plan vs do nothing', unit: ' pts', domain: [0, 20], ramp: 'green', help: 'Points the plan adds to each district (0 = plan leaves the district as it is)' },
  },
  gw_stage_pct: {
    label: 'Groundwater use vs recharge', unit: '%', domain: [0, 200], ramp: 'water', sq: 'gw_stage_status_quo_pct', decimals: 0,
    help: 'CGWB stage of extraction: water pumped ÷ water recharged. Above 100% the aquifer is being mined.',
    diff: { label: 'Change in groundwater use', unit: ' pp', domain: [-40, 40], ramp: 'diverge', help: 'Blue = less pumping than doing nothing' },
  },
  legume_share_pct: {
    label: 'Legume share of cropped area', unit: '%', domain: [0, 50], ramp: 'green', sq: 'legume_share_status_quo_pct',
    help: 'Pulses, groundnut & soybean fix nitrogen (less urea) and replace imports. 33% = one legume in every three crops.',
    diff: { label: 'Change in legume share', unit: ' pp', domain: [0, 10], ramp: 'green', help: 'Percentage points of cropped area moved into legumes' },
  },
  rice_share_pct: {
    label: 'Paddy share of cropped area', unit: '%', domain: [0, 80], ramp: 'paddy', sq: 'rice_share_status_quo_pct',
    help: 'Paddy is the thirstiest staple: ~1,200-1,500 mm of water per crop in the north-west.',
    diff: { label: 'Change in paddy share', unit: ' pp', domain: [-15, 15], ramp: 'diverge', help: 'Blue = paddy replaced by less thirsty crops' },
  },
  water_saved_mcm: { label: 'Groundwater saved vs do nothing', unit: ' MCM/yr', domain: [0, 300], ramp: 'blue', decimals: 0, help: 'Million cubic metres of irrigation pumping avoided per year' },
  rice_area_change_pct: { label: 'Paddy area change vs do nothing', unit: '%', domain: [-30, 30], ramp: 'diverge', help: 'Negative = paddy replaced by pulses, oilseeds, millets or maize' },
  income_vs_today_pct: { label: 'Farmer income vs today', unit: '%', domain: [100, 115], ramp: 'green', help: 'Farm income after the switch, including income-parity incentives. Guardrail: never below 100% in any district.' },
  incentive_cr: { label: 'Policy outlay (all instruments)', unit: ' ₹cr/yr', domain: [0, 200], ramp: 'gold', decimals: 0, help: 'Income-parity transfers + seed & FPO kits + price-deficiency payments in the district, paid from the annual budget' },
  mropi: {
    label: 'Public return per rupee (MROPI)', unit: '×', domain: [0.1, 100], ramp: 'score', decimals: 1, log: true,
    help: 'Forex + power + urea subsidy saved ÷ policy outlay. Tier 1 ≥ 10× (fund first), Tier 2 1-10×, Tier 3 < 1× (cut first if the budget shrinks). Brightest green = shifts that need no outlay.',
  },
}

export function scaleOf(m: Metric, mode: Mode): Scale {
  const c = METRICS[m]
  return mode === 'diff' && c.diff ? c.diff : c
}

type RGB = [number, number, number]
const lerp = (a: RGB, b: RGB, t: number): RGB => [0, 1, 2].map((i) => Math.round(a[i] + (b[i] - a[i]) * t)) as RGB
export function ramp(kind: Ramp, t: number): RGB {
  t = Math.max(0, Math.min(1, t))
  if (kind === 'water') return t < 0.35 ? lerp([46, 160, 110], [240, 200, 70], t / 0.35) : t < 0.5 ? lerp([240, 200, 70], [240, 120, 50], (t - 0.35) / 0.15) : lerp([240, 120, 50], [170, 20, 40], (t - 0.5) / 0.5)
  if (kind === 'score') return t < 0.5 ? lerp([190, 40, 50], [240, 200, 70], t / 0.5) : lerp([240, 200, 70], [40, 170, 90], (t - 0.5) / 0.5)
  // dark-theme ramps: "nothing happens" stays dark so the districts that change stand out
  if (kind === 'green') return t < 0.5 ? lerp([48, 62, 55], [70, 150, 95], t / 0.5) : lerp([70, 150, 95], [150, 240, 140], (t - 0.5) / 0.5)
  if (kind === 'blue') return t < 0.5 ? lerp([44, 58, 66], [40, 120, 210], t / 0.5) : lerp([40, 120, 210], [130, 210, 255], (t - 0.5) / 0.5)
  if (kind === 'gold') return t < 0.5 ? lerp([60, 58, 46], [190, 130, 30], t / 0.5) : lerp([190, 130, 30], [255, 215, 90], (t - 0.5) / 0.5)
  if (kind === 'paddy') return t < 0.5 ? lerp([60, 66, 62], [60, 120, 190], t / 0.5) : lerp([60, 120, 190], [120, 200, 255], (t - 0.5) / 0.5)
  return t < 0.5 ? lerp([50, 150, 255], [78, 86, 82], t / 0.5) : lerp([78, 86, 82], [255, 95, 70], (t - 0.5) / 0.5)
}

export function value(d: DistrictResult | undefined, year: number, m: Metric, mode: Mode = 'plan'): number | null {
  const y = d?.years?.[String(year)] as unknown as Record<string, number | null> | undefined
  if (!y) return null
  const cfg = METRICS[m]
  if (m === 'mropi' && y.mropi == null) return (y as unknown as { mropi_tier?: string }).mropi_tier === 'Self-financing' ? Infinity : null
  const plan = y[m] ?? null
  if (!cfg.sq) return plan
  const sq = y[cfg.sq] ?? null
  if (mode === 'sq') return sq
  if (mode === 'diff') return plan != null && sq != null ? plan - sq : null
  return plan
}

type Props = {
  geo: FeatureCollection | null
  districts: DistrictResult[]
  year: number
  metric: Metric
  mode: Mode
  setMode: (m: Mode) => void
  kpi?: Kpi
  selected: number | null
  onSelect: (code: number) => void
  focusState: string | null
  /** px covered by the map toolbar at the top (measured by the parent) */
  topInset?: number
}

type Box = [[number, number], [number, number]]
/** Bounding box of a state (or all India), or of one modelled district when `only` is given. */
function bounds(geo: FeatureCollection, state: string | null, only?: number): Box | null {
  let x0 = 180, y0 = 90, x1 = -180, y1 = -90
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === 'number') {
      const [x, y] = c as number[]
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y)
    } else if (Array.isArray(c)) c.forEach(walk)
  }
  geo.features.filter((f) => (only != null ? f.properties.data_code === only : !state || f.properties.state_name.toLowerCase() === state.toLowerCase()))
    .forEach((f) => walk((f.geometry as { coordinates: unknown }).coordinates))
  return x0 < x1 ? [[x0, y0], [x1, y1]] : null
}

const code = (f: Feature) => f.properties.data_code ?? null
const fmtv = (v: number | null, s: Scale, signed = false) => (v == null ? 'no data' : `${signed && v > 0 ? '+' : ''}${fmt(v, s.decimals ?? 1)}${s.unit}`)

export default function MapView({ geo, districts, year, metric, mode, setMode, kpi, selected, onSelect, focusState, topInset = 70 }: Props) {
  const byCode = useMemo(() => new globalThis.Map(districts.map((d) => [d.district_code, d])), [districts])
  const cfg = METRICS[metric]
  const eff: Mode = cfg.sq ? mode : 'plan'
  const sc = scaleOf(metric, eff)
  const ref = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [view, setView] = useState<Record<string, unknown>>({ longitude: 82.8, latitude: 22, zoom: 3.4, pitch: 0, bearing: 0 })
  const [fitKey, setFitKey] = useState(0)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // fit India (or the focused state) to the real container size, on load, resize and focus change
  const viewRef = useRef(view)
  const sizeRef = useRef(size)
  const insetRef = useRef(topInset)
  useEffect(() => { viewRef.current = view; sizeRef.current = size; insetRef.current = topInset })
  useEffect(() => {
    if (!geo || size.w < 120 || size.h < 120) return
    const b = bounds(geo, focusState) ?? bounds(geo, null)
    if (!b) return
    const vp = new WebMercatorViewport({ width: size.w, height: size.h })
    // keep India clear of the toolbar (measured: 1 row on wide screens, 3 on phones) and, on phones, the full-width legend
    const narrow = window.innerWidth <= 760
    const pad = { top: Math.min(insetRef.current + 8, size.h / 4), bottom: narrow ? Math.min(100, size.h / 5) : Math.min(24, size.h / 10), left: 16, right: 16 }
    const { longitude, latitude, zoom } = vp.fitBounds(b, { padding: pad })
    setView({ longitude, latitude, zoom: Math.min(zoom, 8), pitch: 0, bearing: 0, transitionDuration: 700, transitionInterpolator: new FlyToInterpolator() })
  }, [geo, focusState, size.w, size.h, fitKey])

  // keep a clicked district visible beside its detail card (min(470px, 58%) wide, on the right)
  useEffect(() => {
    const { w, h } = sizeRef.current
    if (selected == null || !geo || w < 560) return
    const b = bounds(geo, null, selected)
    if (!b) return
    const v = viewRef.current as { longitude: number; latitude: number; zoom: number }
    const vp = new WebMercatorViewport({ width: w, height: h, longitude: v.longitude, latitude: v.latitude, zoom: v.zoom })
    // whole district (projected bbox), not just its centre, must sit left of the card and below the toolbar
    const [x0, y0] = vp.project([b[0][0], b[1][1]])
    const [x1, y1] = vp.project([b[1][0], b[0][1]])
    const free = w - Math.min(470, 0.58 * w) - 20
    const top = insetRef.current + 10
    if (x0 > 16 && x1 < free - 16 && y0 > top && y1 < h - 40) return
    const [longitude, latitude] = vp.unproject([w / 2 + (x0 + x1) / 2 - free / 2, h / 2 + (y0 + y1) / 2 - (top + h - 40) / 2])
    setView({ ...v, longitude, latitude, transitionDuration: 600, transitionInterpolator: new FlyToInterpolator() })
  }, [selected, geo])

  const improved = useMemo(() => districts.filter((d) => {
    const y = d.years[String(year)]
    return y && y.sustainability_score > y.sustainability_score_status_quo + 0.5
  }).length, [districts, year])

  const layer = useMemo(
    () =>
      geo &&
      new GeoJsonLayer<Feature['properties']>({
        id: 'districts',
        data: geo as never,
        pickable: true,
        stroked: true,
        filled: true,
        getLineColor: (f) => (selected != null && code(f as unknown as Feature) === selected ? [255, 255, 255, 255] : [20, 30, 25, 140]),
        getLineWidth: (f) => (selected != null && code(f as unknown as Feature) === selected ? 2.5 : 0.5),
        lineWidthUnits: 'pixels',
        getFillColor: (f) => {
          const ft = f as unknown as Feature
          const dim = focusState && ft.properties.state_name.toLowerCase() !== focusState.toLowerCase()
          const c = code(ft)
          if (c == null) return [200, 205, 202, dim ? 20 : 60]
          const v = value(byCode.get(c), year, metric, eff)
          if (v == null) return [200, 205, 202, dim ? 20 : 60]
          const t = sc.log ? Math.log(Math.max(v, 1e-6) / sc.domain[0]) / Math.log(sc.domain[1] / sc.domain[0]) : (v - sc.domain[0]) / (sc.domain[1] - sc.domain[0])
          return [...ramp(sc.ramp, t), dim ? 45 : 225]
        },
        transitions: { getFillColor: 500 },
        updateTriggers: { getFillColor: [year, metric, districts, focusState, eff], getLineColor: [selected], getLineWidth: [selected] },
        onClick: (info) => {
          const c = info.object ? code(info.object as unknown as Feature) : null
          if (c != null) onSelect(c)
        },
      }),
    [geo, byCode, year, metric, selected, districts, sc, onSelect, focusState, eff],
  )

  const tooltip = ({ object }: { object?: Feature }) => {
    if (!object) return null
    const p = object.properties
    const c = code(object)
    const head = `<b>${p.district_name}</b>, ${p.state_name}`
    if (c == null) return { html: `${head}<br/><span style="opacity:.8">No crop data — ${p.data_note ?? 'not assessed'}</span>`, className: 'tip' }
    const d = byCode.get(c)
    const y = d?.years?.[String(year)]
    const note = p.data_note ? `<br/><span style="opacity:.7">${p.data_note}</span>` : ''
    let line: string
    if (cfg.sq) {
      const a = value(d, year, metric, 'sq'), b = value(d, year, metric, 'plan')
      const dl = a != null && b != null ? b - a : null
      line = `${cfg.label}: do nothing <b>${fmtv(a, cfg)}</b> → plan <b>${fmtv(b, cfg)}</b>${dl != null ? ` <span style="opacity:.8">(${fmtv(dl, cfg.diff ?? cfg, true)})</span>` : ''}`
    } else if (metric === 'mropi') {
      const t = y?.mropi_tier ?? 'No outlay'
      line = `${cfg.label}: <b>${y?.mropi != null ? `${fmt(y.mropi, 1)}×` : t === 'Self-financing' ? 'no outlay needed' : 'no outlay'}</b> · ${t}` +
        (y?.outlay_cr ? `<br/>Outlay ₹${fmt(y.incentive_cr, 1)} cr (transfers ${fmt(y.outlay_cr.dbt, 1)} · kits ${fmt(y.outlay_cr.seed_kits, 1)} · price support ${fmt(y.outlay_cr.price_support, 1)})` : '')
    } else line = `${cfg.label}: <b>${fmtv(value(d, year, metric), cfg)}</b>`
    const score = y ? `<br/>Sustainability score: do nothing <b>${fmt(y.sustainability_score_status_quo, 0)}</b> → plan <b>${fmt(y.sustainability_score, 0)}</b>` +
      (y.income_vs_today_pct != null ? ` · farmer income ${fmt(y.income_vs_today_pct, 1)}% of today` : '') : ''
    return { html: `${head}${note}<br/>${line}${metric === 'sustainability_score' ? '' : score}${metric === 'sustainability_score' && y?.income_vs_today_pct != null ? `<br/>Farmer income ${fmt(y.income_vs_today_pct, 1)}% of today` : ''}<br/><span style="opacity:.6">Click for the decision trace</span>`, className: 'tip' }
  }

  const stops = [0, 0.25, 0.5, 0.75, 1].map((t) => `rgb(${ramp(sc.ramp, t).join(',')})`).join(',')
  const modeLabel = !cfg.sq ? 'change vs do nothing' : eff === 'sq' ? 'DO NOTHING' : eff === 'plan' ? 'AGRI SUS PLAN' : 'PLAN − DO NOTHING'
  return (
    <div className="map" ref={ref}>
      <DeckGL
        viewState={view as never}
        onViewStateChange={({ viewState }) => setView(viewState as Record<string, unknown>)}
        controller
        layers={layer ? [layer] : []}
        getTooltip={tooltip as never}
      >
        <Map mapStyle="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json" attributionControl={{ compact: true }} />
      </DeckGL>
      <button className="map-home" title="Fit India" onClick={() => setFitKey((k) => k + 1)}>⌂</button>
      <div className="legend">
        <div className="legend-title">{sc.label} · {year} · <b className={`mode-tag ${eff}`}>{modeLabel}</b></div>
        <div className="legend-bar" style={{ background: `linear-gradient(90deg, ${stops})` }} />
        <div className="legend-scale">{(sc.log ? [0.1, 1, 10, 100].filter((x) => x >= sc.domain[0] && x <= sc.domain[1]) : sc.domain).map((x) => <span key={x}>{x}{sc.unit}</span>)}</div>
        <div className="legend-help">{sc.help}</div>
        <div className="legend-nodata"><i /> No crop statistics (urban Delhi & Kolkata, island/enclave UTs)</div>
      </div>
      {kpi && (
        <div className="scorecard" aria-label="India sustainability index, do nothing vs plan">
          <div className="sc-title">India sustainability index (ASSI) · {kpi.year}</div>
          <button className={`sc-row ${eff === 'sq' ? 'on' : ''}`} onClick={() => setMode('sq')} title="Show the map if nothing changes">
            <span>Do nothing</span><span className="sc-bar"><i className="sq" style={{ width: `${kpi.assi_status_quo}%` }} /></span><b>{fmt(kpi.assi_status_quo, 1)}</b>
          </button>
          <button className={`sc-row ${eff === 'plan' ? 'on' : ''}`} onClick={() => setMode('plan')} title="Show the map under the optimised plan">
            <span>Agri Sus plan</span><span className="sc-bar"><i style={{ width: `${kpi.assi}%` }} /></span><b>{fmt(kpi.assi, 1)}</b>
          </button>
          <div className="sc-delta">
            <b className={kpi.assi >= kpi.assi_status_quo ? 'good' : 'bad'}>{kpi.assi >= kpi.assi_status_quo ? '▲ +' : '▼ '}{fmt(kpi.assi - kpi.assi_status_quo, 1)} pts</b> · {improved} of {districts.length} districts improve
            {cfg.sq && <button className="link" onClick={() => setMode('diff')}>show where</button>}
          </div>
        </div>
      )}
    </div>
  )
}
