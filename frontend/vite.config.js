import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/webui/',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/v1': 'http://127.0.0.1:8010',
      '/docs': 'http://127.0.0.1:8010',
      '/openapi.json': 'http://127.0.0.1:8010',
      '/healthz': 'http://127.0.0.1:8010',
      '/health': 'http://127.0.0.1:8010',
      '/graphs': 'http://127.0.0.1:8010',
      '/graph': 'http://127.0.0.1:8010',
    },
  },
  build: {
    outDir: '../webui',
    emptyOutDir: true,
    minify: false,
    cssMinify: false,
  },
})

