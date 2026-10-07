# Surat Traffic Intelligence — React dashboard

The front end for the FastAPI server in `../src/api.py`. It holds no traffic
logic: every figure, table and chart comes from the API, which computes them
with the same Python modules the verification suite checks.

```bash
npm install
npm run dev      # http://localhost:5173 — proxies /api to http://127.0.0.1:8000
npm run build    # writes dist/, which `python src/api.py` serves from /
npm run lint
```

Start the API first (`python src/api.py` from the project root, with
`FLUID_ROOT` set if the FLUID dataset lives outside the project).

## Layout

| Path | What it is |
|---|---|
| `src/main.tsx` | Routes — `/surat/*` (Webster, Surat CCTV) and `/fluid/*` (max-pressure, FLUID) |
| `src/components/Layout.tsx` | Sidebar with the control-strategy switch, top bar, theme toggle, mobile drawer |
| `src/components/ui.tsx` | Display primitives: tiles, hero figures, chips, callouts, charts (Plotly, lazy-loaded) |
| `src/components/widgets.tsx` | Sortable tables, images with lightbox, alert feed, background-job progress |
| `src/pages/` | One file per view; `pages/fluid/` for the max-pressure track |
| `src/lib/` | API client, theme provider, number formatting, payload types |
| `src/styles.css` | Design tokens and components — the palette from `src/theme.py`, light and dark |

Charts arrive as Plotly figure JSON built in the current theme; switching
theme refetches them in the other palette. Every chart has a table view, and
every figure carrying the uncalibrated pixel scale is marked `~`, exactly as in
the Streamlit dashboard.
