import { resolveOauthRestAuth } from './native-auth-decisions'

export interface RemoteProfileAuthInput {
  authMode: string
  baseUrl: string
  token: string | null
}

export interface RemoteProfileFetchDeps<T> {
  ensureNativeAccessToken: (baseUrl: string) => Promise<string | null>
  fetchBearerJson: (url: string, bearer: string) => Promise<T>
  fetchCookieJson: (url: string) => Promise<T>
  fetchTokenJson: (url: string, token: string | null) => Promise<T>
}

/**
 * Fetch the remote profile catalog through the connection's real auth lane.
 * Native RFC 8252 sessions are cookieless and must use their stored bearer;
 * older OAuth sessions fall back to Electron's cookie partition; legacy token
 * gateways keep their static session token.
 */
export async function fetchRemoteProfilesJson<T>(
  input: RemoteProfileAuthInput,
  deps: RemoteProfileFetchDeps<T>
): Promise<T> {
  const url = `${input.baseUrl}/api/profiles`

  if (input.authMode !== 'oauth') {
    return deps.fetchTokenJson(url, input.token)
  }

  const nativeAccessToken = await deps.ensureNativeAccessToken(input.baseUrl)
  const auth = resolveOauthRestAuth(nativeAccessToken)

  if (auth.kind === 'bearer') {
    return deps.fetchBearerJson(url, auth.token)
  }

  return deps.fetchCookieJson(url)
}
