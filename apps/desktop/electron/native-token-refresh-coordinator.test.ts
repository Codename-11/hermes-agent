import assert from 'node:assert/strict'

import { test } from 'vitest'

import { createNativeTokenRefreshCoordinator } from './native-token-refresh-coordinator'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void

  const promise = new Promise<T>((ok, fail) => {
    resolve = ok
    reject = fail
  })

  return { promise, resolve, reject }
}

test('concurrent refreshes for one origin share a single rotation', async () => {
  const coordinator = createNativeTokenRefreshCoordinator()
  const gate = deferred<string>()
  let rotations = 0

  const rotate = async () => {
    rotations++

    return gate.promise
  }

  const first = coordinator.run('https://gw.example.com', rotate)
  const second = coordinator.run('https://gw.example.com/', rotate)

  await Promise.resolve()
  assert.equal(rotations, 1)
  gate.resolve('rotated-access-token')
  assert.deepEqual(await Promise.all([first, second]), ['rotated-access-token', 'rotated-access-token'])
})

test('different gateway paths on one origin refresh independently', async () => {
  const coordinator = createNativeTokenRefreshCoordinator()
  let rotations = 0

  const results = await Promise.all([
    coordinator.run('https://gw.example.com/alpha', async () => `token-${++rotations}`),
    coordinator.run('https://gw.example.com/beta', async () => `token-${++rotations}`)
  ])

  assert.equal(rotations, 2)
  assert.deepEqual(new Set(results), new Set(['token-1', 'token-2']))
})

test('different origins refresh independently', async () => {
  const coordinator = createNativeTokenRefreshCoordinator()
  let rotations = 0

  const results = await Promise.all([
    coordinator.run('https://one.example.com', async () => `token-${++rotations}`),
    coordinator.run('https://two.example.com', async () => `token-${++rotations}`)
  ])

  assert.equal(rotations, 2)
  assert.deepEqual(new Set(results), new Set(['token-1', 'token-2']))
})

test('a failed refresh is shared but does not poison the next attempt', async () => {
  const coordinator = createNativeTokenRefreshCoordinator()
  const failure = new Error('temporary gateway outage')
  const gate = deferred<string>()
  let rotations = 0

  const first = coordinator.run('https://gw.example.com', async () => {
    rotations++

    return gate.promise
  })

  const second = coordinator.run('https://gw.example.com', async () => {
    rotations++

    return 'should-not-run'
  })

  gate.reject(failure)

  await assert.rejects(first, error => error === failure)
  await assert.rejects(second, error => error === failure)
  assert.equal(rotations, 1)

  assert.equal(
    await coordinator.run('https://gw.example.com', async () => {
      rotations++

      return 'recovered-token'
    }),
    'recovered-token'
  )
  assert.equal(rotations, 2)
})
