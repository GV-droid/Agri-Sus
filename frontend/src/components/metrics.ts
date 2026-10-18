import type { DistrictResult } from '../api'

/** Map metrics, colour ramps and value lookups (kept apart from MapView so the heavy map libraries can load lazily). */
export type Metric = 'sustainability_score' | 'gw_stage_pct' | 'legume_share_pct' | 'rice_share_pct' | 'water_saved_mcm' | 'rice_area_change_pct' | 'income_vs_today_pct' | 'incentive_cr' | 'mropi'
export type Mode = 'sq' | 'plan' | 'diff'
export type Ramp = 'score' | 'water' | 'diverge' | 'green' | 'gold' | 'blue' | 'paddy'
export type Scale = { label: string; unit: string; domain: [number, number]; ramp: Ramp; help: string; decimals?: number; log?: boolean }
export type Cfg = Scale & { sq?: string; diff?: Scale }

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

export type RGB = [number, number, number]
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
