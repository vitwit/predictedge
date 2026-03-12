import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendUrl = process.env.BACKEND_PROXY_TARGET || 'http://localhost:8888'
const backendWs = backendUrl.replace(/^http/, 'ws')
const frontendPort = parseInt(process.env.FRONTEND_PORT || '3000', 10)

export default defineConfig({
  plugins: [react()],
  server: {
    port: frontendPort,
    proxy: {
      '/api': backendUrl,
      '/health': backendUrl,
      '/ws': {
        target: backendWs,
        ws: true,
      },
    },
  },
})
