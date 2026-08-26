import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Vite only reads .env from this file's own directory by default —
  // the repo's real .env lives one level up (repo root), same file the
  // backend already reads via its own env_file = "../.env" (see
  // app/config.py). Without this, VITE_-prefixed vars (VITE_PRODUCT_NAME,
  // VITE_CLIENT_DISPLAY_NAME, VITE_API_BASE_URL) silently never load in
  // local dev, always falling back to their generic defaults — confirmed
  // directly: the login page/sidebar showed "Research Tool" instead of
  // the real configured name until this was set.
  envDir: '..',
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
