import { useEffect, useState } from 'react'

import { ActionsMenu, type MenuKit, renderActionItem } from '@/components/ui/actions-menu'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { isGitRepoPath, openWorktreeDialog } from '@/store/coding-status'

interface ProjectWorktreeActionProps {
  onNewSession: (path: null | string) => void
  projectLabel: string
  projectPath: null | string
  repoPath: null | string
}

function useRepoAvailable(repoPath: null | string): boolean {
  const [available, setAvailable] = useState(false)

  useEffect(() => {
    let current = true
    const path = repoPath?.trim()

    setAvailable(false)

    if (path) {
      void isGitRepoPath(path).then(isRepo => {
        if (current) {
          setAvailable(isRepo)
        }
      })
    }

    return () => {
      current = false
    }
  }, [repoPath])

  return available
}

/**
 * The explicit Project lifecycle split. Project detail uses the visible buttons;
 * overview-row can reuse ProjectWorktreeMenu without learning worktree rules.
 */
export function ProjectLifecycleActions({
  onNewSession,
  projectLabel,
  projectPath,
  repoPath
}: ProjectWorktreeActionProps) {
  const { t } = useI18n()
  const p = t.sidebar.projects
  const repoAvailable = useRepoAvailable(repoPath)

  return (
    <div className="flex flex-wrap items-center gap-1 px-2 py-1.5">
      <Button onClick={() => onNewSession(projectPath)} size="xs" variant="secondary">
        <Codicon name="comment-add" size="0.75rem" />
        {p.newSessionInProject}
      </Button>
      <Button
        disabled={!repoAvailable}
        onClick={() => void openWorktreeDialog({ repoPath: repoPath ?? undefined })}
        size="xs"
        variant="ghost"
      >
        <Codicon name="git-branch" size="0.75rem" />
        {p.newWorktreeSession}
      </Button>
      <span className="sr-only">{projectLabel}</span>
    </div>
  )
}

/** Reusable row menu for the overview integration lane (intentionally not wired here). */
export function ProjectWorktreeMenu({
  onNewSession,
  projectLabel,
  projectPath,
  repoPath
}: ProjectWorktreeActionProps) {
  const { t } = useI18n()
  const p = t.sidebar.projects
  const repoAvailable = useRepoAvailable(repoPath)

  const items = (kit: MenuKit) => (
    <>
      {renderActionItem(kit, {
        icon: 'comment-add',
        key: 'new-session',
        label: p.newSessionInProject,
        onSelect: () => onNewSession(projectPath)
      })}
      {renderActionItem(kit, {
        disabled: !repoAvailable,
        icon: 'git-branch',
        key: 'new-worktree-session',
        label: p.newWorktreeSession,
        onSelect: () => void openWorktreeDialog({ repoPath: repoPath ?? undefined })
      })}
    </>
  )

  return (
    <ActionsMenu ariaLabel={p.lifecycleMenu(projectLabel)} contentClassName="w-56" items={items}>
      <Button aria-label={p.lifecycleMenu(projectLabel)} size="icon-xs" variant="ghost">
        <Codicon name="kebab-vertical" size="0.75rem" />
      </Button>
    </ActionsMenu>
  )
}
