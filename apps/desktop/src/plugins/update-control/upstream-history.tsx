import { Badge, fmtDateTime, SegmentedControl } from '@hermes/plugin-sdk'
import { useEffect, useMemo, useState } from 'react'

import { type CommitScopeKey, presentCommit, shortSha, type UpdateCommit } from './model'

type ScopeFilter = 'all' | CommitScopeKey

const PAGE_SIZE = 25

const SCOPE_OPTIONS = [
  { id: 'all', label: 'All' },
  { id: 'desktop', label: 'Desktop' },
  { id: 'cli-backend', label: 'CLI & backend' },
  { id: 'skills-docs', label: 'Skills & docs' },
  { id: 'other', label: 'Other' }
] as const

export function UpstreamHistory({ commits, description }: { commits: readonly UpdateCommit[]; description: string }) {
  const [scope, setScope] = useState<ScopeFilter>('all')
  const [page, setPage] = useState(1)
  const presented = useMemo(() => commits.map(presentCommit), [commits])
  const visible = scope === 'all' ? presented : presented.filter(commit => commit.scope === scope)
  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE))
  const pageStart = (page - 1) * PAGE_SIZE
  const pageCommits = visible.slice(pageStart, pageStart + PAGE_SIZE)

  useEffect(() => setPage(1), [commits])

  const selectScope = (nextScope: ScopeFilter) => {
    setScope(nextScope)
    setPage(1)
  }

  return (
    <section aria-labelledby="upstream-history-heading" className="border-t border-(--ui-stroke-tertiary) pt-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-(--ui-text-primary)" id="upstream-history-heading">
            Hermes upstream history
          </h2>
          <p className="mt-1 text-xs text-(--ui-text-tertiary)">{description}</p>
        </div>
        <SegmentedControl onChange={selectScope} options={SCOPE_OPTIONS} value={scope} />
      </div>

      {visible.length === 0 ? (
        <p className="mt-4 text-xs leading-5 text-(--ui-text-tertiary)">No commits match this scope.</p>
      ) : (
        <>
          <div className="mt-3 overflow-x-auto rounded-md border border-(--ui-stroke-tertiary)">
            <table className="w-full min-w-[42rem] border-collapse text-left text-xs">
              <thead className="bg-(--ui-bg-secondary) text-[0.6875rem] font-medium text-(--ui-text-tertiary)">
                <tr>
                  <th className="w-24 px-3 py-2" scope="col">Type</th>
                  <th className="w-32 px-3 py-2" scope="col">Scope</th>
                  <th className="px-3 py-2" scope="col">Change</th>
                  <th className="w-64 px-3 py-2" scope="col">Commit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-(--ui-stroke-tertiary)">
                {pageCommits.map(commit => (
                  <tr key={commit.sha}>
                    <td className="px-3 py-2.5"><Badge>{commit.categoryLabel}</Badge></td>
                    <td className="px-3 py-2.5"><Badge>{commit.scopeLabel}</Badge></td>
                    <td className="px-3 py-2.5 font-medium text-(--ui-text-primary)">{commit.subject}</td>
                    <td className="px-3 py-2.5 font-mono text-[0.6875rem] text-(--ui-text-quaternary)">
                      {[commit.author, commit.at ? fmtDateTime.format(commit.at) : null, shortSha(commit.sha)]
                        .filter(Boolean)
                        .join(' · ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-center justify-between gap-3 text-xs text-(--ui-text-tertiary)">
            <span>{`${pageStart + 1}–${Math.min(pageStart + PAGE_SIZE, visible.length)} of ${visible.length}`}</span>
            <div className="flex items-center gap-2">
              <span>{`Page ${page} of ${pageCount}`}</span>
              <button
                aria-label="Previous page"
                className="rounded border border-(--ui-stroke-secondary) px-2 py-1 disabled:cursor-default disabled:opacity-40"
                disabled={page === 1}
                onClick={() => setPage(current => Math.max(1, current - 1))}
                type="button"
              >
                Previous
              </button>
              <button
                aria-label="Next page"
                className="rounded border border-(--ui-stroke-secondary) px-2 py-1 disabled:cursor-default disabled:opacity-40"
                disabled={page === pageCount}
                onClick={() => setPage(current => Math.min(pageCount, current + 1))}
                type="button"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
