# Bundled plugins

Drop a `<name>/plugin.{ts,tsx}` here that default-exports a `HermesPlugin` and
it registers automatically at boot (vite glob in `../contrib/plugins.ts`), with
the same inventory + live enable/disable contract as runtime plugins.

Keep this tree for real shipped plugins (and the small authoring fixtures that
dogfood the SDK). One-off demos that rebuild a core chrome piece 1:1 do not
belong here — they double the UI and confuse Settings ▸ Plugins. Publish those
in the companion
[`hermes-example-plugins`](https://github.com/NousResearch/hermes-example-plugins)
repo instead.

The Axiom fork's bundled **Update Control** plugin belongs here because it
dogfoods the fork-local `host.updates` facade and provides a shipped management
surface. It reads detached local-client and active-backend snapshots, including
deploy/upstream disparity when core publishes it, then hands the active target
to the core-owned native updater. Do not add direct checks, branch writes, pull,
merge, install, restart, or relaunch mutation to plugin code; polling,
confirmation, dirty-tree policy, deploy reconciliation, and process handoff
stay in core.

User- and agent-authored plugins load at runtime from
`$HERMES_HOME/desktop-plugins/<name>/plugin.js` (the disk door) — see the
`hermes-desktop-plugins` skill.
