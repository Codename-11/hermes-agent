import assert from 'node:assert/strict'

import { test } from 'vitest'

import { desktopConfigPath, desktopGpuOverrideFromConfigText, normalizeDesktopGpuOverride } from './desktop-gpu-policy'

test('desktop GPU policy preserves automatic and both explicit overrides', () => {
  assert.equal(normalizeDesktopGpuOverride('auto'), null)
  assert.equal(normalizeDesktopGpuOverride(false), '0')
  assert.equal(normalizeDesktopGpuOverride('false'), '0')
  assert.equal(normalizeDesktopGpuOverride(true), '1')
  assert.equal(normalizeDesktopGpuOverride('true'), '1')
})

test('desktop GPU policy reads the nested config scalar without matching neighbors', () => {
  assert.equal(desktopGpuOverrideFromConfigText('desktop:\n  disable_gpu: false\nagent:\n  max_turns: 90\n'), '0')
  assert.equal(desktopGpuOverrideFromConfigText('desktop:\n  disable_gpu: "true" # forced\n'), '1')
  assert.equal(desktopGpuOverrideFromConfigText('other:\n  disable_gpu: true\ndesktop:\n  disable_gpu: auto\n'), null)
})

test('desktop config path defaults to the root profile when no marker exists', () => {
  const root = 'C:\\Users\\test\\AppData\\Local\\hermes'
  const userData = 'C:\\Users\\test\\AppData\\Roaming\\Hermes'

  assert.equal(desktopConfigPath(root, userData), 'C:\\Users\\test\\AppData\\Local\\hermes\\config.yaml')
})