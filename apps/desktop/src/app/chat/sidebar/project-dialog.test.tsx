import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type * as Nanostores from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ProjectDialog } from './project-dialog'

afterEach(cleanup)

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      common: { cancel: 'Cancel', save: 'Save' },
      sidebar: {
        projects: {
          addFolder: 'Add folder',
          create: 'Create',
          createDesc: 'Create a new project',
          createFailed: 'Failed to create project',
          createTitle: 'New project',
          foldersLabel: 'Folders',
          folderPathLabel: 'Directory path',
          folderPathPlaceholder: '/path/to/project',
          ideaGenerate: 'Generate',
          ideaGenerating: 'Generating…',
          ideaLabel: 'Idea',
          ideaPlaceholder: 'What are you building?',
          ideaShuffle: 'Shuffle ideas',
          namePlaceholder: 'Project name',
          noFolders: 'No folders yet',
          primaryBadge: 'Primary',
          removeFolder: 'Remove folder'
        }
      }
    }
  })
}))

// $projectDialog is a real nanostore atom in the app; recreate it here so
// useStore behaves identically without pulling in the rest of the projects
// store (backend calls, project list, etc.) which is irrelevant to the Tip fix.
// vi.mock factories are hoisted above the rest of the file, so the atom must
// be created inside vi.hoisted to exist by the time the factory runs.
const { $projectDialog } = vi.hoisted(() => {
  const { atom } = require('nanostores') as typeof Nanostores

  return {
    $projectDialog: atom<{ mode: 'create' | 'rename' | 'add-folder'; name?: string; projectId?: string } | null>({
      mode: 'create'
    })
  }
})

const { closeProjectDialog, createProject, ensureProjectFolder, notifyError } = vi.hoisted(() => ({
  closeProjectDialog: vi.fn(),
  createProject: vi.fn(),
  ensureProjectFolder: vi.fn(async (path: string) => `/resolved${path}`),
  notifyError: vi.fn()
}))

vi.mock('@/store/projects', () => ({
  $projectDialog,
  addProjectFolder: vi.fn(),
  closeProjectDialog,
  createProject,
  ensureProjectFolder,
  generateProjectIdea: vi.fn(),
  pickProjectFolder: vi.fn(async () => '/Users/test/my-folder'),
  renameProject: vi.fn()
}))

vi.mock('@/store/notifications', () => ({
  notifyError
}))

vi.mock('@/lib/project-idea-templates', () => ({
  randomIdeaTemplates: () => [{ emoji: '🚀', idea: 'A rocket tracker', label: 'Rocket tracker' }]
}))

const tipTrigger = (el: HTMLElement) => el.closest('[data-slot="tooltip-trigger"]')

describe('ProjectDialog', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('wraps the "shuffle idea" button in a Tip', () => {
    render(<ProjectDialog />)

    const button = screen.getByRole('button', { name: 'Shuffle ideas' })
    expect(tipTrigger(button)).toBeTruthy()
  })

  it('wraps the "remove folder" button in a Tip once a folder is added', async () => {
    render(<ProjectDialog />)

    fireEvent.click(screen.getByRole('button', { name: 'Add folder' }))

    const button = await screen.findByRole('button', { name: 'Remove folder' })
    expect(tipTrigger(button)).toBeTruthy()
    expect(ensureProjectFolder).not.toHaveBeenCalled()
  })

  it('uses a browsed existing folder without invoking directory creation', async () => {
    render(<ProjectDialog />)

    fireEvent.change(screen.getByPlaceholderText('Project name'), { target: { value: 'Browsed' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add folder' }))
    await screen.findByRole('button', { name: 'Remove folder' })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(createProject).toHaveBeenCalledOnce())
    expect(ensureProjectFolder).not.toHaveBeenCalled()
    expect(createProject).toHaveBeenCalledWith(
      expect.objectContaining({ folders: ['/Users/test/my-folder'], name: 'Browsed' })
    )
  })

  it('creates a typed missing directory only when Create is explicitly submitted', async () => {
    render(<ProjectDialog />)

    fireEvent.change(screen.getByPlaceholderText('Project name'), { target: { value: 'Demo' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Directory path' }), {
      target: { value: '/typed/new-project' }
    })

    expect(ensureProjectFolder).not.toHaveBeenCalled()
    expect(createProject).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(createProject).toHaveBeenCalledOnce())
    expect(ensureProjectFolder).toHaveBeenCalledWith('/typed/new-project')
    expect(createProject).toHaveBeenCalledWith(
      expect.objectContaining({ folders: ['/resolved/typed/new-project'], name: 'Demo', use: true })
    )
    expect(closeProjectDialog).toHaveBeenCalled()
  })

  it('surfaces typed-directory creation errors without creating a project', async () => {
    ensureProjectFolder.mockRejectedValueOnce(new Error('permission denied'))
    render(<ProjectDialog />)

    fireEvent.change(screen.getByPlaceholderText('Project name'), { target: { value: 'Demo' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Directory path' }), {
      target: { value: '/blocked/new-project' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(notifyError).toHaveBeenCalledWith(expect.any(Error), 'Failed to create project'))
    expect(createProject).not.toHaveBeenCalled()
    expect(closeProjectDialog).not.toHaveBeenCalled()
  })
})
