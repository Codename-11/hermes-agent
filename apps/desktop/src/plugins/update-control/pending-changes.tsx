import { Badge, Codicon, fmtDateTime } from '@hermes/plugin-sdk'
import { useId } from 'react'

import { categorizeCommits, shortSha, type UpdateCommit } from './model'

function CommitRow({ commit }: { commit: ReturnType<typeof categorizeCommits>[number]['commits'][number] }) {
  const metadata = [commit.author, commit.at ? fmtDateTime.format(commit.at) : null, shortSha(commit.sha)]
    .filter(Boolean)
    .join(' · ')

  const diff =
    commit.shortstat ||
    [
      commit.filesChanged != null ? `${commit.filesChanged} files` : null,
      commit.additions != null ? `+${commit.additions}` : null,
      commit.deletions != null ? `−${commit.deletions}` : null
    ]
      .filter(Boolean)
      .join(' · ')

  return (
    <li className="py-2.5 first:pt-1 last:pb-1">
      <p className="text-xs font-medium text-(--ui-text-primary)">{commit.subject}</p>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.6875rem] text-(--ui-text-tertiary)">
        <span>{metadata}</span>
        {diff ? <span className="font-mono text-(--ui-text-quaternary)">{diff}</span> : null}
      </div>
    </li>
  )
}

export function PendingChanges({ commits, filesChanged, shortstat }: {
  commits: readonly UpdateCommit[]
  filesChanged?: number
  shortstat?: string
}) {
  const headingId = useId()
  const categories = categorizeCommits(commits)
  const populated = categories.filter(category => category.count > 0)

  return (
    <section aria-labelledby={headingId} className="border-t border-(--ui-stroke-tertiary) pt-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-(--ui-text-primary)" id={headingId}>
            Pending changes
          </h2>
          <p className="mt-1 text-xs text-(--ui-text-tertiary)">
            {commits.length} commit{commits.length === 1 ? '' : 's'}
            {filesChanged != null ? ` · ${filesChanged} changed files` : ''}
            {shortstat ? ` · ${shortstat}` : ''}
          </p>
        </div>
        <div aria-label="Change categories" className="flex flex-wrap gap-1.5" role="list">
          {categories.map(category => (
            <Badge className={category.count === 0 ? 'opacity-50' : ''} key={category.key}>
              {category.label} {category.count}
            </Badge>
          ))}
        </div>
      </div>

      {populated.length === 0 ? (
        <p className="mt-4 text-xs leading-5 text-(--ui-text-tertiary)">No pending commit details were provided.</p>
      ) : (
        <div className="mt-3 space-y-1">
          {populated.map(category => (
            <details className="group border-t border-(--ui-stroke-tertiary) py-2 first:border-t-0" key={category.key}>
              <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-semibold text-(--ui-text-secondary)">
                <Codicon className="transition-transform group-open:rotate-90" name="chevron-right" size="0.75rem" />
                {category.label}
                <span className="font-normal text-(--ui-text-quaternary)">{category.count}</span>
              </summary>
              <ul className="ml-5 divide-y divide-(--ui-stroke-tertiary)">
                {category.commits.map(commit => (
                  <CommitRow commit={commit} key={commit.sha} />
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}
    </section>
  )
}
