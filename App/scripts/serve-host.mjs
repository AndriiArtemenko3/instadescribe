#!/usr/bin/env node
// Host-faithful static server for dist-portfolio-demo.
//
// Implements the DOCUMENTED Cloudflare Pages semantics
// (https://developers.cloudflare.com/pages/configuration/headers/) so
// statuses and headers can be asserted locally:
//   - `_redirects` rewrites (only `/onboarding → /index.html 200` is used);
//   - a top-level `404.html` served with a genuine 404 status for unknown
//     paths (its presence disables Pages' implicit SPA fallback);
//   - `_headers`: EVERY matching rule applies; duplicate header names are
//     COMMA-JOINED (not overwritten); a `! Header-Name` line detaches the
//     header from previous matches;
//   - `_headers` and `_redirects` are configuration, not assets — they are
//     never served (genuine 404);
//   - binds to loopback (127.0.0.1) by default;
//   - basic single-range support so media elements can actually play/seek.
//
// This is an emulation of documented behavior for verification — the final
// authority remains the real host, which is deliberately not deployed here.
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { extname, join, normalize } from 'node:path'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist-portfolio-demo')
const portArg = process.argv.indexOf('--port')
const PORT = portArg !== -1 ? Number(process.argv[portArg + 1]) : Number(process.env.PORT ?? 4174)

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
  '.vtt': 'text/vtt; charset=utf-8',
  '.mp4': 'video/mp4',
  '.mp3': 'audio/mpeg',
  '.jpg': 'image/jpeg',
  '.avif': 'image/avif',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
  '.ico': 'image/x-icon',
}

function parseHeaders() {
  const file = join(DIST, '_headers')
  if (!existsSync(file)) return []
  const blocks = []
  let current = null
  for (const raw of readFileSync(file, 'utf8').split('\n')) {
    const line = raw.replace(/\s+$/, '')
    if (!line || line.trimStart().startsWith('#')) continue
    if (!/^\s/.test(line)) {
      current = { pattern: line.trim(), headers: [] }
      blocks.push(current)
    } else if (current) {
      const trimmed = line.trim()
      if (trimmed.startsWith('!')) {
        current.headers.push({ detach: trimmed.slice(1).trim() })
        continue
      }
      const idx = line.indexOf(':')
      if (idx > 0) {
        current.headers.push({ name: line.slice(0, idx).trim(), value: line.slice(idx + 1).trim() })
      }
    }
  }
  return blocks
}

function parseRedirects() {
  const file = join(DIST, '_redirects')
  if (!existsSync(file)) return []
  return readFileSync(file, 'utf8')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
    .map((l) => {
      const [from, to, status] = l.split(/\s+/)
      return { from, to, status: Number(status ?? 200) }
    })
}

const HEADER_BLOCKS = parseHeaders()
const REDIRECTS = parseRedirects()

function patternMatches(pattern, path) {
  // Cloudflare `_headers` globs: '*' spans any characters.
  const re = new RegExp(
    '^' + pattern.split('*').map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$',
  )
  return re.test(path)
}

function headersFor(path) {
  // Cloudflare semantics: all matching rules apply in file order; duplicate
  // names comma-join; `!Name` detaches the header accumulated so far.
  const out = {}
  for (const block of HEADER_BLOCKS) {
    if (!patternMatches(block.pattern, path)) continue
    for (const entry of block.headers) {
      if (entry.detach) {
        delete out[entry.detach.toLowerCase()]
        continue
      }
      const key = entry.name.toLowerCase()
      out[key] = out[key] ? `${out[key]}, ${entry.value}` : entry.value
    }
  }
  return out
}

function send(res, status, filePath, extraHeaders, rangeHeader) {
  const stat = statSync(filePath)
  const type = MIME[extname(filePath)] ?? 'application/octet-stream'
  const headers = { 'Content-Type': type, 'Accept-Ranges': 'bytes', ...extraHeaders }
  const range = rangeHeader && /^bytes=(\d*)-(\d*)$/.exec(rangeHeader)
  if (range && status === 200) {
    const start = range[1] === '' ? Math.max(0, stat.size - Number(range[2])) : Number(range[1])
    const end = range[2] === '' || range[1] === '' ? stat.size - 1 : Math.min(Number(range[2]), stat.size - 1)
    if (start <= end && start < stat.size) {
      res.writeHead(206, {
        ...headers,
        'Content-Range': `bytes ${start}-${end}/${stat.size}`,
        'Content-Length': end - start + 1,
      })
      createReadStream(filePath, { start, end }).pipe(res)
      return
    }
  }
  res.writeHead(status, { ...headers, 'Content-Length': stat.size })
  createReadStream(filePath).pipe(res)
}

const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://localhost:${PORT}`)
  let path = decodeURIComponent(url.pathname)
  if (path.endsWith('/') && path !== '/') path = path.slice(0, -1)
  if (path === '/') path = '/index.html'

  const safe = normalize(path).replace(/^(\.\.[/\\])+/, '')
  const filePath = join(DIST, safe)
  const headers = headersFor(path === '/index.html' ? '/' : path)

  // Cloudflare parses these as configuration and never serves them.
  if (path === '/_headers' || path === '/_redirects') {
    send(res, 404, join(DIST, '404.html'), headersFor(path))
    return
  }

  if (filePath.startsWith(DIST) && existsSync(filePath) && statSync(filePath).isFile()) {
    send(res, 200, filePath, headers, req.headers.range)
    return
  }

  const rule = REDIRECTS.find((r) => r.from === path)
  if (rule) {
    send(res, rule.status, join(DIST, rule.to), headersFor(rule.from), req.headers.range)
    return
  }

  // Top-level 404.html → genuine not-found boundary (no SPA fall-through).
  send(res, 404, join(DIST, '404.html'), headersFor(path))
})

if (!existsSync(join(DIST, 'index.html'))) {
  console.error('dist-portfolio-demo/ is missing or unbuilt — run `npm run build:portfolio-demo` first.')
  process.exit(1)
}

const HOST = process.env.HOST ?? '127.0.0.1' // loopback by default
server.listen(PORT, HOST, () => {
  console.log(`host-faithful server: http://${HOST}:${PORT} (dist-portfolio-demo)`)
})
