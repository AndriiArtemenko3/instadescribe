import { useRef } from 'react'
import { Button } from '@/components/ui/button'
import { ModalDialog } from './ModalDialog'
import { claimAudio, clearAudioClaim } from '../lib/audioBus'
import { EXPORT_SRC, DESCRIBED_CAPTIONS_SRC } from '../lib/fixtures'

interface ListenDialogProps {
  onClose: () => void
  /** Fired when the video ACTUALLY enters playback — the step's evidence. */
  onPlaybackStarted: () => void
  sceneTwoRemoved: boolean
}

/**
 * The LISTEN step. The video here is the committed, pre-rendered described
 * example produced by the full pipeline from the ORIGINAL nine drafts — the
 * copy states plainly that the visitor's edits are not in it. The file is only
 * fetched when this dialog mounts (i.e. on explicit request), and playback
 * starts on the visitor's own press of play — no autoplay with sound.
 */
export function ListenDialog({ onClose, onPlaybackStarted, sceneTwoRemoved }: ListenDialogProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  return (
    <ModalDialog titleId="pd-listen-title" title="Pre-rendered described example" onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm leading-relaxed text-neutral-600">
          This is the film with narration mixed in,{' '}
          <strong className="font-medium text-neutral-900">
            pre-rendered by the full InstaScribe pipeline from the original nine drafts
          </strong>
          . Your edits are not in this file
          {sceneTwoRemoved && (
            <> — so you will still hear scene 2's narration collide with the dialogue you just
            resolved</>
          )}
          .
        </p>
        <video
          ref={videoRef}
          controls
          preload="metadata"
          className="w-full rounded-lg bg-black"
          aria-label="Sintel excerpt with audio description narration mixed in (pre-rendered)"
          onPlaying={() => {
            onPlaybackStarted()
            claimAudio('described-example', () => videoRef.current?.pause())
          }}
          onPause={() => clearAudioClaim('described-example')}
        >
          <source src={EXPORT_SRC} type="video/mp4" />
          <track
            kind="captions"
            src={DESCRIBED_CAPTIONS_SRC}
            srcLang="en"
            label="Dialogue + narration (English)"
          />
        </video>
        <p className="text-xs leading-relaxed text-neutral-500">
          Try focusing on the soundtrack alone — notice how the narration carries what's on
          screen between the dialogue. (A listening exercise, nothing more: it doesn't
          reproduce anyone's experience of blindness or low vision.) Captions for dialogue and
          narration are available via the video's caption control.
        </p>
        <div className="flex justify-end">
          <Button onClick={onClose}>Done listening</Button>
        </div>
      </div>
    </ModalDialog>
  )
}
