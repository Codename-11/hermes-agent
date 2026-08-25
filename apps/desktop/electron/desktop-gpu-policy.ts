import fs from 'node:fs'
import path from 'node:path'

type DesktopGpuOverride = '0' | '1' | null

const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

function unquoteYamlScalar(value: string): string {
  const trimmed = value.trim().replace(/\s+#.*$/, '').trim()

  if (
    trimmed.length >= 2 &&
    ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'")))
  ) {
    return trimmed.slice(1, -1).trim()
  }

  return trimmed
}

/** Map the established desktop.disable_gpu values to Electron's env contract. */
function normalizeDesktopGpuOverride(value: unknown): DesktopGpuOverride {
  if (value === true) {
    return '1'
  }

  if (value === false) {
    return '0'
  }

  const normalized = String(value ?? '').trim().toLowerCase()

  if (['1', 'true', 'yes', 'on'].includes(normalized)) {
    return '1'
  }

  if (['0', 'false', 'no', 'off'].includes(normalized)) {
    return '0'
  }

  return null
}

/**
 * Read only desktop.disable_gpu from config.yaml without loading a YAML parser
 * into Electron's pre-ready path. Hermes' own config writer emits this ordinary
 * nested mapping shape; malformed or exotic YAML safely falls back to auto.
 */
function desktopGpuOverrideFromConfigText(text: string): DesktopGpuOverride {
  const lines = String(text || '').split(/\r?\n/)
  let desktopIndent: number | null = null

  for (const line of lines) {
    if (!line.trim() || line.trimStart().startsWith('#')) {
      continue
    }

    const indent = line.length - line.trimStart().length

    if (desktopIndent === null) {
      if (/^desktop\s*:\s*(?:#.*)?$/.test(line.trim())) {
        desktopIndent = indent
      }

      continue
    }

    if (indent <= desktopIndent) {
      desktopIndent = null

      if (/^desktop\s*:\s*(?:#.*)?$/.test(line.trim())) {
        desktopIndent = indent
      }

      continue
    }

    const match = line.trim().match(/^disable_gpu\s*:\s*(.*?)\s*$/)

    if (match) {
      return normalizeDesktopGpuOverride(unquoteYamlScalar(match[1]))
    }
  }

  return null
}

function readProfileName(userDataDir: string, hermesHome: string): string {
  try {
    const marker = JSON.parse(fs.readFileSync(path.join(userDataDir, 'active-profile.json'), 'utf8'))
    const profile = String(marker?.profile ?? '').trim().toLowerCase()

    if (profile === 'default' || PROFILE_NAME_RE.test(profile)) {
      return profile
    }
  } catch {
    // Fall through to the CLI's sticky active_profile marker.
  }

  try {
    const profile = fs.readFileSync(path.join(hermesHome, 'active_profile'), 'utf8').trim().toLowerCase()

    if (profile === 'default' || PROFILE_NAME_RE.test(profile)) {
      return profile
    }
  } catch {
    // Fresh installs have neither marker and use the root/default profile.
  }

  return 'default'
}

function desktopConfigPath(hermesHome: string, userDataDir: string): string {
  const profile = readProfileName(userDataDir, hermesHome)

  return profile === 'default'
    ? path.join(hermesHome, 'config.yaml')
    : path.join(hermesHome, 'profiles', profile, 'config.yaml')
}

/** Best-effort direct-launch bridge for shortcuts that bypass `hermes desktop`. */
function readDesktopGpuOverride(hermesHome: string, userDataDir: string): DesktopGpuOverride {
  try {
    return desktopGpuOverrideFromConfigText(fs.readFileSync(desktopConfigPath(hermesHome, userDataDir), 'utf8'))
  } catch {
    return null
  }
}

export {
  desktopConfigPath,
  desktopGpuOverrideFromConfigText,
  normalizeDesktopGpuOverride,
  readDesktopGpuOverride
}
export type { DesktopGpuOverride }