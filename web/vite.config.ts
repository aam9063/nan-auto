import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
// The gateway port is a local choice: 8000 is the documented default, but any
// other process may hold it. Override with GATEWAY_URL when it does.
const gateway = process.env.GATEWAY_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/v1': {
        target: gateway,
        changeOrigin: true,
      },
    },
  },
})
