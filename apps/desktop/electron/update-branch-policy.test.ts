import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  collectUpstreamDisparity,
  isDeployUpdateBranch,
  manualUpdateCommand,
  posixHandoffBranchArgs,
  stagedUpdaterBranchArgs,
  windowsHandoffBranchArgs
} from './update-branch-policy'

test('deploy branches share bare-update policy', () => {
  assert.equal(isDeployUpdateBranch('tgi'), true)
  assert.equal(isDeployUpdateBranch('axiom'), true)
  assert.equal(isDeployUpdateBranch('release/1.2'), false)
})

test('manual command stays bare for deploy branches and main', () => {
  assert.equal(manualUpdateCommand('main'), 'hermes update')
  assert.equal(manualUpdateCommand('tgi'), 'hermes update')
  assert.equal(manualUpdateCommand('axiom'), 'hermes update')
  assert.equal(manualUpdateCommand('release/1.2'), 'hermes update --branch release/1.2')
})

test('staged updater receives explicit bare-update intent for deploy branches', () => {
  assert.deepEqual(stagedUpdaterBranchArgs('tgi'), ['--bare-update'])
  assert.deepEqual(stagedUpdaterBranchArgs('axiom'), ['--bare-update'])
  assert.deepEqual(stagedUpdaterBranchArgs('main'), ['--branch', 'main'])
  assert.deepEqual(stagedUpdaterBranchArgs('release/1.2'), ['--branch', 'release/1.2'])
})

test('repo handoffs keep branch identity while selecting bare update', () => {
  assert.deepEqual(windowsHandoffBranchArgs('tgi'), ['-Branch', 'tgi', '-BareUpdate'])
  assert.deepEqual(windowsHandoffBranchArgs('release/1.2'), ['-Branch', 'release/1.2'])
  assert.deepEqual(posixHandoffBranchArgs('tgi'), ['--branch', 'tgi', '--bare-update'])
  assert.deepEqual(posixHandoffBranchArgs('release/1.2'), ['--branch', 'release/1.2'])
})

test('deploy disparity reports upstream/main...HEAD without changing installable state', async () => {
  const calls: string[][] = []
  const result = await collectUpstreamDisparity('tgi', async args => {
    calls.push(args)
    const command = args.join(' ')

    if (command === 'remote get-url upstream') {return { code: 0, stdout: 'https://example/upstream.git\n' }}
    if (command === 'fetch --quiet upstream main') {return { code: 0, stdout: '' }}
    if (command === 'rev-parse upstream/main') {return { code: 0, stdout: 'abc123\n' }}
    if (command === 'rev-list --left-right --count upstream/main...HEAD') {return { code: 0, stdout: '2\t194\n' }}

    return { code: 1, stdout: '' }
  })

  assert.deepEqual(result, {
    upstreamAhead: 194,
    upstreamBehind: 2,
    upstreamBranch: 'upstream/main',
    upstreamSha: 'abc123'
  })
  assert.deepEqual(calls, [
    ['remote', 'get-url', 'upstream'],
    ['fetch', '--quiet', 'upstream', 'main'],
    ['rev-parse', 'upstream/main'],
    ['rev-list', '--left-right', '--count', 'upstream/main...HEAD']
  ])
})

test('non-deploy, shallow, and failed upstream fetches omit disparity', async () => {
  let calls = 0
  assert.deepEqual(
    await collectUpstreamDisparity('release/1.2', async () => {
      calls += 1
      return { code: 0, stdout: '' }
    }),
    {}
  )
  assert.equal(calls, 0)

  assert.deepEqual(
    await collectUpstreamDisparity(
      'tgi',
      async () => {
        calls += 1
        return { code: 0, stdout: '' }
      },
      { isShallow: true }
    ),
    {}
  )
  assert.equal(calls, 0)

  assert.deepEqual(
    await collectUpstreamDisparity('tgi', async args =>
      args[0] === 'remote' ? { code: 0, stdout: 'upstream\n' } : { code: 1, stdout: '' }
    ),
    {}
  )
})
