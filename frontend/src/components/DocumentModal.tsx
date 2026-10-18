import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import type { DocEvent, DocStep, PolicyDocument } from '../api'

export type DocTarget = { runId: string; state: string | null; language?: string; name?: string | null }
type Props = DocTarget & {
  states: string[]
  languages: Record<string, string>
  geminiLive: boolean
  onClose: () => void
  onAsk?: (q: string) => void
}

const STEPS: [string, string][] = [
  ['facts', 'Reading the plan from the optimizer'],
  ['schemes', 'Searching existing central and state schemes'],
  ['duties', 'Looking up today’s import duties'],
  ['imports', 'Testing whether more production cuts imports'],
  ['draft', 'Drafting the document'],
  ['save', 'Saving'],
]
const ICON: Record<DocStep['status'], string> = { running: '◌', done: '✓', failed: '⚠', skipped: '–' }
const VERDICT: Record<string, string> = { likely: 'good', partly: 'warn', unlikely: 'bad' }
const MAX_INSTRUCTIONS = 2000
const when = (iso: string) => new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const MD = ({ children }: { children: string }) => (
  <Markdown remarkPlugins={[remarkGfm]} disallowedElements={['img']}
    components={{ a: ({ href, children: c }) => <a href={href} target="_blank" rel="noopener noreferrer">{c}</a> }}>{children}</Markdown>
)

/** The policy document of a plan or saved policy: written once by the document agent, saved, and reopened from the
 * store; a new version is written only when regenerated (optionally with instructions). */
export default function DocumentModal({ runId, state, language, name, states, languages, geminiLive, onClose, onAsk }: Props) {
  const [scope, setScope] = useState<string | null>(state)
  const [lang, setLang] = useState(language ?? 'en')
  const [doc, setDoc] = useState<PolicyDocument | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [writing, setWriting] = useState(false)
  const [steps, setSteps] = useState<Record<string, DocStep>>({})
  const [started, setStarted] = useState(0)
  const [now, setNow] = useState(Date.now())
  const [revising, setRevising] = useState(false)
  const [instr, setInstr] = useState('')
  const [refresh, setRefresh] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const article = useRef<HTMLElement>(null)

  useEffect(() => { // the saved document for this plan, scope and language (never writes one)
    let current = true
    setLoading(true)
    setErr(null)
    setNotice(null)
    setDoc(null)
    setRevising(false)
    api.documents.get(runId, scope, lang)
      .then((r) => {
        if (!current) return
        setDoc(r.document)
        // being written right now (another window, or the co-pilot): follow it live instead of waiting
        if (r.job) run((signal) => api.documents.follow(r.job!.id, signal), r.document?.version ?? null)
      })
      .catch((e) => current && setErr((e as Error).message))
      .finally(() => current && setLoading(false))
    return () => { current = false; abort.current?.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, scope, lang])

  useEffect(() => () => abort.current?.abort(), [])
  useEffect(() => {
    if (!writing) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [writing])

  /** The writing job is gone from the server (restart, or another instance): wait for the saved version instead. */
  const savedVersion = async (before: number | null, ctrl: AbortController) => {
    for (let i = 0; i < 60 && !ctrl.signal.aborted; i++) {
      const r = await api.documents.get(runId, scope, lang).catch(() => null)
      if (ctrl.signal.aborted) return
      if (r?.document && r.document.version !== before) { setDoc(r.document); return }
      await new Promise((ok) => window.setTimeout(ok, 5000))
    }
    if (!ctrl.signal.aborted) setErr('The document is not ready yet. Close this dialog and open it again in a few minutes.')
  }

  /** Show a write's progress (a new one, or one already running) until the document arrives. */
  const run = async (open: (signal: AbortSignal) => AsyncGenerator<DocEvent>, before: number | null) => {
    abort.current?.abort()
    const ctrl = new AbortController()
    abort.current = ctrl
    setWriting(true)
    setErr(null)
    setNotice(null)
    setSteps({})
    setStarted(Date.now())
    setNow(Date.now())
    try {
      for await (const ev of open(ctrl.signal)) {
        if (ctrl.signal.aborted) return
        if (ev.type === 'step') setSteps((cur) => ({ ...cur, [ev.id]: ev }))
        else if (ev.type === 'document') {
          setDoc(ev.document)
          setRevising(false)
          setInstr('')
          setRefresh(false)
          article.current?.parentElement?.scrollTo({ top: 0 })
        } else if (ev.type === 'notice') setNotice(ev.text)
        else if (ev.type === 'error') setErr(ev.message)
        else if (ev.type === 'lost') await savedVersion(before, ctrl)
      }
    } catch (e) {
      if (!ctrl.signal.aborted) setErr((e as Error).message)
    } finally {
      if (abort.current === ctrl) { setWriting(false); abort.current = null }
    }
  }

  const write = (regenerate: boolean) => {
    const body = { run_id: runId, state: scope, language: lang, regenerate, instructions: regenerate ? instr.trim() : '', refresh_research: regenerate && refresh }
    return run((signal) => api.documents.generate(body, signal), doc?.version ?? null)
  }

  const download = () => {
    if (!doc) return
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([doc.markdown], { type: 'text/markdown' }))
    a.download = `agrisus-policy-document-${doc.scope.replace(/\W+/g, '_')}-${doc.language}-v${doc.version}.md`
    a.click()
    URL.revokeObjectURL(a.href)
  }
  const jump = (i: number) => article.current?.querySelectorAll('h2')[i]?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  const who = name ? `the saved policy “${name}”` : 'the plan on screen'
  const verdicts = doc?.import_check?.groups ?? []
  const elapsed = Math.max(0, Math.round((now - started) / 1000))

  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body docm" role="dialog" aria-label="Policy document" onClick={(e) => e.stopPropagation()}>
        <button className="ghost doc-close-m" onClick={onClose} aria-label="Close">✕</button>
        <div className="doc-head">
          <div className="doc-title">
            <h2>📄 Policy document</h2>
            <span className="muted small">{name ? `Saved policy “${name}”` : 'Plan on screen'} · {scope ?? 'India'}</span>
          </div>
          <div className="row">
            <select value={scope ?? ''} onChange={(e) => setScope(e.target.value || null)} aria-label="Scope" disabled={writing}>
              <option value="">India</option>
              {states.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Document language" disabled={writing}>
              {Object.entries(languages).map(([id, l]) => <option key={id} value={id}>{l}</option>)}
            </select>
            <button className={`ghost ${revising ? 'active' : ''}`} onClick={() => setRevising(!revising)} disabled={!doc || writing} title="Write a new version">↻ Regenerate…</button>
            <button className="ghost" onClick={() => window.print()} disabled={!doc || writing}>Print / PDF</button>
            <button className="ghost" onClick={download} disabled={!doc || writing}>Download .md</button>
            <button className="ghost doc-close" onClick={onClose} aria-label="Close">✕</button>
          </div>
        </div>

        {doc && (
          <div className="doc-meta small">
            <span className="vbadge">v{doc.version}</span>
            <span>{doc.generator === 'gemini' ? `Written by Gemini (${doc.model})` : 'Deterministic template'} · {doc.words.toLocaleString('en-IN')} words · saved {when(doc.updated_at)}</span>
            {verdicts.map((g) => (
              <span key={g.group} className={`vchip ${VERDICT[g.verdict] ?? ''}`} title={g.verdict_reason}>{g.label}: imports cut {g.verdict}</span>
            ))}
            {doc.history.length > 1 && <button className="link" onClick={() => setShowHistory(!showHistory)} aria-expanded={showHistory}>{showHistory ? 'Hide' : 'Show'} version history ({doc.history.length})</button>}
            {onAsk && <button className="link" onClick={() => { onAsk(`Read the policy document of ${who} (${doc.scope}) and summarise its import-displacement findings (section G) and the existing schemes it converges with (section H).`); onClose() }}>Ask the co-pilot about it</button>}
          </div>
        )}
        {doc && showHistory && (
          <ol className="doc-history small" reversed>
            {[...doc.history].reverse().map((h) => (
              <li key={h.version}><b>v{h.version}</b> · {when(h.created_at)} · {h.generator === 'gemini' ? `Gemini${h.model ? ` (${h.model})` : ''}` : 'template'}{h.source === 'copilot' ? ' · by the co-pilot' : ''}{h.degraded ? ' · fallback' : ''}
                {h.instructions && <div className="muted">“{h.instructions.slice(0, 300)}{h.instructions.length > 300 ? '…' : ''}”</div>}</li>
            ))}
          </ol>
        )}
        {doc?.degraded && doc.degraded_reason && !doc.markdown.includes(doc.degraded_reason) &&
          <div className="doc-banner small">⚠ {doc.degraded_reason}</div>}

        {revising && doc && !writing && (
          <div className="doc-revise">
            <label htmlFor="doc-instr"><b>What should change in version {doc.version + 1}?</b> <span className="muted small">optional · {instr.length}/{MAX_INSTRUCTIONS}</span></label>
            <textarea id="doc-instr" rows={4} maxLength={MAX_INSTRUCTIONS} value={instr} onChange={(e) => setInstr(e.target.value)}
              placeholder="e.g. Put rice-fallow pulses in Odisha and Chhattisgarh first; make the funding section more conservative; add a clause on yellow-pea import windows…" />
            <label className="check small"><input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} /> Search again for existing schemes and today’s import duties (otherwise the saved research from v{doc.version} is reused)</label>
            {!geminiLive && <div className="muted small">Gemini is offline here: the template is rebuilt and your instructions are recorded in the version history.</div>}
            <div className="row">
              <button className="primary" onClick={() => write(true)}>Write version {doc.version + 1}</button>
              <button className="ghost" onClick={() => setRevising(false)}>Cancel</button>
            </div>
          </div>
        )}

        <div className="doc-main">
          {doc && !writing && (
            <nav className="doc-toc small" aria-label="Sections">
              {doc.sections.map((s, i) => (
                <button key={s.id} className="link" onClick={() => jump(i)} title={`${s.words} words${s.from_template && doc.generator === 'gemini' ? ' · from the template' : ''}`}>
                  {s.id.length === 1 ? `${s.id}. ` : ''}{s.title}
                </button>
              ))}
            </nav>
          )}
          <div className="doc-scroll">
            {err && <div className="error">{err}</div>}
            {notice && <div className="doc-banner small">ℹ {notice}</div>}
            {loading && <div className="typing">Opening the saved document…</div>}
            {!loading && !doc && !writing && !err && (
              <div className="doc-empty">
                <p><b>No policy document has been written for {who} ({scope ?? 'India'}, {languages[lang] ?? lang}) yet.</b></p>
                <p className="muted">The document agent writes it once and saves it: {geminiLive ? 'Gemini drafts' : 'it assembles'} sections A–H — context, objectives, sustainability dimensions, instruments, funding, monitoring — from this plan’s optimizer results, then adds <b>G. Will higher domestic production cut imports?</b> (each risk answered by a policy clause) and <b>H. Convergence with existing schemes</b> ({geminiLive ? 'Google Search grounded' : 'reference list offline'}). Opening it again shows the saved version; nothing is rewritten unless you regenerate it.</p>
                <button className="primary" onClick={() => write(false)}>✦ Write the policy document</button>
              </div>
            )}
            {writing && (
              <div className="doc-progress" role="status" aria-live="polite">
                <p><b>Writing the policy document…</b> <span className="muted small">{elapsed}s · {geminiLive ? 'usually 1–3 minutes' : 'a few seconds'}</span></p>
                {geminiLive && <p className="muted small">You can close this dialog: the document keeps being written on the server and is saved when ready.</p>}
                <ul>
                  {STEPS.map(([id, l]) => {
                    const s = steps[id]
                    return <li key={id} className={s?.status ?? 'pending'}><span className="ico">{s ? ICON[s.status] : '·'}</span>{s?.label ?? l}{s?.detail ? <span className="muted"> — {s.detail}</span> : null}</li>
                  })}
                </ul>
              </div>
            )}
            {doc && !writing && (
              <article className="doc-article" ref={article}>
                <MD>{doc.markdown}</MD>
              </article>
            )}
          </div>
        </div>
      </div>
      {/* the printed copy: a plain block under <body>, so it prints once and flows across pages (see index.css) */}
      {doc && createPortal(<article id="print-root" className="doc-article"><MD>{doc.markdown}</MD></article>, document.body)}
    </div>
  )
}
