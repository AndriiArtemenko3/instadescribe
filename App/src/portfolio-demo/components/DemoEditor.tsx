import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Headphones, RotateCcw, Info } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Logo } from '@/components/ui/Logo'
import type { Scene, Entity } from '@/types'
import {
  estimateSpeechSecs,
  getSceneCollision,
  sceneGapSecs,
  type SceneCollision,
} from '@/lib/collisions'
import { renderCaptionTemplate } from '../lib/captionTemplate'
import { canFitToGap, fitToGap } from '../lib/fitToGap'
import { WALKTHROUGH_STEPS, SCENE_TWO_ID, SCENE_FIVE_ID } from '../lib/walkthrough'
import { CLIP_DURATION_SECS, VIDEO_SRC, type DemoData } from '../lib/fixtures'
import { WalkthroughOverlay } from './WalkthroughOverlay'
import { DemoSceneList } from './DemoSceneList'
import { DemoVideoPanel } from './DemoVideoPanel'
import { DemoScriptPanel } from './DemoScriptPanel'
import { DemoCharactersPanel } from './DemoCharactersPanel'
import { ChecksPanel } from './ChecksPanel'
import { ListenDialog } from './ListenDialog'
import { AboutDialog } from './AboutDialog'
import type { DemoTab } from './DemoPanelTabs'

interface DemoEditorProps {
  data: DemoData
  embed: boolean
  onRestart: () => void
}

/**
 * The bounded onboarding editor. All state lives in memory: a reload or
 * Restart is a complete, deterministic reset. No request leaves this page
 * except for the committed static fixtures and media.
 */
export function DemoEditor({ data, embed, onRestart }: DemoEditorProps) {
  const navigate = useNavigate()
  const [scenes, setScenes] = useState<Scene[]>(data.scenes)
  const [entities, setEntities] = useState<Entity[]>(data.entities)
  const [editedSceneIds, setEditedSceneIds] = useState<ReadonlySet<number>>(new Set())
  const [fitSceneIds, setFitSceneIds] = useState<ReadonlySet<number>>(new Set())
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(SCENE_TWO_ID)
  const [currentTime, setCurrentTime] = useState(0)
  const [rightTab, setRightTab] = useState<DemoTab>('script')
  const [speed, setSpeed] = useState(1.0)

  // Walkthrough + dialog state.
  const [walkIndex, setWalkIndex] = useState<number | null>(0)
  const [walkDone, setWalkDone] = useState(false)
  const [playedNarration, setPlayedNarration] = useState(false)
  const [listenOpen, setListenOpen] = useState(false)
  const [listenOpened, setListenOpened] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)

  const selectedScene = scenes.find((s) => s.id === selectedSceneId) ?? scenes[0] ?? null

  const collisionsBySceneId = useMemo(() => {
    const map: Record<number, SceneCollision> = {}
    for (const s of scenes) {
      map[s.id] = getSceneCollision({ ...s, voiceSpeed: speed }, data.audioEvents, data.adGaps)
    }
    return map
  }, [scenes, speed, data.audioEvents, data.adGaps])

  const availableGapSecs = selectedScene
    ? sceneGapSecs(selectedScene, data.adGaps)
    : 0

  // Offer the local trim only when it can genuinely deliver: the line overruns
  // usable silence AND, if it also collides with dialogue, the trimmed version
  // would actually clear that collision (scene 2's head-on overlap cannot be
  // cleared by any trim, so the control stays disabled there — matching what
  // the walkthrough says).
  let selectedFittable = false
  if (selectedScene && selectedScene.active) {
    const est = estimateSpeechSecs(selectedScene.text, speed)
    if (canFitToGap(est, availableGapSecs)) {
      const coll = collisionsBySceneId[selectedScene.id]
      if (!coll?.collides) {
        selectedFittable = true
      } else {
        const trimmed = fitToGap(selectedScene.text, availableGapSecs)
        const after = getSceneCollision(
          { ...selectedScene, text: trimmed.text, voiceSpeed: speed },
          data.audioEvents,
          data.adGaps,
        )
        selectedFittable = !after.collides
      }
    }
  }

  // ── Walkthrough machinery ──────────────────────────────────────────────────
  const step = walkIndex !== null ? WALKTHROUGH_STEPS[walkIndex] : null

  const sceneTwo = scenes.find((s) => s.id === SCENE_TWO_ID)
  const sceneFive = scenes.find((s) => s.id === SCENE_FIVE_ID)
  // The fit step completes only when scene 5 GENUINELY fits its silence — via
  // the trim button or a sufficient hand edit.
  const sceneFiveFits =
    !!sceneFive &&
    estimateSpeechSecs(sceneFive.text, speed) <= sceneGapSecs(sceneFive, data.adGaps)
  const actionDone = !step
    ? false
    : step.id === 'refine-off'
      ? sceneTwo?.active === false
      : step.id === 'refine-fit'
        ? sceneFiveFits
        : step.id === 'listen-line'
          ? playedNarration
          : step.id === 'listen-film'
            ? listenOpened
            : false

  // Step side effects: point the editor at the scene each step talks about.
  useEffect(() => {
    if (!step) return
    if (step.id === 'identify' || step.id === 'refine-off') {
      setSelectedSceneId(SCENE_TWO_ID)
      setRightTab('script')
      setCurrentTime(26)
    } else if (step.id === 'refine-overrun' || step.id === 'refine-fit' || step.id === 'listen-line') {
      setSelectedSceneId(SCENE_FIVE_ID)
      setRightTab('script')
      if (step.id === 'refine-overrun') setCurrentTime(60)
    }
  }, [step])

  function closeWalkthrough() {
    setWalkIndex(null)
    setWalkDone(true)
    // Deterministic, useful focus target after the dialog goes away.
    requestAnimationFrame(() => document.getElementById('pd-replay')?.focus())
  }

  const walkSteps = useMemo(() => {
    // The completion step summarises what actually happened this session.
    const offCount = scenes.filter((s) => !s.active).length
    const trimmed = fitSceneIds.size
    const edited = editedSceneIds.size
    return WALKTHROUGH_STEPS.map((s) =>
      s.id === 'complete'
        ? {
            ...s,
            body:
              `In this session you switched ${offCount} ${offCount === 1 ? 'line' : 'lines'} off, ` +
              `trimmed ${trimmed} to fit` +
              (edited > 0 ? `, and hand-edited ${edited}` : '') +
              '. ' +
              s.body,
          }
        : s,
    )
  }, [scenes, fitSceneIds, editedSceneIds])

  // ── Editor actions (each does exactly what its control label says) ────────
  function handleTextChange(sceneId: number, text: string) {
    setScenes((prev) => prev.map((s) => (s.id === sceneId ? { ...s, text } : s)))
    setEditedSceneIds((prev) => new Set(prev).add(sceneId))
  }

  function handleToggleActive(sceneId: number) {
    setScenes((prev) => prev.map((s) => (s.id === sceneId ? { ...s, active: !s.active } : s)))
  }

  // The local trim is not a hand edit: it is tracked separately so the
  // completion summary stays truthful and renames still respect the trim.
  function handleFitText(sceneId: number, text: string) {
    setScenes((prev) => prev.map((s) => (s.id === sceneId ? { ...s, text } : s)))
    setFitSceneIds((prev) => new Set(prev).add(sceneId))
  }

  function handleRename(characterId: string, newName: string) {
    const nextEntities = entities.map((e) =>
      e.id === characterId ? { ...e, name: newName, user_renamed: true } : e,
    )
    setEntities(nextEntities)
    const byId = Object.fromEntries(nextEntities.map((e) => [e.id, e]))
    // Re-render captions from templates — the same deterministic rule the
    // pipeline applies. Hand-edited lines keep the visitor's wording.
    setScenes((prev) =>
      prev.map((s) =>
        editedSceneIds.has(s.id) || fitSceneIds.has(s.id) || !s.template
          ? s
          : { ...s, text: renderCaptionTemplate(s.template, byId) },
      ),
    )
  }

  // "Return to case study / Close" for the future embed: notify the parent
  // (documented in docs/portfolio-demo/EMBED_CONTRACT.md; harmless when
  // unhandled) and return the stage to the intro invitation state.
  function exitDemo() {
    if (embed) {
      try {
        window.parent?.postMessage({ type: 'instascribe-live-onboarding:exit' }, '*')
      } catch {
        /* messaging is best-effort */
      }
    }
    navigate('/')
  }

  function handleSceneSelect(scene: Scene) {
    setSelectedSceneId(scene.id)
    setCurrentTime(scene.startSecs)
  }

  const modalStepActive = step?.mode === 'modal'
  const editorInert = modalStepActive || listenOpen || aboutOpen
  const overlayVisible = walkIndex !== null && !listenOpen && !aboutOpen

  return (
    <div className="pd-editor-enter flex h-screen flex-col overflow-hidden bg-neutral-50">
      <h1 className="pd-visually-hidden">
        InstaScribe live onboarding — interactive audio-description editor walkthrough
      </h1>

      <div inert={editorInert} className="flex min-h-0 flex-1 flex-col">
        <header className="flex h-topnav shrink-0 items-center gap-3 border-b border-neutral-200 bg-neutral-0 px-4">
          <Logo size={18} className="text-brand-400" />
          <span className="text-sm font-medium text-neutral-900">InstaScribe</span>
          <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-neutral-500">
            Live onboarding · preloaded demo
          </span>
          <div className="flex-1" />
          {walkDone && (
            <button
              id="pd-replay"
              onClick={() => {
                setWalkDone(false)
                setWalkIndex(0)
              }}
              className="text-xs text-neutral-500 transition-colors hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
            >
              Replay walkthrough
            </button>
          )}
          <button
            onClick={() => setAboutOpen(true)}
            className="flex items-center gap-1 text-xs text-neutral-500 transition-colors hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
          >
            <Info size={13} />
            About & licensing
          </button>
          <button
            onClick={onRestart}
            className="flex items-center gap-1.5 text-xs text-neutral-500 transition-colors hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
            title="Reset everything to a clean start"
          >
            <RotateCcw size={13} />
            Restart
          </button>
          <Separator orientation="vertical" className="h-4" />
          {embed ? (
            <button
              onClick={exitDemo}
              className="text-xs text-neutral-500 transition-colors hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
              title="Close the walkthrough and return to the start screen"
            >
              Close demo
            </button>
          ) : (
            <Link
              to="/"
              className="text-xs text-neutral-500 transition-colors hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
            >
              Exit to intro
            </Link>
          )}
          <span data-tour="preview">
            <Button size="sm" className="gap-2" onClick={() => {
              setListenOpen(true)
              setListenOpened(true)
            }}>
              <Headphones size={14} />
              Play described example
            </Button>
          </span>
        </header>

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <div data-tour="scenes" className="flex">
            <DemoSceneList
              scenes={scenes}
              activeSceneId={selectedScene?.id ?? null}
              onSceneSelect={handleSceneSelect}
              onActiveToggle={handleToggleActive}
              collisions={collisionsBySceneId}
            />
          </div>

          <div data-tour="video" className="flex min-w-0 flex-1 overflow-hidden">
            <DemoVideoPanel
              videoSrc={VIDEO_SRC}
              duration={CLIP_DURATION_SECS}
              scenes={scenes}
              adGaps={data.adGaps}
              audioEvents={data.audioEvents}
              collisions={collisionsBySceneId}
              currentTime={currentTime}
              onSeek={setCurrentTime}
              onTimeUpdate={setCurrentTime}
            />
          </div>

          <div data-tour="script" className="flex">
            {rightTab === 'script' ? (
              <DemoScriptPanel
                scene={selectedScene}
                availableGapSecs={availableGapSecs}
                collision={selectedScene ? (collisionsBySceneId[selectedScene.id] ?? null) : null}
                activeTab={rightTab}
                onTabChange={setRightTab}
                onTextChange={handleTextChange}
                onToggleActive={handleToggleActive}
                speed={speed}
                onSpeedChange={setSpeed}
                sceneEdited={
                  selectedScene
                    ? editedSceneIds.has(selectedScene.id) || fitSceneIds.has(selectedScene.id)
                    : false
                }
                fittable={selectedFittable}
                onFitText={handleFitText}
                onPlayedOriginal={() => setPlayedNarration(true)}
                onPlayedBrowserVoice={() => setPlayedNarration(true)}
              />
            ) : rightTab === 'characters' ? (
              <DemoCharactersPanel
                entities={entities}
                scenes={scenes}
                activeTab={rightTab}
                onTabChange={setRightTab}
                onRename={handleRename}
                protectedSceneCount={new Set([...editedSceneIds, ...fitSceneIds]).size}
              />
            ) : (
              <ChecksPanel
                scenes={scenes}
                collisions={collisionsBySceneId}
                speed={speed}
                activeTab={rightTab}
                onTabChange={setRightTab}
                onSelectScene={(id) => {
                  setSelectedSceneId(id)
                  setRightTab('script')
                }}
              />
            )}
          </div>
        </div>
      </div>

      {overlayVisible && walkIndex !== null && (
        <WalkthroughOverlay
          steps={walkSteps}
          index={walkIndex}
          actionDone={actionDone}
          onNext={() =>
            walkIndex >= walkSteps.length - 1 ? closeWalkthrough() : setWalkIndex(walkIndex + 1)
          }
          onBack={() => setWalkIndex(Math.max(0, walkIndex - 1))}
          onExit={closeWalkthrough}
          onRestartDemo={onRestart}
          exitDemoLabel={embed ? 'Close demo' : 'Back to intro'}
          onExitDemo={exitDemo}
        />
      )}

      {listenOpen && (
        <ListenDialog
          onClose={() => setListenOpen(false)}
          sceneTwoRemoved={sceneTwo ? !sceneTwo.active : false}
        />
      )}
      {aboutOpen && <AboutDialog embed={embed} onClose={() => setAboutOpen(false)} />}
    </div>
  )
}
