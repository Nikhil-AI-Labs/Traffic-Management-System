import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the FastAPI server (python src/api.py) runs on :8000 and the
// Vite dev server proxies /api to it, so the front end always calls a
// same-origin /api. In production FastAPI serves the built files itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  build: {
    chunkSizeWarningLimit: 4000,
    rollupOptions: {
      output: {
        // Plotly is ~3.5 MB; keep it in its own chunk so the shell, the
        // navigation and every non-chart view load without waiting for it.
        manualChunks(id) {
          if (id.includes('plotly')) return 'plotly'
          // Leaflet only travels with the map views.
          if (id.includes('leaflet')) return 'leaflet'
          if (id.includes('node_modules')) return 'vendor'
        },
      },
    },
  },
})
