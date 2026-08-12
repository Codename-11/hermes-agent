import assert from 'node:assert/strict'

import { test } from 'vitest'

import { createUpdateOperationCoordinator } from './update-operation-coordinator'

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(done => {
    resolve = done
  })

  return { promise, resolve }
}

test('rejects a competing update mutation while one operation owns the lifecycle', async () => {
  const coordinator = createUpdateOperationCoordinator()
  const gate = deferred<string>()
  const sync = coordinator.run('sync-upstream', () => gate.promise)

  assert.equal(coordinator.active(), 'sync-upstream')
  assert.deepEqual(await coordinator.run('prepare', async () => 'prepared'), {
    acquired: false,
    active: 'sync-upstream'
  })

  gate.resolve('published')
  assert.deepEqual(await sync, { acquired: true, value: 'published' })
  assert.equal(coordinator.active(), null)
})

test('releases ownership after failure so the user can retry', async () => {
  const coordinator = createUpdateOperationCoordinator()

  await assert.rejects(
    coordinator.run('prepare', async () => {
      throw new Error('failed')
    }),
    /failed/
  )

  assert.deepEqual(await coordinator.run('sync-upstream', async () => 'ok'), {
    acquired: true,
    value: 'ok'
  })
})
