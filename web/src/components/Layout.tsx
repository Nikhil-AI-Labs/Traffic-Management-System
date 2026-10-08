import {
  Aperture,
  BadgeCheck,
  BookOpen,
  Camera,
  Clapperboard,
  Gauge,
  GitCompareArrows,
  Lock,
  LogOut,
  Map as MapIcon,
  Menu,
  Monitor,
  Moon,
  Radio,
  Ruler,
  ScrollText,
  ShieldCheck,
  Siren,
  Sun,
  TrafficCone,
  TrendingUp,
  TriangleAlert,
  UserRound,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { post, useApi } from '../lib/api'
import { AUTH_REQUIRED, signOut, useOperator } from '../lib/auth'
import { int } from '../lib/format'
import { useTheme, type ThemeChoice } from '../lib/theme'
import type { Meta } from '../lib/types'
import SignInDialog from './SignInDialog'
import { Chip } from './ui'

export type Track = 'surat' | 'fluid'

interface View {
  path: string
  label: string
  subtitle: string
  icon: LucideIcon
}

export const VIEWS: Record<Track, View[]> = {
  surat: [
    { path: '/surat/live', label: 'Live monitor', subtitle: 'Every camera as it happens — replay or real-time detection', icon: Radio },
    { path: '/surat/network', label: 'Network', subtitle: 'Which site is worst, and how the network is behaving', icon: MapIcon },
    { path: '/surat/site', label: 'Site detail', subtitle: 'One camera in depth — zones, scene, trends, diagnostics', icon: Camera },
    { path: '/surat/signal', label: 'Signal timing', subtitle: 'What the signal should do differently, and why', icon: TrafficCone },
    { path: '/surat/bottlenecks', label: 'Bottlenecks', subtitle: 'Sustained problems, with the evidence that raised them', icon: TriangleAlert },
    { path: '/surat/incidents', label: 'Incidents', subtitle: 'Every alert, who picked it up, and what was done', icon: Siren },
    { path: '/surat/outlook', label: 'Outlook', subtitle: 'What is trending worse over the next minute', icon: TrendingUp },
    { path: '/surat/analyse', label: 'Analyse footage', subtitle: 'Run the pipeline on a clip or still it has never seen', icon: Clapperboard },
    { path: '/surat/calibration', label: 'Calibration', subtitle: 'Pin pixels to metres, once, and promote every estimate', icon: Ruler },
    { path: '/surat/method', label: 'How it works', subtitle: 'The reasoning behind every number on the other views', icon: BookOpen },
  ],
  fluid: [
    { path: '/fluid/validation', label: 'Validation', subtitle: 'Max-pressure replayed against three real signals', icon: BadgeCheck },
    { path: '/fluid/scene', label: 'Scene', subtitle: 'The intersection itself — aerial, zones, movement, video', icon: Aperture },
    { path: '/fluid/pressure', label: 'Phase pressure', subtitle: 'The phase ranking at one instant, from measured queues', icon: Gauge },
    { path: '/fluid/head-to-head', label: 'Head-to-head', subtitle: 'Webster against max-pressure in a controlled network', icon: GitCompareArrows },
    { path: '/fluid/method', label: 'Method', subtitle: 'What the controller needs, and why Surat cannot supply it', icon: ScrollText },
  ],
}

function currentView(pathname: string): { track: Track; view: View } {
  const track: Track = pathname.startsWith('/fluid') ? 'fluid' : 'surat'
  const view = VIEWS[track].find((v) => pathname.startsWith(v.path)) ?? VIEWS[track][0]
  return { track, view }
}

function ThemeToggle() {
  const { choice, setChoice } = useTheme()
  const order: ThemeChoice[] = ['system', 'light', 'dark']
  const Icon = choice === 'light' ? Sun : choice === 'dark' ? Moon : Monitor
  const next = order[(order.indexOf(choice) + 1) % order.length]
  return (
    <button
      className="btn btn-icon btn-ghost"
      onClick={() => setChoice(next)}
      title={`Theme: ${choice} — switch to ${next}`}
      aria-label={`Theme: ${choice}. Switch to ${next}.`}
    >
      <Icon size={18} />
    </button>
  )
}

export default function Layout() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const { track, view } = currentView(pathname)
  // The drawer is open only on the page it was opened from, so navigating
  // closes it without an effect.
  const [openOn, setOpenOn] = useState<string | null>(null)
  const open = openOn === pathname
  const setOpen = (f: (o: boolean) => boolean) => setOpenOn(f(open) ? pathname : null)
  const meta = useApi<Meta>('/meta', { staleTime: 15_000, refetchInterval: 20_000 })
  const m = meta.data
  const op = useOperator()
  const [signInOpen, setSignInOpen] = useState(false)
  const closeSignIn = useCallback(() => setSignInOpen(false), [])

  useEffect(() => {
    document.title = `${view.label} · Surat Traffic Intelligence`
  }, [view.label])

  // Any request refused for want of sign-in opens the dialog.
  useEffect(() => {
    const open = () => setSignInOpen(true)
    window.addEventListener(AUTH_REQUIRED, open)
    return () => window.removeEventListener(AUTH_REQUIRED, open)
  }, [])

  const authRequired = !!m?.auth.required
  const openCritical = m?.incidents?.open_critical ?? 0

  const switchTrack = (t: Track) => {
    if (t === track) return
    // The strategy is a standing operator decision, so it is persisted
    // server-side as well as reflected in the URL.
    post('/settings/mode', { mode: t === 'surat' ? 'webster' : 'max_pressure' }).catch(() => {})
    navigate(VIEWS[t][0].path)
  }

  const analysed = m?.cameras.filter((c) => c.analysed) ?? []
  const fluidOn = !!m?.fluid.available
  const nRec = m?.fluid.recordings.length ?? 0
  const nDataset = m?.cameras.filter((c) => c.dataset).length ?? 7

  return (
    <div className="app">
      <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Navigation">
        <div className="sidebar-scroll">
          <NavLink to="/" className="brand">
            <span className="brand-mark" aria-hidden>
              <i style={{ background: '#d03b3b' }} />
              <i style={{ background: '#fab219' }} />
              <i style={{ background: '#0ca30c' }} />
            </span>
            <span>
              <div className="brand-t">Surat Traffic Intelligence</div>
              <div className="brand-s">PS-08 · E-Rakshak Hackathon 2026</div>
            </span>
          </NavLink>

          <div className="side-h">Control strategy</div>
          <div className="track-switch" role="group" aria-label="Control strategy">
            <button aria-pressed={track === 'surat'} onClick={() => switchTrack('surat')}>
              <span className="ts-t">Webster</span>
              <span className="ts-s">Surat CCTV</span>
            </button>
            <button aria-pressed={track === 'fluid'} onClick={() => switchTrack('fluid')}>
              <span className="ts-t">Max-pressure</span>
              <span className="ts-s">FLUID drone</span>
            </button>
          </div>

          <div className="side-h">Views</div>
          <nav className="nav">
            {VIEWS[track].map((v) => (
              <div key={v.path}>
                <NavLink to={v.path} className={({ isActive }) => (isActive ? 'active' : '')}>
                  <v.icon size={18} />
                  {v.label}
                  {v.path === '/surat/incidents' && openCritical > 0 && (
                    <span className="nav-badge" aria-label={`${openCritical} open critical incidents`}>
                      {openCritical}
                    </span>
                  )}
                </NavLink>
                {v.path === '/surat/site' && pathname.startsWith('/surat/site') && analysed.length > 0 && (
                  <div className="sub">
                    {analysed.map((c) => (
                      <NavLink
                        key={c.id}
                        to={`/surat/site/${encodeURIComponent(c.id)}`}
                        className={({ isActive }) => (isActive ? 'active' : '')}
                      >
                        {c.short}
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </nav>

          {track === 'surat' && m?.recommendation && (
            <>
              <div className="side-h">Recommendation</div>
              <div className="side-card">
                <Chip color="var(--good)">Webster for this network</Chip>
                <div className="mt-1">{m.recommendation.reasons[0]}</div>
              </div>
            </>
          )}

          <div className="side-h">System</div>
          {track === 'surat' ? (
            <div className="side-card">
              <b>{analysed.length}</b> cameras analysed · <b>{int(m?.metrics.windows)}</b> windows ·{' '}
              <b>{int(m?.metrics.minutes)}</b> min of footage
              <br />
              <b>{m?.calibrated ?? 0}</b> of {nDataset} cameras have a measured scale.
              <div className="mt-1">
                Detection: YOLO11-S on UVH-26 + ByteTrack · density in PCU (IRC:106-1990) · Webster's method ·
                absolute edge-density occupancy.
              </div>
            </div>
          ) : (
            <div className="side-card">
              {fluidOn ? (
                <>
                  <b>{nRec}</b> drone recordings · <b>{new Set(m?.fluid.recordings.map((r) => r.site)).size}</b>{' '}
                  signalised intersections
                  {m?.fluid.totals && (
                    <>
                      {' '}
                      · <b>{int(m.fluid.totals.minutes)}</b> min · <b>{int(m.fluid.totals.vehicles)}</b> vehicles
                    </>
                  )}
                  <div className="mt-1">
                    Queues from metre-scale trajectories · turn ratios counted per vehicle · per-second signal ground
                    truth · Varaiya (2013) max-pressure.
                  </div>
                </>
              ) : (
                <>
                  FLUID dataset not found. Set <code>FLUID_ROOT</code> to where it was extracted.
                </>
              )}
            </div>
          )}
        </div>
        <div className="side-foot">
          {m?.detector.ready ? `Detector ready${m.detector.fast ? ` · live: ${m.detector.fast}` : ''}` : 'Detector weights not downloaded'} · API{' '}
          {meta.isError ? 'offline' : 'connected'}
        </div>
      </aside>
      <div className={`scrim ${open ? 'open' : ''}`} onClick={() => setOpen(() => false)} />

      <div className="main">
        <header className="topbar">
          <div className="topbar-in">
            <div className="topbar-l">
              <button className="btn btn-icon btn-ghost menu-btn" onClick={() => setOpen((o) => !o)} aria-label="Menu">
                {open ? <X size={20} /> : <Menu size={20} />}
              </button>
              <div>
                <div className="eyebrow">
                  {track === 'surat' ? "Surat CCTV · Webster's method" : 'FLUID drone data · max-pressure validation'}
                </div>
                <h1 className="title">{view.label}</h1>
                <div className="subtitle">{view.subtitle}</div>
              </div>
            </div>
            <div className="topbar-r">
              {track === 'surat' ? (
                <>
                  {analysed.length > 0 && <Chip color="var(--s1)">{analysed.length} cameras</Chip>}
                  {m && (
                    <Chip color={m.calibrated ? 'var(--good)' : 'var(--warning)'}>
                      {m.calibrated}/{nDataset} scale measured
                    </Chip>
                  )}
                </>
              ) : (
                m && (
                  <Chip color={fluidOn ? 'var(--good)' : 'var(--warning)'}>
                    {fluidOn ? `${nRec} recordings · 3 intersections` : 'FLUID not found'}
                  </Chip>
                )
              )}
              {authRequired && op.signedIn ? (
                <span className="row" style={{ gap: 2 }}>
                  <Chip color="var(--good)">
                    <ShieldCheck size={13} /> {op.name || 'Operator'}
                  </Chip>
                  <button className="btn btn-icon btn-ghost" onClick={signOut} title="Sign out" aria-label="Sign out">
                    <LogOut size={17} />
                  </button>
                </span>
              ) : (
                <button
                  className="btn btn-sm btn-ghost op-btn"
                  onClick={() => setSignInOpen(true)}
                  title={authRequired ? 'Sign in to act on incidents, run detection and calibrate' : 'Your name for the incident log'}
                >
                  {authRequired ? <Lock size={15} /> : <UserRound size={15} />}
                  {authRequired ? 'Operator sign-in' : op.name || 'Set your name'}
                </button>
              )}
              <ThemeToggle />
            </div>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
      {signInOpen && <SignInDialog required={authRequired} onClose={closeSignIn} />}
    </div>
  )
}
