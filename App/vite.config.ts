/// <reference types="vitest/config" />
import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import svgr from 'vite-plugin-svgr'

// Local preview helper: when PREVIEW_BACKEND is set (e.g. the live Fly app),
// proxy the backend-served routes so localhost behaves like the single-origin
// deploy — real clips, real render, no local backend needed. No effect on builds.
const backend = process.env.PREVIEW_BACKEND
const proxy = backend
  ? Object.fromEntries(
      ['/api', '/data', '/videos', '/vibe.mp4'].map((p) => [
        p,
        { target: backend, changeOrigin: true, secure: true },
      ]),
    )
  : undefined

export default defineConfig({
  plugins: [react(), svgr()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: { proxy },
  test: {
    setupFiles: ['./src/test/setup.ts'],
  },
})
