import { useCallback, useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { api } from '../api'
import type { ChatSummary, Meta } from '../api'

type Step = { kind: 'call' | 'result' | 'error'; name: string; text: string }
type PolicyLink = { id: string; name: string; run_id: string; created?: boolean }
type Msg = { role: 'user' | 'assistant'; content: string; steps?: Step[]; citations?: { title: string; uri: string }[]; model?: string; policies?: PolicyLink[] }

const SUGGESTIONS = [
  'What happens to India’s pulse imports and Punjab groundwater if a strong El Niño hits in 2028?',
  'Why does the plan cut paddy in Sangrur, and what do farmers get in return?',
  'Create a policy that cuts the pulse and edible-oil import bill 40% at least cost over 6 years, and save it.',
  'Which 10 districts have the worst groundwater stage and how much paddy do they grow? (use BigQuery)',
  'What is the latest IMD / ENSO outlook and edible-oil import duty? Re-run the plan with it.',
  'Which saved policies do we have, and which one saves the most water?',
  'महाराष्ट्र में भूजल बचाने के लिए किसान कौन सी फसल उगाएँ?',
]

// what each co-pilot tool is doing, in plain words: [while running, when done]
const TOOLS: Record<string, [string, string]> = {
  run_simulation: ['Running the optimizer for this what-if', 'Optimizer what-if'],
  solve_min_budget: ['Finding the least-cost budget', 'Least-cost budget'],
  get_national_scorecard: ['Reading the national scorecard', 'National scorecard'],
  get_sustainability_outlook: ['Checking the sustainability outlook and stress tests', 'Sustainability outlook'],
  get_fiscal_ledger: ['Reading the fiscal ledger', 'Fiscal ledger'],
  explain_district: ['Explaining the district’s plan', 'District plan'],
  get_district_profile: ['Looking up district data', 'District data'],
  get_district_allocation_audit: ['Auditing next-crop choices', 'Next-crop audit'],
  predict_yield: ['Predicting yields', 'Yield prediction'],
  query_bigquery: ['Querying BigQuery', 'BigQuery query'],
  get_schema: ['Reading the BigQuery tables', 'BigQuery tables'],
  web_grounded_search: ['Searching Google for current facts', 'Google Search (grounded)'],
  gemini_outlook_forecast: ['Building a grounded climate & price outlook', 'Gemini outlook'],
  decompose_trade_target: ['Mapping the trade shock to crops and districts', 'Trade shock'],
  get_trade_dependencies: ['Looking up import suppliers', 'Import suppliers'],
  list_policies: ['Opening the policy library', 'Policy library'],
  get_policy: ['Reading a saved policy', 'Saved policy'],
  create_policy: ['Saving the policy', 'Policy saved'],
}
const label = (name: string, i: 0 | 1) => TOOLS[name]?.[i] ?? (i ? 'Analysis step' : 'Working')

const SPEECH: Record<string, string> = { en: 'en-IN', hi: 'hi-IN', ta: 'ta-IN', te: 'te-IN', kn: 'kn-IN', mr: 'mr-IN', bn: 'bn-IN', pa: 'pa-IN', gu: 'gu-IN', ml: 'ml-IN', or: 'or-IN' }
const safeUrl = (u: string) => (/^https?:\/\//i.test(u) ? u : undefined)
const CHAT_KEY = 'agrisus_chat'
const ago = (iso: string) => {
  const s = (Date.now() - Date.parse(iso)) / 1000
  if (!Number.isFinite(s)) return ''
  if (s < 90) return 'just now'
  if (s < 5400) return `${Math.round(s / 60)} min ago`
  if (s < 129600) return `${Math.round(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

type SR = { lang: string; interimResults: boolean; onresult: (e: { results: { 0: { transcript: string } }[] }) => void; onend: () => void; start: () => void; stop: () => void }
const SpeechRec = (window as unknown as { SpeechRecognition?: new () => SR; webkitSpeechRecognition?: new () => SR }).SpeechRecognition ?? (window as unknown as { webkitSpeechRecognition?: new () => SR }).webkitSpeechRecognition

function speak(text: string, lang: string) {
  if (!('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(text.replace(/[#*_`>|]/g, '').slice(0, 1200))
  u.lang = SPEECH[lang] ?? 'en-IN'
  window.speechSynthesis.speak(u)
}

/** The steps behind an answer, in plain words and collapsed by default (no tool names or raw arguments). */
function HowBuilt({ steps }: { steps: Step[] }) {
  const done = steps.filter((s) => s.kind !== 'call')
  if (!done.length) return null
  return (
    <details className="how">
      <summary>How this answer was built · {done.length} {done.length === 1 ? 'step' : 'steps'}</summary>
      <ul>
        {done.map((s, k) => (
          <li key={k} className={s.kind}>{s.kind === 'error' ? '⚠ ' : '✓ '}{label(s.name, 1)}{s.kind === 'error' ? ' — not available, answered without it' : ''}</li>
        ))}
      </ul>
    </details>
  )
}

type Props = {
  meta: Meta | null
  runId?: string
  onSimulation: (runId: string) => void
  onOpenPolicy: (runId: string) => void
  onPoliciesChanged: () => void
  pending: string | null
  clearPending: () => void
}

export default function Copilot({ meta, runId, onSimulation, onOpenPolicy, onPoliciesChanged, pending, clearPending }: Props) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [lang, setLang] = useState('en')
  const [listening, setListening] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<ChatSummary[] | null>(null)
  const [showSessions, setShowSessions] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const rec = useRef<SR | null>(null)
  const abort = useRef<AbortController | null>(null)

  const mic = () => {
    if (!SpeechRec) return
    if (listening) { rec.current?.stop(); return }
    const r = new SpeechRec()
    r.lang = SPEECH[lang] ?? 'en-IN'
    r.interimResults = false
    r.onresult = (e) => setInput((cur) => (cur ? cur + ' ' : '') + e.results[0][0].transcript)
    r.onend = () => setListening(false)
    rec.current = r
    setListening(true)
    r.start()
  }
  const body = useRef<HTMLDivElement>(null)
  // scroll only the chat log — scrollIntoView() would also scroll the page when the co-pilot sits below the map (narrow screens)
  useEffect(() => { const el = body.current; if (el && msgs.length) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }) }, [msgs])

  const refreshSessions = useCallback(() => {
    api.chats.list().then((r) => setSessions(r.chats)).catch(() => setSessions((cur) => cur ?? []))
  }, [])

  const openSession = useCallback(async (id: string) => {
    abort.current?.abort()
    try {
      const s = await api.chats.get(id)
      setMsgs(s.messages.map((m) => ({
        role: m.role, content: m.content, citations: m.citations, model: m.model ?? undefined,
        policies: (m.policies ?? []).map((p) => ({ ...p, run_id: `p-${p.id}` })),
      })))
      setSessionId(s.id)
      if (s.language && meta?.languages?.[s.language]) setLang(s.language)
      localStorage.setItem(CHAT_KEY, s.id)
      setShowSessions(false)
    } catch {
      localStorage.removeItem(CHAT_KEY)
      setNote('That chat is no longer available.')
    }
  }, [meta?.languages])

  // reopen this browser's last conversation (sessions themselves are shared by everyone using the site)
  useEffect(() => {
    const id = localStorage.getItem(CHAT_KEY)
    if (id && /^[a-z0-9]{12}$/.test(id)) openSession(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const resetChat = () => {
    abort.current?.abort()
    setMsgs([])
    setSessionId(null)
    setBusy(false)
    localStorage.removeItem(CHAT_KEY)
  }
  const newChat = () => {
    resetChat()
    setShowSessions(false)
  }

  const removeSession = async (id: string, title: string) => {
    if (!window.confirm(`Delete the chat “${title.slice(0, 60)}” for everyone?`)) return
    try {
      await api.chats.remove(id)
      setSessions((cur) => (cur ?? []).filter((s) => s.id !== id))
      if (id === sessionId) resetChat()  // keep the list open: several chats may be deleted in a row
    } catch (e) {
      setNote(`Could not delete the chat: ${(e as Error).message}`)
    }
  }

  const send = async (text: string) => {
    if (!text.trim() || busy) return
    const history: Msg[] = [...msgs, { role: 'user', content: text }]
    setMsgs([...history, { role: 'assistant', content: '', steps: [] }])
    setInput('')
    setNote(null)
    setBusy(true)
    const ctrl = new AbortController()
    abort.current = ctrl
    const upd = (f: (m: Msg) => Msg) => setMsgs((cur) => (ctrl.signal.aborted ? cur : [...cur.slice(0, -1), f(cur[cur.length - 1])]))
    try {
      for await (const ev of api.chat(history.slice(-12).map((m) => ({ role: m.role, content: m.content })), lang, sessionId, runId, ctrl.signal)) {
        if (ctrl.signal.aborted) break
        if (ev.type === 'text') upd((m) => ({ ...m, content: m.content + ev.text }))
        else if (ev.type === 'model') upd((m) => ({ ...m, model: `${ev.model} · ${ev.backend}` }))
        else if (ev.type === 'tool_call') upd((m) => ({ ...m, steps: [...(m.steps ?? []), { kind: 'call', name: ev.name, text: '' }] }))
        else if (ev.type === 'tool_result') upd((m) => ({ ...m, steps: [...(m.steps ?? []), { kind: ev.ok === false ? 'error' : 'result', name: ev.name, text: ev.summary }] }))
        else if (ev.type === 'citations') upd((m) => ({ ...m, citations: ev.citations }))
        else if (ev.type === 'simulation') onSimulation(ev.run_id)
        else if (ev.type === 'policy') {
          upd((m) => ({ ...m, policies: [...(m.policies ?? []).filter((p) => p.id !== ev.id), { id: ev.id, name: ev.name, run_id: ev.run_id, created: ev.created }] }))
          if (ev.created) onPoliciesChanged()
        } else if (ev.type === 'session') {
          setSessionId(ev.id)
          localStorage.setItem(CHAT_KEY, ev.id)
          if (ev.saved && sessions) refreshSessions()
        } else if (ev.type === 'error') upd((m) => ({ ...m, content: `${m.content}${m.content ? '\n\n' : ''}_⚠ ${ev.text}_` }))
      }
    } catch (e) {
      if (!ctrl.signal.aborted) upd((m) => ({ ...m, content: m.content + `\n\n_Error: ${(e as Error).message}_` }))
    }
    if (abort.current === ctrl) { setBusy(false); abort.current = null }
  }

  useEffect(() => {
    if (pending) {
      send(pending)
      clearPending()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending])

  const toggleSessions = () => {
    if (!showSessions) refreshSessions()
    setShowSessions(!showSessions)
  }
  const last = msgs[msgs.length - 1]
  const live = busy && last?.role === 'assistant' ? [...(last.steps ?? [])].reverse().find((s) => s.kind === 'call') : undefined
  const liveDone = live && last?.steps ? last.steps.filter((s) => s.kind !== 'call').length >= last.steps.filter((s) => s.kind === 'call').length : true

  return (
    <aside className="panel copilot">
      <div className="cp-head">
        <h3>✦ Agri Sus Co-pilot</h3>
        <select className="lang" value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Reply language">
          {Object.entries(meta?.languages ?? { en: 'English' }).map(([id, l]) => <option key={id} value={id}>{l}</option>)}
        </select>
        <span className={`pill ${meta?.gemini.enabled ? 'live' : ''}`}>{meta?.gemini.enabled ? `${meta.gemini.agent_model} · ${meta.gemini.backend}` : 'offline analyst'}</span>
        <div className="cp-actions">
          <button className="ghost small-btn" onClick={newChat} title="Start a new conversation">＋ New chat</button>
          <button className={`ghost small-btn ${showSessions ? 'active' : ''}`} onClick={toggleSessions} aria-expanded={showSessions} title="Saved conversations">💬 Chats</button>
        </div>
        {showSessions && (
          <div className="cp-sessions" role="dialog" aria-label="Saved chats">
            <div className="cp-sessions-head"><b>Saved chats</b><span className="muted small">shared by everyone using this site</span></div>
            {sessions == null && <div className="muted small">Loading…</div>}
            {sessions?.length === 0 && <div className="muted small">No saved chats yet — every conversation is saved automatically.</div>}
            <ul>
              {(sessions ?? []).map((s) => (
                <li key={s.id} className={s.id === sessionId ? 'active' : ''}>
                  <button className="cp-session" onClick={() => openSession(s.id)} title={s.title}>
                    <span className="t">{s.title || 'Untitled chat'}</span>
                    <span className="muted small">{Math.ceil(s.n_messages / 2)} {s.n_messages > 2 ? 'questions' : 'question'} · {ago(s.updated_at)}</span>
                  </button>
                  <button className="link del" onClick={() => removeSession(s.id, s.title)} aria-label={`Delete chat ${s.title}`} title="Delete this chat">🗑</button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
      <div className="cp-body" ref={body}>
        {note && <div className="cp-note small">{note}</div>}
        {msgs.length === 0 && (
          <div className="cp-empty">
            <p className="muted">A multilingual Gemini agent (type or 🎤 speak) that runs the optimizer, queries BigQuery, predicts yields, searches the web (grounded, with citations) and saves policies for you. Ask about the plan on screen, a saved policy, or a new what-if.</p>
            {SUGGESTIONS.map((s) => (
              <button key={s} className="suggest" onClick={() => send(s)}>{s}</button>
            ))}
            <p className="muted small">Chats are saved and shared with everyone using this site, so please don’t enter personal information.</p>
          </div>
        )}
        {msgs.map((m, i) => {
          const streaming = busy && i === msgs.length - 1
          return (
            <div key={i} className={`msg ${m.role}`}>
              {m.content && <Markdown disallowedElements={['img']}>{m.content}</Markdown>}
              {streaming && m.role === 'assistant' && (live && !liveDone || !m.content) && (
                <div className="typing" aria-live="polite">{live && !liveDone ? `${label(live.name, 0)}…` : 'Thinking…'}</div>
              )}
              {m.policies && m.policies.length > 0 && (
                <div className="cp-policies">
                  {m.policies.map((p) => (
                    <button key={p.id} className="primary small-btn" onClick={() => onOpenPolicy(p.run_id)} title="Open this saved policy on the dashboard (from its snapshot, no re-run)">
                      📂 Open policy “{p.name}”
                    </button>
                  ))}
                </div>
              )}
              {m.citations && m.citations.length > 0 && (
                <div className="cites">
                  Sources: {m.citations.map((c, k) => <a key={k} href={safeUrl(c.uri)} target="_blank" rel="noreferrer">[{k + 1}] {c.title}</a>)}
                </div>
              )}
              {!streaming && m.steps && <HowBuilt steps={m.steps} />}
              {m.role === 'assistant' && m.content && !streaming && (
                <button className="link speak" onClick={() => speak(m.content, lang)} title="Read aloud">🔊 Listen</button>
              )}
              {m.model && !streaming && <div className="model-tag">{m.model}</div>}
            </div>
          )
        })}
      </div>
      <form className="cp-input" onSubmit={(e) => { e.preventDefault(); send(input) }}>
        {SpeechRec && (
          <button type="button" className={`ghost mic ${listening ? 'on' : ''}`} onClick={mic} title="Speak your question" aria-label="Voice input">🎤</button>
        )}
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={listening ? 'Listening…' : 'Ask about a district, a crisis, a policy…'} disabled={busy} aria-label="Ask the co-pilot" />
        <button className="primary" disabled={busy || !input.trim()}>Send</button>
      </form>
    </aside>
  )
}
