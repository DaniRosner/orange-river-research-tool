import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxies API calls to the local backend under the same origin as the
    // dev server itself, mirroring the production Caddy reverse proxy
    // (see frontend/Caddyfile) — this is what keeps the session cookie
    // same-site everywhere instead of just in production, so a browser's
    // cross-site cookie protections (Firefox's Total Cookie Protection,
    // Safari's ITP) never come into play.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
