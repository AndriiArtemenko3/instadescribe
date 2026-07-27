import { Link } from 'react-router-dom'
import { ModalDialog } from './ModalDialog'

interface AboutDialogProps {
  embed: boolean
  onClose: () => void
}

/** Visible provenance, licensing, and an explicit what's-real inventory. */
export function AboutDialog({ embed, onClose }: AboutDialogProps) {
  return (
    <ModalDialog titleId="pd-about-title" title="About this demo" onClose={onClose}>
      <div className="space-y-3 text-sm leading-relaxed text-neutral-600">
        <p>
          An interactive product walkthrough of{' '}
          <strong className="font-medium text-neutral-900">InstaScribe</strong>, an
          audio-description authoring tool. Everything here runs from files bundled with this
          page — there is no account, no upload, no server, and no AI or model call.
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <strong className="font-medium text-neutral-900">Real and live in your browser:</strong>{' '}
            scene editing, on/off decisions, timing estimates and dialogue-collision checks,
            character rename propagation, the local "Fit to gap" trim, playback speed.
          </li>
          <li>
            <strong className="font-medium text-neutral-900">Pre-generated:</strong> the per-line
            narration ("Original line · Onyx") and the final described example were produced
            earlier by the project's pipeline (OpenAI text-to-speech) for the original drafts.
            They never reflect your edits — the interface says so wherever they appear.
          </li>
          <li>
            <strong className="font-medium text-neutral-900">Browser voice:</strong> "Read my
            text" uses your browser's own speech synthesis, so the voice varies by device.
          </li>
          <li>
            <strong className="font-medium text-neutral-900">Timing checks:</strong> simple local
            arithmetic (0.4 s per word vs. each scene's window and the film's dialogue map) —
            not a measure of writing quality.
          </li>
        </ul>
        <p>
          Prefer reading?{' '}
          <Link
            className="text-brand-500 underline underline-offset-2"
            to={embed ? '/onboarding?embed=1&view=text' : '/onboarding?view=text'}
          >
            The whole walkthrough is available as text
          </Link>
          , including the film's dialogue transcript.
        </p>
        <p className="border-t border-neutral-200 pt-3 text-xs text-neutral-500">
          Film: <em>Sintel</em> — © Blender Foundation ·{' '}
          <a
            className="underline underline-offset-2"
            href="https://durian.blender.org"
            target="_blank"
            rel="noreferrer noopener"
          >
            durian.blender.org
          </a>{' '}
          · licensed{' '}
          <a
            className="underline underline-offset-2"
            href="https://creativecommons.org/licenses/by/3.0/"
            target="_blank"
            rel="noreferrer noopener"
          >
            CC BY 3.0
          </a>
          . A ~2-minute excerpt, adapted here with an audio-description track. Nothing you do
          on this page is recorded or sent anywhere.
        </p>
      </div>
    </ModalDialog>
  )
}
