// The only module that imports Plotly. It is loaded lazily (see Chart.tsx), so
// the ~3.5 MB bundle is fetched once, on the first view that draws a chart.

import Plotly from 'plotly.js-dist-min'
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
