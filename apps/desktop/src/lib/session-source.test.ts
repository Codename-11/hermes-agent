import { describe, expect, it } from 'vitest'

import {
  AUTOMATION_SESSION_SOURCE_IDS,
  isMessagingSource,
  isProjectConversationSource,
  MESSAGING_SESSION_SOURCE_IDS,
  sessionSourceSearchTerms,
  SIDEBAR_RECENTS_EXCLUDED_SOURCE_IDS
} from './session-source'

// Regression guard for #46761 / PR #47395: Photon (iMessage) must keep its own
// sidebar section. refreshMessagingSessions() filters rows through
// isMessagingSource(), so this entry is the sole condition that keeps Photon
// sessions out of generic recents. A silent removal would regress the feature
// with no test failure — these asserts pin the contract.
describe('photon messaging source registration', () => {
  it('treats photon as a messaging source (own sidebar section)', () => {
    expect(isMessagingSource('photon')).toBe(true)
  })

  it('is case/space insensitive on the source id', () => {
    expect(isMessagingSource('PHOTON')).toBe(true)
    expect(isMessagingSource('  photon ')).toBe(true)
  })

  it('exposes the iMessage/messages search aliases so Photon sessions are findable', () => {
    const terms = sessionSourceSearchTerms('photon')
    expect(terms).toContain('imessage')
    expect(terms).toContain('messages')
  })

  it('is registered in the messaging source id list', () => {
    expect(MESSAGING_SESSION_SOURCE_IDS).toContain('photon')
  })

  it('does not flag local/CLI-ish sources as messaging (guard sanity)', () => {
    expect(isMessagingSource('cli')).toBe(false)
    expect(isMessagingSource(null)).toBe(false)
    expect(isMessagingSource(undefined)).toBe(false)
  })
})

describe('sidebar source taxonomy', () => {
  it('keeps messaging and automation out of flat recents', () => {
    expect(SIDEBAR_RECENTS_EXCLUDED_SOURCE_IDS).toEqual(
      expect.arrayContaining(['discord', 'telegram', 'a2a', 'webhook', 'api_server', 'cron', 'kanban', 'subagent'])
    )
  })

  it('classifies A2A/API/webhook runs as automation rather than local conversation history', () => {
    expect(AUTOMATION_SESSION_SOURCE_IDS).toEqual(expect.arrayContaining(['a2a', 'api_server', 'webhook']))
  })

  it('admits only local conversations and fails unknown future sources closed', () => {
    expect(isProjectConversationSource('desktop')).toBe(true)
    expect(isProjectConversationSource(' CLI ')).toBe(true)
    expect(isProjectConversationSource(null)).toBe(true)
    expect(isProjectConversationSource('discord')).toBe(false)
    expect(isProjectConversationSource('a2a')).toBe(false)
    expect(isProjectConversationSource('future_runner')).toBe(false)
  })
})
