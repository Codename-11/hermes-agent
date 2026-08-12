import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useI18n } from '@/i18n'
import { FolderOpen } from '@/lib/icons'
import { notify, notifyError } from '@/store/notifications'
import { $projects } from '@/store/projects'
import {
  applyConfiguredDefaultProjectDir,
  ensureDefaultWorkspaceCwd,
  getDefaultProjectCwd,
  setDefaultProjectCwd
} from '@/store/session'

import { ListRow, SectionHeading } from './primitives'

export function DefaultProjectSetting() {
  const { t } = useI18n()
  const s = t.settings.sessions
  const projects = useStore($projects)
  const [dir, setDir] = useState<null | string>(null)
  const [projectCwd, setProjectCwdState] = useState(() => getDefaultProjectCwd())
  const [fallback, setFallback] = useState('')
  const [busy, setBusy] = useState(false)

  const projectOptions = useMemo(
    () =>
      projects
        .map(project => ({
          id: project.id,
          name: project.name,
          path: (
            project.primary_path ||
            project.folders.find(folder => folder.is_primary)?.path ||
            project.folders[0]?.path ||
            ''
          ).trim()
        }))
        .filter((project): project is { id: string; name: string; path: string } => Boolean(project.path)),
    [projects]
  )

  const selectedProject = projectOptions.find(project => project.path === projectCwd)

  useEffect(() => {
    const settings = window.hermesDesktop?.settings

    if (!settings) {
      return
    }

    let alive = true

    void settings.getDefaultProjectDir().then(result => {
      if (!alive) {
        return
      }

      setDir(result.dir)
      setFallback(result.defaultLabel)
      applyConfiguredDefaultProjectDir(result.dir)
    })

    return () => {
      alive = false
    }
  }, [])

  const chooseFolder = useCallback(async () => {
    const settings = window.hermesDesktop?.settings

    if (!settings) {
      return
    }

    setBusy(true)

    try {
      const picked = await settings.pickDefaultProjectDir()

      if (picked.canceled || !picked.dir) {
        return
      }

      const result = await settings.setDefaultProjectDir(picked.dir)
      setDefaultProjectCwd(null)
      setProjectCwdState('')
      setDir(result.dir)
      applyConfiguredDefaultProjectDir(result.dir)
      notify({ durationMs: 4_000, kind: 'success', message: s.defaultDirUpdated })
    } catch (err) {
      notifyError(err, s.updateDirFailed)
    } finally {
      setBusy(false)
    }
  }, [s])

  const clear = useCallback(async () => {
    const settings = window.hermesDesktop?.settings

    setBusy(true)

    try {
      if (settings) {
        await settings.setDefaultProjectDir(null)
      }

      setDefaultProjectCwd(null)
      setProjectCwdState('')
      setDir(null)
      applyConfiguredDefaultProjectDir(null)
      await ensureDefaultWorkspaceCwd()
    } catch (err) {
      notifyError(err, s.clearDirFailed)
    } finally {
      setBusy(false)
    }
  }, [s])

  const hasDefault = Boolean(projectCwd || dir)

  return (
    <div className="mb-6">
      <SectionHeading icon={FolderOpen} title={s.defaultDirTitle} />
      <p className="mb-2 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
        {s.defaultDirDesc}
      </p>
      {projectOptions.length > 0 && (
        <Select
          onValueChange={id => {
            const project = projectOptions.find(option => option.id === id)

            if (!project) {
              return
            }

            void (async () => {
              const settings = window.hermesDesktop?.settings

              if (settings) {
                await settings.setDefaultProjectDir(null)
              }

              setDir(null)
              applyConfiguredDefaultProjectDir(null)
              setDefaultProjectCwd(project.path)
              setProjectCwdState(project.path)
              notify({ durationMs: 4_000, kind: 'success', message: s.defaultDirUpdated })
            })().catch(err => notifyError(err, s.updateDirFailed))
          }}
          value={selectedProject?.id}
        >
          <SelectTrigger aria-label={s.defaultProjectSelect} className="mb-2 w-full">
            <SelectValue placeholder={s.defaultProjectPlaceholder} />
          </SelectTrigger>
          <SelectContent>
            {projectOptions.map(project => (
              <SelectItem key={project.id} value={project.id}>
                <span className="flex min-w-0 flex-col items-start">
                  <span>{project.name}</span>
                  <span className="max-w-96 truncate text-[0.6875rem] text-(--ui-text-tertiary)">{project.path}</span>
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <ListRow
        action={
          <div className="flex items-center gap-3">
            <Button disabled={busy} onClick={() => void chooseFolder()} size="sm" type="button" variant="textStrong">
              <FolderOpen className="size-3.5" />
              <span>{dir && !projectCwd ? s.change : s.chooseFolder}</span>
            </Button>
            {hasDefault && (
              <Button disabled={busy} onClick={() => void clear()} size="sm" type="button" variant="text">
                {s.clear}
              </Button>
            )}
          </div>
        }
        description={selectedProject?.path || dir || s.defaultsTo(fallback || '~')}
        title={selectedProject?.name || (dir ? dir : s.notSet)}
      />
    </div>
  )
}
