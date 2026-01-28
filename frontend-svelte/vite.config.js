import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
//
// Architecture: Direct CORS-based requests (no proxy)
// -------------------------------------------------
// The Svelte frontend makes direct requests to the backend API.
// This avoids Vite's http-proxy which has known issues with hanging requests.
//
// The backend has CORS configured to allow requests from any origin in development.
// For production, update backend CORS to restrict to your domain.

export default defineConfig(({ mode }) => ({
  plugins: [svelte()],
  define: {
    // API base URL - injected at build time
    // Development: direct to backend on port 8000
    // Production: same-origin (served by FastAPI)
    '__API_BASE__': JSON.stringify(
      mode === 'production' ? '/api' : 'http://localhost:8000/api'
    ),
  },
  server: {
    port: 5173,
    strictPort: true,
    // Explicit HMR settings to avoid connection issues
    hmr: {
      port: 5173,
    },
    // No proxy needed - using direct CORS-based requests to backend
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  resolve: {
    alias: {
      '$lib': '/src/lib',
      '$components': '/src/components',
    },
  },
}))
