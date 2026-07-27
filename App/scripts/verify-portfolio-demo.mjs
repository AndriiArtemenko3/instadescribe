#!/usr/bin/env node
// Post-build verification for the dedicated portfolio-demo bundle:
//   1. forbidden-string audit — backend routes/endpoints, study/auth surfaces,
//      model hosts and secrets must be absent from every deployed text asset;
//   2. artifact hygiene — no source maps, no .env, host security files present;
//   3. size budget — total deploy package ≤ 21 MB; per-file report;
//   4. SHA-256 manifests — regenerates the deploy manifest and verifies the
//      committed fixture manifest still matches the repository files.
// Exits non-zero on any violation.
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync, existsSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, extname } from 'node:path'
import { fileURLToPath } from 'node:url'

const APP = join(dirname(fileURLToPath(import.meta.url)), '..')
const DIST = join(APP, 'dist-portfolio-demo')
const DOCS = join(APP, '..', 'docs', 'portfolio-demo')

if (!existsSync(DIST)) {
  console.error('dist-portfolio-demo/ not found — run `npm run build:portfolio-demo` first.')
  process.exit(1)
}

const MAX_TOTAL_BYTES = 21 * 1024 * 1024

// Strings that must never appear in the deployed text assets. Each entry is a
// [pattern, why] pair; patterns are literal substrings.
const FORBIDDEN = [
  ['/api/', 'backend API path'],
  ['api/log', 'telemetry endpoint'],
  ['api/study', 'study provisioning endpoint'],
  ['api/jobs', 'jobs endpoint'],
  ['api/providers', 'providers endpoint'],
  ['localhost:8765', 'dev backend origin'],
  ['openai.com', 'model-vendor host'],
  ['api.anthropic', 'model-vendor host'],
  ['VITE_STUDY_MODE', 'study flag'],
  ['studySessionId', 'study session state'],
  ['instascribe:', 'app localStorage namespace'],
  ['/forgot-password', 'auth route'],
  ['/register', 'auth route'],
  ['/login', 'auth route'],
  ['/dashboard', 'app route'],
  ['/upload', 'app route'],
  ['/settings', 'app route'],
  ['/usage', 'app route'],
  ['/providers', 'app route'],
  ['/tutorials', 'app route'],
  ['/study', 'study route'],
  ['demo1234', 'fake credential'],
  ['OPENAI_API_KEY', 'key variable name'],
  ['figure-00', 'dev review harness route'],
]

const walk = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)],
  )

const files = walk(DIST)
  .filter((f) => !f.endsWith('manifest.sha256'))
  .sort()
const textExt = new Set(['.js', '.css', '.html', '.txt', '.vtt', '.json', '.svg'])
let failures = 0

// 1 — forbidden strings.
for (const f of files) {
  if (!textExt.has(extname(f))) continue
  const content = readFileSync(f, 'utf8')
  for (const [needle, why] of FORBIDDEN) {
    if (content.includes(needle)) {
      console.error(`FORBIDDEN ${relative(DIST, f)}: contains "${needle}" (${why})`)
      failures++
    }
  }
}

// 2 — artifact hygiene.
for (const f of files) {
  if (f.endsWith('.map')) {
    console.error(`FORBIDDEN source map in deploy package: ${relative(DIST, f)}`)
    failures++
  }
  if (/\.env/.test(f)) {
    console.error(`FORBIDDEN env file in deploy package: ${relative(DIST, f)}`)
    failures++
  }
}
for (const required of ['_headers', '_redirects', 'robots.txt', 'index.html']) {
  if (!existsSync(join(DIST, required))) {
    console.error(`MISSING required host file: ${required}`)
    failures++
  }
}

// 3 — size report + budget.
let total = 0
const rows = files.map((f) => {
  const size = statSync(f).size
  total += size
  return [relative(DIST, f), size]
})
rows.sort((a, b) => b[1] - a[1])
console.log('\nDeploy package (largest first):')
for (const [name, size] of rows) {
  if (size > 50 * 1024) console.log(`  ${(size / 1024 / 1024).toFixed(2).padStart(6)} MB  ${name}`)
}
const smallCount = rows.filter(([, s]) => s <= 50 * 1024).length
console.log(`  … plus ${smallCount} files ≤ 50 KB`)
console.log(
  `TOTAL: ${(total / 1024 / 1024).toFixed(2)} MB of ${(MAX_TOTAL_BYTES / 1024 / 1024).toFixed(0)} MB budget (${files.length} files)`,
)
if (total > MAX_TOTAL_BYTES) {
  console.error('BUDGET EXCEEDED: deploy package is over 21 MB')
  failures++
}

// 4 — manifests. The deploy manifest is VERIFIED against the committed copy;
// pass UPDATE_MANIFEST=1 to intentionally refresh it after a reviewed change.
const sha256 = (p) => createHash('sha256').update(readFileSync(p)).digest('hex')
const manifest = files.map((f) => `${sha256(f)}  ${relative(DIST, f)}`).join('\n') + '\n'
writeFileSync(join(DIST, 'manifest.sha256'), manifest)
const committedManifest = join(DOCS, 'DEPLOY_MANIFEST.sha256')
if (process.env.UPDATE_MANIFEST === '1' || !existsSync(committedManifest)) {
  writeFileSync(committedManifest, manifest)
  console.log(`Deploy manifest UPDATED: ${files.length} entries → docs/portfolio-demo/DEPLOY_MANIFEST.sha256`)
} else if (readFileSync(committedManifest, 'utf8') !== manifest) {
  console.error(
    'DEPLOY MANIFEST MISMATCH: dist-portfolio-demo differs from docs/portfolio-demo/DEPLOY_MANIFEST.sha256.\n' +
      'If the change is intentional, re-run with UPDATE_MANIFEST=1 and review the diff.',
  )
  failures++
} else {
  console.log(`Deploy manifest verified: ${files.length} entries match the committed manifest.`)
}

const fixtureManifest = join(DOCS, 'FIXTURES.sha256')
if (existsSync(fixtureManifest)) {
  for (const line of readFileSync(fixtureManifest, 'utf8').trim().split('\n')) {
    const [expected, ...rest] = line.split(/\s+/)
    const rel = rest.join(' ')
    const p = join(APP, rel)
    if (!existsSync(p)) {
      console.error(`FIXTURE MISSING: ${rel}`)
      failures++
      continue
    }
    if (sha256(p) !== expected) {
      console.error(`FIXTURE HASH MISMATCH: ${rel}`)
      failures++
    }
  }
  console.log('Fixture manifest verified.')
} else {
  console.error('MISSING docs/portfolio-demo/FIXTURES.sha256')
  failures++
}

if (failures > 0) {
  console.error(`\nverify:portfolio-demo FAILED with ${failures} violation(s).`)
  process.exit(1)
}
console.log('\nverify:portfolio-demo PASSED.')
