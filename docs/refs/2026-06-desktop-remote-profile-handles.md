# Desktop remote profile handles

Status: Axiom fork patch, temporary until upstream lands equivalent peer-gateway/profile switching.
Last reviewed: 2026-06-16

## Why this exists

Upstream `main` already supports Desktop remote gateways and per-profile remote overrides, but it does not yet provide an operator-friendly way to discover named profiles from a remote gateway and make them visible beside local profiles in the Desktop profile rail.

Axiom needs that workflow so operators can keep a local Desktop backend available while also adding remote Atlas/Titan-style profiles as first-class switch targets. The profile rail then becomes the practical local/remote gateway switcher without requiring operators to manually create stub profiles and copy remote connection settings by hand.

## Local behavior added

Settings -> Gateway Connection now exposes a **Remote profiles** panel when the selected scope is configured as a remote gateway.

The panel:

1. Calls the selected remote gateway's `/api/profiles` endpoint using the same auth mode as the gateway connection.
2. Shows the remote gateway's named profiles in the settings UI.
3. Lets an operator add or update a distinct local profile handle for a remote profile.
4. Pins that local handle to the selected remote gateway as a per-profile remote override, including the special remote `default` profile.
5. Leaves the existing profile rail/sidebar and keyboard shortcuts as the switching surface.

This specifically supports Axiom/TGI Desktop cases where the local machine and a remote server both expose a `default` profile/persona. The remote Atlas/default profile can be pinned as a local handle such as `tgi-atlas` while the local Atlas/default remains `default`.

The implementation intentionally does **not** port upstream draft PR #39337 wholesale. That draft is broad and stale relative to current `main`; this patch keeps the fork delta narrow and retire-able.

## Files owned by this patch

- `apps/desktop/electron/connection-config.cjs`
  - Preserves the pinned remote profile name (`remoteProfile`) through sanitized per-profile remote config and WebSocket URL generation.
- `apps/desktop/electron/connection-config.test.cjs`
  - Covers remote-profile metadata and profile query routing.
- `apps/desktop/electron/main.cjs`
  - Adds IPC handlers to list remote profiles and pin a named local profile handle to a specific remote profile.
- `apps/desktop/electron/preload.cjs`
  - Exposes the new IPC methods to the renderer.
- `apps/desktop/src/global.d.ts`
  - Types the new Desktop bridge methods/results.
- `apps/desktop/src/app/settings/gateway-settings.tsx`
  - Adds the Remote profiles panel, editable local-handle input, and pin/add workflow.
- `apps/desktop/src/app/settings/gateway-settings.remote-profiles.test.ts`
  - Covers profile-safe handle normalization and remote/default handle suggestions.
- `apps/desktop/src/i18n/*.ts`
  - Adds user-facing strings for the panel.
- `FORK.md`
  - Tracks the patch group, focused checks, and retirement criteria.

## Upstream references

Watch these upstream PRs or successors:

- <https://github.com/NousResearch/hermes-agent/pull/39337> — draft peer gateway settings/aggregation/routing work. Closest conceptual upstream target, but too stale/broad to cherry-pick safely.
- <https://github.com/NousResearch/hermes-agent/pull/39778> — merged per-profile remote gateway hosts. This patch builds on that landed behavior.
- <https://github.com/NousResearch/hermes-agent/pull/39837> — profile switch/gateway-swap race hardening.
- <https://github.com/NousResearch/hermes-agent/pull/44855> — reconnect/active remote profile sync hardening.
- <https://github.com/NousResearch/hermes-agent/pull/45992> — model selector refresh on profile switch.
- <https://github.com/NousResearch/hermes-agent/pull/46458> — route profile-scoped settings to the correct profile in global remote mode.

Equivalent upstream functionality may land under different PR numbers. Judge by behavior, not just PR identity.

## Retirement criteria

Remove this patch when upstream provides an equivalent or better Desktop workflow that satisfies all of these:

1. A Desktop operator can see profiles available from a remote gateway without manually creating local stubs first.
2. The operator can add/select local and remote profile/gateway targets from a clear UI surface.
3. Switching between those targets routes chat/session/profile-scoped API calls to the correct backend.
4. Remote profile settings survive Desktop restart without requiring token or URL re-entry.
5. Bad or unreachable remotes fail visibly and do not silently leave the UI on the wrong backend.
6. Upstream has regression coverage or a documented manual smoke path for local -> remote -> local switching.

When those are true:

1. Merge/update to upstream first.
2. Remove the fork IPC methods, renderer panel, strings, and this docs entry.
3. Update `FORK.md` to mark this group retired with the upstream commit/PR that replaced it.
4. Run the focused checks below plus a manual Desktop smoke test.

## Focused checks

```bash
node --check apps/desktop/electron/main.cjs
node --check apps/desktop/electron/preload.cjs
cd apps/desktop && npm run typecheck
```

Manual smoke test:

1. Open Settings -> Gateway Connection.
2. Configure All profiles or a named profile as Remote gateway.
3. Authenticate/test the remote.
4. Click **Load remote profiles**.
5. Add or pin a remote profile. For remote `default`, use a distinct local handle such as `tgi-atlas`; do not reuse local `default`.
6. Select that handle from the profile rail; verify chat/session traffic lands on the remote backend.
7. Switch back to a local profile; verify traffic returns to the local backend.
8. Restart Desktop and confirm the pinned remote profile remains available.

## Security notes

- Do not commit remote URLs, session tokens, OAuth cookies, or screenshots containing credentials.
- The patch reuses Desktop's encrypted saved connection config for token-backed gateways.
- OAuth gateways use the existing Desktop OAuth session path; no token value is exposed to the renderer.
