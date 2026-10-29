#!/usr/bin/env bun
import { mkdir } from 'node:fs/promises'
import { basename, join } from 'node:path'
import {
  parseScenarioDocument,
  type Run,
  type RunEvent,
  type Turn,
  type Verdict,
} from '@vaatt/core'
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml'

const HELP = `run-benchmark — repeat VAATT scenarios and export benchmark aggregates

Usage:
  bun run benchmark -- --profile benchmark.yaml [options]

Options:
  --profile <file>            Benchmark profile YAML/JSON
  --server <url>              VAATT server URL (default $VAATT_SERVER or http://localhost:3000)
  --token <token>             Bearer token (default $VAATT_TOKEN)
  --runs-per-provider <n>     Override profile runsPerProvider
  --concurrency <n>           Override profile concurrency
  --out <dir>                 Output directory (default reports/benchmark-<timestamp>)
  --dry-run                   Write generated scenarios without placing calls
  --max-calls <n>             Override the profile's guardrails.maxCalls — the batch
                              stops placing calls once reached; remaining runs are skipped
  --max-consecutive-errors <n> Override guardrails.maxConsecutiveErrors — N consecutive
                              harness-level errored runs (default 3) stop the batch (exit 3)

Profile shape:
  name: appointment-reschedule-150
  runsPerProvider: 30
  concurrency: 1
  scenario:                   # any normal VAATT scenario; target is overridden per provider
    id: appointment-reschedule
    name: Appointment reschedule
    target: { to: "+10000000000" }
    ...
  providers:
    - id: telnyx
      name: Telnyx
      target: { to: "+15550100000" }
`

interface ProviderConfig {
  id: string
  name?: string
  target: {
    to: string
    from?: string
    connectionId?: string
  }
  tags?: string[]
  metadata?: Record<string, unknown>
}

interface BenchmarkProfile {
  name?: string
  description?: string
  runsPerProvider?: number
  concurrency?: number
  guardrails?: {
    maxCalls?: number
    maxConsecutiveErrors?: number
    maxCallSeconds?: number
  }
  scenario: Record<string, unknown>
  providers: ProviderConfig[]
}

interface Options {
  profile?: string
  server: string
  token?: string
  runsPerProvider?: number
  concurrency?: number
  out?: string
  dryRun: boolean
  maxCalls?: number
  maxConsecutiveErrors?: number
}

interface PlannedRun {
  provider: ProviderConfig
  repeat: number
  scenarioId: string
  source: string
}

interface RunRecord {
  providerId: string
  providerName: string
  repeat: number
  scenarioId: string
  runId?: string
  verdict: Verdict
  status?: Run['status']
  error?: string
  run?: Run
  latencyMs: number[]
  firstResponseLatencyMs?: number
  durationSeconds?: number
  transcriptCaptured: boolean
  recordingCaptured: boolean
  interruptions: number
  skipped?: boolean
}

interface GuardrailState {
  maxCalls: number | null
  maxConsecutiveErrors: number
  placed: number
  consecutiveErrors: number
  tripped: string | null
}

function parseArgs(argv: string[]): Options {
  const options: Options = {
    server: Bun.env.VAATT_SERVER ?? 'http://localhost:3000',
    ...(Bun.env.VAATT_TOKEN ? { token: Bun.env.VAATT_TOKEN } : {}),
    dryRun: false,
  }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]!
    if (arg === '--profile') options.profile = argv[++i]
    else if (arg === '--server') options.server = argv[++i] ?? options.server
    else if (arg === '--token') options.token = argv[++i] ?? options.token
    else if (arg === '--runs-per-provider') options.runsPerProvider = Number(argv[++i])
    else if (arg === '--concurrency') options.concurrency = Number(argv[++i])
    else if (arg === '--out') options.out = argv[++i]
    else if (arg === '--dry-run') options.dryRun = true
    else if (arg === '--max-calls') options.maxCalls = Number(argv[++i])
    else if (arg === '--max-consecutive-errors') options.maxConsecutiveErrors = Number(argv[++i])
    else if (arg === '--help' || arg === '-h') {
      console.log(HELP)
      process.exit(0)
    } else throw new Error(`unknown argument: ${arg}`)
  }
  if (!options.profile) throw new Error('missing --profile')
  return options
}

function slug(input: string): string {
  return input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function api(options: Options, path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)
  if (options.token) headers.set('Authorization', `Bearer ${options.token}`)
  return fetch(`${options.server.replace(/\/+$/, '')}${path}`, { ...init, headers })
}

async function readProfile(path: string): Promise<BenchmarkProfile> {
  const source = await Bun.file(path).text()
  const raw = path.endsWith('.json') ? JSON.parse(source) : parseYaml(source)
  const profile = raw as BenchmarkProfile
  if (!profile.scenario || typeof profile.scenario !== 'object') {
    throw new Error('profile.scenario must be an object')
  }
  if (!Array.isArray(profile.providers) || profile.providers.length === 0) {
    throw new Error('profile.providers must contain at least one provider')
  }
  for (const p of profile.providers) {
    if (!p.id || !p.target?.to) throw new Error('each provider needs id and target.to')
  }
  return profile
}

function buildPlan(profile: BenchmarkProfile, options: Options): PlannedRun[] {
  const repeats = options.runsPerProvider ?? profile.runsPerProvider ?? 1
  if (!Number.isInteger(repeats) || repeats < 1) {
    throw new Error('runsPerProvider must be a positive integer')
  }
  const base = profile.scenario
  const baseId = String(base.id ?? profile.name ?? 'benchmark')
  const baseName = String(base.name ?? profile.name ?? 'Benchmark scenario')
  const plan: PlannedRun[] = []
  for (const provider of profile.providers) {
    const providerSlug = slug(provider.id)
    for (let i = 1; i <= repeats; i += 1) {
      const scenario = clone(base)
      const scenarioId = `${slug(baseId)}-${providerSlug}-${String(i).padStart(3, '0')}`
      scenario.id = scenarioId
      scenario.name = `${baseName} / ${provider.name ?? provider.id} / ${i}`
      scenario.target = { ...((scenario.target as object | undefined) ?? {}), ...provider.target }
      const tags = Array.isArray(scenario.tags) ? scenario.tags : []
      scenario.tags = [...new Set([...tags, 'benchmark', providerSlug, ...(provider.tags ?? [])])]
      const source = stringifyYaml(scenario)
      parseScenarioDocument(source)
      plan.push({ provider, repeat: i, scenarioId, source })
    }
  }
  return plan
}

async function waitForRun(options: Options, id: string): Promise<Run> {
  const stream = await api(options, `/api/runs/${id}/events`, {
    headers: { Accept: 'text/event-stream' },
  })
  if (!stream.ok || !stream.body) throw new Error(`could not open event stream (${stream.status})`)

  const reader = stream.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const dataLine = frame.split('\n').find((l) => l.startsWith('data:'))
        if (!dataLine) continue
        const payload = dataLine.slice(5).trim()
        if (!payload || payload === '{}') continue
        let event: RunEvent
        try {
          event = JSON.parse(payload) as RunEvent
        } catch {
          continue
        }
        if (event.type === 'run.result' || event.type === 'run.error') {
          await reader.cancel().catch(() => {})
          return await fetchFinalRun(options, id)
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => {})
  }
  return await fetchFinalRun(options, id)
}

async function fetchFinalRun(options: Options, id: string): Promise<Run> {
  let run: Run | undefined
  for (let i = 0; i < 8; i += 1) {
    const res = await api(options, `/api/runs/${id}`)
    if (!res.ok) throw new Error(`could not fetch run ${id} (${res.status})`)
    run = ((await res.json()) as { data: Run }).data
    if (run.recording || i === 7) break
    await Bun.sleep(1500)
  }
  return run!
}

async function runOne(
  options: Options,
  planned: PlannedRun,
  guard: GuardrailState,
): Promise<RunRecord> {
  const providerName = planned.provider.name ?? planned.provider.id
  const label = `[${providerName}] ${planned.repeat}`
  if (guard.tripped || (guard.maxCalls !== null && guard.placed >= guard.maxCalls)) {
    const reason = guard.tripped ?? `maxCalls=${guard.maxCalls} reached`
    console.log(`${label} SKIPPED: ${reason}`)
    return {
      providerId: planned.provider.id,
      providerName,
      repeat: planned.repeat,
      scenarioId: planned.scenarioId,
      verdict: 'errored',
      error: `SKIPPED: guardrail — ${reason}`,
      latencyMs: [],
      transcriptCaptured: false,
      recordingCaptured: false,
      interruptions: 0,
      skipped: true,
    }
  }
  try {
    const res = await api(options, '/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: planned.source,
    })
    if (!res.ok) throw new Error(`server rejected run (${res.status}): ${await res.text()}`)
    const started = ((await res.json()) as { data: Run }).data
    console.log(`[${providerName}] ${planned.repeat} started ${started.id} (${planned.scenarioId})`)
    guard.placed += 1
    const run = await waitForRun(options, started.id)
    const record = summarizeRun(planned, run)
    console.log(
      `[${providerName}] ${planned.repeat} ${record.verdict} ${
        record.firstResponseLatencyMs ?? 'n/a'
      }ms first response`,
    )
    if (record.verdict === 'errored') {
      guard.consecutiveErrors += 1
      if (guard.consecutiveErrors >= guard.maxConsecutiveErrors && !guard.tripped) {
        guard.tripped = `maxConsecutiveErrors=${guard.maxConsecutiveErrors} consecutive errored runs`
        console.error('')
        console.error(`GUARDRAIL TRIPPED: ${guard.tripped}`)
        console.error('Stopping the batch — no further calls will be placed.')
      }
    } else {
      guard.consecutiveErrors = 0
    }
    return record
  } catch (err) {
    console.error(`[${providerName}] ${planned.repeat} errored: ${(err as Error).message}`)
    guard.consecutiveErrors += 1
    if (guard.consecutiveErrors >= guard.maxConsecutiveErrors && !guard.tripped) {
      guard.tripped = `maxConsecutiveErrors=${guard.maxConsecutiveErrors} consecutive errored runs`
      console.error('')
      console.error(`GUARDRAIL TRIPPED: ${guard.tripped}`)
      console.error('Stopping the batch — no further calls will be placed.')
    }
    return {
      providerId: planned.provider.id,
      providerName,
      repeat: planned.repeat,
      scenarioId: planned.scenarioId,
      verdict: 'errored',
      error: (err as Error).message,
      latencyMs: [],
      transcriptCaptured: false,
      recordingCaptured: false,
      interruptions: 0,
    }
  }
}

function summarizeRun(planned: PlannedRun, run: Run): RunRecord {
  const latencyMs = responseLatencies(run.turns)
  return {
    providerId: planned.provider.id,
    providerName: planned.provider.name ?? planned.provider.id,
    repeat: planned.repeat,
    scenarioId: planned.scenarioId,
    runId: run.id,
    verdict: run.result?.verdict ?? 'errored',
    status: run.status,
    run,
    latencyMs,
    firstResponseLatencyMs: firstResponseLatency(run.turns),
    durationSeconds: run.durationSeconds,
    transcriptCaptured: run.turns.length > 0,
    recordingCaptured: Boolean(run.recording),
    interruptions: overlapCount(run.turns),
  }
}

function responseLatencies(turns: Turn[]): number[] {
  const agentStarts = turns
    .filter((t) => t.speaker === 'agent')
    .map((t) => t.atMs)
    .sort((a, b) => a - b)
  const out: number[] = []
  for (const caller of turns.filter((t) => t.speaker === 'caller' && t.endMs !== undefined)) {
    const next = agentStarts.find((at) => at > caller.endMs!)
    if (next !== undefined) out.push(next - caller.endMs!)
  }
  return out
}

function firstResponseLatency(turns: Turn[]): number | undefined {
  const caller = turns
    .filter((t) => t.speaker === 'caller' && t.endMs !== undefined)
    .sort((a, b) => a.atMs - b.atMs)[0]
  if (!caller || caller.endMs === undefined) return undefined
  const agent = turns
    .filter((t) => t.speaker === 'agent' && t.atMs > caller.endMs!)
    .sort((a, b) => a.atMs - b.atMs)[0]
  return agent ? agent.atMs - caller.endMs : undefined
}

function overlapCount(turns: Turn[]): number {
  const agent = turns.filter((t) => t.speaker === 'agent' && t.endMs !== undefined)
  const caller = turns.filter((t) => t.speaker === 'caller' && t.endMs !== undefined)
  let count = 0
  for (const a of agent) {
    for (const c of caller) {
      const overlap = Math.min(a.endMs!, c.endMs!) - Math.max(a.atMs, c.atMs)
      if (overlap >= 300) count += 1
    }
  }
  return count
}

async function mapLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length)
  let cursor = 0
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (true) {
      const index = cursor
      cursor += 1
      if (index >= items.length) return
      results[index] = await fn(items[index]!)
    }
  })
  await Promise.all(workers)
  return results
}

function percentile(values: number[], p: number): number | undefined {
  if (values.length === 0) return undefined
  const sorted = [...values].sort((a, b) => a - b)
  if (sorted.length === 1) return sorted[0]
  const position = (p / 100) * (sorted.length - 1)
  const lo = Math.floor(position)
  const hi = Math.ceil(position)
  const lower = sorted[lo]!
  const upper = sorted[hi]!
  return lower + (upper - lower) * (position - lo)
}

function mean(values: number[]): number | undefined {
  return values.length ? values.reduce((sum, v) => sum + v, 0) / values.length : undefined
}

function round(value: number | undefined): number | undefined {
  return value === undefined ? undefined : Math.round(value * 100) / 100
}

function aggregate(records: RunRecord[]) {
  const byProvider = new Map<string, RunRecord[]>()
  for (const record of records) {
    const existing = byProvider.get(record.providerId) ?? []
    existing.push(record)
    byProvider.set(record.providerId, existing)
  }
  return [...byProvider.entries()].map(([providerId, rows]) => {
    const real = rows.filter((r) => !r.skipped)
    const allLatency = real.flatMap((r) => r.latencyMs)
    const firstResponse = real
      .map((r) => r.firstResponseLatencyMs)
      .filter((v): v is number => v !== undefined)
    const durations = real.map((r) => r.durationSeconds).filter((v): v is number => v !== undefined)
    const passed = real.filter((r) => r.verdict === 'passed').length
    return {
      providerId,
      providerName: real[0]?.providerName ?? providerId,
      total: real.length,
      skipped: rows.length - real.length,
      passed,
      failed: real.filter((r) => r.verdict === 'failed').length,
      inconclusive: real.filter((r) => r.verdict === 'inconclusive').length,
      errored: real.filter((r) => r.verdict === 'errored').length,
      successRate: round(real.length > 0 ? (passed / real.length) * 100 : 0),
      transcripts: real.filter((r) => r.transcriptCaptured).length,
      recordings: real.filter((r) => r.recordingCaptured).length,
      interruptions: real.reduce((sum, r) => sum + r.interruptions, 0),
      latency: stats(allLatency),
      firstResponse: stats(firstResponse),
      durations: stats(durations),
    }
  })
}

function stats(values: number[]) {
  return {
    n: values.length,
    mean: round(mean(values)),
    p50: round(percentile(values, 50)),
    p95: round(percentile(values, 95)),
    p99: round(percentile(values, 99)),
    min: round(values.length ? Math.min(...values) : undefined),
    max: round(values.length ? Math.max(...values) : undefined),
  }
}

function csvEscape(value: unknown): string {
  const text = value === undefined || value === null ? '' : String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function toCsv(rows: Array<Record<string, unknown>>): string {
  const headers = Object.keys(rows[0] ?? {})
  return [
    headers.map(csvEscape).join(','),
    ...rows.map((row) => headers.map((h) => csvEscape(row[h])).join(',')),
  ].join('\n')
}

async function writeOutputs(
  outDir: string,
  profile: BenchmarkProfile,
  plan: PlannedRun[],
  records: RunRecord[],
) {
  await mkdir(outDir, { recursive: true })
  await mkdir(join(outDir, 'scenarios'), { recursive: true })
  await mkdir(join(outDir, 'runs'), { recursive: true })
  for (const planned of plan) {
    await Bun.write(join(outDir, 'scenarios', `${planned.scenarioId}.yaml`), planned.source)
  }
  for (const record of records) {
    if (record.run) {
      await Bun.write(
        join(outDir, 'runs', `${record.run.id}.json`),
        JSON.stringify(record.run, null, 2),
      )
    }
  }
  const summary = {
    profile: {
      name: profile.name,
      description: profile.description,
      providers: profile.providers.map((p) => ({
        id: p.id,
        name: p.name ?? p.id,
        target: p.target,
        metadata: p.metadata,
      })),
    },
    generatedAt: new Date().toISOString(),
    totals: {
      plannedRuns: plan.length,
      placedRuns: records.filter((r) => !r.skipped).length,
      skippedRuns: records.filter((r) => r.skipped).length,
      completedRuns: records.filter((r) => r.status === 'completed').length,
      passedRuns: records.filter((r) => r.verdict === 'passed').length,
      erroredRuns: records.filter((r) => r.verdict === 'errored' && !r.skipped).length,
    },
    providers: aggregate(records),
    runs: records.map((r) => ({
      providerId: r.providerId,
      providerName: r.providerName,
      repeat: r.repeat,
      scenarioId: r.scenarioId,
      runId: r.runId,
      status: r.status,
      verdict: r.verdict,
      error: r.error,
      durationSeconds: r.durationSeconds,
      firstResponseLatencyMs: r.firstResponseLatencyMs,
      latencySamples: r.latencyMs.length,
      transcriptCaptured: r.transcriptCaptured,
      recordingCaptured: r.recordingCaptured,
      interruptions: r.interruptions,
      skipped: r.skipped ?? false,
    })),
  }
  await Bun.write(join(outDir, 'summary.json'), JSON.stringify(summary, null, 2))
  await Bun.write(join(outDir, 'runs.csv'), toCsv(summary.runs))
  await Bun.write(
    join(outDir, 'providers.csv'),
    toCsv(
      summary.providers.map((p) => ({
        providerId: p.providerId,
        providerName: p.providerName,
        total: p.total,
        passed: p.passed,
        failed: p.failed,
        inconclusive: p.inconclusive,
        errored: p.errored,
        successRate: p.successRate,
        transcripts: p.transcripts,
        recordings: p.recordings,
        interruptions: p.interruptions,
        latencySamples: p.latency.n,
        latencyMeanMs: p.latency.mean,
        latencyP50Ms: p.latency.p50,
        latencyP95Ms: p.latency.p95,
        latencyP99Ms: p.latency.p99,
        firstResponseSamples: p.firstResponse.n,
        firstResponseMeanMs: p.firstResponse.mean,
        firstResponseP50Ms: p.firstResponse.p50,
        firstResponseP95Ms: p.firstResponse.p95,
        firstResponseP99Ms: p.firstResponse.p99,
        durationMeanSeconds: p.durations.mean,
        durationP50Seconds: p.durations.p50,
        durationP95Seconds: p.durations.p95,
      })),
    ),
  )
}

async function main(): Promise<number> {
  let options: Options
  try {
    options = parseArgs(Bun.argv.slice(2))
  } catch (err) {
    console.error((err as Error).message)
    console.log('')
    console.log(HELP)
    return 2
  }
  const profile = await readProfile(options.profile!)
  const plan = buildPlan(profile, options)
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const outDir = options.out ?? join('reports', `benchmark-${stamp}`)
  const concurrency = options.concurrency ?? profile.concurrency ?? 1
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new Error('concurrency must be positive')
  }

  if (options.dryRun) {
    await writeOutputs(outDir, profile, plan, [])
    console.log(`Dry run wrote ${plan.length} generated scenarios to ${outDir}`)
    return 0
  }

  console.log(
    `Running ${plan.length} call(s) from ${basename(options.profile!)} at concurrency ${
      concurrency
    }`,
  )
  const guardrails = profile.guardrails ?? {}
  const guard: GuardrailState = {
    maxCalls: options.maxCalls ?? guardrails.maxCalls ?? null,
    maxConsecutiveErrors: options.maxConsecutiveErrors ?? guardrails.maxConsecutiveErrors ?? 3,
    placed: 0,
    consecutiveErrors: 0,
    tripped: null,
  }
  console.log(
    `guardrails: maxCalls=${guard.maxCalls ?? 'none'}, maxConsecutiveErrors=${guard.maxConsecutiveErrors} (consecutive harness-level errored runs stop the batch)`,
  )
  const records = await mapLimit(plan, concurrency, (planned) => runOne(options, planned, guard))
  await writeOutputs(outDir, profile, plan, records)
  console.log(`Benchmark artifacts written to ${outDir}`)
  if (guard.tripped) {
    console.error(
      `Batch stopped by guardrail: ${guard.tripped} — ${records.filter((r) => r.skipped).length} runs skipped`,
    )
    return 3
  }
  if (records.some((r) => r.verdict === 'errored' && !r.skipped)) return 2
  return records.some((r) => r.verdict !== 'passed') ? 1 : 0
}

process.exit(await main())
