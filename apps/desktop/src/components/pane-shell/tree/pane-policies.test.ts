import assert from 'node:assert/strict'

import { test } from 'vitest'

import { resolvePaneCloseAction } from './pane-close-policy'
import { edgeWeightsForShare, sanitizePaneShares } from './pane-persistence'

test('single-pane plugins disable while dismissible and multi-pane contributions only dismiss the pane', () => {
  assert.equal(
    resolvePaneCloseAction({ hasCloser: false, closeBehavior: undefined, source: 'plugin:one', sameSourceCount: 1 }),
    'disable-plugin'
  )
  assert.equal(
    resolvePaneCloseAction({ hasCloser: false, closeBehavior: undefined, source: 'plugin:many', sameSourceCount: 2 }),
    'dismiss'
  )
  assert.equal(
    resolvePaneCloseAction({ hasCloser: false, closeBehavior: 'dismiss', source: 'plugin:one', sameSourceCount: 1 }),
    'dismiss'
  )
  assert.equal(
    resolvePaneCloseAction({ hasCloser: true, closeBehavior: 'dismiss', source: 'plugin:one', sameSourceCount: 1 }),
    'closer'
  )
})

test('pane share persistence rejects untrusted values and recalls a target-added weight pair', () => {
  assert.deepEqual(sanitizePaneShares({ browser: 0.3, zero: 0, one: 1, nan: Number.NaN, text: '0.2' }), {
    browser: 0.3
  })
  assert.deepEqual(edgeWeightsForShare(0.3), [0.7, 0.3])
  assert.equal(edgeWeightsForShare(1.2), undefined)
})
