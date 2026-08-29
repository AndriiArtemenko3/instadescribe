import type { Metadata } from 'next'
import { UploadForm } from '@/app/_components/upload-form'

export const metadata: Metadata = { title: 'Upload' }

export default function UploadPage() {
  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-widest text-brand-500">New project</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-900">Upload video</h1>
      <p className="mt-2 max-w-2xl text-sm text-neutral-500">
        Create a processing job, upload the source directly to object storage, then confirm the upload with FastAPI.
      </p>

      <UploadForm />
    </>
  )
}
