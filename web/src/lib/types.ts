// Shapes of the API payloads (src/api.py). Numbers the backend could not
// compute arrive as null, never NaN, so every numeric field that can be
// missing is typed `number | null`.

import type { Los, Severity } from './format'

export type Figure = { data: object[]; layout: Record<string, unknown> }
export type Row = Record<string, string | number | boolean | null>

export interface Camera {
  id: string
  name: string
  short: string
  mode: 'junction' | 'area'
  notes: string
  dataset: boolean
  analysed: boolean
  windows: number
  scale_verified: boolean
  has_video: boolean
  has_reference: boolean
  has_heatmap: boolean
  has_annotated: boolean
}

export interface Recording {
  id: string
  site: string
  name: string
  site_label: string
  has_video: boolean
}

export interface IncidentCounts {
  open: number
  acknowledged: number
  resolved: number
  cleared: number
  open_critical: number
}

export interface Meta {
  cameras: Camera[]
  metrics: { windows: number; cameras: number; minutes: number }
  calibrated: number
  detector: { ready: boolean; path: string; fast: string | null }
  auth: { required: boolean }
  incidents: IncidentCounts | null
  fluid: {
    available: boolean
    root?: string
    recordings: Recording[]
    totals: { recordings: number; intersections: number; minutes: number; vehicles: number } | null
  }
  mode: 'webster' | 'max_pressure'
  recommendation: {
    recommended: string
    confidence: string
    headline: string
    reasons: string[]
    against: string[]
  } | null
}

export interface Alert {
  camera_id: string
  camera_name: string
  camera_short: string
  zone_id: string
  zone_label: string
  kind: string
  severity: Severity
  start_s: number
  end_s: number
  duration_s: number
  headline: string
  evidence: string
  recommendation: string
  // Workflow fields. Present on network alerts; absent or not actionable on
  // an upload that has not been kept.
  id?: string
  status?: IncidentStatus
  actionable?: boolean
  source?: 'recorded' | 'replay' | 'detect'
  raised_at?: string
}

// "cleared": a live alert whose condition ended before anyone acted on it.
export type IncidentStatus = 'open' | 'acknowledged' | 'resolved' | 'cleared'

export interface IncidentAction {
  at: string
  action: 'acknowledge' | 'resolve' | 'reopen'
  by: string
  note: string
}

export interface Incident extends Alert {
  id: string
  status: IncidentStatus
  history: IncidentAction[]
  current: boolean
  active?: boolean
}

export interface IncidentsPayload {
  items: Incident[]
  counts: IncidentCounts
}

export interface AlertsPayload {
  alerts: Alert[]
  counts: Record<Severity, number>
}

export interface NetworkSite {
  id: string
  name: string
  short: string
  mode: string
  los: Los
  zones: number
  mean_congestion: number
  peak_congestion: number
  worst_zone: string
  mean_speed_kmph: number | null
  saturated_windows: number
  total_windows: number
  has_overlay: boolean
}

export interface NetworkPayload {
  kpi: {
    over_capacity_pct: number
    sites: number
    junction_sites: number
    area_sites: number
    zones: number
    windows: number
    saturated: number
    saturated_pct: number
    worst: { id: string; short: string; mean: number }
  }
  sites: NetworkSite[]
  scale_verified_any: boolean
  charts: { los_timeline: Figure; ranking: Figure }
}

export interface ZoneCard {
  zone_id: string
  label: string
  kind: string
  congestion: number
  los: Los
  occupancy: number | null
  density: number | null
  speed: number | null
  flow: number | null
  peak: number | null
  saturated_windows: number
  windows: number
}

export interface TrendMetric {
  id: string
  label: string
  estimate?: boolean
}

export interface SitePayload extends AlertsPayload {
  camera: { id: string; name: string; short: string; mode: string; notes: string; scale_verified: boolean }
  mean_congestion: number
  los: Los
  windows: number
  saturated_pct: number
  zones: ZoneCard[]
  trend_metrics: TrendMetric[]
  assets: { overlay: boolean; heatmap: boolean; annotated_video: boolean }
  charts: { zone_los: Figure; count_vs_occupancy: Figure; fundamental?: Figure }
  min_sustained_windows: number
}

export interface TrendPayload {
  chart: Figure | null
  columns: string[]
  rows: Row[]
}

export interface PhasePlan {
  approach_id: string
  label: string
  short: string
  green_s: number
  amber_s: number
  all_red_s: number
  total_s: number
  flow_ratio: number
  capacity_pcu_per_hr: number
  degree_of_saturation: number
  est_delay_s_per_pcu: number | null
  delay_model: string
}

export interface SignalPlan {
  junction: string
  cycle_s: number
  lost_time_s: number
  total_flow_ratio: number
  phases: PhasePlan[]
  feasible: boolean
  note: string
  mean_delay_s_per_pcu: number | null
}

export interface SignalPayload {
  approaches: { id: string; label: string; short: string; lanes: number; default_width_m: number }[]
  sat_sources: { id: string; label: string }[]
  factor: number | null
  width_notes: string[]
  base: SignalPlan
  opt: SignalPlan
  compare: {
    baseline_mean_delay_s: number | null
    optimised_mean_delay_s: number | null
    delay_reduction_pct: number | null
  }
  Y: number
  L: number
  x_common: number
  c_min: number | null
  max_cycle: number
  amber_s: number
  all_red_s: number
  hcm_period_min: number
  worst_before: number
  worst_after: number
  over_before: number
  delay_models: {
    model: string
    label: string
    baseline: number | null
    optimised: number | null
    reduction_pct: number | null
    diverges: boolean
  }[]
  sweep: Row[]
  best_cycle: number | null
  best_cycle_webster: number | null
  delay_model: string
  plan_table: Row[]
  plans_text: string
  charts: { cycle: Figure; dumbbell: Figure; delay_curve: Figure }
}

export interface Forecast {
  camera_id: string
  camera_short: string
  camera_name: string
  zone_id: string
  zone_label: string
  current: number
  predicted: number
  lower: number
  upper: number
  horizon_s: number
  direction: string
  reliable: boolean
}

export interface Job<R = unknown> {
  id: string
  kind: string
  label: string
  status: 'queued' | 'running' | 'done' | 'error'
  progress: number
  message: string
  error: string
  result: R | null
  elapsed_s: number
}

// ------------------------------------------------------------------ live

export interface LiveZone {
  zone_id: string
  label: string
  kind: string | null
  ci: number
  los: Los
  occupancy: number | null
  density: number | null
  speed: number | null
  flow: number | null
  queue: number | null
  saturated: boolean
  mode: string | null
  t: number | null
}

export interface LiveCamera {
  id: string
  name: string
  short: string
  mode: 'junction' | 'area'
  has_video: boolean
  has_annotated: boolean
  can_detect: boolean
  source: 'replay' | 'detect'
  clip_t: number
  duration: number | null
  lap: number
  ci: number
  los: Los
  zones: LiveZone[]
  spark: number[]
}

export interface LiveSession {
  id: string
  camera_id: string
  camera_short: string
  status: 'starting' | 'running' | 'stopped' | 'error'
  message: string
  model: string
  fps: number
  frames: number
  t: number
  elapsed_s: number
  vehicles: Record<string, number>
}

export interface LiveRisk {
  camera_id: string
  camera_short: string
  zone_label: string
  current: number
  predicted: number
  horizon_s: number
}

export interface LiveState {
  t: number
  playing: boolean
  speed: number
  speeds: number[]
  cameras: LiveCamera[]
  active: Alert[]
  detect: LiveSession | null
  risks: LiveRisk[]
}

export type LiveEvent =
  | { type: 'alert'; seq: number; at: string; alert: Alert }
  | { type: 'clear'; seq: number; at: string; id: string; camera_short: string; zone_label: string; kind: string }
  | { type: 'detect'; seq: number; at: string; session: LiveSession }
  | { type: 'control'; seq: number; at: string; playing: boolean; speed: number; t: number }

// --------------------------------------------------------------- what-if

export interface Headroom {
  Y: number
  lost_time_s: number
  max_cycle_s: number
  x_practical: number
  Y_practical: number
  Y_ultimate: number
  best_x_at_max_cycle: number | null
  reserve_practical_pct: number | null
  reserve_ultimate_pct: number | null
  growth_per_year_pct: number
  years_to_practical: number | null
  years_to_failure: number | null
  approaches: { id: string; y: number; share_pct: number }[]
}

export interface WhatIfBlock {
  headroom: Headroom
  base: SignalPlan
  opt: SignalPlan
  compare: SignalPayload['compare']
  worst_x_fixed: number
  worst_x_adaptive: number
}

export interface Benefits {
  pcu_hours_per_hour: number
  vehicle_hours_per_day: number
  vehicle_hours_per_year: number
  fuel_l_per_day: number
  fuel_l_per_year: number
  co2_t_per_year: number
  rupees_per_year: number
  per_approach: {
    id: string
    flow_pcu_per_hr: number
    delay_before_s: number | null
    delay_after_s: number | null
    saved_s_per_pcu: number
  }[]
  fleet: { group: string; share_pct: number; idle_l_per_h: number; fuel: string }[]
  assumptions: {
    hours_per_day: number
    days_per_year: number
    pcu_per_vehicle: number
    price_rs_per_l: Record<string, number>
    co2_kg_per_l: Record<string, number>
    note: string
  }
}

export interface WhatIfPayload {
  approaches: { id: string; short: string; label: string }[]
  today: WhatIfBlock
  scenario: WhatIfBlock & { growth_pct: number; loss_approach: string | null; loss_pct: number }
  benefits: Benefits
  curve: Row[]
  practical_x: number
  chart: Figure
}

// --------------------------------------------------------------- map

export interface CameraLocation {
  id: string
  name: string
  short: string
  mode: 'junction' | 'area'
  lat: number | null
  lon: number | null
  verified: boolean
  mean_congestion: number | null
  los: Los | null
}

export interface LocationsPayload {
  cameras: CameraLocation[]
  note: string
  bounds: { lat: [number, number]; lon: [number, number] }
}
