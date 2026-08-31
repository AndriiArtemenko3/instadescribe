import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { InvestigationReport } from '@/app/_components/investigation-report'

interface InvestigationReportPageProps {
  params: Promise<{ id: string }>
}

export const metadata: Metadata = { title: 'Investigation report' }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export default async function InvestigationReportPage({ params }: InvestigationReportPageProps) {
  const { id } = await params
  if (!UUID.test(id)) notFound()
  return <InvestigationReport investigationId={id} />
}
