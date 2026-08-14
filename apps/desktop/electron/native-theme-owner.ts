export type NativeThemeSource = 'dark' | 'light' | 'system'

export const NATIVE_THEME_SOURCES = new Set<NativeThemeSource>(['dark', 'light', 'system'])

interface IpcEventLike {
  sender: unknown
}

interface PrimaryWindowLike {
  isDestroyed: () => boolean
  webContents: unknown
}

interface NativeThemeLike {
  themeSource: string
}

export function applyNativeThemeFromPrimary(
  event: IpcEventLike,
  mode: unknown,
  primaryWindow: null | PrimaryWindowLike,
  nativeTheme: NativeThemeLike,
  persist: (mode: NativeThemeSource) => void
): void {
  if (
    !primaryWindow ||
    primaryWindow.isDestroyed() ||
    event.sender !== primaryWindow.webContents ||
    !NATIVE_THEME_SOURCES.has(mode as NativeThemeSource)
  ) {
    return
  }

  const next = mode as NativeThemeSource

  if (nativeTheme.themeSource !== next) {
    nativeTheme.themeSource = next
    persist(next)
  }
}