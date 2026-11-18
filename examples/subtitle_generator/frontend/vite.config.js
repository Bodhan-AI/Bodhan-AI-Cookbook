import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The browser calls /api/...; Vite forwards those requests to the FastAPI backend.
// Same origin for the browser, so no CORS setup and no API URL to configure.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
