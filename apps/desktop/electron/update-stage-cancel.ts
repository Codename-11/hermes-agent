import path from 'node:path'

export interface StagePreparationOwner {
  pid: number
  startedAt?: number
  targetSha?: string
  token: string
}

export function parseStagePreparationOwner(value: unknown): StagePreparationOwner | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const owner = value as Record<string, unknown>
  const pid = Number(owner.pid)
  const token = typeof owner.token === 'string' ? owner.token.trim() : ''
  const startedAt = Number(owner.startedAt)
  const targetSha = typeof owner.targetSha === 'string' && /^[0-9a-f]{40}$/i.test(owner.targetSha) ? owner.targetSha : undefined

  if (!Number.isInteger(pid) || pid <= 0 || !token) {
    return null
  }

  return {
    pid,
    token,
    startedAt: Number.isFinite(startedAt) ? startedAt : undefined,
    ...(targetSha ? { targetSha } : {})
  }
}

function normalized(value: string): string {
  return path.win32.normalize(value).replaceAll('\\', '/').toLowerCase()
}

export function commandOwnsStagePreparation(commandLine: string, scriptPath: string, stageRoot: string): boolean {
  if (!commandLine.trim()) {
    return false
  }

  const command = normalized(commandLine)
  const script = normalized(scriptPath)
  const stage = normalized(stageRoot)

  return command.includes(script) && command.includes(stage)
}

export function cancelledStageProgress(now = Date.now()) {
  return {
    schema: 1,
    phase: 'failed',
    percent: 100,
    message: 'Update preparation was cancelled.',
    updatedAt: now,
    cancelled: true
  }
}

export function cancelledStageResult(targetSha?: string, now = Date.now()) {
  return {
    schema: 1,
    ok: false,
    phase: 'cancelled',
    message: 'Update preparation was cancelled.',
    targetSha,
    finishedAt: now,
    cancelled: true
  }
}
