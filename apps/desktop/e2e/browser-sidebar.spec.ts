import { expect, test } from '@playwright/test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'

test.describe('Browser sidebar entry', () => {
  let fixture: MockBackendFixture

  test.beforeAll(async () => {
    fixture = await setupMockBackend()
    await waitForAppReady(fixture, 120_000)
  })

  test.afterAll(async () => {
    await fixture?.cleanup()
  })

  test('opens and fronts the singleton in-app Browser tab', async () => {
    const { page } = fixture
    const browserRow = page.locator('[data-slot="sidebar"] button').filter({ hasText: 'Browser' }).first()

    await expect(browserRow).toBeVisible()
    await browserRow.click()

    const browserTab = page.getByRole('tab', { name: 'Browser' })

    await expect(browserTab).toBeVisible()
    await expect(browserTab).toHaveAttribute('aria-selected', 'true')
  })
})
