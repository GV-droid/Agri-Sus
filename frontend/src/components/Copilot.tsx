import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { api } from '../api'
import type { Meta } from '../api'

type Step = { kind: 'call' | 'result' | 'error'; name: string; text: string }
type Msg = { role: 'user' | 'assistant'; content: string; steps?: Step[]; citations?: { title: string; uri: string }[]; model?: string }

const SUGGESTIONS = [
  'What happens to India’s pulse imports and Punjab groundwater if a strong El Niño hits in 2028?',
  'Why does the plan cut paddy in Sangrur, and what do farmers get in return?',
  'Which 10 districts have the worst groundwater stage and how much paddy do they grow? (use BigQuery)',
  'What is the latest IMD / ENSO outlook and edible-oil import duty? Re-run the plan with it.',
  'How sustainable is the plan by 2030, and which pillar is weakest? Does it survive an El Niño?',
  'Compare a 20% land-shift limit with the 12% default.',
  'महाराष्ट्र में भूजल बचाने के लिए किसान कौन सी फसल उगाएँ?',
]

const SPEECH: Record<string, string> = { en: 'en-IN', hi: 'hi-IN', ta: 'ta-IN', te: 'te-IN', kn: 'kn-IN', mr: 'mr-IN', bn: 'bn-IN', pa: 'pa-IN', gu: 'gu-IN', ml: 'ml-IN', or: 'or-IN' }
const safeUrl = (u: string) => (/^https?:\/\//i.test(u) ? u : undefined)

type SR = { lang: string; interimResults: boolean; onresult: (e: { results: { 0: { transcript: string } }[] }) => void; onend: () => void; start: () => void; stop: () => void }
const SpeechRec = (window as unknown as { SpeechRecognition?: new () => SR; webkitSpeechRecognition?: new () => SR }).SpeechRecognition ?? (window as unknown as { webkitSpeechRecognition?: new () => SR }).webkitSpeechRecognition

function speak(text: string, lang: string) {
  if (!('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(text.replace(/[#*_`>|]/g, '').slice(0, 1200))
  u.lang = SPEECH[lang] ?? 'en-IN'
  window.speechSynthesis.speak(u)
}

export default function Copilot({ meta, onSimulation, pending, clearPending }: { meta: Meta | null; onSimulation: (runId: string) => void; pending: string | null; clearPending: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [lang, setLang] = useState('en')
  const [listening, setListening] = useState(false)
  const rec = useRef<SR | null>(null)

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

  const send = async (text: string) => {
    if (!text.trim() || busy) return
    const history: Msg[] = [...msgs, { role: 'user', content: text }]
    setMsgs([...history, { role: 'assistant', content: '', steps: [] }])
    setInput('')
    setBusy(true)
    const upd = (f: (m: Msg) => Msg) => setMsgs((cur) => [...cur.slice(0, -1), f(cur[cur.length - 1])])
    try {
      for await (const ev of api.chat(history.slice(-12).map((m) => ({ role: m.role, content: m.content })), lang)) {
        if (ev.type === 'text') upd((m) => ({ ...m, content: m.content + ev.text }))
        else if (ev.type === 'model') upd((m) => ({ ...m, model: `${ev.model} · ${ev.backend}` }))
        else if (ev.type === 'tool_call') upd((m) => ({ ...m, steps: [...(m.steps ?? []), { kind: 'call', name: ev.name, text: JSON.stringify(ev.args) }] }))
        else if (ev.type === 'tool_result') upd((m) => ({ ...m, steps: [...(m.steps ?? []), { kind: 'result', name: ev.name, text: ev.summary }] }))
        else if (ev.type === 'citations') upd((m) => ({ ...m, citations: ev.citations }))
        else if (ev.type === 'simulation') onSimulation(ev.run_id)
        else if (ev.type === 'error') upd((m) => ({ ...m, steps: [...(m.steps ?? []), { kind: 'error', name: 'error', text: ev.text }] }))
      }
    } catch (e) {
      upd((m) => ({ ...m, content: m.content + `\n\n_Error: ${(e as Error).message}_` }))
    }
    setBusy(false)
  }

  useEffect(() => {
    if (pending) {
      send(pending)
      clearPending()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending])

  return (
    <aside className="panel copilot">
      <div className="cp-head">
        <h3>✦ Agri Sus Co-pilot</h3>
        <select className="lang" value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Reply language">
          {Object.entries(meta?.languages ?? { en: 'English' }).map(([id, l]) => <option key={id} value={id}>{l}</option>)}
        </select>
        <span className={`pill ${meta?.gemini.enabled ? 'live' : ''}`}>{meta?.gemini.enabled ? `${meta.gemini.agent_model} · ${meta.gemini.backend}` : 'offline analyst'}</span>
      </div>
      <div className="cp-body" ref={body}>
        {msgs.length === 0 && (
          <div className="cp-empty">
            <p className="muted">A multilingual Gemini agent (type or 🎤 speak) that runs the optimizer, queries BigQuery, predicts yields and searches the web (grounded, with citations). Every number comes from a tool call you can inspect.</p>
            {SUGGESTIONS.map((s) => (
              <button key={s} className="suggest" onClick={() => send(s)}>{s}</button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.steps && m.steps.length > 0 && (
              <div className="steps">
                {m.steps.map((s, k) => (
                  <div key={k} className={`step ${s.kind}`}>
                    <span className="step-icon">{s.kind === 'call' ? '⚙' : s.kind === 'result' ? '✓' : '!'}</span>
                    <code>{s.name}</code> <span className="step-text">{s.text}</span>
                  </div>
                ))}
              </div>
            )}
            {m.content ? <Markdown disallowedElements={['img']}>{m.content}</Markdown> : m.role === 'assistant' && busy && i === msgs.length - 1 ? <div className="typing">thinking…</div> : null}
            {m.citations && m.citations.length > 0 && (
              <div className="cites">
                Sources: {m.citations.map((c, k) => <a key={k} href={safeUrl(c.uri)} target="_blank" rel="noreferrer">[{k + 1}] {c.title}</a>)}
              </div>
            )}
            {m.role === 'assistant' && m.content && !(busy && i === msgs.length - 1) && (
              <button className="link speak" onClick={() => speak(m.content, lang)} title="Read aloud">🔊 Listen</button>
            )}
            {m.model && <div className="model-tag">{m.model}</div>}
          </div>
        ))}
      </div>
      <form className="cp-input" onSubmit={(e) => { e.preventDefault(); send(input) }}>
        {SpeechRec && (
          <button type="button" className={`ghost mic ${listening ? 'on' : ''}`} onClick={mic} title="Speak your question" aria-label="Voice input">🎤</button>
        )}
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={listening ? 'Listening…' : 'Ask about a district, a crisis, a policy…'} disabled={busy} />
        <button className="primary" disabled={busy || !input.trim()}>Send</button>
      </form>
    </aside>
  )
}
