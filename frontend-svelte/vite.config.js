import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    strictPort: true,
    // Explicit HMR settings to avoid connection issues
    hmr: {
      port: 5173,
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // Timeout settings to prevent indefinite hangs when backend is unavailable
        timeout: 10000,       // 10 second timeout for incoming request
        proxyTimeout: 10000,  // 10 second timeout for proxy connection
        configure: (proxy, options) => {
          // Handle proxy errors gracefully instead of hanging
          proxy.on('error', (err, req, res) => {
            console.error('[vite proxy] Error connecting to backend:', err.message);
            if (!res.headersSent) {
              res.writeHead(502, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({ detail: 'Backend unavailable - is the server running on port 8000?' }));
            }
          });
          // Log proxy timeouts
          proxy.on('proxyReq', (proxyReq, req, res) => {
            // Set a safety timeout on the response
            res.setTimeout(10000, () => {
              console.error('[vite proxy] Request timeout for:', req.url);
              if (!res.headersSent) {
                res.writeHead(504, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ detail: 'Backend request timed out' }));
              }
            });
          });
        },
      },
    },
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
