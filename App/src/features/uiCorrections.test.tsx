import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/cloudMode', () => ({
  isCloudMode: () => true,
  isCloudSession: () => true,
  cloudApiBase: () => 'http://localhost:8000',
}))

import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { StepProgress } from './upload/components/StepProgress'
import { ScriptPanel } from './editor/components/ScriptPanel'
import SettingsPage from './dashboard/pages/SettingsPage'
import HelpPage from './dashboard/pages/HelpPage'
import type { Scene } from '@/types'

const noop = () => {}
const scene: Scene = {
  id: 1,
  sceneNumber: 1,
  sceneKey: 'scene_alpha',
  startSecs: 0,
  endSecs: 4,
  durationSecs: 4,
  text: 'A person crosses the room.',
  template: '',
  characterIds: [],
  locked: false,
  needsReview: false,
  active: true,
}

function inRouter(node: React.ReactNode): string {
  return renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>)
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

describe('G7.2 rendered correction states', () => {
  it('settled ready state exposes visible 100%, progress value, and terminal copy', () => {
    const html = inRouter(<StepProgress
      progress={100}
      isReady
      isFailed={false}
      failedError={null}
      stage="complete"
      chunksDone={2}
      chunksTotal={2}
      estimatedMinutes={2}
      newProjectId="project-1"
      onRetry={noop}
    />)
    expect(html).toContain('aria-valuenow="100"')
    expect(html).toContain('100%')
    expect(html).toContain('Your audio descriptions are ready to review')
    expect(html).not.toContain('Your audio descriptions are being generated')
  })

  it('real Apply success is a visible accessible status', () => {
    const html = renderToStaticMarkup(<ScriptPanel
      projectId="project-1"
      scene={scene}
      characters={[]}
      availableGapSecs={4}
      collision={null}
      activeTab="script"
      onTabChange={noop}
      onAdChange={noop}
      onActiveToggle={noop}
      onApply={noop}
      justApplied
      onVoiceChange={noop}
      onSpeedChange={noop}
      onLockedChange={noop}
      onRenameRequest={noop}
      cloudDeferred
    />)
    expect(html).toContain('role="status"')
    expect(html).toContain('aria-live="polite"')
    expect(html).toContain('Changes applied')
  })

  it('cloud editor exposes explicit generated, approve, and reject review controls', () => {
    const html = renderToStaticMarkup(<ScriptPanel
      projectId="project-1"
      scene={scene}
      characters={[]}
      availableGapSecs={4}
      collision={null}
      activeTab="script"
      onTabChange={noop}
      onAdChange={noop}
      onActiveToggle={noop}
      onApply={noop}
      onVoiceChange={noop}
      onSpeedChange={noop}
      onLockedChange={noop}
      onRenameRequest={noop}
      cloudDeferred
      cloudReviewEnabled
      cloudReviewStatus="generated"
      onCloudReview={noop}
    />)
    expect(html).toContain('Human review')
    expect(html).toContain('Generated')
    expect(html).toContain('Approve')
    expect(html).toContain('Reject')
  })

  it('cloud Settings renders validated API/session truth without legacy Flask guidance', () => {
    const html = inRouter(<SettingsPage />)
    expect(html).toContain('Cloud API')
    expect(html).toContain('http://localhost:8000')
    expect(html).toContain('Session storage')
    expect(html).toContain('Access tokens and session metadata are preserved')
    expect(html).not.toContain('localhost:8765')
    expect(html).not.toContain('python3 server.py')
  })

  it('cloud Help marks rejected v0.1 settings unavailable and uses domain-accurate copy', () => {
    const html = inRouter(<HelpPage />)
    expect(html).toContain('Cloud v0.1 supports 0.5 fps and 1 fps')
    expect(html).toContain('Cloud v0.1 accepts GPT-4.1 only')
    expect(html).toContain('description lines')
    expect(html).not.toContain('audio description (AD) captions')
    expect(html).not.toContain('caption template')
  })
})
