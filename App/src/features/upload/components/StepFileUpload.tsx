import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Upload, HelpCircle, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { isCloudMode } from '@/lib/cloudMode'
import { CLOUD_MAX_FILE_BYTES, CLOUD_MAX_FILE_LABEL } from '@/lib/cloudUpload'

const LEGACY_MAX_BYTES = 4 * 1024 * 1024 * 1024
// One centralized limit (G7.1 D1): the cloud contract is 250 MiB; legacy
// keeps its 4 GB wording and behavior.
const MAX_BYTES = isCloudMode() ? CLOUD_MAX_FILE_BYTES : LEGACY_MAX_BYTES
const MAX_LABEL = isCloudMode() ? CLOUD_MAX_FILE_LABEL : '4 GB'

interface StepFileUploadProps {
  onFileSelected: (file: File) => Promise<void>
  onCancel: () => void
  onNext: () => void
  hasFile: boolean
}

export function StepFileUpload({ onFileSelected, onCancel, onNext, hasFile }: StepFileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleFile(file: File) {
    setError(null)
    if (file.size > MAX_BYTES) {
      setError(`File exceeds the ${MAX_LABEL} limit.`)
      return
    }
    await onFileSelected(file)
    onNext()
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
    e.target.value = ''
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  return (
    <div className="flex flex-col gap-6">
      <input
        ref={inputRef}
        type="file"
        accept="video/mp4,video/quicktime,video/webm"
        className="hidden"
        onChange={handleInputChange}
      />

      <div
        className={cn(
          'flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed py-16 cursor-pointer transition-colors',
          dragging ? 'border-neutral-400 bg-neutral-100' : 'border-neutral-200 bg-neutral-50 hover:bg-neutral-100 hover:border-neutral-300',
        )}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        <Upload size={28} className="text-neutral-400" />
        <div className="text-center">
          <p className="text-sm font-medium text-neutral-700 underline">Upload File</p>
          <p className="mt-1 text-xs text-neutral-400">Click to upload your files or drag and drop them here</p>
        </div>
      </div>

      {error && (
        <p className="flex items-center gap-1.5 text-xs text-danger-400">
          <AlertTriangle size={13} /> {error}
        </p>
      )}

      <p className="text-right text-xs text-neutral-400">
        Maximum size: {MAX_LABEL}
        {!isCloudMode() && (
          <span className="ml-1">(files &gt; 500 MB may take longer)</span>
        )}
      </p>

      <div className="flex items-center">
        <Link to="/dashboard/help" className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-600 transition-colors">
          <HelpCircle size={15} />
          Need Help?
        </Link>
        <div className="flex-1" />
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onCancel}>Cancel</Button>
          <Button variant="default" size="sm" disabled={!hasFile} onClick={onNext}>Upload</Button>
        </div>
      </div>
    </div>
  )
}
