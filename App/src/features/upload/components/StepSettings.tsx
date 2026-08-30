import { useState } from 'react'
import { Link } from 'react-router-dom'
import { HelpCircle, ChevronDown, ChevronUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { isCloudMode } from '@/lib/cloudMode'
import { FileHeader } from './FileHeader'
import type { UploadSettings } from '@/types'

// ─── Shared primitives ────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <p className="mb-3 text-sm font-semibold text-neutral-700 underline">{children}</p>
}

function RadioRow<T extends string | number>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string; sub?: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          onClick={() => onChange(o.value)}
          className={cn(
            'flex flex-col items-start rounded-lg border px-4 py-2.5 text-left transition-colors',
            value === o.value
              ? 'border-neutral-900 bg-neutral-50'
              : 'border-neutral-200 bg-white hover:border-neutral-300',
          )}
        >
          <span className={cn('text-sm font-medium', value === o.value ? 'text-neutral-900' : 'text-neutral-600')}>
            {o.label}
          </span>
          {o.sub && <span className="mt-0.5 text-xs text-neutral-400">{o.sub}</span>}
        </button>
      ))}
    </div>
  )
}

function SegmentedControl({
  value,
  onChange,
}: {
  value: 1 | 2 | 3 | 4 | 5
  onChange: (v: 1 | 2 | 3 | 4 | 5) => void
}) {
  const labels: Record<number, string> = { 1: 'Brief', 2: 'Standard', 3: 'Detailed', 4: 'Rich', 5: 'Comprehensive' }
  return (
    <div className="flex rounded-lg border border-neutral-200 overflow-hidden">
      {([1, 2, 3, 4, 5] as const).map((n) => (
        <button
          key={n}
          type="button"
          onClick={() => onChange(n)}
          className={cn(
            'flex flex-1 flex-col items-center py-2 text-xs transition-colors',
            n !== 5 && 'border-r border-neutral-200',
            value === n ? 'bg-neutral-900 text-white' : 'bg-white text-neutral-500 hover:bg-neutral-50',
          )}
        >
          <span className="font-semibold">{n}</span>
          <span className="mt-0.5 text-[10px] leading-tight">{labels[n]}</span>
        </button>
      ))}
    </div>
  )
}

function PillRadio<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded-full border px-4 py-1.5 text-xs font-medium transition-colors',
            value === o.value
              ? 'border-neutral-900 bg-neutral-900 text-white'
              : 'border-neutral-200 bg-white text-neutral-600 hover:border-neutral-400',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ─── Mode selector ────────────────────────────────────────────────────────────

const MODES: { value: UploadSettings['mode']; label: string; sub: string }[] = [
  { value: 'cheap',  label: 'Cheap Mode',     sub: 'Lowest cost · GPT-4.1 · 0.5 fps' },
  {
    value: 'normal',
    label: 'Normal Mode',
    // The cloud contract offers gpt-4.1 only (G7); legacy keeps GPT-5.4.
    sub: isCloudMode() ? 'Balanced · GPT-4.1 · 1 fps' : 'Balanced · GPT-5.4 · 1 fps',
  },
  { value: 'custom', label: 'Custom Settings', sub: 'Configure each parameter manually' },
]

// ─── Custom panel ─────────────────────────────────────────────────────────────

interface CustomPanelProps {
  settings: UploadSettings
  onChange: (patch: Partial<UploadSettings>) => void
}

function CustomPanel({ settings, onChange }: CustomPanelProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false)

  return (
    <div className="mt-4 flex flex-col gap-6 rounded-xl border border-neutral-200 bg-neutral-50 px-5 py-5">
      {/* AI Model */}
      <div>
        <SectionLabel>AI Model:</SectionLabel>
        <RadioRow
          value={settings.model}
          onChange={(v) => onChange({ model: v })}
          options={
            isCloudMode()
              ? [{ value: 'gpt-4.1', label: 'ChatGPT 4.1', sub: 'Efficient · lower cost' }]
              : [
                  { value: 'gpt-4.1', label: 'ChatGPT 4.1', sub: 'Efficient · lower cost' },
                  { value: 'gpt-5.4', label: 'ChatGPT 5.4', sub: 'Best quality · higher cost' },
                ]
          }
        />
      </div>

      {/* Frame Rate */}
      <div>
        <SectionLabel>Frame Rate:</SectionLabel>
        <RadioRow
          value={settings.fps}
          onChange={(v) => onChange({ fps: v })}
          options={
            isCloudMode()
              ? [
                  { value: 0.5, label: '0.5 fps', sub: 'Light · budget' },
                  { value: 1, label: '1 fps', sub: 'Standard · default' },
                ]
              : [
                  { value: 0.5, label: '0.5 fps', sub: 'Light · budget' },
                  { value: 1, label: '1 fps', sub: 'Standard · default' },
                  { value: 8, label: '8 fps', sub: 'Dynamic · action content' },
                ]
          }
        />
      </div>

      {/* Frame Quality */}
      <div>
        <SectionLabel>Frame Quality:</SectionLabel>
        <RadioRow
          value={settings.frameQuality}
          onChange={(v) => onChange({ frameQuality: v })}
          options={
            isCloudMode()
              ? [{ value: 'low', label: 'Low', sub: 'Cost-efficient · default' }]
              : [
                  { value: 'low', label: 'Low', sub: 'Cost-efficient · default' },
                  { value: 'high', label: 'High', sub: 'More detail per frame' },
                ]
          }
        />
      </div>

      {/* Chunk Size */}
      <div>
        <SectionLabel>Chunk Size:</SectionLabel>
        <RadioRow
          value={settings.chunkSizeSecs}
          onChange={(v) => onChange({ chunkSizeSecs: v })}
          options={
            isCloudMode()
              ? [
                  { value: 60, label: '60s', sub: 'Balanced' },
                  { value: 120, label: '120s', sub: 'Fewer API calls' },
                ]
              : [
                  { value: 30, label: '30s', sub: 'Fine-grained' },
                  { value: 60, label: '60s', sub: 'Balanced' },
                  { value: 120, label: '120s', sub: 'Fewer API calls' },
                ]
          }
        />
      </div>

      {/* Audio Extraction */}
      <div>
        <SectionLabel>Audio / Voice Detection:</SectionLabel>
        <RadioRow
          value={settings.audioExtraction ? 'on' : 'off'}
          onChange={(v) => onChange({ audioExtraction: v === 'on' })}
          options={[
            { value: 'on',  label: 'On',  sub: 'Transcribe speech · default' },
            { value: 'off', label: 'Off', sub: 'Skip audio · saves cost' },
          ]}
        />
      </div>

      {/* AD Detail Level */}
      <div>
        <SectionLabel>AD Detail Level:</SectionLabel>
        <SegmentedControl value={settings.detailLevel} onChange={(v) => onChange({ detailLevel: v })} />
      </div>

      {/* Preset Style */}
      <div>
        <SectionLabel>Preset Style:</SectionLabel>
        <PillRadio
          value={settings.presetStyle}
          onChange={(v) => onChange({ presetStyle: v })}
          options={[
            { value: 'documentary', label: 'Documentary' },
            { value: 'cinematic',   label: 'Cinematic' },
            { value: 'news',        label: 'News' },
            { value: 'sports',      label: 'Sports' },
            { value: 'education',   label: 'Education' },
          ]}
        />
      </div>

      {/* Advanced collapsible */}
      <div>
        <button
          type="button"
          onClick={() => setAdvancedOpen((o) => !o)}
          className="flex items-center gap-1 text-xs font-medium text-neutral-500 hover:text-neutral-700 transition-colors"
        >
          {advancedOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          Advanced
        </button>

        {advancedOpen && (
          <div className="mt-4 flex flex-col gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-neutral-600">Transcription Language</span>
              <select
                value={settings.language ?? ''}
                onChange={(e) => onChange({ language: e.target.value || null })}
                className="rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm text-neutral-700 outline-none focus:border-neutral-400"
              >
                <option value="">Auto-detect</option>
                <option value="en">English</option>
                <option value="fr">French</option>
                <option value="es">Spanish</option>
                <option value="de">German</option>
                <option value="ja">Japanese</option>
                <option value="pt">Portuguese</option>
                <option value="uk">Ukrainian</option>
              </select>
            </label>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Main component ────────────────────────────────────────────────────────────

interface StepSettingsProps {
  file: File
  fileUrl: string
  settings: UploadSettings
  onSettingsChange: (patch: Partial<UploadSettings>) => void
  onReplace: () => void
  onBack: () => void
  onNext: () => void
}

export function StepSettings({ file, fileUrl, settings, onSettingsChange, onReplace, onBack, onNext }: StepSettingsProps) {
  return (
    <div className="flex flex-col gap-6">
      <FileHeader file={file} fileUrl={fileUrl} onReplace={onReplace} />

      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium text-neutral-700">Choose Project Mode:</p>
        <div className="flex flex-col gap-2">
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              onClick={() => onSettingsChange({ mode: m.value })}
              className={cn(
                'rounded-lg border px-4 py-3 text-left transition-colors',
                settings.mode === m.value
                  ? 'border-neutral-900 bg-neutral-50'
                  : 'border-neutral-200 bg-white hover:border-neutral-300',
              )}
            >
              <span className={cn('text-sm font-medium', settings.mode === m.value ? 'text-neutral-900' : 'text-neutral-700')}>
                {m.label}
              </span>
              <span className="ml-2 text-xs text-neutral-400">{m.sub}</span>
            </button>
          ))}
        </div>

        {settings.mode === 'custom' && (
          <CustomPanel settings={settings} onChange={onSettingsChange} />
        )}
      </div>

      <div className="flex items-center">
        <Link to="/dashboard/help#frame-rate" className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-600 transition-colors">
          <HelpCircle size={15} />
          Need Help?
        </Link>
        <div className="flex-1" />
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onBack}>Go Back</Button>
          <Button variant="default" size="sm" onClick={onNext}>Next</Button>
        </div>
      </div>
    </div>
  )
}
