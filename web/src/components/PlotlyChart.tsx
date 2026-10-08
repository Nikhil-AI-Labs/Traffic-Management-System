// The only module that imports Plotly. It is loaded lazily (see Chart in
// ui.tsx), so the bundle is fetched once, on the first view that draws a chart.
//
// The *basic* distribution: scatter and bar traces plus the core (subplots,
// shapes, annotations). Every figure charts.py builds is go.Scatter or go.Bar,
// so the full 4.6 MB build was shipping 3.6 MB of trace types nothing used.
// verify_system.py fails if charts.py ever reaches for another trace type.

import Plotly from 'plotly.js-basic-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import type { Figure } from '../lib/types'

const Plot = createPlotlyComponent(Plotly)

const CONFIG = { displayModeBar: false, displaylogo: false, scrollZoom: false, responsive: true }

export default function PlotlyChart({ figure, label }: { figure: Figure; label?: string }) {
  // The server sizes the height to the data; the width always follows the
  // container, so drop any fixed width the figure carries.
  const layout: Record<string, unknown> = { ...figure.layout, autosize: true }
  delete layout.width
  return (
    <div role="img" aria-label={label}>
      <Plot
        data={figure.data as Plotly.Data[]}
        layout={layout as Partial<Plotly.Layout>}
        config={CONFIG}
        useResizeHandler
        style={{ width: '100%' }}
      />
    </div>
  )
}
