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
        // Timeout settings to prevent hanging when backend is slow/unresponsive
        timeout: 15000, // 15 second timeout for proxy to establish connection
        proxyTimeout: 15000, // 15 second timeout for proxy to receive response
        // Configure proxy with proper error handling
        configure: (proxy, options) => {
          // Handle proxy errors - return 502 to browser instead of hanging
          proxy.on('error', (err, req, res) => {
            console.error('[vite-proxy] Proxy error:', err.message);
            if (!res.headersSent) {
              res.writeHead(502, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({
                detail: `Backend unavailable: ${err.message}`
              }));
            }
          });

          // Log proxy requests for debugging
          proxy.on('proxyReq', (proxyReq, req) => {
            console.log('[vite-proxy] Proxying:', req.method, req.url);
          });

          // Handle proxy response timeout
          proxy.on('proxyRes', (proxyRes, req, res) => {
            console.log('[vite-proxy] Response:', proxyRes.statusCode, req.url);
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
