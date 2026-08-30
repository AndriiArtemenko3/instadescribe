// Talks to the backend's /api/providers endpoints. The picker chooses WHICH model
// backend runs; API keys stay server-side in .env and are never sent from here.

import { legacyApiBase } from './runtimeEnv'

const API_BASE = legacyApiBase()

export interface BackendStatus {
  id: string
  label: string
  ready: boolean
  reason: string
}

export interface ProvidersState {
  backends: BackendStatus[]
  current: string
}

export async function fetchProviders(): Promise<ProvidersState> {
  const res = await fetch(`${API_BASE}/api/providers`)
  if (!res.ok) throw new Error(`providers ${res.status}`)
  return res.json()
}

export async function setProvider(backend: string): Promise<ProvidersState> {
  const res = await fetch(`${API_BASE}/api/providers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ backend }),
  })
  if (!res.ok) throw new Error(`set provider ${res.status}`)
  return res.json()
}
