import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import svgr from 'vite-plugin-svgr'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'
import path from 'node:path'
import fs from 'node:fs'
import tailwindPortfolioConfig from './tailwind.portfolio-demo.config'

// Dedicated compile-time entry for the portfolio demo (see
// docs/portfolio-demo/INSTASCRIBE_LIVE_ONBOARDING_V1.md). Builds ONLY
// src/portfolio-demo/** into dist-portfolio-demo/ so the general application
// (auth, dashboard, upload, study, backend clients) is absent from the output —
// verified post-build by scripts/verify-portfolio-demo.mjs. The default
// `npm run build` is untouched.

const APP_DIR = path.dirname(new URL(import.meta.url).pathname)
const EXTRA_ASSETS = path.resolve(APP_DIR, 'portfolio-demo-assets')
const HOST_FILES = path.resolve(APP_DIR, 'portfolio-demo-host')
const OUT_DIR = path.resolve(APP_DIR, 'dist-portfolio-demo')

const MIME: Record<string, string> = {
  '.mp3': 'audio/mpeg',
  '.vtt': 'text/vtt',
}

function portfolioDemoPlugin(): Plugin {
  return {
    name: 'portfolio-demo',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0]
        // Demo-only static assets (baked narration, caption tracks) live outside
        // App/public so the default application build output stays identical.
        const candidate = path.join(EXTRA_ASSETS, url)
        if (
          url.startsWith('/data/') &&
          candidate.startsWith(EXTRA_ASSETS) &&
          fs.existsSync(candidate) &&
          fs.statSync(candidate).isFile()
        ) {
          res.setHeader('Content-Type', MIME[path.extname(candidate)] ?? 'application/octet-stream')
          fs.createReadStream(candidate).pipe(res)
          return
        }
        // History-API fallback for the demo's routes in dev.
        if (url === '/' || url === '/onboarding' || url.startsWith('/review/')) {
          req.url = '/portfolio-demo.html'
        }
        next()
      })
    },
    closeBundle() {
      const built = path.join(OUT_DIR, 'portfolio-demo.html')
      if (fs.existsSync(built)) fs.renameSync(built, path.join(OUT_DIR, 'index.html'))
      if (fs.existsSync(EXTRA_ASSETS)) fs.cpSync(EXTRA_ASSETS, OUT_DIR, { recursive: true })
      if (fs.existsSync(HOST_FILES)) fs.cpSync(HOST_FILES, OUT_DIR, { recursive: true })
      // App/public is shared with the default build; drop the pieces this demo
      // never references so no unused application asset ships (notably
      // system_info.json, which carries internal pipeline metadata).
      for (const unused of [
        'demo',
        'icons.svg',
        'data/sintel-blender-cc/system_info.json',
        'data/sintel-blender-cc/poster.avif',
      ]) {
        fs.rmSync(path.join(OUT_DIR, unused), { recursive: true, force: true })
      }
    },
  }
}

export default defineConfig({
  plugins: [react(), svgr(), portfolioDemoPlugin()],
  resolve: {
    alias: { '@': path.resolve(APP_DIR, './src') },
  },
  css: {
    postcss: {
      plugins: [tailwindcss(tailwindPortfolioConfig), autoprefixer()],
    },
  },
  build: {
    outDir: OUT_DIR,
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: path.resolve(APP_DIR, 'portfolio-demo.html'),
    },
  },
  preview: {
    port: 4174,
  },
})
