import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { lazy, StrictMode, Suspense, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import { ErrorState, PageSkeleton } from './components/ui'
import { useApi } from './lib/api'
import { ThemeProvider } from './lib/theme'
import type { Meta } from './lib/types'
import './styles.css'

const Network = lazy(() => import('./pages/Network'))
const Site = lazy(() => import('./pages/Site'))
const Signal = lazy(() => import('./pages/Signal'))
const Bottlenecks = lazy(() => import('./pages/Bottlenecks'))
const Outlook = lazy(() => import('./pages/Outlook'))
const Analyse = lazy(() => import('./pages/Analyse'))
const Calibration = lazy(() => import('./pages/Calibration'))
const Method = lazy(() => import('./pages/Method'))
const Validation = lazy(() => import('./pages/fluid/Validation'))
const Scene = lazy(() => import('./pages/fluid/Scene'))
const Pressure = lazy(() => import('./pages/fluid/Pressure'))
const HeadToHead = lazy(() => import('./pages/fluid/HeadToHead'))
const MpMethod = lazy(() => import('./pages/fluid/MpMethod'))

const NO_RETRY = new Set([404, 409, 415, 422])

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The data only changes when someone re-measures or calibrates, and
      // those flows invalidate what they touch, so there is no need to
      // refetch on every window focus.
      staleTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (n, err) => n < 1 && !NO_RETRY.has((err as { status?: number }).status ?? 0),
    },
  },
})

/** Land on whichever track the operator last chose. */
function Home() {
  const meta = useApi<Meta>('/meta')
  if (meta.isLoading) return <PageSkeleton />
  return <Navigate to={meta.data?.mode === 'max_pressure' ? '/fluid/validation' : '/surat/network'} replace />
}

function NotFound() {
  return <ErrorState error={new Error('There is no view at this address.')} />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Home />} />
              <Route path="surat">
                <Route index element={<Navigate to="network" replace />} />
                <Route path="network" element={<Page el={<Network />} />} />
                <Route path="site" element={<Page el={<Site />} />} />
                <Route path="site/:cam" element={<Page el={<Site />} />} />
                <Route path="signal" element={<Page el={<Signal />} />} />
                <Route path="bottlenecks" element={<Page el={<Bottlenecks />} />} />
                <Route path="outlook" element={<Page el={<Outlook />} />} />
                <Route path="analyse" element={<Page el={<Analyse />} />} />
                <Route path="calibration" element={<Page el={<Calibration />} />} />
                <Route path="method" element={<Page el={<Method />} />} />
              </Route>
              <Route path="fluid">
                <Route index element={<Navigate to="validation" replace />} />
                <Route path="validation" element={<Page el={<Validation />} />} />
                <Route path="scene" element={<Page el={<Scene />} />} />
                <Route path="pressure" element={<Page el={<Pressure />} />} />
                <Route path="head-to-head" element={<Page el={<HeadToHead />} />} />
                <Route path="method" element={<Page el={<MpMethod />} />} />
              </Route>
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
)

// The shell stays mounted while a page's code loads; only the content area
// shows the skeleton.
function Page({ el }: { el: ReactNode }) {
  return <Suspense fallback={<PageSkeleton />}>{el}</Suspense>
}
