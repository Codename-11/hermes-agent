export interface ProfileSessionIdentity {
  id: string
  profile?: string
}

export const sessionPaletteItemId = (session: ProfileSessionIdentity): string =>
  `session-${session.profile?.trim() || 'default'}-${session.id}`
