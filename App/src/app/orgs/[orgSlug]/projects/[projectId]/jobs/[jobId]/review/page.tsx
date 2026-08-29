import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { ReviewClient } from '@/app/_components/review-client'

interface ReviewPageProps {
  params: Promise<{ orgSlug: string; projectId: string; jobId: string }>
}

export const metadata: Metadata = { title: 'Review' }

const SAFE_IDENTIFIER = /^[A-Za-z0-9_-]{1,128}$/

export default async function ReviewPage({ params }: ReviewPageProps) {
  const { orgSlug, projectId, jobId } = await params
  if (![orgSlug, projectId, jobId].every((value) => SAFE_IDENTIFIER.test(value))) notFound()

  return <ReviewClient orgSlug={orgSlug} projectId={projectId} jobId={jobId} />
}
