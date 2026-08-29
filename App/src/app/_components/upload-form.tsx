'use client'

import { useState, type FormEvent } from 'react'
import {
  BrowserIntegrationError,
  completeBrowserUpload,
  createBrowserJob,
  uploadBrowserFile,
} from '@/lib/browserIntegration'

const VIDEO_BYTES = 1024 * 1024 * 1024
const TRANSCRIPT_BYTES = 10 * 1024 * 1024
const VIDEO_TYPES: Record<string, string> = {
  mp4: 'video/mp4',
  mov: 'video/quicktime',
  webm: 'video/webm',
}

function extension(name: string): string {
  return name.split('.').pop()?.toLowerCase() ?? ''
}

export function UploadForm() {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = new FormData(event.currentTarget)
    const name = String(form.get('projectName') ?? '').trim()
    const externalId = String(form.get('externalId') ?? '').trim()
    const preset = form.get('preset') === 'economy' ? 'economy' : 'standard'
    const video = form.get('video')
    const transcriptValue = form.get('transcript')
    const transcript = transcriptValue instanceof File && transcriptValue.size > 0 ? transcriptValue : null
    if (!name || !(video instanceof File) || video.size < 1) {
      setMessage('Choose a source video and enter a project name.')
      return
    }
    const contentType = VIDEO_TYPES[extension(video.name)]
    if (!contentType || video.size > VIDEO_BYTES) {
      setMessage('Video must be MP4, MOV or WebM and no larger than 1 GiB.')
      return
    }
    if (transcript && (!['vtt', 'srt'].includes(extension(transcript.name)) || transcript.size > TRANSCRIPT_BYTES)) {
      setMessage('Transcript must be timed VTT/SRT and no larger than 10 MiB.')
      return
    }

    setBusy(true)
    setMessage('Reserving upload…')
    let jobId: string | undefined
    try {
      const transcriptExtension = transcript ? extension(transcript.name) : null
      const created = await createBrowserJob({
        project: { name, ...(externalId ? { externalId } : {}) },
        video: {
          fileName: video.name,
          contentType,
          sizeBytes: video.size,
        },
        ...(transcript && transcriptExtension ? {
          transcript: {
            fileName: transcript.name,
            format: transcriptExtension,
            contentType: transcriptExtension === 'vtt' ? 'text/vtt' : 'application/x-subrip',
            sizeBytes: transcript.size,
          },
        } : {}),
        settings: { preset, style: 'documentary', detail: 3 },
      })
      jobId = created.job.id
      setMessage('Uploading video directly to private storage…')
      await uploadBrowserFile(created.uploads.video, video)
      if (transcript) {
        if (!created.uploads.transcript) throw new BrowserIntegrationError('missing_transcript_upload', jobId)
        setMessage('Uploading timed transcript…')
        await uploadBrowserFile(created.uploads.transcript, transcript)
      }
      setMessage('Verifying upload and queueing analysis…')
      await completeBrowserUpload(jobId)
      window.location.assign('/projects')
    } catch (error) {
      const code = error instanceof BrowserIntegrationError ? error.code : 'request_failed'
      setMessage(jobId
        ? `Upload was not accepted (${code}). Reserved job: ${jobId}. You can retry confirmation from the CLI.`
        : `Upload could not start (${code}).`)
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="mt-7 max-w-2xl space-y-5 rounded-xl border border-neutral-200 bg-white p-6 shadow-sm">
      <label className="block text-sm font-semibold text-neutral-900">
        Project name
        <input name="projectName" required maxLength={200} disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 font-normal" />
      </label>
      <label className="block text-sm font-semibold text-neutral-900">
        External ID <span className="font-normal text-neutral-400">(optional)</span>
        <input name="externalId" maxLength={255} disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 font-normal" />
      </label>
      <label className="block text-sm font-semibold text-neutral-900">
        Source video
        <input name="video" type="file" required accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" disabled={busy} className="mt-2 block w-full text-sm font-normal" />
      </label>
      <label className="block text-sm font-semibold text-neutral-900">
        Timed transcript <span className="font-normal text-neutral-400">(optional VTT/SRT)</span>
        <input name="transcript" type="file" accept=".vtt,.srt,text/vtt,application/x-subrip" disabled={busy} className="mt-2 block w-full text-sm font-normal" />
      </label>
      <label className="block text-sm font-semibold text-neutral-900">
        Processing preset
        <select name="preset" defaultValue="standard" disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 font-normal">
          <option value="standard">Standard</option>
          <option value="economy">Economy</option>
        </select>
      </label>
      {message && <p role="status" className="rounded-lg bg-neutral-50 px-3 py-2 text-sm text-neutral-600">{message}</p>}
      <button type="submit" disabled={busy} className="rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-50">
        {busy ? 'Working…' : 'Create and upload'}
      </button>
    </form>
  )
}
