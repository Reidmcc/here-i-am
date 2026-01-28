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

export default defineConfig({
  plugins: [svelte()],
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
})
