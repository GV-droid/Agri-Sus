import { useCallback, useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import type { AgentEvent, ChatMessage, ChatSession, ChatSummary, DocRef, Meta } from '../api'

/** One line of the activity feed: what the co-pilot is doing, in plain words (never tool names or arguments). */
type Act = { id?: string | null; text: string; state: 'run' | 'ok' | 'warn' | 'info'; detail?: string; kind?: string; t0?: number; eta?: number }
/** A policy document the co-pilot started that is still being written in the background. */
type DocJob = { id: string; run_id: string; scope: string; language: string; detail?: string; failed?: boolean }
type PolicyLink = { id: string; name: string; run_id: string; created?: boolean }
type Msg = {
  role: 'user' | 'assistant'; content: string; citations?: { title: string; uri: string }[]; model?: string
  policies?: PolicyLink[]; documents?: (DocRef & { created?: boolean })[]
  acts?: Act[]; thinking?: string; t0?: number; elapsed?: number; job?: string; docJobs?: DocJob[]
}
/** An answer in progress, kept across a page reload so it can be picked up again. */
type Turn = { job: string; sid: string | null; q: string; at: number }

const SUGGESTIONS = [
  'What happens to India’s pulse imports and Punjab groundwater if a strong El Niño hits in 2028?',
  'Why does the plan cut paddy in Sangrur, and what do farmers get in return?',
  'Create a policy that cuts the pulse and edible-oil import bill 40% at least cost over 6 years, and save it.',
  'Which 10 districts have the worst groundwater stage and how much paddy do they grow? (use BigQuery)',
  'What is the latest IMD / ENSO outlook and edible-oil import duty? Re-run the plan with it.',
  'Which saved policies do we have, and which one saves the most water?',
  'Will higher pulse and oilseed production actually cut imports? Write the policy document with the counter-measures.',
  'महाराष्ट्र में भूजल बचाने के लिए किसान कौन सी फसल उगाएँ?',
]

const SPEECH: Record<string, string> = { en: 'en-IN', hi: 'hi-IN', ta: 'ta-IN', te: 'te-IN', kn: 'kn-IN', mr: 'mr-IN', bn: 'bn-IN', pa: 'pa-IN', gu: 'gu-IN', ml: 'ml-IN', or: 'or-IN' }
const safeUrl = (u: string) => (/^https?:\/\//i.test(u) ? u : undefined)
const CHAT_KEY = 'agrisus_chat'
const TURN_KEY = 'agrisus_turn'
const TURN_MAX_AGE_MS = 30 * 60 * 1000
const readTurn = (): Turn | null => {
  try {
    const t = JSON.parse(localStorage.getItem(TURN_KEY) ?? 'null') as Turn | null
    return t && /^[a-z0-9]{16}$/.test(t.job) && Date.now() - t.at < TURN_MAX_AGE_MS ? t : null
  } catch { return null }
}
const secs = (ms: number) => Math.max(0, Math.round(ms / 1000))
const dur = (s: number) => (s < 60 ? `${Math.max(1, Math.round(s))} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`)
const sameDoc = (a: DocRef, b: DocRef) => a.run_id === b.run_id && a.scope === b.scope && a.language === b.language
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

/** A saved answer -> a chat message (with the activity feed it was saved with). */
function fromSaved(m: ChatMessage): Msg {
  return {
    role: m.role, content: m.content, citations: m.citations, model: m.model ?? undefined,
    policies: (m.policies ?? []).map((p) => ({ ...p, run_id: `p-${p.id}` })), documents: m.documents,
    job: m.turn, elapsed: m.elapsed_s,
    acts: (m.activity ?? []).map((a) => ({
      text: a.text, kind: a.kind, detail: a.ok === false ? 'not available - answered without it' : undefined,
      state: a.ok === false || a.kind === 'fallback' ? 'warn' : a.ok ? 'ok' : 'info',
    })),
  }
}

/** The answer is complete: freeze the timer and close any step that never reported back. */
function finish(m: Msg, now: number): Msg {
  return {
    ...m, thinking: undefined, elapsed: m.elapsed ?? (m.t0 ? (now - m.t0) / 1000 : undefined),
    acts: (m.acts ?? []).map((a) => (a.state === 'run' ? { ...a, state: 'warn', detail: 'did not finish in time' } : a)),
  }
}

/** One co-pilot event -> the answer being built (pure; side effects live in the stream loop). */
function apply(m: Msg, ev: AgentEvent, now: number): Msg {
  const acts = m.acts ?? []
  switch (ev.type) {
    case 'job': return { ...m, job: ev.id }
    case 'model': return { ...m, model: `${ev.model} · ${ev.backend}` }
    case 'status':
      if (ev.phase === 'thinking') return { ...m, thinking: ev.text }
      return { ...m, acts: [...acts, { text: ev.text, kind: ev.phase, state: ev.phase === 'fallback' ? 'warn' : 'info' }] }
    case 'tool_call':
      return { ...m, thinking: undefined, acts: [...acts, { id: ev.id, text: ev.label || 'Gathering information', state: 'run', t0: now, eta: ev.eta_s }] }
    case 'progress':
      return { ...m, acts: acts.map((a) => (a.id === ev.id && a.state === 'run' ? { ...a, detail: ev.text, eta: ev.eta_s ?? a.eta } : a)) }
    case 'tool_result': {
      let hit = false
      return {
        ...m, acts: acts.map((a) => {
          if (hit || a.state !== 'run' || (ev.id != null && a.id !== ev.id)) return a
          hit = true
          return { ...a, state: ev.ok === false ? 'warn' : 'ok', detail: ev.ok === false ? 'not available - answered without it' : undefined }
        }),
      }
    }
    case 'text': return { ...m, content: m.content + ev.text }
    case 'text_reset': return { ...m, content: '' }  // a stalled draft is discarded; the retry writes it again
    case 'citations': return { ...m, citations: ev.citations }
    case 'policy':
      return { ...m, policies: [...(m.policies ?? []).filter((p) => p.id !== ev.id), { id: ev.id, name: ev.name, run_id: ev.run_id, created: ev.created }] }
    case 'document': {
      const { type: _t, seq: _s, ...d } = ev
      return { ...m, documents: [...(m.documents ?? []).filter((x) => !sameDoc(x, d)), d], docJobs: (m.docJobs ?? []).filter((x) => !sameDoc(x, d)) }
    }
    case 'doc_job':
      return (m.docJobs ?? []).some((x) => x.id === ev.id) ? m
        : { ...m, docJobs: [...(m.docJobs ?? []), { id: ev.id, run_id: ev.run_id, scope: ev.scope, language: ev.language }] }
    case 'error': return { ...m, content: `${m.content}${m.content ? '\n\n' : ''}_⚠ ${ev.text}_` }
    case 'done': return finish(m, now)
    default: return m
  }
}

function ActRow({ a, now }: { a: Act; now: number }) {
  const ico = a.state === 'run' ? <span className="spin" aria-hidden="true" /> : a.state === 'ok' ? '✓' : a.state === 'warn' ? '⚠' : a.kind === 'retry' ? '↻' : '•'
  return (
    <li className={`act ${a.state}${a.kind ? ` ${a.kind}` : ''}`}>
      <span className="ico">{ico}</span>
      <span className="t">{a.text}{a.detail && <span className="muted"> — {a.detail}</span>}</span>
      {a.state === 'run' && a.t0 && <span className="secs">{secs(now - a.t0)} s{a.eta ? ` of ~${a.eta} s` : ''}</span>}
    </li>
  )
}

/** After the answer: how it was built, folded away (like a coding agent's "worked for ..." line). */
function HowBuilt({ m }: { m: Msg }) {
  const acts = m.acts ?? []
  if (!acts.length) return null
  const steps = acts.filter((a) => !a.kind).length
  const head = [m.elapsed != null ? `Worked for ${dur(m.elapsed)}` : 'How this answer was built', steps ? `${steps} ${steps === 1 ? 'step' : 'steps'}` : '']
  return (
    <details className="how">
      <summary>{head.filter(Boolean).join(' · ')}</summary>
      <ul className="acts">{acts.map((a, k) => <ActRow key={k} a={a} now={0} />)}</ul>
    </details>
  )
}

/** The line under an answer while it is being worked on. */
function liveLine(m: Msg): string {
  if (m.thinking) return m.thinking
  if ((m.acts ?? []).some((a) => a.state === 'run')) return 'Working'
  return m.content ? 'Writing the answer' : 'Thinking'
}

type Props = {
  meta: Meta | null
  runId?: string
  onSimulation: (runId: string) => void
  onOpenPolicy: (runId: string) => void
  onOpenDocument: (d: DocRef) => void
  onPoliciesChanged: () => void
  pending: string | null
  clearPending: () => void
}

export default function Copilot({ meta, runId, onSimulation, onOpenPolicy, onOpenDocument, onPoliciesChanged, pending, clearPending }: Props) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [lang, setLang] = useState('en')
  const [listening, setListening] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<ChatSummary[] | null>(null)
  const [showSessions, setShowSessions] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())
  const rec = useRef<SR | null>(null)
  const abort = useRef<AbortController | null>(null)
  const jobRef = useRef<string | null>(null)  // the answer being written now (for Stop)
  const listed = useRef(false)  // the saved-chats list was loaded: refresh it when an answer is saved
  const following = useRef(new Set<string>())  // background document jobs being followed

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
  const ticking = busy || msgs.some((m) => m.docJobs?.some((d) => !d.failed))
  useEffect(() => { // the live timers of the activity feed
    if (!ticking) return
    setNow(Date.now())
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [ticking])

  const refreshSessions = useCallback(() => {
    listed.current = true
    api.chats.list().then((r) => setSessions(r.chats)).catch(() => setSessions((cur) => cur ?? []))
  }, [])

  const openSession = useCallback(async (id: string, quiet = false): Promise<ChatSession | null> => {
    abort.current?.abort()
    try {
      const s = await api.chats.get(id)
      setMsgs(s.messages.map(fromSaved))
      setSessionId(s.id)
      if (s.language && meta?.languages?.[s.language]) setLang(s.language)
      localStorage.setItem(CHAT_KEY, s.id)
      setShowSessions(false)
      return s
    } catch {
      if (!quiet) {
        localStorage.removeItem(CHAT_KEY)
        setNote('That chat is no longer available.')
      }
      return null
    }
  }, [meta?.languages])

  /** Follow a policy document the co-pilot is writing in the background; its card becomes "Open document". */
  const followDoc = useCallback((d: { id: string; run_id: string; scope: string; language: string }) => {
    if (following.current.has(d.id)) return
    following.current.add(d.id)
    const update = (f: (x: DocJob) => DocJob | null, ref?: DocRef) => setMsgs((cur) => cur.map((m) => {
      if (!m.docJobs?.some((x) => x.id === d.id)) return m
      return {
        ...m, docJobs: m.docJobs.map((x) => (x.id === d.id ? f(x) : x)).filter((x): x is DocJob => !!x),
        documents: ref ? [...(m.documents ?? []).filter((x) => !sameDoc(x, ref)), ref] : m.documents,
      }
    }))
    const saved = async () => {
      const r = await api.documents.get(d.run_id, d.scope, d.language).catch(() => null)
      if (r?.document) update(() => null, { run_id: d.run_id, scope: d.scope, language: d.language, version: r.document.version, title: r.document.title })
      else update((x) => ({ ...x, failed: true, detail: 'It may still be finishing: open Policy document in a minute.' }))
    }
    ;(async () => {
      try {
        for await (const ev of api.documents.follow(d.id)) {
          if (ev.type === 'step' && ev.status === 'running') update((x) => ({ ...x, detail: ev.detail ? `${ev.label} — ${ev.detail}` : ev.label }))
          else if (ev.type === 'document') {
            const doc = ev.document
            update(() => null, { run_id: d.run_id, scope: doc.scope, language: doc.language, version: doc.version, title: doc.title })
          } else if (ev.type === 'error') update((x) => ({ ...x, failed: true, detail: ev.message }))
          else if (ev.type === 'lost') await saved()
        }
      } catch {
        await saved()
      } finally {
        following.current.delete(d.id)
      }
    })()
  }, [])

  /** The stream of an answer was lost and its job is gone from the server: the answer is (or soon will be) in the
   * saved chat session, so show it from there. */
  const recover = async (job: string, sid: string | null, ctrl: AbortController) => {
    for (let i = 0; sid && i < 40 && !ctrl.signal.aborted; i++) {
      const s = await api.chats.get(sid).catch(() => null)
      const k = s ? s.messages.findIndex((m) => m.role === 'assistant' && m.turn === job) : -1
      if (s && k >= 0) {
        if (!ctrl.signal.aborted) setMsgs(s.messages.slice(0, k + 1).map(fromSaved))
        return
      }
      await new Promise((r) => window.setTimeout(r, 3000))
    }
    if (!ctrl.signal.aborted) {
      setMsgs((cur) => [...cur.slice(0, -1), finish({ ...cur[cur.length - 1], content: `${cur[cur.length - 1].content}\n\n_⚠ The connection to this answer was lost. If it finished, it is in 💬 Chats._` }, Date.now())])
    }
  }

  /** Read an answer's events into the last message (a new question, or an answer picked up after a reload). */
  const consume = async (stream: AsyncGenerator<AgentEvent>, ctrl: AbortController, q: string, sid0: string | null) => {
    const upd = (f: (m: Msg) => Msg) => setMsgs((cur) => (ctrl.signal.aborted || !cur.length ? cur : [...cur.slice(0, -1), f(cur[cur.length - 1])]))
    let sid = sid0
    let job: string | null = null
    const remember = () => { if (job) localStorage.setItem(TURN_KEY, JSON.stringify({ job, sid, q, at: Date.now() } satisfies Turn)) }
    try {
      for await (const ev of stream) {
        if (ctrl.signal.aborted) break
        if (ev.type === 'lost') { await recover(ev.job, sid, ctrl); break }
        if (ev.type === 'job') { job = ev.id; jobRef.current = ev.id; remember() }
        else if (ev.type === 'session') {
          sid = ev.id
          setSessionId(ev.id)
          localStorage.setItem(CHAT_KEY, ev.id)
          remember()
          if (ev.saved && listed.current) refreshSessions()
        } else if (ev.type === 'simulation') onSimulation(ev.run_id)
        else if (ev.type === 'policy' && ev.created) onPoliciesChanged()
        else if (ev.type === 'doc_job') followDoc(ev)
        const t = Date.now()
        upd((m) => apply(m, ev, t))
        if (ev.type === 'done') localStorage.removeItem(TURN_KEY)
      }
    } catch (e) {
      if (!ctrl.signal.aborted) upd((m) => finish({ ...m, content: `${m.content}\n\n_Error: ${(e as Error).message}_` }, Date.now()))
    }
    if (!ctrl.signal.aborted) upd((m) => (m.elapsed == null ? finish(m, Date.now()) : m))
    if (abort.current === ctrl) { setBusy(false); abort.current = null; jobRef.current = null }
  }

  const begin = () => {
    const ctrl = new AbortController()
    abort.current = ctrl
    setBusy(true)
    setNote(null)
    return ctrl
  }

  // reopen this browser's last conversation (sessions themselves are shared by everyone using the site), and pick up
  // an answer that was still being written when the page was closed or reloaded
  useEffect(() => {
    const turn = readTurn()
    const id = localStorage.getItem(CHAT_KEY)
    if (!turn) {
      localStorage.removeItem(TURN_KEY)
      if (id && /^[a-z0-9]{12}$/.test(id)) openSession(id)
      return
    }
    (async () => {
      const s = turn.sid ? await openSession(turn.sid, true) : null
      if (s?.messages.some((m) => m.role === 'assistant' && m.turn === turn.job)) { localStorage.removeItem(TURN_KEY); return }
      setMsgs((cur) => [...cur, { role: 'user', content: turn.q }, { role: 'assistant', content: '', acts: [], t0: turn.at }])
      const ctrl = begin()
      await consume(api.jobs.follow(turn.job, ctrl.signal), ctrl, turn.q, turn.sid)
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const resetChat = () => {
    abort.current?.abort()  // the answer itself goes on on the server and is saved to its chat
    setMsgs([])
    setSessionId(null)
    setBusy(false)
    jobRef.current = null
    localStorage.removeItem(CHAT_KEY)
    localStorage.removeItem(TURN_KEY)
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
    setMsgs([...history, { role: 'assistant', content: '', acts: [], t0: Date.now() }])
    setInput('')
    const ctrl = begin()
    await consume(api.chat(history.slice(-12).map((m) => ({ role: m.role, content: m.content })), lang, sessionId, runId, ctrl.signal), ctrl, text, sessionId)
  }

  /** Stop: the server ends the answer and saves what was said so far (the stream then closes by itself). */
  const stop = async () => {
    const jid = jobRef.current
    if (jid) {
      try { await api.jobs.cancel(jid); return } catch { /* not on this server any more: stop listening */ }
    }
    abort.current?.abort()
    abort.current = null
    jobRef.current = null
    localStorage.removeItem(TURN_KEY)
    setMsgs((cur) => (cur.length ? [...cur.slice(0, -1), finish({ ...cur[cur.length - 1], content: `${cur[cur.length - 1].content}\n\n_Stopped._` }, Date.now())] : cur))
    setBusy(false)
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
          const streaming = busy && i === msgs.length - 1 && m.role === 'assistant'
          const acts = m.acts ?? []
          return (
            <div key={i} className={`msg ${m.role}`}>
              {streaming && acts.length > 0 && (
                <ul className="acts live" aria-label="What the co-pilot is doing">
                  {acts.slice(-8).map((a, k) => <ActRow key={k} a={a} now={now} />)}
                </ul>
              )}
              {!streaming && m.role === 'assistant' && <HowBuilt m={m} />}
              {m.content && <Markdown remarkPlugins={[remarkGfm]} disallowedElements={['img']}>{m.content}</Markdown>}
              {streaming && (
                <div className="typing" aria-live="polite"><span className="spin" aria-hidden="true" />{liveLine(m)}… <span className="secs">{secs(now - (m.t0 ?? now))} s</span></div>
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
              {m.docJobs && m.docJobs.length > 0 && m.docJobs.map((d) => (
                <div key={d.id} className={`cp-docjob small ${d.failed ? 'failed' : ''}`} role="status">
                  {d.failed ? '⚠ ' : <span className="spin" aria-hidden="true" />}
                  <b>{d.failed ? 'The policy document is not ready yet' : 'Writing the policy document'}{d.scope !== 'India' ? ` · ${d.scope}` : ''}</b>
                  {d.detail && <span className="muted"> — {d.detail}</span>}
                  {!d.failed && <div className="muted">It keeps going in the background and is saved when ready — this button turns into “Open document”.</div>}
                </div>
              ))}
              {m.documents && m.documents.length > 0 && (
                <div className="cp-policies cp-docs">
                  {m.documents.map((d) => (
                    <button key={`${d.run_id}|${d.scope}|${d.language}`} className="primary small-btn" onClick={() => onOpenDocument(d)}
                      title="Open the saved policy document (read, regenerate, print or download)">
                      📄 Open document{d.version ? ` v${d.version}` : ''}{d.scope && d.scope !== 'India' ? ` · ${d.scope}` : ''}
                    </button>
                  ))}
                </div>
              )}
              {m.citations && m.citations.length > 0 && (
                <div className="cites">
                  <span className="muted">Web sources (Google Search):</span> {m.citations.map((c, k) => <a key={k} href={safeUrl(c.uri)} target="_blank" rel="noreferrer">[{k + 1}] {c.title}</a>)}
                </div>
              )}
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
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={listening ? 'Listening…' : busy ? 'Working on your answer…' : 'Ask about a district, a crisis, a policy…'} disabled={busy} aria-label="Ask the co-pilot" />
        {busy
          ? <button type="button" className="ghost stop" onClick={stop} title="Stop this answer (what was said so far is kept)">■ Stop</button>
          : <button className="primary" disabled={!input.trim()}>Send</button>}
      </form>
    </aside>
  )
}
