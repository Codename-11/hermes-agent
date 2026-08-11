import { describe, expect, it } from 'vitest'

import {
  categorizeCommits,
  derivePreparationView,
  formatHistoryEntry,
  friendlyError,
  hasUpdate,
  shortSha,
  type UpdateHistoryEntry,
  type UpdateStageSnapshot
} from './model'

describe('update summaries', () => {
  it('treats either the explicit flag or a positive behind count as an update', () => {
    expect(hasUpdate({ supported: true, updateAvailable: true })).toBe(true)
    expect(hasUpdate({ supported: true, behind: 2 })).toBe(true)
    expect(hasUpdate({ supported: false, behind: 2 })).toBe(false)
    expect(hasUpdate({ supported: true, behind: 0 })).toBe(false)
  })

  it('shortens commit identifiers without inventing one', () => {
    expect(shortSha('1234567890abcdef')).toBe('12345678')
    expect(shortSha('abc')).toBe('abc')
    expect(shortSha()).toBe('—')
  })

  it('turns unknown failures into useful general-purpose copy', () => {
    expect(friendlyError(new Error('bridge offline'))).toBe('bridge offline')
    expect(friendlyError('timeout')).toBe('timeout')
    expect(friendlyError(null)).toBe('Update information is unavailable right now.')
  })
})

describe('pending change categorization', () => {
  it('categorizes conventional commit prefixes while preserving fixed display order', () => {
    const categories = categorizeCommits([
      { sha: '1', summary: 'fix(ui): stop flicker', author: 'A', at: 1 },
      { sha: '2', summary: 'feat!: add staged updates', author: 'B', at: 2 },
      { sha: '3', summary: 'docs: explain lifecycle', author: 'C', at: 3 },
      { sha: '4', summary: 'chore: bump metadata', author: 'D', at: 4 },
      { sha: '5', summary: 'perf(core): cache status', author: 'E', at: 5 },
      { sha: '6', summary: 'refactor: extract model', author: 'F', at: 6 }
    ])

    expect(categories.map(category => [category.key, category.count])).toEqual([
      ['features', 1],
      ['fixes', 1],
      ['performance', 1],
      ['refactors', 1],
      ['docs', 1],
      ['other', 1]
    ])
    expect(categories[0]?.commits[0]?.subject).toBe('add staged updates')
    expect(categories[1]?.commits[0]?.subject).toBe('stop flicker')
  })

  it('keeps unknown and prefix-free subjects in Other', () => {
    const categories = categorizeCommits([
      { sha: '1', summary: 'build: package app', author: 'A', at: 1 },
      { sha: '2', summary: 'Improve copy', author: 'B', at: 2 }
    ])

    expect(categories.find(category => category.key === 'other')?.count).toBe(2)
  })
})

describe('preparation view derivation', () => {
  const status = { supported: true, updateAvailable: true, behind: 3 }

  it.each<[
    UpdateStageSnapshot | null,
    string,
    string | null,
    boolean
  ]>([
    [null, 'available', 'prepare', false],
    [{ state: 'preparing', phase: 'rebuild', percent: 70 }, 'preparing', null, false],
    [{ state: 'ready', targetSha: 'abcdef123' }, 'ready', 'restartAndApply', true],
    [{ state: 'invalid', invalidationReason: 'live checkout changed' }, 'invalid', 'prepare', true],
    [{ state: 'failed', error: 'build failed' }, 'failed', 'prepare', true]
  ])('derives %s as %s with the safe primary action', (stage, state, action, canDiscard) => {
    expect(derivePreparationView(status, stage)).toMatchObject({ action, canDiscard, state })
  })

  it('falls back to refresh when there is no update or the install is unsupported', () => {
    expect(derivePreparationView({ supported: true, behind: 0 }, null).action).toBe('refresh')
    expect(derivePreparationView({ supported: false, behind: 2 }, null)).toMatchObject({
      action: 'refresh',
      diagnostic: 'This install method does not support staged updates.'
    })
  })

  it('surfaces dirty checkout diagnostics without hiding the available update', () => {
    expect(derivePreparationView({ ...status, dirty: true }, null)).toMatchObject({
      action: 'prepare',
      diagnostic: 'Local changes are present. Preparation may require cleanup before it can continue.',
      state: 'available'
    })
  })
})

describe('history formatting', () => {
  it('formats completed ranges, elapsed duration, and result', () => {
    const entry: UpdateHistoryEntry = {
      branch: 'axiom',
      finishedAt: 67_500,
      fromSha: '111111111111',
      id: 'run-1',
      result: 'completed',
      startedAt: 5_000,
      toSha: '222222222222'
    }

    expect(formatHistoryEntry(entry)).toMatchObject({
      duration: '1m 2s',
      range: '11111111 → 22222222',
      resultLabel: 'Completed',
      tone: 'good'
    })
  })

  it('uses a useful fallback for failed entries with incomplete timing and range', () => {
    expect(formatHistoryEntry({ id: 'run-2', result: 'failed', error: 'handoff failed' })).toMatchObject({
      duration: '—',
      range: 'Unknown range',
      resultLabel: 'Failed',
      summary: 'handoff failed',
      tone: 'bad'
    })
  })
})
