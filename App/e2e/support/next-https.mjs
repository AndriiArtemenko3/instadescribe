import { execFileSync, spawn } from 'node:child_process'
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const supportDirectory = path.dirname(fileURLToPath(import.meta.url))
const appDirectory = path.resolve(supportDirectory, '../..')
const standaloneDirectory = path.join(appDirectory, '.next/standalone/App')
const standaloneServer = path.join(standaloneDirectory, 'server.js')

function port(name, fallback) {
  const raw = process.env[name] ?? String(fallback)
  if (!/^\d{2,5}$/.test(raw)) throw new Error(`${name} must be a TCP port`)
  const parsed = Number(raw)
  if (parsed < 1024 || parsed > 65_535) throw new Error(`${name} must be an unprivileged TCP port`)
  return parsed
}

if (!existsSync(path.join(appDirectory, '.next/BUILD_ID')) || !existsSync(standaloneServer)) {
  throw new Error('Next production build is missing; run `npm run build:next -w App` first')
}

// Mirror the two explicit asset copies in App/Dockerfile, then execute the
// exact standalone server entry point used by the beta container.
for (const [source, destination] of [
  [path.join(appDirectory, '.next/static'), path.join(standaloneDirectory, '.next/static')],
  [path.join(appDirectory, 'public'), path.join(standaloneDirectory, 'public')],
]) {
  rmSync(destination, { recursive: true, force: true })
  cpSync(source, destination, { recursive: true })
}

const externalPort = port('PLAYWRIGHT_APP_PORT', 3217)
const nextPort = port('PLAYWRIGHT_NEXT_PORT', 3218)
if (externalPort === nextPort) throw new Error('Playwright proxy and Next ports must differ')

const externalAuthority = `127.0.0.1:${externalPort}`
const externalOrigin = `https://${externalAuthority}`
const certificateDirectory = mkdtempSync(path.join(tmpdir(), 'instadescribe-playwright-'))
const keyPath = path.join(certificateDirectory, 'localhost.key')
const certificatePath = path.join(certificateDirectory, 'localhost.crt')

try {
  execFileSync('openssl', [
    'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-nodes',
    '-keyout', keyPath,
    '-out', certificatePath,
    '-days', '1',
    '-subj', '/CN=127.0.0.1',
    '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
  ], { stdio: 'ignore' })
} catch (error) {
  rmSync(certificateDirectory, { recursive: true, force: true })
  throw new Error('OpenSSL is required for the local production HTTPS boundary', { cause: error })
}

const nextProcess = spawn(
  process.execPath,
  [standaloneServer],
  {
    cwd: standaloneDirectory,
    env: {
      ...process.env,
      APP_ORIGIN: externalOrigin,
      HOSTNAME: '127.0.0.1',
      NEXT_TELEMETRY_DISABLED: '1',
      NODE_ENV: 'production',
      PORT: String(nextPort),
    },
    stdio: 'inherit',
  },
)

const proxyServer = https.createServer({
  key: readFileSync(keyPath),
  cert: readFileSync(certificatePath),
}, (incoming, outgoing) => {
  const headers = {
    ...incoming.headers,
    host: externalAuthority,
    'x-forwarded-host': externalAuthority,
    'x-forwarded-proto': 'https',
  }
  delete headers.connection

  const upstream = http.request({
    hostname: '127.0.0.1',
    port: nextPort,
    path: incoming.url,
    method: incoming.method,
    headers,
  }, (response) => {
    const responseHeaders = { ...response.headers }
    const location = responseHeaders.location
    if (typeof location === 'string') {
      responseHeaders.location = location
        .replace(`http://${externalAuthority}`, externalOrigin)
        .replace(`http://127.0.0.1:${nextPort}`, externalOrigin)
    }
    outgoing.writeHead(response.statusCode ?? 502, responseHeaders)
    response.pipe(outgoing)
  })
  upstream.on('error', () => {
    if (!outgoing.headersSent) outgoing.writeHead(502, { 'Content-Type': 'text/plain' })
    outgoing.end('Next production server is not ready')
  })
  incoming.pipe(upstream)
})

proxyServer.listen(externalPort, '127.0.0.1', () => {
  process.stdout.write(`Playwright HTTPS boundary listening on ${externalOrigin}\n`)
})

let shuttingDown = false
function shutdown(exitCode = 0) {
  if (shuttingDown) return
  shuttingDown = true
  proxyServer.close()
  if (!nextProcess.killed) nextProcess.kill('SIGTERM')
  rmSync(certificateDirectory, { recursive: true, force: true })
  process.exitCode = exitCode
}

process.once('SIGINT', () => shutdown(130))
process.once('SIGTERM', () => shutdown(143))
nextProcess.once('error', () => shutdown(1))
nextProcess.once('exit', (code, signal) => {
  if (!shuttingDown) {
    process.stderr.write(`Next production server exited unexpectedly (${signal ?? code ?? 'unknown'})\n`)
    shutdown(code ?? 1)
  }
})
