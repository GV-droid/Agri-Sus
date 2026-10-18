import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import 'maplibre-gl/dist/maplibre-gl.css'
import { setWorkerUrl } from 'maplibre-gl'
// maplibre-gl v6 resolves its worker at runtime relative to its own module URL, which Vite cannot see;
// emit the worker as a hashed asset and point maplibre at it, otherwise the basemap never renders.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import './index.css'
import App from './App.tsx'

setWorkerUrl(maplibreWorkerUrl)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
