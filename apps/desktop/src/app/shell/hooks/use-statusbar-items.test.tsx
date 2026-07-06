import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'
import { $connection } from '@/store/session'
import {
  $backendUpdateApply,
  $backendUpdateStatus,
  $desktopVersion,
  $updateApply,
  $updateStatus
} from '@/store/updates'

import { useStatusbarItems } from './use-statusbar-items'

function resetStores() {
  $connection.set(null)
  $desktopVersion.set(null)
  $updateStatus.set(null)
  $backendUpdateStatus.set(null)
  $updateApply.set({ applying: false, stage: 'idle', message: '', percent: null, error: null, command: null, log: [] })
  $backendUpdateApply.set({ applying: false, stage: 'idle', message: '', percent: null, error: null, command: null, log: [] })
}

function StatusbarProbe({ remote = false }: { remote?: boolean }) {
  const { statusbarItems } = useStatusbarItems({
    agentsOpen: false,
    chatOpen: true,
    commandCenterOpen: false,
    extraLeftItems: [],
    extraRightItems: [],
    freshDraftReady: false,
    gatewayState: 'open',
    inferenceStatus: null,
    openAgents: () => {},
    openCommandCenterSection: () => {},
    requestGateway: async <T,>() => ({} as T),
    statusSnapshot: remote ? ({ version: '0.16.0' } as any) : null,
    toggleCommandCenter: () => {}
  })

  const client = statusbarItems.find(item => item.id === 'version-client')
  const backend = statusbarItems.find(item => item.id === 'version-backend')

  return (
    <div>
      <span data-testid="client-label">{client?.label}</span>
      <span data-testid="client-detail">{client?.detail}</span>
      <span data-testid="client-title">{String(client?.title ?? '')}</span>
      <span data-testid="backend-label">{backend?.label}</span>
      <span data-testid="backend-title">{String(backend?.title ?? '')}</span>
    </div>
  )
}

function renderProbe(remote = false) {
  render(
    <I18nProvider configClient={null}>
      <StatusbarProbe remote={remote} />
    </I18nProvider>
  )
}

describe('useStatusbarItems version update badges', () => {
  afterEach(() => {
    cleanup()
    resetStores()
  })

  it('reports client upstream carried/behind disparity in the footer statusbar tooltip', () => {
    $desktopVersion.set({
      appVersion: '0.16.0',
      electronVersion: '40.9.3',
      nodeVersion: '22.21.1',
      platform: 'win32',
      hermesRoot: 'C:/Users/Bailey/AppData/Local/hermes/hermes-agent'
    })
    $updateStatus.set({
      supported: true,
      branch: 'axiom',
      behind: 18,
      currentSha: '15a76ce2e39f',
      targetSha: '5e01a5db0000',
      upstreamBranch: 'upstream/main',
      upstreamAhead: 259,
      upstreamBehind: 18
    })

    renderProbe()

    expect(screen.getByTestId('client-label').textContent).toBe('v0.16.0 (+18)')
    expect(screen.getByTestId('client-detail').textContent).toBe('+259 carried / 18 behind')
    expect(screen.getByTestId('client-title').textContent).toContain('18 commits behind axiom')
    expect(screen.getByTestId('client-title').textContent).toContain('upstream/main: +259 carried, 18 behind')
  })

  it('reports remote backend deploy/upstream disparity in the footer statusbar tooltip', () => {
    $connection.set({
      baseUrl: 'http://127.0.0.1:9119',
      isFullscreen: false,
      mode: 'remote',
      nativeOverlayWidth: 0,
      token: 'test-token',
      wsUrl: 'ws://127.0.0.1:9119/api/ws',
      logs: [],
      windowButtonPosition: null
    })
    $desktopVersion.set({
      appVersion: '0.16.0',
      electronVersion: '40.9.3',
      nodeVersion: '22.21.1',
      platform: 'win32',
      hermesRoot: 'C:/Users/Bailey/AppData/Local/hermes/hermes-agent'
    })
    $backendUpdateStatus.set({
      supported: true,
      branch: 'axiom',
      behind: 18,
      deployBranch: 'origin/axiom',
      deployBehind: 0,
      upstreamBranch: 'upstream/main',
      upstreamBehind: 18,
      backendMessage: 'Pending backend update: 18 upstream commits.'
    })

    renderProbe(true)

    expect(screen.getByTestId('backend-label').textContent).toBe('backend v0.16.0 (+18)')
    expect(screen.getByTestId('backend-title').textContent).toContain('Pending backend update: 18 from upstream/main')
    expect(screen.getByTestId('backend-title').textContent).toContain('Pending backend update: 18 upstream commits.')
  })
})
