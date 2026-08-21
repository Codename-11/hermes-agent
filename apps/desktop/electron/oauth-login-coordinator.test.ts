import assert from 'node:assert/strict'

import { test } from 'vitest'

import { createOauthLoginCoordinator } from './oauth-login-coordinator'

test('shares one pending login for the same normalized gateway', async () => {
  const coordinator = createOauthLoginCoordinator()
  let starts = 0
  let finish!: (value: string) => void

  const operation = () => {
    starts += 1

    return new Promise<string>(resolve => {
      finish = resolve
    })
  }

  const first = coordinator.run('https://gw.example.com', operation)
  const second = coordinator.run('https://gw.example.com', operation)

  assert.equal(first, second)
  assert.equal(starts, 1)
  assert.equal(coordinator.isPending('https://gw.example.com'), true)

  finish('signed-in')

  assert.equal(await first, 'signed-in')
  assert.equal(await second, 'signed-in')
  assert.equal(coordinator.isPending('https://gw.example.com'), false)
})

test('allows different gateways to authenticate independently', async () => {
  const coordinator = createOauthLoginCoordinator()
  let starts = 0

  const [one, two] = await Promise.all([
    coordinator.run('https://one.example.com', async () => ++starts),
    coordinator.run('https://two.example.com', async () => ++starts)
  ])

  assert.deepEqual([one, two], [1, 2])
  assert.equal(starts, 2)
})

test('propagates a failed native login and clears it for an explicit retry', async () => {
  const coordinator = createOauthLoginCoordinator()
  let starts = 0

  const first = coordinator.run('https://gw.example.com', async () => {
    starts += 1

    throw new Error('native PKCE failed')
  })

  const concurrent = coordinator.run('https://gw.example.com', async () => {
    starts += 1

    return 'must not silently downgrade'
  })

  assert.equal(first, concurrent)
  await assert.rejects(first, /native PKCE failed/)
  await assert.rejects(concurrent, /native PKCE failed/)
  assert.equal(starts, 1)
  assert.equal(coordinator.isPending('https://gw.example.com'), false)

  assert.equal(
    await coordinator.run('https://gw.example.com', async () => {
      starts += 1

      return 'retried'
    }),
    'retried'
  )
  assert.equal(starts, 2)
})
