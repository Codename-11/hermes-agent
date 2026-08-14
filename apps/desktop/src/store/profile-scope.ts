import { atom } from 'nanostores'

// Canonical Desktop workspace key: trimmed, empty → "default". Kept in this
// side-effect-free module so generic persisted UI stores do not import the
// gateway/profile orchestration module merely to follow workspace scope.
export function normalizeProfileKey(name: string | null | undefined): string {
  const value = (name ?? '').trim()

  return value || 'default'
}

// The profile the live gateway WebSocket is currently connected to. Profile
// orchestration re-exports and drives this atom; workspace stores only observe it.
export const $activeGatewayProfile = atom<string>('default')

// False only during renderer cold start, before use-gateway-boot has adopted the
// primary window's authoritative profile. Consumers that restore profile-scoped
// state must not treat the synthetic initial "default" as a real profile yet.
export const $gatewayProfileAdopted = atom(false)
