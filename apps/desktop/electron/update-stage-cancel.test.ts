import { describe, expect, it } from 'vitest'

import {
  cancelledStageProgress,
  cancelledStageResult,
  commandOwnsStagePreparation,
  parseStagePreparationOwner
} from './update-stage-cancel'

describe('stage preparation cancellation', () => {
  it('accepts only token-owned process records', () => {
    expect(parseStagePreparationOwner({ pid: 42, token: 'abc', startedAt: 100 })).toEqual({
      pid: 42,
      token: 'abc',
      startedAt: 100
    })
    expect(parseStagePreparationOwner({ pid: 0, token: 'abc' })).toBeNull()
    expect(parseStagePreparationOwner({ pid: 42, token: '' })).toBeNull()
  })

  it('requires the exact worker script and Hermes-owned stage root in the process command line', () => {
    const script = 'C:\\Users\\Bailey\\AppData\\Local\\hermes\\hermes-agent\\scripts\\desktop-stage-update.ps1'
    const stage = 'C:\\Users\\Bailey\\AppData\\Local\\hermes\\update-stage\\desktop'
    const command = `powershell -File "${script}" -StageRoot "${stage}"`

    expect(commandOwnsStagePreparation(command, script, stage)).toBe(true)
    expect(commandOwnsStagePreparation(command.replace('desktop-stage-update.ps1', 'other.ps1'), script, stage)).toBe(false)
    expect(commandOwnsStagePreparation(command.replace('update-stage\\desktop', 'update-stage\\foreign'), script, stage)).toBe(false)
  })

  it('creates terminal cancelled progress and result records', () => {
    expect(cancelledStageProgress(123)).toMatchObject({
      cancelled: true,
      phase: 'failed',
      percent: 100,
      updatedAt: 123
    })
    expect(cancelledStageResult('abc', 456)).toMatchObject({
      cancelled: true,
      finishedAt: 456,
      ok: false,
      phase: 'cancelled',
      targetSha: 'abc'
    })
  })
})
