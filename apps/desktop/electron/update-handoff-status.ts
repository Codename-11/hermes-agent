import fs from 'node:fs'
import path from 'node:path'

export function hasRetainedDeployHandoff(options: {
  branch: string
  hermesHome: string
  repo: string
}): boolean {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(options.hermesHome, '.update_handoff.json'), 'utf8'))

    return path.resolve(String(value?.repo || '')) === path.resolve(options.repo) && value?.branch === options.branch
  } catch {
    return false
  }
}
