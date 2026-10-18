import { useEffect, useState } from 'react'
import { api, fmt } from '../api'
import type { PolicyMeta, PolicyRef, SimResult, StoreStatus } from '../api'

const date = (iso?: string | null) => (iso ? new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '')
const crore = (v?: number | null) => (v == null ? '–' : `₹${fmt(v, 0)} cr`)

/** A descriptive default name for the plan on screen. */
export function suggestName(r: SimResult, scenarioLabel?: string): string {
  const p = r.params as Record<string, unknown>
  const bits = [(scenarioLabel ?? String(p.scenario)).split(' (')[0]]
  const imp = Number(p.import_target_pct ?? 25)
  if (p.budget_mode === 'min_budget') bits.push(`least cost, ${imp}% import cut`)
  else bits.push(`₹${fmt(Number(p.incentive_budget_cr ?? 0), 0)} cr/yr${imp !== 25 ? `, ${imp}% import cut` : ''}`)
  for (const c of ((p.trade_cuts as { country: string; product: string; pct: number }[]) ?? []).slice(0, 2)) bits.push(`−${c.pct}% ${c.product} from ${c.country}`)
  const ys = r.kpis.map((k) => k.year)
  bits.push(`${ys[0]}–${ys[ys.length - 1]}`)
  return bits.join(' · ').slice(0, 80)
}

export function SavePolicyDialog({ result, scenarioLabel, onClose, onSaved }: { result: SimResult; scenarioLabel?: string; onClose: () => void; onSaved: (m: PolicyMeta) => void }) {
  const [name, setName] = useState(() => suggestName(result, scenarioLabel))
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const k = result.kpis[result.kpis.length - 1]
  const save = async () => {
    if (!name.trim()) return
    setBusy(true)
    setErr(null)
    try {
      onSaved(await api.policies.save(result.run_id, name.trim(), note.trim()))
    } catch (e) {
      setErr((e as Error).message)
      setBusy(false)
    }
  }
  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body save-policy" role="dialog" aria-label="Save policy" onClick={(e) => e.stopPropagation()}>
        <h2>💾 Save as policy</h2>
        <p className="muted small">Saves the levers and every result on screen — national KPIs, all {result.districts.length} district plans, budget path, trade and stress tests — to the shared policy library. Opening it later re-renders this exact plan without re-running the optimizer.</p>
        <label className="field">Name<input type="text" value={name} maxLength={80} autoFocus onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') save() }} /></label>
        <label className="field"><span>Note <span className="muted">(optional)</span></span><textarea value={note} maxLength={500} rows={3} onChange={(e) => setNote(e.target.value)} placeholder="Why this policy, who asked for it…" /></label>
        <div className="muted small">{k.year}: sustainability index {fmt(k.assi)} (doing nothing {fmt(k.assi_status_quo)}) · import saving ${fmt(k.import_saving_usd_bn, 2)} bn · water saved {fmt(k.water_saved_bcm)} BCM</div>
        {err && <div className="error">{err}</div>}
        <div className="row">
          <button className="primary" onClick={save} disabled={busy || !name.trim()}>{busy ? 'Saving…' : 'Save policy'}</button>
          <button className="ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

function storeText(s?: StoreStatus) {
  if (!s) return ''
  return s.shared ? `Stored in ${s.documents} + ${s.snapshots}: shared by everyone and kept across restarts.` : `Stored on ${s.documents} of this server only (deploy with Firestore + Cloud Storage to share and keep it).`
}

type ModalProps = { currentRunId?: string; version: number; onClose: () => void; onOpen: (runId: string) => void; onAsk: (q: string) => void }

export function PoliciesModal({ currentRunId, version, onClose, onOpen, onAsk }: ModalProps) {
  const [rows, setRows] = useState<PolicyMeta[] | null>(null)
  const [store, setStore] = useState<StoreStatus>()
  const [tab, setTab] = useState<'saved' | 'deleted'>('saved')
  const [err, setErr] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const load = () => api.policies.list().then((r) => { setRows(r.policies); setStore(r.store) }).catch((e) => setErr((e as Error).message))
  useEffect(() => { load() }, [version])

  const act = async (id: string, f: () => Promise<unknown>) => {
    setBusy(id)
    setErr(null)
    try { await f(); await load() } catch (e) { setErr((e as Error).message) }
    setBusy(null)
  }
  const shown = (rows ?? []).filter((r) => (tab === 'deleted') === !!r.deleted)
  const nDeleted = (rows ?? []).filter((r) => r.deleted).length
  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body policies" role="dialog" aria-label="Saved policies" onClick={(e) => e.stopPropagation()}>
        <div className="pol-head">
          <h2>📁 Policies</h2>
          <div className="seg" role="tablist">
            <button role="tab" aria-selected={tab === 'saved'} className={tab === 'saved' ? 'active' : ''} onClick={() => setTab('saved')}>Saved ({(rows ?? []).length - nDeleted})</button>
            <button role="tab" aria-selected={tab === 'deleted'} className={tab === 'deleted' ? 'active' : ''} onClick={() => setTab('deleted')}>Deleted ({nDeleted})</button>
          </div>
          <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <p className="muted small">Each policy is a snapshot of one optimizer run: its levers and every computed result. Opening one renders it instantly from the snapshot — nothing is re-solved. Save the plan on screen with <b>💾 Save as policy</b>, or ask the co-pilot to create one.</p>
        {err && <div className="error">{err}</div>}
        {rows == null && !err && <div className="muted">Loading…</div>}
        {rows != null && shown.length === 0 && <div className="muted pol-empty">{tab === 'saved' ? 'No saved policies yet.' : 'Nothing in the bin.'}</div>}
        <ul className="pol-list">
          {shown.map((p) => {
            const s = p.summary
            const on = currentRunId === `p-${p.id}`
            return (
              <li key={p.id} className={`pol ${on ? 'on' : ''}`}>
                <div className="pol-main">
                  {editing === p.id ? (
                    <form className="pol-rename" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) act(p.id, () => api.policies.update(p.id, { name: draft.trim() })).then(() => setEditing(null)) }}>
                      <input type="text" value={draft} maxLength={80} autoFocus onChange={(e) => setDraft(e.target.value)} aria-label="Policy name" />
                      <button className="primary small-btn" disabled={!draft.trim()}>Save</button>
                      <button type="button" className="ghost small-btn" onClick={() => setEditing(null)}>Cancel</button>
                    </form>
                  ) : (
                    <div className="pol-name"><b>{p.name}</b>{p.source === 'copilot' && <span className="pill small">✦ co-pilot</span>}{on && <span className="pill live small">on screen</span>}</div>
                  )}
                  <div className="muted small">{p.scenario_label} · saved {date(p.created_at)}{p.deleted ? ` · deleted ${date(p.deleted_at)}` : ''}</div>
                  {p.note && <div className="small pol-note">{p.note}</div>}
                  {s && (
                    <div className="pol-kpis small">
                      <span title="Agri Sus Sustainability Index in the final plan year (doing nothing in brackets)">Index {fmt(s.assi)} <span className="muted">({fmt(s.assi_status_quo)})</span></span>
                      <span title="Pulses + edible-oil import bill saved in the final plan year">Imports −${fmt(s.import_saving_usd_bn, 2)} bn</span>
                      <span title="Groundwater saved in the final plan year">Water {fmt(s.water_saved_bcm)} BCM</span>
                      <span title={`Total public outlay over ${s.first_year}–${s.end_year}`}>Outlay {crore(s.outlay_total_cr)} <span className="muted">{s.first_year}–{String(s.end_year).slice(2)}</span></span>
                      {s.budget_mode === 'min_budget' && <span className={s.target_met ? 'good' : 'warn'}>{s.target_met ? 'target met' : 'target missed'}</span>}
                    </div>
                  )}
                </div>
                <div className="pol-actions">
                  {!p.deleted ? (
                    <>
                      <button className="primary small-btn" onClick={() => onOpen(`p-${p.id}`)} disabled={busy === p.id}>Open</button>
                      <button className="ghost small-btn" onClick={() => onAsk(`Tell me about the saved policy "${p.name}" and how it compares with the plan on screen.`)} title="Ask the co-pilot about this policy">✦ Ask</button>
                      <button className="ghost small-btn" onClick={() => { setEditing(p.id); setDraft(p.name) }} disabled={busy === p.id}>Rename</button>
                      <button className="ghost small-btn danger" onClick={() => act(p.id, () => api.policies.remove(p.id))} disabled={busy === p.id} title="Move to Deleted (can be restored)">Delete</button>
                    </>
                  ) : (
                    <>
                      <button className="ghost small-btn" onClick={() => act(p.id, () => api.policies.update(p.id, { deleted: false }))} disabled={busy === p.id}>Restore</button>
                      <button className="ghost small-btn danger" disabled={busy === p.id}
                        onClick={() => { if (window.confirm(`Permanently delete “${p.name}” and its snapshot for everyone?`)) act(p.id, () => api.policies.remove(p.id, true)) }}>Delete forever</button>
                    </>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
        <p className="muted small">{storeText(store)} Policies and chats are shared by everyone using this site (no login).</p>
      </div>
    </div>
  )
}

export function PolicyBanner({ policy, onLibrary, onAsk }: { policy: PolicyRef; onLibrary: () => void; onAsk: () => void }) {
  return (
    <div className={`policy-banner ${policy.deleted ? 'deleted' : ''}`} role="status">
      <span>📁 <b>{policy.name}</b> <span className="muted">· {policy.scenario_label} · saved {date(policy.created_at)}{policy.source === 'copilot' ? ' by the co-pilot' : ''} · rendered from its snapshot, no re-run{policy.deleted ? ' · in Deleted' : ''}</span></span>
      <span className="pb-actions">
        <button className="link" onClick={onAsk}>✦ Ask about it</button>
        <button className="link" onClick={onLibrary}>All policies</button>
      </span>
    </div>
  )
}
