import { useState } from 'react'
import type { StateRow } from '../api'
import { fmt } from '../api'

type SortKey = 'water' | 'gain' | 'score' | 'paddy' | 'incentive'
const SORTS: [SortKey, string, (r: StateRow) => number][] = [
  ['water', 'Water saved', (r) => r.water_saved_mcm],
  ['gain', 'Score gain', (r) => r.score_gain],
  ['score', 'Score (plan)', (r) => r.score],
  ['paddy', 'Paddy shifted', (r) => -r.rice_area_change_ha],
  ['incentive', 'Incentive', (r) => r.incentive_cr],
]

export default function StateBoard({ rows, focus, onFocus, year }: { rows: StateRow[]; focus: string | null; onFocus: (s: string | null) => void; year?: number }) {
  const [sort, setSort] = useState<SortKey>('water')
  const key = SORTS.find((s) => s[0] === sort)![2]
  const list = rows.filter((r) => r.districts >= 1).slice().sort((a, b) => key(b) - key(a))
  const max = Math.max(...list.map((r) => r.score_gain), 1)
  return (
    <div className="board">
      <div className="board-head">
        <span>States · {year ?? 2030} <span className="muted">— district sustainability score, do nothing → plan. Click a state to zoom the map.</span></span>
        <span className="board-sort" role="group" aria-label="Sort states by">
          {SORTS.map(([id, label]) => <button key={id} className={sort === id ? 'active' : ''} onClick={() => setSort(id)}>{label}</button>)}
          {focus && <button className="link" onClick={() => onFocus(null)}>Show all India</button>}
        </span>
      </div>
      <div className="board-table">
        <div className="board-row hdr">
          <span>#</span><span>State</span><span>Score</span><span>Gain</span><span>Water saved</span><span>Paddy → pulses/oilseeds</span><span>Incentive</span>
        </div>
        {list.map((r, i) => (
          <button key={r.state_name} className={`board-row ${focus === r.state_name ? 'active' : ''}`} onClick={() => onFocus(focus === r.state_name ? null : r.state_name)}>
            <span className="muted">{i + 1}</span>
            <span className="st-name">{r.state_name}<small className="muted"> · {r.districts}</small></span>
            <span><span className="muted">{fmt(r.score_status_quo, 0)} →</span> <b>{fmt(r.score, 0)}</b></span>
            <span className="gainbar"><i style={{ width: `${Math.max(0, (r.score_gain / max) * 100)}%` }} /><b>{r.score_gain >= 0 ? '+' : ''}{fmt(r.score_gain, 1)}</b></span>
            <span>{fmt(r.water_saved_mcm / 1000, 2)} BCM</span>
            <span>{r.rice_area_change_ha > 0 ? '+' : ''}{fmt(r.rice_area_change_ha / 1000, 0)}k / {r.legume_area_change_ha >= 0 ? '+' : ''}{fmt(r.legume_area_change_ha / 1000, 0)}k ha</span>
            <span>₹{fmt(r.incentive_cr, 0)} cr</span>
          </button>
        ))}
      </div>
    </div>
  )
}
