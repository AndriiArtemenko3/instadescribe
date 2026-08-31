import type { Metadata } from 'next'
import { NewInvestigationForm } from '@/app/_components/new-investigation-form'

export const metadata: Metadata = { title: 'New investigation' }

export default function NewInvestigationPage() {
  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-widest text-brand-500">Authorised source upload</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-900">New investigation</h1>
      <p className="mt-2 max-w-2xl text-sm text-neutral-500">
        Set the network boundary before processing. The policy is fixed for the life of this investigation.
      </p>
      <NewInvestigationForm />
    </>
  )
}
