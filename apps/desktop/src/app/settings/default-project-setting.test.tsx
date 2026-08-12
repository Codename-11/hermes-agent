import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type * as React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { $projects } from '@/store/projects'
import { $connection, setDefaultProjectCwd, workspaceCwdForNewSession } from '@/store/session'
import type { ProjectInfo } from '@/types/hermes'

import { DefaultProjectSetting } from './default-project-setting'

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      settings: {
        sessions: {
          change: 'Change',
          chooseFolder: 'Choose folder',
          clear: 'Clear',
          clearDirFailed: 'Could not clear default directory',
          defaultDirDesc: 'New sessions start in this project or folder.',
          defaultDirTitle: 'Default project',
          defaultDirUpdated: 'Default project updated',
          defaultProjectPlaceholder: 'Choose a saved project',
          defaultProjectSelect: 'Default project for new sessions',
          defaultsTo: (label: string) => `Defaults to ${label}.`,
          notSet: 'Not set',
          updateDirFailed: 'Could not update default directory'
        }
      }
    }
  })
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

vi.mock('@/components/ui/select', () => ({
  Select: ({ children, onValueChange, value }: { children: React.ReactNode; onValueChange: (value: string) => void; value?: string }) => (
    <select aria-label="Default project for new sessions" onChange={event => onValueChange(event.target.value)} value={value ?? ''}>
      <option value="">Choose a saved project</option>
      {children}
    </select>
  ),
  SelectContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  SelectItem: ({ value }: { children: React.ReactNode; value: string }) => <option value={value}>{value}</option>,
  SelectTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  SelectValue: () => null
}))

const project: ProjectInfo = {
  archived: false,
  board_slug: null,
  color: null,
  created_at: 0,
  description: null,
  folders: [{ added_at: 0, is_primary: true, label: null, path: '/srv/axiom' }],
  icon: null,
  id: 'p_axiom',
  name: 'Axiom',
  primary_path: '/srv/axiom',
  slug: 'axiom'
}

afterEach(() => {
  cleanup()
  setDefaultProjectCwd(null)
  $connection.set(null)
  $projects.set([])
})

describe('DefaultProjectSetting', () => {
  it('uses a saved project root for new sessions without invoking the local folder bridge', () => {
    $connection.set({ baseUrl: 'http://backend', mode: 'remote', profile: 'victor' } as never)
    $projects.set([project])

    render(<DefaultProjectSetting />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Default project for new sessions' }), {
      target: { value: 'p_axiom' }
    })

    expect(workspaceCwdForNewSession()).toBe('/srv/axiom')
    expect(screen.getAllByText('Axiom')).toHaveLength(1)
    expect(screen.getAllByText('/srv/axiom')).toHaveLength(1)
  })
})
