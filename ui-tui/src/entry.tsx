#!/usr/bin/env -S node --max-old-space-size=8192 --expose-gc
import { bootBanner } from './bootBanner.js'
import { GatewayClient } from './gatewayClient.js'
import { setupGracefulExit } from './lib/gracefulExit.js'
import { formatBytes, type HeapDumpResult, performHeapDump } from './lib/memory.js'
import { type MemorySnapshot, startMemoryMonitor } from './lib/memoryMonitor.js'
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
// Phase 3 (hermes_cli/main.py is the proper home for a full CLI).
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

const buildTransport = (): Transport => {
  if (!remoteUrl) {
    return new LocalSubprocessTransport()
  }

  return new RelayTransport({
    url: remoteUrl,
    sessionToken: process.env.HERMES_RELAY_TOKEN?.trim() || undefined,
    pairingCode: process.env.HERMES_RELAY_CODE?.trim() || undefined,
    deviceName: process.env.HERMES_RELAY_DEVICE_NAME?.trim() || `hermes-tui (${process.platform})`,
    deviceId: process.env.HERMES_RELAY_DEVICE_ID?.trim() || undefined
  })
}

const gw = new GatewayClient(buildTransport())

gw.start()

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
