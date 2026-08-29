import { useRef } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useUploadFlow } from '../hooks/useUploadFlow'
import { StepIndicator } from '../components/StepIndicator'
import { StepFileUpload } from '../components/StepFileUpload'
import { StepPrompt } from '../components/StepPrompt'
import { StepSettings } from '../components/StepSettings'
import { StepCostReview } from '../components/StepCostReview'
import { StepProgress } from '../components/StepProgress'

function EditableTitle({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <input
      ref={inputRef}
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="Untitled Project"
      className="mb-8 w-full bg-transparent text-center text-2xl font-semibold text-neutral-800 outline-none placeholder:text-neutral-300 focus:placeholder:text-neutral-200"
    />
  )
}

export default function UploadPage() {
  const { state, estimatedTokens, estimatedMinutes, setFile, setProjectName, setCustomPrompt, setSettings, next, back, submit, cancel } = useUploadFlow()

  const fileInputRef = useRef<HTMLInputElement>(null)

  // G7.1 D2: the replace control owns a REAL file input (the previous ref
  // pointed at nothing); object-URL revocation happens inside setFile.
  function replaceFile() {
    fileInputRef.current?.click()
  }

  function handleReplaceChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) void setFile(f)
    e.target.value = ''
  }

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <input
        ref={fileInputRef}
        type="file"
        accept="video/mp4,video/quicktime,video/webm"
        className="hidden"
        onChange={handleReplaceChange}
      />
      {/* Top bar — matches editor style */}
      <header className="flex h-topnav shrink-0 items-center gap-2 border-b border-neutral-200 bg-neutral-0 px-4">
        <Link
          to="/dashboard"
          className="flex items-center gap-2 text-neutral-700 hover:text-neutral-900 transition-colors"
        >
          <ArrowLeft size={16} />
          <span className="text-sm font-medium">Upload Video</span>
        </Link>
      </header>

      {/* Content */}
      <main className="mx-auto w-full max-w-2xl flex-1 px-8 py-10">
        <EditableTitle value={state.projectName} onChange={setProjectName} />

        {state.step === 1 && (
          <StepFileUpload
            onFileSelected={setFile}
            onCancel={cancel}
            onNext={next}
            hasFile={!!state.file}
          />
        )}

        {state.step === 2 && state.file && state.fileUrl && (
          <StepPrompt
            file={state.file}
            fileUrl={state.fileUrl}
            customPrompt={state.customPrompt}
            onPromptChange={setCustomPrompt}
            onReplace={replaceFile}
            onBack={back}
            onNext={next}
          />
        )}

        {state.step === 3 && state.file && state.fileUrl && (
          <StepSettings
            file={state.file}
            fileUrl={state.fileUrl}
            settings={state.settings}
            onSettingsChange={setSettings}
            onReplace={replaceFile}
            onBack={back}
            onNext={next}
          />
        )}

        {state.step === 4 && state.file && state.fileUrl && (
          <StepCostReview
            file={state.file}
            fileUrl={state.fileUrl}
            estimatedTokens={estimatedTokens}
            estimatedMinutes={estimatedMinutes}
            onReplace={replaceFile}
            onBack={back}
            onConfirm={submit}
          />
        )}

        {/* Submit/selection errors are visible on EVERY wizard step — a
            rejected replacement at steps 1-3 must not fail silently. */}
        {state.step < 5 && state.submitError && (
          <p role="alert" className="mx-auto mt-3 max-w-xl text-center text-sm text-danger-400">
            {state.submitError}
          </p>
        )}

        {state.step === 5 && (
          <StepProgress
            progress={state.progress}
            isReady={state.isReady}
            isFailed={state.isFailed}
            failedError={state.failedError}
            stage={state.stage}
            chunksDone={state.chunksDone}
            chunksTotal={state.chunksTotal}
            estimatedMinutes={estimatedMinutes}
            newProjectId={state.newProjectId}
            onRetry={cancel}
          />
        )}
      </main>

      <StepIndicator step={state.step} total={5} />
    </div>
  )
}
