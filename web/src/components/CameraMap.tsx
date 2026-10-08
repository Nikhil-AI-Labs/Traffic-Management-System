// The camera network on a street map of Surat.
//
// Plain Leaflet rather than a React wrapper: markers are updated in place
// every second on the live view, and keeping each one alive (instead of
// rebuilding the layer) is what stops tooltips from flickering shut and lets
// an operator drag a marker while the feed keeps updating around it.
//
// Tiles come from OpenStreetMap, so the base map needs an internet
// connection. Without one the markers, colours and labels still render on a
// plain background - the map degrades to a schematic, it does not break.

import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef } from 'react'

export interface MapPoint {
  id: string
  label: string
  lat: number
  lon: number
  color: string
  detail?: string
  pulse?: boolean
  verified?: boolean
}

const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string)

/**
 * Hide labels that would collide. Several cameras sit within a kilometre of
 * each other around the station, and at the zoom that also fits GauravPath
 * their labels pile up. Labels are placed in priority order - the selected
 * camera, then any with a pulsing alert, then the rest - and a label that
 * would overlap one already placed is hidden; its dot and tooltip stay.
 */
function declutter(markers: Map<string, { m: L.Marker }>, priority: string[]) {
  const placed: DOMRect[] = []
  for (const id of priority) {
    const span = markers.get(id)?.m.getElement()?.querySelector<HTMLElement>('.cam-pin span')
    if (!span) continue
    span.style.visibility = ''
    const r = span.getBoundingClientRect()
    const hit = placed.some(
      (p) => r.left < p.right + 4 && r.right > p.left - 4 && r.top < p.bottom + 2 && r.bottom > p.top - 2,
    )
    if (hit) span.style.visibility = 'hidden'
    else placed.push(r)
  }
}

function pinHtml(p: MapPoint, selected: boolean) {
  const cls = ['cam-pin', selected ? 'sel' : '', p.pulse ? 'pulse' : '', p.verified === false ? 'approx' : '']
    .filter(Boolean)
    .join(' ')
  return `<div class="${cls}" style="--c:${p.color}"><i></i><span>${esc(p.label)}</span></div>`
}

export default function CameraMap({
  points,
  selected,
  onSelect,
  editable = false,
  onMove,
  height = 360,
}: {
  points: MapPoint[]
  selected?: string | null
  onSelect?: (id: string) => void
  editable?: boolean
  onMove?: (id: string, lat: number, lon: number) => void
  height?: number
}) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const markers = useRef(new Map<string, { m: L.Marker; html: string; dragging: boolean }>())
  const fitted = useRef(false)
  const priority = useRef<string[]>([])
  const handlers = useRef({ onSelect, onMove })

  useEffect(() => {
    handlers.current = { onSelect, onMove }
  }, [onSelect, onMove])

  useEffect(() => {
    if (!el.current) return
    const m = L.map(el.current, { scrollWheelZoom: false, attributionControl: true })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(m)
    m.setView([21.19, 72.82], 13)
    m.on('zoomend', () => declutter(markers.current, priority.current))
    map.current = m
    const all = markers.current
    return () => {
      m.remove()
      map.current = null
      all.clear()
      fitted.current = false
    }
  }, [])

  useEffect(() => {
    const m = map.current
    if (!m) return
    const seen = new Set<string>()
    for (const p of points) {
      seen.add(p.id)
      const html = pinHtml(p, p.id === selected)
      const have = markers.current.get(p.id)
      if (have) {
        if (have.html !== html) {
          have.m.setIcon(L.divIcon({ className: 'cam-pin-wrap', html, iconSize: [0, 0] }))
          have.html = html
        }
        // Follow the data, except under the operator's cursor mid-drag.
        if (!have.dragging) have.m.setLatLng([p.lat, p.lon])
        if (have.m.dragging) {
          if (editable) have.m.dragging.enable()
          else have.m.dragging.disable()
        }
        if (p.detail) have.m.setTooltipContent(p.detail)
        continue
      }
      const mk = L.marker([p.lat, p.lon], {
        icon: L.divIcon({ className: 'cam-pin-wrap', html, iconSize: [0, 0] }),
        draggable: editable,
        title: p.label,
        keyboard: true,
        riseOnHover: true,
      })
      const entry = { m: mk, html, dragging: false }
      mk.on('click', () => handlers.current.onSelect?.(p.id))
      mk.on('dragstart', () => {
        entry.dragging = true
      })
      mk.on('dragend', () => {
        entry.dragging = false
        const ll = mk.getLatLng()
        handlers.current.onMove?.(p.id, ll.lat, ll.lng)
      })
      if (p.detail) mk.bindTooltip(p.detail, { direction: 'top', offset: [0, -16] })
      mk.addTo(m)
      markers.current.set(p.id, entry)
    }
    for (const [id, { m: mk }] of markers.current) {
      if (!seen.has(id)) {
        mk.remove()
        markers.current.delete(id)
      }
    }
    if (!fitted.current && points.length) {
      m.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lon] as [number, number])), {
        padding: [48, 48],
        maxZoom: 15,
      })
      fitted.current = true
    }
    priority.current = [
      ...points.filter((p) => p.id === selected),
      ...points.filter((p) => p.id !== selected && p.pulse),
      ...points.filter((p) => p.id !== selected && !p.pulse),
    ].map((p) => p.id)
    declutter(markers.current, priority.current)
  }, [points, selected, editable])

  return <div ref={el} className="map" style={{ height }} role="region" aria-label="Map of the camera network" />
}
