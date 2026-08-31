import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { InvestigationWorkspace } from '@/app/_components/investigation-workspace'

interface InvestigationPageProps {
  params: Promise<{ id: string }>
}

export const metadata: Metadata = { title: 'Investigation workspace' }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export default async function InvestigationPage({ params }: InvestigationPageProps) {
  const { id } = await params
  if (!UUID.test(id)) notFound()
  return <InvestigationWorkspace investigationId={id} />
}
