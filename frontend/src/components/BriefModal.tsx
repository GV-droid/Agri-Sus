import { useEffect, useState } from 'react'
import Markdown from 'react-markdown'
import { api } from '../api'

type Props = { runId?: string; state: string | null; languages: Record<string, string>; onClose: () => void }

export default function BriefModal({ runId, state, languages, onClose }: Props) {
  const [lang, setLang] = useState('en')
  const [brief, setBrief] = useState<{ markdown: string; model: string; scope: string } | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let current = true // a slower reply for a previous language must not overwrite the one now selected
    setBrief(null)
    setErr(null)
    api
      .brief(runId, state, lang)
      .then((b) => current && setBrief(b))
      .catch((e) => current && setErr((e as Error).message))
    return () => {
      current = false
    }
  }, [runId, state, lang])

  const download = () => {
    if (!brief) return
    const blob = new Blob([brief.markdown], { type: 'text/markdown' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `agrisus-brief-${brief.scope.replace(/\s+/g, '_')}.md`
    a.click()
  }

  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body brief" onClick={(e) => e.stopPropagation()}>
        <div className="brief-head">
          <h2>✦ Policy brief · {state ?? 'India'}</h2>
          <div className="row">
            <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Language">
              {Object.entries(languages).map(([id, l]) => <option key={id} value={id}>{l}</option>)}
            </select>
            <button className="ghost" onClick={() => window.print()} disabled={!brief}>Print / PDF</button>
            <button className="ghost" onClick={download} disabled={!brief}>Download .md</button>
            <button className="ghost" onClick={onClose}>✕</button>
          </div>
        </div>
        <div className="brief-body printable">
          {err && <div className="error">{err}</div>}
          {!brief && !err && <div className="typing">Gemini is drafting a one-page brief from this simulation…</div>}
          {brief && <Markdown disallowedElements={['img']}>{brief.markdown}</Markdown>}
        </div>
        {brief && <div className="model-tag">Written by {brief.model} from the optimizer output only — every number traces to a tool result.</div>}
      </div>
    </div>
  )
}
