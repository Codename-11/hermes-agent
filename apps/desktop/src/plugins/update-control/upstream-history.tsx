import { Badge, fmtDateTime, SegmentedControl } from '@hermes/plugin-sdk'
import { useMemo, useState } from 'react'

import { type CommitScopeKey, presentCommit, shortSha, type UpdateCommit } from './model'

type ScopeFilter = 'all' | CommitScopeKey

const SCOPE_OPTIONS = [
  { id: 'all', label: 'All' },
  { id: 'desktop', label: 'Desktop' },
  { id: 'cli-backend', label: 'CLI & backend' },
  { id: 'skills-docs', label: 'Skills & docs' },
  { id: 'other', label: 'Other' }
] as const

export function UpstreamHistory({ commits, description }: { commits: readonly UpdateCommit[]; description: string }) {
  const [scope, setScope] = useState<ScopeFilter>('all')
  const presented = useMemo(() => commits.map(presentCommit), [commits])
  const visible = scope === 'all' ? presented : presented.filter(commit => commit.scope === scope)

  return (
    <section aria-labelledby="upstream-history-heading" className="border-t border-(--ui-stroke-tertiary) pt-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-(--ui-text-primary)" id="upstream-history-heading">
            Hermes upstream history
          </h2>
          <p className="mt-1 text-xs text-(--ui-text-tertiary)">{description}</p>
        </div>
        <SegmentedControl onChange={setScope} options={SCOPE_OPTIONS} value={scope} />
      </div>

      {visible.length === 0 ? (
        <p className="mt-4 text-xs leading-5 text-(--ui-text-tertiary)">No commits match this scope.</p>
      ) : (
        <ul className="mt-3 divide-y divide-(--ui-stroke-tertiary)">
          {visible.map(commit => (
            <li className="py-3 first:pt-1" key={commit.sha}>
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge>{commit.categoryLabel}</Badge>
                <Badge>{commit.scopeLabel}</Badge>
                <span className="min-w-0 text-xs font-medium text-(--ui-text-primary)">{commit.subject}</span>
              </div>
              <p className="mt-1 font-mono text-[0.6875rem] text-(--ui-text-quaternary)">
                {[commit.author, commit.at ? fmtDateTime.format(commit.at) : null, shortSha(commit.sha)]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
