import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/plugins/hermes-bots/plugin.js'), 'utf8')

describe('Hermes Bots pane layout contract', () => {
  it('changes only the local pane id while preserving its docking metadata', () => {
    const data = source.match(
      /ctx\.register\(\{\s*\/\/[\s\S]*?id: 'bots-dock',[\s\S]*?title: 'Bots',[\s\S]*?data: \{([\s\S]*?)\},\s*render:/
    )?.[1]

    expect(data).toBeDefined()
    expect(data).toContain("placement: 'left'")
    expect(data).toContain("width: '260px'")
    expect(data).toContain("dock: { pane: 'sessions', pos: 'bottom' }")
    expect(data).not.toContain('closeBehavior')
    expect(source).not.toMatch(/id: 'pane',\s*area: 'panes',\s*title: 'Bots'/)
  })
})