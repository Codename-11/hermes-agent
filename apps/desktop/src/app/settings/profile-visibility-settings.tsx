import { useStore } from '@nanostores/react'

import { useI18n } from '@/i18n'
import { Users } from '@/lib/icons'
import {
  $hiddenProfiles,
  $profileOrder,
  $profiles,
  normalizeProfileKey,
  setProfileHidden,
  sortByProfileOrder
} from '@/store/profile'

import { SectionHeading, ToggleRow } from './primitives'

export function ProfileVisibilitySettings() {
  const { t } = useI18n()
  const profiles = useStore($profiles)
  const hiddenProfiles = useStore($hiddenProfiles)
  const profileOrder = useStore($profileOrder)

  const namedProfiles = sortByProfileOrder(
    profiles.filter(profile => !profile.is_default),
    profileOrder
  )

  if (namedProfiles.length === 0) {
    return null
  }

  return (
    <div>
      <SectionHeading icon={Users} title={t.profiles.visibilityTitle} />
      <p className="max-w-2xl text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
        {t.profiles.visibilityDesc}
      </p>
      <div className="mt-2">
        {namedProfiles.map(profile => {
          const key = normalizeProfileKey(profile.name)

          return (
            <ToggleRow
              checked={!hiddenProfiles.includes(key)}
              description={t.profiles.showProfileInUiDesc}
              key={key}
              label={t.profiles.showProfileInUi(profile.name)}
              onChange={shown => setProfileHidden(profile.name, !shown)}
            />
          )
        })}
      </div>
    </div>
  )
}
