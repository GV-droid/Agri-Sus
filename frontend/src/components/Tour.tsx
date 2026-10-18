import { useState } from 'react'

const STEPS = [
  {
    title: 'The problem, in one line',
    body: 'India spends over $20 bn a year importing pulses and edible oil, while paddy and sugarcane pump aquifers dry across the north-west. Agri Sus asks: which crops should each district grow in 2027-2030 so the country imports less and saves water, without hurting farmers or food security?',
  },
  {
    title: '1 · Pick a crisis, set the rules',
    body: 'On the left choose a scenario (El Niño drought, palm-oil price shock, Indonesia halting palm-oil exports, heatwave…) or let Gemini build a live outlook from today’s news. Set the annual budget and the instruments it may fund (income transfers, seed & FPO kits, assured prices), or let the optimizer find the least budget that hits the import target. Add a trade shock to cut a supplier country. Sliders are guardrails: how fast land can change, minimum cereal self-sufficiency.',
  },
  {
    title: '2 · Read the sustainability index',
    body: 'The Agri Sustainability & Self-reliance Index (ASSI, 0-100) is an absolute score for India’s farm system: import self-reliance, groundwater health, soil health and crop diversity. Grey is “do nothing” (today’s crops continue), green is the Agri Sus plan — the gap is what the plan achieves. Farmer income and food security are hard guardrails, never traded away. Shaded bands show both futures under climate shocks.',
  },
  {
    title: '3 · Follow the money',
    body: 'The 💰 Budget & returns tab shows how the budget splits across instruments, what it earns back (forex, power and urea subsidy saved) and where each rupee works hardest: the MROPI tiers, also a map layer. The 🚢 Trade shocks tab shows who India buys each pulse and oil from, and what replacing a supplier at home takes.',
  },
  {
    title: '4 · Explore the map',
    body: 'Every district in India is coloured by the layer you choose. Switch between “Do nothing”, “Agri Sus plan” and “Difference” to see where the plan makes a change, press ▶ to animate 2027→2030, or click a state in the leaderboard to zoom in. Click any district for its score, crop mix today vs the plan, why, and the 🌱 Next crop ranking for each season.',
  },
  {
    title: '5 · Ask the Gemini co-pilot',
    body: 'Ask in English, Hindi, Tamil, Telugu and more — by typing or with the 🎤 microphone. The agent runs the optimizer, queries BigQuery and searches the web with citations, and shows every tool call it makes. 📄 Policy document turns the plan into a structured policy proposal (sections A–H) — including whether more production will really cut imports, and which existing schemes to build on — written once by a Gemini document agent and saved.',
  },
]

export default function Tour({ onClose }: { onClose: () => void }) {
  const [i, setI] = useState(0)
  const s = STEPS[i]
  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body tour" onClick={(e) => e.stopPropagation()}>
        <div className="tour-dots">{STEPS.map((_, k) => <span key={k} className={k === i ? 'on' : ''} />)}</div>
        <h2>{s.title}</h2>
        <p>{s.body}</p>
        <div className="row">
          {i > 0 && <button className="ghost" onClick={() => setI(i - 1)}>Back</button>}
          {i < STEPS.length - 1 ? <button className="primary" onClick={() => setI(i + 1)}>Next</button> : <button className="primary" onClick={onClose}>Start exploring</button>}
          <button className="link" onClick={onClose}>Skip</button>
        </div>
      </div>
    </div>
  )
}
