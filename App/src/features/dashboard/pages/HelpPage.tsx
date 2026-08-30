import { Link } from 'react-router-dom'
import { BookOpen, Cpu, Mic, Sparkles, Coins, FileText } from 'lucide-react'
import { isCloudMode } from '@/lib/cloudMode'

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-xs font-medium uppercase tracking-widest text-neutral-400">
      {children}
    </h2>
  )
}

function Card({ id, title, children }: { id?: string; title: string; children: React.ReactNode }) {
  return (
    <article
      id={id}
      className="scroll-mt-20 rounded-lg border border-neutral-200 bg-neutral-0 p-5 space-y-3"
    >
      <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
      <div className="flex flex-col gap-3 text-sm text-neutral-600 leading-relaxed">
        {children}
      </div>
    </article>
  )
}

function OptionRow({
  name, summary, badge,
}: { name: string; summary: string; badge: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-neutral-100 py-3 first:border-t-0 first:pt-0 last:pb-0">
      <div className="min-w-0">
        <p className="text-sm font-medium text-neutral-900">{name}</p>
        <p className="text-xs text-neutral-500 leading-relaxed">{summary}</p>
      </div>
      <span className="shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-medium text-neutral-700">
        {badge}
      </span>
    </div>
  )
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-neutral-100 px-1 py-0.5 text-[11px] font-mono text-neutral-700">
      {children}
    </code>
  )
}

const TOPICS: { href: string; label: string; icon: React.ReactNode }[] = [
  { href: '#getting-started',  label: 'Getting started',      icon: <BookOpen size={12} /> },
  { href: '#frame-rate',       label: 'Frame rate',           icon: <Cpu size={12} /> },
  { href: '#models',           label: 'AI models',            icon: <Cpu size={12} /> },
  { href: '#audio-extraction', label: 'Audio & voice',        icon: <Mic size={12} /> },
  { href: '#editor',           label: 'Editor & Smart Fill',  icon: <Sparkles size={12} /> },
  { href: '#export',           label: 'Export formats',       icon: <FileText size={12} /> },
  { href: '#custom-prompt',    label: 'Custom prompt',        icon: <BookOpen size={12} /> },
  { href: '#tokens-pricing',   label: 'Tokens & pricing',     icon: <Coins size={12} /> },
]

export default function HelpPage() {
  return (
    <main className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-6 max-w-3xl">
        <div className="flex flex-col gap-10">

          <section className="flex flex-col gap-4">
            <SectionHeading>Topics</SectionHeading>
            <nav className="flex flex-wrap gap-2">
              {TOPICS.map((t) => (
                <a
                  key={t.href}
                  href={t.href}
                  className="inline-flex items-center gap-1.5 rounded-full border border-neutral-200 bg-neutral-0 px-3 py-1 text-xs font-medium text-neutral-700 hover:border-neutral-300 hover:bg-neutral-50 transition-colors"
                >
                  {t.icon}
                  {t.label}
                </a>
              ))}
            </nav>
          </section>

          <section className="flex flex-col gap-4">
            {isCloudMode() && (
              <div className="rounded-lg border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-neutral-700">
                <p className="font-medium text-neutral-900">Cloud demo (v0.1)</p>
                <p className="mt-1">
                  This build covers upload, automatic analysis, and the editor
                  with persistent per-scene edits. Voice preview, Smart Fill,
                  character rename, project management (rename/star/delete),
                  and Export are coming in v0.2 — the sections below describe
                  the full product and are marked where a feature is not yet
                  available here.
                </p>
              </div>
            )}
            <SectionHeading>Getting started</SectionHeading>
            <Card id="getting-started" title="What is InstaDescribe?">
              <p>
                InstaDescribe generates audio descriptions (AD) for video content. It
                extracts frames from a video, sends them to an AI vision model, and produces
                scene-level descriptions timed to match the action.
              </p>
              <p>
                The pipeline produces five output files: <Code>scenes.json</Code>,{' '}
                <Code>audio_events.json</Code>, <Code>ad_placement_gaps.json</Code>,{' '}
                <Code>transcript.json</Code>, and <Code>entities.json</Code>. The editor loads
                them to let you review, edit, preview, and export.
              </p>
              <p>
                <span className="font-medium text-neutral-900">Typical workflow</span>: upload
                video → choose processing options → confirm cost → wait for processing →
                review and edit in the editor → preview narration → export the format you need.
              </p>
            </Card>
          </section>

          <section className="flex flex-col gap-4">
            <SectionHeading>Processing options</SectionHeading>
            <Card id="frame-rate" title="Frame rate (FPS)">
              <p>
                Frame rate controls how many video frames per second are extracted and analysed.
                Higher FPS adds visual context at the cost of tokens and processing time.
              </p>
              <div>
                <OptionRow
                  name="0.5 fps — Light"
                  summary="One frame every 2 seconds. Best for slow-paced or dialogue-heavy content."
                  badge="Lowest cost"
                />
                <OptionRow
                  name="1 fps — Standard"
                  summary="One frame every second. Balanced for most content. The default."
                  badge="Default"
                />
                <OptionRow
                  name="8 fps — Dynamic"
                  summary="Eight frames per second. For fast motion, sports, action."
                  badge={isCloudMode() ? 'Cloud v0.2' : 'Highest fidelity'}
                />
              </div>
              <p className="text-xs text-neutral-400">
                FPS maps to the pipeline's STEP parameter. 8 fps = STEP 0.125, 1 fps = STEP
                1.0, 0.5 fps = STEP 2.0.
              </p>
              {isCloudMode() && (
                <p className="text-xs font-medium text-neutral-500">
                  Cloud v0.1 supports 0.5 fps and 1 fps. 8 fps is coming in v0.2.
                </p>
              )}
            </Card>

            <Card id="models" title="AI models">
              <p>
                Two OpenAI vision models analyse video frames. Switch per-job from the upload
                wizard.
              </p>
              <div>
                <OptionRow
                  name="GPT-4.1"
                  summary="Lower cost, faster, good quality for most content."
                  badge="Drafts"
                />
                <OptionRow
                  name="GPT-5.4"
                  summary="Highest quality, richer scene understanding, costlier."
                  badge={isCloudMode() ? 'Cloud v0.2' : 'Production'}
                />
              </div>
              {isCloudMode() && (
                <p className="text-xs font-medium text-neutral-500">
                  Cloud v0.1 accepts GPT-4.1 only. GPT-5.4 is coming in v0.2.
                </p>
              )}
            </Card>

            <Card id="audio-extraction" title="Audio & voice detection">
              <p>
                When enabled, Whisper transcribes the video's audio track. The transcript feeds
                character detection and helps the AI avoid describing audible sounds.
              </p>
              <p>
                <span className="font-medium text-neutral-900">Turn it off</span> if the video
                has no spoken dialogue, you already have an accurate transcript, or you want to
                reduce processing cost.
              </p>
              <p>
                <span className="font-medium text-neutral-900">Language</span>: auto-detected by
                default. Override in Advanced settings if auto-detection misfires (heavy
                accents, low audio quality, mixed languages).
              </p>
            </Card>
          </section>

          <section className="flex flex-col gap-4">
            <SectionHeading>Editor</SectionHeading>
            <Card id="editor" title="Editing, preview, and Smart Fill">
              {isCloudMode() && (
                <p className="text-neutral-500">
                  In this cloud demo: editing and Apply work today; Preview,
                  Smart Fill, and character rename are coming in v0.2.
                </p>
              )}
              <p>
                Each scene has its own AD line, voice, and speed. Edits persist locally on
                save and to the server when you click{' '}
                <span className="font-medium text-neutral-900">
                  {isCloudMode() ? 'Apply' : 'Apply to export'}
                </span>
                ; the server-side overrides survive page reloads and device
                switches.
              </p>
              <p>
                <span className="font-medium text-neutral-900">Preview</span> renders the
                current line via OpenAI tts-1-hd at the selected voice and speed. Pause and
                resume during playback; changing the voice, speed, or text drops the cached
                audio so the next preview re-renders.
              </p>
              <p>
                <span className="font-medium text-neutral-900">Smart Fill (beta)</span> sends
                the line plus the available silence-gap duration to the model and asks for a
                rewrite that fits the time budget. Use it for dense scenes where the original
                line overruns the gap.
              </p>
              <p>
                <span className="font-medium text-neutral-900">Characters tab</span> lists every
                detected entity. Renaming an entity rewrites every scene that references it in
                its description lines; locked scenes are left alone.
              </p>
            </Card>
          </section>

          <section className="flex flex-col gap-4">
            <SectionHeading>Export</SectionHeading>
            <Card id="export" title="Output formats">
              {isCloudMode() && (
                <p className="text-neutral-500">
                  Export is coming in v0.2 of the cloud demo. The formats below
                  describe the full product.
                </p>
              )}
              <p>
                The Export dialog ships five formats. Text formats skip TTS and complete
                in seconds; audio and video formats render TTS for every active scene then
                mix.
              </p>
              <div>
                <OptionRow
                  name="mp4"
                  summary="Source video with TTS narration mixed in, loudness-matched ducking applied."
                  badge="Video"
                />
                <OptionRow
                  name="mp3"
                  summary="Audio-only mix for sound engineers and dubbing handoff."
                  badge="Audio"
                />
                <OptionRow
                  name="srt"
                  summary="Subtitle file with AD timecodes — drop into any NLE."
                  badge="Subtitle"
                />
                <OptionRow
                  name="csv"
                  summary="Spreadsheet with one row per scene: id, start, end, characters, AD text."
                  badge="Data"
                />
                <OptionRow
                  name="docx"
                  summary="Formatted script document with character lists and scene timecodes."
                  badge="Script"
                />
              </div>
            </Card>
          </section>

          <section className="flex flex-col gap-4">
            <SectionHeading>Custom prompt</SectionHeading>
            <Card id="custom-prompt" title="Free-text guidance for the model">
              <p>
                The custom prompt is optional. It injects into every chunk's analysis prompt to
                give the model context it cannot infer from the video.
              </p>
              <p className="font-medium text-neutral-900">Effective patterns</p>
              <div>
                <OptionRow
                  name="Content type"
                  summary={'"This is a football match. Prioritise player positions and ball movement."'}
                  badge="Context"
                />
                <OptionRow
                  name="Tone"
                  summary={'"Children\'s educational video. Keep descriptions simple and positive."'}
                  badge="Voice"
                />
                <OptionRow
                  name="Style"
                  summary={'"British English. Avoid American idioms. Use precise medical terms."'}
                  badge="Language"
                />
                <OptionRow
                  name="Character names"
                  summary={'"The protagonist is Maya. The antagonist is Dr Vance."'}
                  badge="Naming"
                />
                <OptionRow
                  name="Focus"
                  summary={'"Foreground action over background. Skip the calendar visuals."'}
                  badge="Focus"
                />
              </div>
              <p className="text-xs text-neutral-400">
                Prompts sit alongside the automatic Detail Level and Preset Style instructions.
              </p>
            </Card>
          </section>

          <section className="flex flex-col gap-4">
            <SectionHeading>Tokens & pricing</SectionHeading>
            <Card id="tokens-pricing" title="How processing cost is calculated">
              <p>
                InstaDescribe is token-billed. Every vision-model call consumes tokens; the total
                depends on FPS, frame quality, chunk size, and model.
              </p>
              <div>
                <OptionRow
                  name="Per frame"
                  summary="85 tokens at low quality, 1,105 tokens at high quality."
                  badge="Frames"
                />
                <OptionRow
                  name="Per chunk"
                  summary="~7,000 tokens overhead for prompt + memory context + model output."
                  badge="Chunks"
                />
                <OptionRow
                  name="Formula"
                  summary="(frames × tokens_per_frame) + (chunks × 7,000)"
                  badge="Estimate"
                />
              </div>
              <p>
                The estimate on the cost-review screen is a ceiling — segments with little
                visual change come in lower, complex multi-character scenes may come in higher.
              </p>
              <p className="text-xs text-neutral-400">
                Reference: a 2-minute video at 1 fps / low / 60 s chunks on GPT-4.1 costs about{' '}
                <span className="font-medium text-neutral-700">12,000 tokens</span>. The same
                clip at 8 fps / high / GPT-5.4 costs around{' '}
                <span className="font-medium text-neutral-700">200,000 tokens</span>.
              </p>
            </Card>
          </section>

          <div className="pt-2 pb-8 text-center text-xs text-neutral-400">
            Missing something?{' '}
            <Link to="/dashboard" className="underline hover:text-neutral-600 transition-colors">
              Back to dashboard
            </Link>
            {' · '}
            <Link to="/dashboard/settings" className="underline hover:text-neutral-600 transition-colors">
              Settings
            </Link>
          </div>

        </div>
      </div>
    </main>
  )
}
