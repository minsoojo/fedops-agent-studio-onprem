import path from 'node:path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  server: {
    host: '0.0.0.0',
    port: Number(process.env.PORT || 8443),
    strictPort: true,
    proxy: {
      '/api': process.env.STUDIO_DEV_API_URL || 'http://127.0.0.1:24369',
      '/auth': process.env.STUDIO_DEV_API_URL || 'http://127.0.0.1:24369',
      '/socket.io': {
        target: process.env.STUDIO_DEV_API_URL || 'http://127.0.0.1:24369',
        ws: true,
      },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: Number(process.env.PORT || 8443),
  },
})
