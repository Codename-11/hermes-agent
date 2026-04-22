#!/usr/bin/env -S node --max-old-space-size=8192 --expose-gc
import { bootBanner } from './bootBanner.js'
import { GatewayClient } from './gatewayClient.js'
import { setupGracefulExit } from './lib/gracefulExit.js'
import { formatBytes, type HeapDumpResult, performHeapDump } from './lib/memory.js'
import { type MemorySnapshot, startMemoryMonitor } from './lib/memoryMonitor.js'
import { getSession, saveSession } from './remoteSessions.js'
import { LocalSubprocessTransport } from './transport/LocalSubprocessTransport.js'
import { RelayTransport } from './transport/RelayTransport.js'
import type { Transport } from './transport/Transport.js'

if (!process.stdin.isTTY) {
  console.log('hermes-tui: no TTY')
  process.exit(0)
}

process.stdout.write(bootBanner())

// ── Transport selection ────────────────────────────────────────────────
// Keep arg parsing deliberately tiny — anything more elaborate lands in
// hermes_cli/main.py (the proper home for a full CLI).
const argvRemote = (() => {
  const a = process.argv

  for (let i = 2; i < a.length; i++) {
    if (a[i] === '--remote' && i + 1 < a.length) {return a[i + 1]}
    const v = a[i]

    if (v?.startsWith('--remote=')) {return v.slice('--remote='.length)}
  }

  return null
})()

const remoteUrl = process.env.HERMES_RELAY_URL?.trim() || argvRemote?.trim() || null

// Active RelayTransport handle (null in local mode). We need this at module
// scope to wire the resize pump after transport construction.
let activeRelay: null | RelayTransport = null

const buildTransport = async (): Promise<Transport> => {
  if (!remoteUrl) {
    return new LocalSubprocessTransport()
  }

  // Credential precedence: explicit HERMES_RELAY_TOKEN env, then stored
  // session token for this URL, then pairing code. The CLI (hermes --remote)
  // enforces that at least one is present before spawning us — a missing
  // credential here means someone ran the TUI directly without going
  // through the CLI.
  const envToken = process.env.HERMES_RELAY_TOKEN?.trim() || undefined
  const pairingCode = process.env.HERMES_RELAY_CODE?.trim() || undefined
  let sessionToken = envToken

  if (!sessionToken && !pairingCode) {
    const stored = await getSession(remoteUrl)
    if (stored) {sessionToken = stored.token}
  }

  const relay = new RelayTransport({
    url: remoteUrl,
    sessionToken,
    pairingCode,
    deviceName: process.env.HERMES_RELAY_DEVICE_NAME?.trim() || `hermes-tui (${process.platform})`,
    deviceId: process.env.HERMES_RELAY_DEVICE_ID?.trim() || undefined
  })

  // Persist the freshly-minted token back to ~/.hermes/remote-sessions.json
  // so subsequent `hermes --remote <url>` launches reconnect without a code.
  relay.onAuthSuccess((token, serverVersion) => {
    void saveSession(remoteUrl, token, serverVersion)
  })

  activeRelay = relay

  return relay
}

const gw = new GatewayClient(await buildTransport())

gw.start()

// ── Resize pump (remote only) ──────────────────────────────────────────
// The local subprocess transport inherits the parent TTY, so SIGWINCH
// propagates to the `tui_gateway` subprocess natively. Over WSS there's
// no SIGWINCH — we have to forward cols/rows explicitly as a tui.resize
// envelope. The relay translates to a `terminal.resize` JSON-RPC request.
if (activeRelay) {
  let lastCols = process.stdout.columns ?? 0
  let lastRows = process.stdout.rows ?? 0
  process.stdout.on('resize', () => {
    const cols = process.stdout.columns ?? lastCols
    const rows = process.stdout.rows ?? lastRows

    if (cols === lastCols && rows === lastRows) {return}
    lastCols = cols
    lastRows = rows
    activeRelay?.sendResize(cols, rows)
  })
}

const dumpNotice = (snap: MemorySnapshot, dump: HeapDumpResult | null) =>
  `hermes-tui: ${snap.level} memory (${formatBytes(snap.heapUsed)}) — auto heap dump → ${dump?.heapPath ?? '(failed)'}\n`

setupGracefulExit({
  cleanups: [() => gw.kill()],
  onError: (scope, err) => {
    const message = err instanceof Error ? `${err.name}: ${err.message}` : String(err)

    process.stderr.write(`hermes-tui ${scope}: ${message.slice(0, 2000)}\n`)
  },
  onSignal: signal => process.stderr.write(`hermes-tui: received ${signal}\n`)
})

const stopMemoryMonitor = startMemoryMonitor({
  onCritical: (snap, dump) => {
    process.stderr.write(dumpNotice(snap, dump))
    process.stderr.write('hermes-tui: exiting to avoid OOM; restart to recover\n')
    process.exit(137)
  },
  onHigh: (snap, dump) => process.stderr.write(dumpNotice(snap, dump))
})

if (process.env.HERMES_HEAPDUMP_ON_START === '1') {
  void performHeapDump('manual')
}

process.on('beforeExit', () => stopMemoryMonitor())

const [{ render }, { App }] = await Promise.all([import('@hermes/ink'), import('./app.js')])

render(<App gw={gw} />, { exitOnCtrlC: false })
