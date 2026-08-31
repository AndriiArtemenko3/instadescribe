import type { Metadata } from 'next'
import { InvestigationsPanel } from '@/app/_components/investigations-panel'

export const metadata: Metadata = { title: 'Investigations' }

export default function InvestigationsPage() {
  return <InvestigationsPanel />
}
