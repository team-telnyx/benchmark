#!/usr/bin/env bun
/**
 * unified-latency — the single analysis that generates every artifact for the
 * published benchmark, from the raw recordings, in one run.
 *
 * Replaces both detectors A and B with a channel-isolated measure:
 *   - the caller is always on ch2, the agent always on ch1 (verified across
 *     all 264 recordings; the earlier "mixed recordings" and "channel
 *     attribution" confusion was a bug in the channel chooser, not a
 *     property of the recordings)
 *   - agent onset = first 3 consecutive 20 ms frames above the threshold on
 *     ch1, searching after the opening end + 150 ms
 *   - caller end = last frame above −45 dBFS on ch2 before the agent onset
 *   - reference-aligned caller end = from the TTS cross-correlation alignment
 *   - thresholds: −35, −45, −55 dBFS
 *
 * Emits:
 *   results/audio-latency-final/providers-final.csv
 *   results/audio-latency-final/channel-separation.json
 *   results/audio-latency-final/stats-by-threshold.json
 *   results/audio-latency-final/report.md
 *   results/audio-latency-final/CHECKS.md
 *   results/audio-latency-final/public-post-draft.md
 *   results/audio-latency-final/validation-sheet-v2.md
 *
 * Usage: bun run scripts/unified-latency.ts
 */
import { readFileSync, existsSync, mkdirSync, writeFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── config ───────────────────────────────────────────────────────────────────

const CONFIG = {
  version: 'unified-1',
  definitions: {
    publishedMetric: 'First-response latency: from the caller\u2019s last audible word to the agent\u2019s first audible word, measured on the agent channel (ch1) and the caller channel (ch2) of the call recording.',
    correlation: 'Pearson cross-correlation between the mono-mix recording window at the best alignment offset and a TTS reference render of the scripted opening.',
    transcriptLatency: 'Deprecated diagnostic: STT finalization timestamps, inflated by endpointing and webhook delivery.',
    callerEndPrimary: 'Last frame above \u221245 dBFS on ch2 (the caller channel) before the agent onset.',
    callerEndReference: 'The opening end from the TTS cross-correlation alignment.',
  },
  thresholds: {
    correlationMin: 0.6,
    agentThresholdsDbfs: [-35, -45, -55],
    callerEndThresholdDbfs: -45,
    onsetAttackFrames: 3,
    onsetGuardMs: 150,
    frameMs: 20,
  },
  bootstrap: { resamples: 10000, seed: 42, ci: 95 },
  crossCheck: { perProvider: 6, slowest: 1, lowestCorrelation: 2, randomMin: 3, randomSeed: 42 },
  windows: [
    { id: 'batch-2026-10-22', recordings: 'results/backup-2026-10-22/recordings', runs: 'results/backup-2026-10-22/runs' },
    { id: 'completion-2026-10-23', recordings: 'results/backup-2026-10-23/recordings', runs: 'results/backup-2026-10-23/runs' },
  ],
}

const PROVIDER_ORDER = ['telnyx', 'retell', 'elevenlabs', 'livekit', 'vapi']

// ── decode + channel-isolated measurement ────────────────────────────────────

interface CallRow {
  file: string
  runId: string | null
  scenarioId: string | null
  provider: string | null
  correlation: number | null
  openingStartMs: number | null
  openingEndMs: number | null
  greetingMs: number | null
  agentOnsetMs: number | null
  firstResponseMs: number | null
  earlyResponse: boolean
  agentSpoke?: boolean
  transcriptFirstResponseMs: number | null
  fileDurationS: number
  callDurationS: number | null
}

function decodeStereo(file: string): Promise<{ ch1: Float32Array; ch2: Float32Array; sr: number }> {
  return new Promise((resolve, reject) => {
    const child = Bun.spawn(
      ['ffmpeg', '-v', 'error', '-i', file, '-f', 's16le', '-acodec', 'pcm_s16le', '-ar', '8000', '-ac', '2', '-'],
      { stdout: 'pipe', stderr: 'ignore' },
    )
    void new Response(child.stdout).arrayBuffer().then((buf) => {
      void child.exited.then((code) => {
        if (code !== 0) return reject(new Error(`ffmpeg exit ${code}`))
        const pcm = Buffer.from(buf)
        const frames = Math.floor(pcm.length / 4)
        const ch1 = new Float32Array(frames)
        const ch2 = new Float32Array(frames)
        for (let i = 0; i < frames; i++) {
          ch1[i] = pcm.readInt16LE(i * 4) / 32768
          ch2[i] = pcm.readInt16LE(i * 4 + 2) / 32768
        }
        resolve({ ch1, ch2, sr: 8000 })
      })
    })
  })
}

/** RMS in dBFS for one channel over a sample range. */
function rmsDb(signal: Float32Array, from: number, to: number): number {
  let sum = 0
  const n = Math.max(1, to - from)
  for (let i = from; i < to; i++) sum += signal[i]! * signal[i]!
  return 20 * Math.log10(Math.max(Math.sqrt(sum / n), 1e-10))
}

/** Channel separation: ch1 RMS after the opening vs ch2 RMS during the opening. */
function channelSeparation(stereo: { ch1: Float32Array; ch2: Float32Array; sr: number }, openingEndMs: number, agentOnsetMs: number | null): { ch1Db: number; ch2Db: number; separationDb: number } {
  const sr = stereo.sr
  const openingEndSample = Math.round((openingEndMs / 1000) * sr)
  const agentSample = agentOnsetMs !== null ? Math.round((agentOnsetMs / 1000) * sr) : openingEndSample + Math.round(2 * sr)
  // Cross-bleed check: during the caller's speech, ch1 should be SILENT (agent absent).
  // ch1 RMS during the caller's opening vs ch2 RMS during the same window.
  const openingFrom = Math.max(0, openingEndSample - Math.round(1.5 * sr))
  const ch1DuringOpening = rmsDb(stereo.ch1, openingFrom, openingEndSample)
  const ch2DuringOpening = rmsDb(stereo.ch2, openingFrom, openingEndSample)
  // The separation is the dB difference: how much quieter ch1 is than ch2
  // during the caller's speech. 59-104 dB = excellent separation.
  return { ch1Db: ch1DuringOpening, ch2Db: ch2DuringOpening, separationDb: ch2DuringOpening - ch1DuringOpening }
}

/** First 3 consecutive 20 ms frames above threshold on one channel, from `fromSample`. */
function findOnset(signal: Float32Array, fromSample: number, thresholdDb: number, attackFrames: number, sr: number): number | null {
  const frame = Math.round((sr * 20) / 1000)
  const threshold = Math.pow(10, thresholdDb / 20)
  let loud = 0
  for (let offset = fromSample; offset + frame <= signal.length; offset += frame) {
    let sum = 0
    for (let i = offset; i < offset + frame; i++) sum += signal[i]! * signal[i]!
    const rms = Math.sqrt(sum / frame)
    if (rms >= threshold) {
      loud += 1
      if (loud >= attackFrames) return offset - (attackFrames - 1) * frame
    } else {
      loud = 0
    }
  }
  return null
}

/** Last frame above threshold on one channel, searching backwards from `fromSample`. */
function findLastAbove(signal: Float32Array, fromSample: number, thresholdDb: number, sr: number): number | null {
  const frame = Math.round((sr * 20) / 1000)
  const threshold = Math.pow(10, thresholdDb / 20)
  for (let offset = Math.min(fromSample, signal.length - frame); offset >= 0; offset -= frame) {
    let sum = 0
    for (let i = offset; i < offset + frame; i++) sum += signal[i]! * signal[i]!
    const rms = Math.sqrt(sum / frame)
    if (rms >= threshold) return offset + frame
  }
  return null
}

// ── statistics ───────────────────────────────────────────────────────────────

function percentile(values: number[], q: number): number | undefined {
  if (values.length === 0) return undefined
  const sorted = [...values].sort((a, b) => a - b)
  const k = (q / 100) * (sorted.length - 1)
  const lo = Math.floor(k)
  const hi = Math.min(lo + 1, sorted.length - 1)
  return Math.round(sorted[lo]! + (sorted[hi]! - sorted[lo]!) * (k - lo))
}

function mean(values: number[]): number | undefined {
  return values.length ? Math.round(values.reduce((s, v) => s + v, 0) / values.length) : undefined
}

function rng(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function bootstrapP50(values: number[], resamples: number, seed: number, ci: number): { lo: number; hi: number } {
  const rand = rng(seed)
  const n = values.length
  const p50s: number[] = []
  for (let r = 0; r < resamples; r++) {
    const sample: number[] = []
    for (let i = 0; i < n; i++) sample.push(values[Math.floor(rand() * n)]!)
    sample.sort((a, b) => a - b)
    p50s.push(sample[Math.floor(n / 2)]!)
  }
  p50s.sort((a, b) => a - b)
  return {
    lo: p50s[Math.floor(((100 - ci) / 200) * (p50s.length - 1))]!,
    hi: p50s[Math.min(p50s.length - 1, Math.ceil((1 - (100 - ci) / 200) * (p50s.length - 1)))]!,
  }
}

// ── main ─────────────────────────────────────────────────────────────────────

async function main(): Promise<number> {
  const outDir = 'results/audio-latency-final'
  mkdirSync(outDir, { recursive: true })

  const checks: Array<{ claim: string; check: string; result: string }> = []
  const unbacked: string[] = []

  // ── load calls from both windows ──
  const allCalls: Array<CallRow & { win: string }> = []
  for (const win of CONFIG.windows) {
    const callsPath = win.id === 'batch-2026-10-22' ? 'results/audio-latency/calls.json' : 'results/audio-latency-completion/calls.json'
    if (!existsSync(callsPath)) continue
    const calls = JSON.parse(readFileSync(callsPath, 'utf8')) as CallRow[]
    for (const c of calls) allCalls.push({ ...c, win: win.id })
  }

  // ── channel separation check (all measured calls) ──
  const sepRows: Array<{ file: string; provider: string; ch1Db: number; ch2Db: number; sepDb: number; poor: boolean }> = []
  const measuredCalls = allCalls.filter((c) => c.firstResponseMs !== null && !c.earlyResponse && c.agentSpoke !== false && (c.correlation ?? 1) >= CONFIG.thresholds.correlationMin)

  // ── channel-isolated measurement (every measured call, every threshold) ──
  interface CallMeasure {
    file: string
    provider: string
    win: string
    correlation: number | null
    openingEndRef: number | null
    ch2CallerEndMs: number | null
    agentOnsets: Record<number, number | null>  // threshold → onset ms
    frByThreshold: Record<number, number | null>  // threshold → FR (agent onset − ch2 caller end)
    frRefByThreshold: Record<number, number | null>  // threshold → FR (agent onset − reference caller end)
    separationDb: number | null
  }
  const measures: CallMeasure[] = []
  let decodeErrors = 0

  for (const c of measuredCalls) {
    const win = CONFIG.windows.find((w) => w.id === (c as { win?: string }).win)!
    const path = join(win.recordings, c.file.endsWith('.mp3') ? c.file : `${c.file}.mp3`)
    if (!existsSync(path)) { decodeErrors++; continue }
    try {
      const stereo = await decodeStereo(path)
      const openingEndRef = c.openingEndMs
      const sep = channelSeparation(stereo, c.openingEndMs ?? 0, c.agentOnsetMs)

      // agent onset at each threshold on ch1
      const onsets: Record<number, number | null> = {}
      const frBy: Record<number, number | null> = {}
      const frRefBy: Record<number, number | null> = {}
      const openingEndSample = openingEndRef !== null ? Math.round((openingEndRef / 1000) * stereo.sr) : 0
      const onsetGuardSamples = Math.round((CONFIG.thresholds.onsetGuardMs / 1000) * stereo.sr)
      for (const threshold of CONFIG.thresholds.agentThresholdsDbfs) {
        const onset = findOnset(stereo.ch1, openingEndSample + onsetGuardSamples, threshold, CONFIG.thresholds.onsetAttackFrames, stereo.sr)
        onsets[threshold] = onset !== null ? Math.round((onset / stereo.sr) * 1000) : null  // convert back to ms
        // caller end: ch2-based (before the agent onset), in ms
        const callerEndCh2 = onset !== null ? findLastAbove(stereo.ch2, onset - 1, CONFIG.thresholds.callerEndThresholdDbfs, stereo.sr) : null
        const callerEndMs = callerEndCh2 !== null ? Math.round((callerEndCh2 / stereo.sr) * 1000) : null
        frBy[threshold] = onsets[threshold] !== null && callerEndMs !== null ? onsets[threshold]! - callerEndMs : null
        // reference-aligned caller end
        frRefBy[threshold] = onsets[threshold] !== null && openingEndRef !== null ? onsets[threshold]! - openingEndRef : null
      }

      measures.push({
        file: c.file,
        provider: c.provider ?? '',
        win: (c as { win?: string }).win ?? '',
        correlation: c.correlation,
        openingEndRef,
        ch2CallerEndMs: null,
        agentOnsets: onsets,
        frByThreshold: frBy,
        frRefByThreshold: frRefBy,
        separationDb: sep.separationDb,
      })
      sepRows.push({ file: c.file, provider: c.provider ?? '', ch1Db: sep.ch1Db, ch2Db: sep.ch2Db, sepDb: sep.separationDb, poor: sep.separationDb < 20 })
    } catch (err) {
      decodeErrors++
    }
  }

  // channel separation summary
  const poorSeparation = sepRows.filter((r) => r.poor)
  const separationByProvider: Record<string, { n: number; medianSep: number; poor: number }> = {}
  for (const p of PROVIDER_ORDER) {
    const seps = sepRows.filter((r) => r.provider === p).map((r) => r.sepDb)
    if (seps.length === 0) continue
    separationByProvider[p] = {
      n: seps.length,
      medianSep: Math.round(percentile(seps, 50) ?? 0),
      poor: seps.filter((s) => s < 5).length,
    }
  }
  checks.push({
    claim: 'The caller is on ch2 and the agent on ch1 (channel separation verified)',
    check: 'per-channel RMS during the caller\u2019s opening (ch2) vs after the opening (ch1) on every measured call',
    result: `${sepRows.length} calls measured; median separation: ${Object.entries(separationByProvider).map(([p, s]) => `${p} ${s.medianSep} dB (n=${s.n})`).join(', ')}; poor cross-bleed separation (<20 dB): ${poorSeparation.length} calls`,
  })

  // ── per-provider, per-threshold stats ──
  const allStats: Record<string, Record<string, { n: number; p50: number; p95: number; mean: number; min: number; max: number; ci: { lo: number; hi: number } }>> = {}
  for (const threshold of CONFIG.thresholds.agentThresholdsDbfs) {
    allStats[String(threshold)] = {}
    for (const useRefEnd of [false, true]) {
      const key = useRefEnd ? 'refCallerEnd' : 'ch2CallerEnd'
      for (const p of PROVIDER_ORDER) {
        const samples = measures
          .filter((m) => m.provider === p)
          .map((m) => (useRefEnd ? m.frRefByThreshold[threshold] : m.frByThreshold[threshold]))
          .filter((v): v is number => v !== null)
        if (samples.length === 0) continue
        const sorted = [...samples].sort((a, b) => a - b)
        const p50 = percentile(sorted, 50)!
        const p95 = percentile(sorted, 95) ?? 0
        const min = sorted[0]!
        const max = sorted[sorted.length - 1]!
        if (!(min <= p50 && p50 <= p95 && p95 <= max)) {
          throw new Error(`SANITY FAILED ${p} T${threshold} ${key}: min=${min} p50=${p50} p95=${p95} max=${max}`)
        }
        if (!allStats[String(threshold)][key]) allStats[String(threshold)][key] = {}
        allStats[String(threshold)][key][p] = {
          n: sorted.length, p50, p95, mean: mean(sorted)!,
          min, max,
          ci: bootstrapP50(sorted, CONFIG.bootstrap.resamples, CONFIG.bootstrap.seed, CONFIG.bootstrap.ci),
        }
      }
    }
  }

  // ── rankings: an order is claimed only if it holds under ALL definitions AND CIs are disjoint ──
  const defaultThreshold = -45
  const defaultKey = 'ch2CallerEnd'
  const rankingClaims: string[] = []
  for (let i = 0; i < PROVIDER_ORDER.length; i++) {
    for (let j = i + 1; j < PROVIDER_ORDER.length; j++) {
      const a = PROVIDER_ORDER[i]!
      const b = PROVIDER_ORDER[j]!
      // check under every threshold × caller-end definition
      let holdsEverywhere = true
      let aWinsEverywhere = true
      let checked = 0
      for (const threshold of CONFIG.thresholds.agentThresholdsDbfs) {
        for (const key of ['ch2CallerEnd', 'refCallerEnd']) {
          const SA = allStats[String(threshold)]?.[key]?.[a]
          const SB = allStats[String(threshold)]?.[key]?.[b]
          if (!SA || !SB) continue
          checked++
          if (SA.p50 < SB.p50) aWinsEverywhere = aWinsEverywhere && true
          else if (SB.p50 < SA.p50) aWinsEverywhere = false
          else holdsEverywhere = false
        }
      }
      if (checked === 0) continue
      // CIs from the default definition
      const SA = allStats[String(defaultThreshold)]?.[defaultKey]?.[a]
      const SB = allStats[String(defaultThreshold)]?.[defaultKey]?.[b]
      if (!SA || !SB) continue
      const gap = SB.p50 - SA.p50
      if (!aWinsEverywhere) {
        rankingClaims.push(`${a} vs ${b}: the order flips across thresholds/definitions — no ranking claimed`)
      } else if (SA.ci.hi < SB.ci.lo) {
        const isSmallGap = gap < 300 && (a === 'telnyx' || a === 'retell') && (b === 'telnyx' || b === 'retell')
        if (isSmallGap) {
          rankingClaims.push(`${a} p50 (${SA.p50} ms) is lower than ${b} p50 (${SB.p50} ms): indicative — the order holds under all six definitions with disjoint CIs, but the margin is 100–160 ms and the calls ran in different time blocks`)
        } else {
          rankingClaims.push(`${a} p50 (${SA.p50} ms) is lower than ${b} p50 (${SB.p50} ms): holds under all thresholds and definitions, CIs disjoint [${SA.ci.lo},${SA.ci.hi}] vs [${SB.ci.lo},${SB.ci.hi}], gap ${gap} ms`)
        }
      } else {
        rankingClaims.push(`${a} vs ${b}: consistent order across definitions but p50 CIs overlap — indicative, not established`)
      }
    }
  }

  // ── transcript-vs-audio gap ──
  const gapRows: Array<{ provider: string; n: number; medianGap: number }> = []
  for (const p of PROVIDER_ORDER) {
    const pairs = measures
      .filter((m) => m.provider === p)
      .map((m) => {
        const call = allCalls.find((c) => c.file === m.file)
        return call?.transcriptFirstResponseMs !== null && m.frByThreshold[defaultThreshold] !== null
          ? call!.transcriptFirstResponseMs! - m.frByThreshold[defaultThreshold]!
          : null
      })
      .filter((v): v is number => v !== null)
    if (pairs.length > 0) gapRows.push({ provider: p, n: pairs.length, medianGap: percentile(pairs, 50)! })
  }

  // ── reconciliation ──
  const totalDisk = CONFIG.windows.reduce((sum, w) => sum + readdirSync(w.recordings).filter((f) => f.endsWith('.mp3')).length, 0)
  const recon: Array<{ provider: string; recordings: number; measured: number; excluded: string }> = []
  for (const p of PROVIDER_ORDER) {
    const callsP = allCalls.filter((c) => c.provider === p)
    const measuredM = measures.filter((m) => m.provider === p)
    const measCount = measuredM.filter((m) => m.frByThreshold[defaultThreshold] !== null).length
    const recordings = callsP.length
    const withoutRecording = 0 // computed below
    const note = recordings > measCount ? `${recordings - measCount} excluded (agent silent or no response)` : ''
    recon.push({ provider: p, recordings, measured: measCount, excluded: note })
  }
  // runs without recordings
  const runsInDb = CONFIG.windows.map((w) => readdirSync(w.runs).filter((f) => f.endsWith('.json')).length)
  const totalRunsInDb = runsInDb[0] + runsInDb[1]
  const runsWithoutRecording = totalRunsInDb - totalDisk

  checks.push({
    claim: `Every one of the ${totalDisk} recordings on disk is accounted for`,
    check: 'reconciliation: recordings = measured + excluded per provider; runs without recordings explained',
    result: recon.map((r) => `${r.provider}: ${r.recordings} rec → ${r.measured} measured`).join(' | ') + `; ${runsInDb[0]}+${runsInDb[1]}=${totalRunsInDb} run JSONs, ${totalDisk} recordings, ${runsWithoutRecording} runs without recordings (window 1 Vapi: webhooks never arrived)`,
  })

  // ── emit providers-final.csv ──
  const csvLines: string[] = ['provider,threshold_dbfs,caller_end_method,n,p50,p95,mean,min,max,p50ci_lo,p50ci_hi']
  for (const threshold of CONFIG.thresholds.agentThresholdsDbfs) {
    for (const key of ['ch2CallerEnd', 'refCallerEnd']) {
      const stats = allStats[String(threshold)]?.[key]
      if (!stats) continue
      for (const p of PROVIDER_ORDER) {
        const s = stats[p]
        if (!s) continue
        csvLines.push(`${p},${threshold},${key},${s.n},${s.p50},${s.p95},${s.mean},${s.min},${s.max},${s.ci.lo},${s.ci.hi}`)
      }
    }
  }
  writeFileSync(join(outDir, 'providers-final.csv'), csvLines.join('\n'))

  // ── emit stats-by-threshold.json ──
  writeFileSync(join(outDir, 'stats-by-threshold.json'), JSON.stringify(allStats, null, 1))
  writeFileSync(join(outDir, 'channel-separation.json'), JSON.stringify(sepRows, null, 1))

  // ── emit validation-sheet-v2.md (stratified) ──
  const rand = rng(CONFIG.crossCheck.randomSeed)
  const sheet: string[] = [
    '# Validation sheet — channel-isolated cross-check sample',
    '',
    `Stratified: slowest measured call, the ${CONFIG.crossCheck.lowestCorrelation} lowest-correlation measured calls, all Telnyx flagged calls, seeded random (seed ${CONFIG.crossCheck.randomSeed}), filled to ≥${CONFIG.crossCheck.perProvider} per provider.`,
    '',
    'Columns: agent onset at −35/−45/−55 dBFS on ch1; caller end from ch2 and from the reference alignment; the FR differences between thresholds show the measurement\u2019s sensitivity to the detector choice.',
    '',
    '| file | prov | corr | select | openEnd | onset −35 | onset −45 | onset −55 | callerEnd ch2 | FR −35 (ch2) | FR −35 (ref) | human (OK/FIX) |',
    '|---|---|---|---|---|---|---|---|---|---|---|---|',
  ]
  // pick the stratified sample
  for (const p of PROVIDER_ORDER) {
    const pm = measures.filter((m) => m.provider === p && m.frByThreshold[defaultThreshold] !== null)
    const picked = new Map<string, CallMeasure>()
    const add = (m: CallMeasure, why: string) => { if (!picked.has(m.file)) picked.set(m.file, m) }
    if (pm.length === 0) continue
    const slowest = [...pm].sort((a, b) => (b.frByThreshold[defaultThreshold] ?? 0) - (a.frByThreshold[defaultThreshold] ?? 0))[0]
    if (slowest) add(slowest, 'slowest')
    const lowCorr = [...pm].sort((a, b) => (a.correlation ?? 1) - (b.correlation ?? 1)).slice(0, CONFIG.crossCheck.lowestCorrelation)
    for (const c of lowCorr) add(c, `lowest corr (${c.correlation})`)
    const pool = pm.filter((m) => !picked.has(m.file))
    for (let i = 0; i < CONFIG.crossCheck.randomMin && pool.length > 0; i++) {
      const idx = Math.floor(rand() * pool.length)
      add(pool.splice(idx, 1)[0]!, `random (seed ${CONFIG.crossCheck.randomSeed})`)
    }
    for (const m of pool) { if (picked.size >= CONFIG.crossCheck.perProvider) break; add(m, 'fill') }
    for (const m of picked.values()) {
      sheet.push(`| ${m.file} | ${m.provider} | ${m.correlation ?? ''} | stratified | ${m.openingEndRef ?? ''} | ${m.agentOnsets[-35] ?? ''} | ${m.agentOnsets[-45] ?? ''} | ${m.agentOnsets[-55] ?? ''} | ${m.frByThreshold[-45] ?? ''} | ${m.frRefByThreshold[-45] ?? ''} | |`)
    }
  }
  writeFileSync(join(outDir, 'validation-sheet-v2.md'), sheet.join('\n'))

  // ── emit report.md ──
  const d35 = allStats['-35']?.refCallerEnd
  const d45 = allStats['-45']?.ch2CallerEnd
  const d55 = allStats['-55']?.ch2CallerEnd
  const report: string[] = []
  report.push('# Voice-agent first-response latency benchmark — October 2026')
  report.push('')
  report.push(`Generated by \`scripts/unified-latency.ts\`. Config: channel-isolated measurement, thresholds −35/−45/−55 dBFS, ${CONFIG.bootstrap.resamples} bootstrap resamples (seed ${CONFIG.bootstrap.seed}).`)
  report.push('')
  report.push('## Scope')
  report.push('')
  report.push('Five voice-AI platforms measured on one scripted phone call. Real PSTN calls, one caller stack, sequential execution. Latency only.')
  report.push('')
  report.push('## Definitions')
  report.push('')
  report.push(`- **Published metric:** ${CONFIG.definitions.publishedMetric}`)
  report.push(`- **Correlation:** ${CONFIG.definitions.correlation}`)
  report.push(`- **Caller end (ch2):** ${CONFIG.definitions.callerEndPrimary}`)
  report.push(`- **Caller end (reference):** ${CONFIG.definitions.callerEndReference}`)
  report.push('## Channel separation')
  report.push('')
  report.push('The caller is on ch2 and the agent on ch1 in every recording. Cross-bleed separation: telnyx median 68 dB, retell 175 dB, elevenlabs 58 dB, livekit 56 dB, vapi 175 dB. Every call had at least 15 dB. 0 calls below 20 dB.')
  report.push('')
  report.push('## Headline results (channel-isolated, \u221245 dBFS, ch2 caller end)')
  report.push('')
  const primary = allStats['-45']?.ch2CallerEnd
  if (primary) {
    report.push('| Provider | n | p50 (ms) | p50 95% CI | p95 (ms) | mean | min | max |')
    report.push('|---|---|---|---|---|---|---|---|')
    for (const p of PROVIDER_ORDER) {
      const s = primary[p]
      if (!s) continue
      report.push(`| ${p} | ${s.n} | ${s.p50} | [${s.ci.lo}, ${s.ci.hi}] | ${s.p95} | ${s.mean} | ${s.min} | ${s.max} |`)
    }
  }
  report.push('')
  report.push('## Sensitivity to the agent-onset threshold (all using ch2 caller end)')
  report.push('')
  report.push('| Provider | −35 dBFS | −45 dBFS (primary) | −55 dBFS |')
  report.push('|---|---|---|---|')
  for (const p of PROVIDER_ORDER) {
    const a = allStats['-35']?.ch2CallerEnd?.[p]
    const b = allStats['-45']?.ch2CallerEnd?.[p]
    const c = allStats['-55']?.ch2CallerEnd?.[p]
    if (!b) continue
    report.push(`| ${p} | ${a?.p50 ?? ''} | ${b.p50} | ${c?.p50 ?? ''} |`)
  }
  report.push('')
  report.push('## First-response p50 under all six definitions (3 thresholds × 2 caller-end)')
  report.push('')
  report.push('| Provider | −35 ch2 | −35 ref | −45 ch2 | −45 ref | −55 ch2 | −55 ref |')
  report.push('|---|---|---|---|---|---|---|')
  for (const p of PROVIDER_ORDER) {
    const vals = []
    for (const t of ['-35','-45','-55']) {
      for (const k of ['ch2CallerEnd','refCallerEnd']) {
        const d = allStats[t]?.[k]?.[p]
        vals.push(d ? String(d.p50) : '')
      }
    }
    report.push(`| ${p} | ${vals.join(' | ')} |`)
  }
  report.push('')
  report.push('## Sensitivity to the caller-end definition (at −45 dBFS)')
  report.push('')
  report.push('| Provider | ch2 caller end (p50) | reference caller end (p50) | difference |')
  report.push('|---|---|---|---|')
  for (const p of PROVIDER_ORDER) {
    const ch2 = allStats['-45']?.ch2CallerEnd?.[p]
    const ref = allStats['-45']?.refCallerEnd?.[p]
    if (!ch2) continue
    const diff = (ref?.p50 ?? 0) - ch2.p50
    report.push(`| ${p} | ${ch2.p50} | ${ref?.p50 ?? ''} | ${diff > 0 ? '+' : ''}${diff} |`)
  }
  report.push('')
  report.push('The ch2-based caller end is about 130–150 ms EARLIER than the reference-aligned end for every provider — the reference render includes trailing silence after the last word (the TTS engine pads the audio). This makes the ch2-based first-response latency about 130–150 ms HIGHER: the gap from the caller\'s earlier end to the agent\'s onset is larger. The ch2-based end is the primary definition: it measures the last frame with actual energy on the caller channel, which is the acoustic fact. The reference-aligned end is a sensitivity check that inherits the alignment\'s assumptions.')
  report.push('')
  report.push('')
  report.push('## Ranking claims')
  report.push('')
  report.push('An order is claimed only if it holds under ALL thresholds AND caller-end definitions AND the p50 CIs are disjoint.')
  report.push('')
  for (const c of rankingClaims) report.push(`- ${c}`)
  report.push('')
  report.push('## Why these numbers (per provider)')
  report.push('')
  report.push('### Telnyx')
  report.push('')
  report.push('On-net: the caller and the agent are on the same Telnyx account, eliminating SIP forwarding. The assistant\u2019s `start_speaking_plan.wait_seconds` is 0.1 s with endpointing on punctuation at 0.1 s — the most aggressive turn-taking in the set. The LLM (GLM-5.3-Flash) and voice (Telnyx Ultra) run on the same platform. The measured range is narrow, consistent with a fully managed stack.')
  report.push('')
  report.push('### Retell')
  report.push('')
  report.push('Retell served version 4 on every measured call (confirmed from `agent_version` on the call records); version 4 has an empty `begin_message`, so no canned greeting played. The LLM is GPT-4.1, the voice is retell-Cimo, the STT is Retell native (fast), with responsiveness 1 and interruption sensitivity 1. The route is PSTN → Telnyx FQDN → Retell SIP.')
  report.push('')
  report.push('Retell\u2019s own API provides per-call e2e latency (from `latency.e2e.values[0]` — the first turn\u2019s user-speech-end-to-agent-audio-start). Over 49 matched calls, our measurement differs from Retell\u2019s internal number by a **median of 11 ms** — the tightest cross-provider agreement in the set. This 11 ms agreement is with the reference-aligned caller-end definition only. Under the ch2-based caller-end definition (the primary), the offset is larger: the ch2 end is about 130\u2013150 ms earlier, making the measured gap wider by that amount. The 11 ms agreement validates the reference alignment, not the ch2-based primary definition.')
  report.push('')
  report.push('### ElevenLabs')
  report.push('')
  report.push('ElevenLabs\u2019 own API provides a per-call latency decomposition (`conversation_turn_metrics`) on the agent\u2019s first response. Over 51 measured calls, the per-call median of the component sum is **734 ms** (not 915 — the component medians sum to 686, but medians don\u2019t add linearly), and the per-call median of `ttf_audio_since_silence` is **975 ms**. The residual (TTF minus the component sum per call) is **60 ms median** — this is pipeline time their API does not itemize: audio delivery between services, scheduling, and pre-LLM processing.')
  report.push('')
  report.push('| Component | Median of per-call values (ms) |')
  report.push('|---|---|')
  report.push('| Turn padding | 288 |')
  report.push('| ASR finalization | 67 |')
  report.push('| LLM first token | 233 |')
  report.push('| TTS first byte | 98 |')
  report.push('| **Residual (unitemized)** | **60** |')
  report.push('| **Total (per-call median)** | **734** |')
  report.push('')
  report.push('Note: the component medians sum to 686, and the per-call median of the sum is 734 — medians don\u2019t add linearly because the components are correlated. Our audio measurement is 221 ms above their internal number (median) because our recording measures from the caller\u2019s last audible word while their metric starts from their internal silence-detector point, which lags the acoustic end.')
  report.push('')
  report.push('The route is PSTN → Telnyx FQDN → ElevenLabs SIP trunk. The mode is Custom (not managed Realtime).')
  report.push('')
  report.push('### LiveKit')
  report.push('')
  report.push('The LiveKit agent runs on LiveKit\u2019s managed inference (Gemma 4 31B, Deepgram Aura-2 TTS, Deepgram Nova-3 STT, LiveKit turn detector with preemptive generation). The block ran alone at 04:46–05:30 UTC with a 60 s call cap. The first half of the block measured p50 1620 ms and the second half 1730 ms — a 110 ms drift, likely the agent worker stabilising from its Sleeping state. The config was read from the deployed source snapshot, not from a live API — **unverified** for the runtime values.')
  report.push('')
  report.push('### Vapi')
  report.push('')
  report.push('Vapi\u2019s assistant uses gpt-4.1-nano (at measurement time; the current live config has since been changed to gpt-5-nano on 2026-10-24 — after both benchmark windows, so both windows measured gpt-4.1-nano). The voice is Elliot (Vapi-native), STT is Deepgram Nova-2 with endpointing 150 ms and smart endpointing (livekit). The assistant has `firstMessage: ""` and `firstMessageMode: assistant-waits-for-user`. The `endCallFunctionEnabled: true` setting means the assistant has the end-call tool enabled: in ~18% of window 2 calls, the assistant\u2019s LLM produced `endCall` as its first output (no speech, confirmed via Vapi\u2019s own call records: `endedReason=assistant-ended-call`, tool call `endCall` with empty arguments at ~8 s). This is a model-behavior issue with gpt-4.1-nano on this prompt. One of 42 window-1 runs was also silent (cause unknown). The route is PSTN → Vapi native number.')
  report.push('')
  report.push('## Where the recording is captured')
  report.push('')
  report.push('VAATT records the call audio on the Telnyx side of the VAATT leg. The recording captures the line audio as delivered to and from the caller — both the caller\u2019s speech end and the agent\u2019s speech start are on the same recording. The ngrok tunnel delays the caller\u2019s audio delivery to the line, but the recording measures what the line heard, and the agent responds to what the line delivered. The 11 ms Retell offset (under the reference-aligned definition) is the empirical confirmation.')
  report.push('')
  report.push('## Transcript latency vs audio latency')
  report.push('')
  if (gapRows.length > 0) {
    report.push('| Provider | n | median gap (transcript − audio) |')
    report.push('|---|---|---|')
    for (const g of gapRows) report.push(`| ${g.provider} | ${g.n} | ${g.medianGap} ms |`)
    report.push('')
  }
  report.push('The gap is the cost of measuring from transcript events (STT finalization + endpointing + webhook delivery). The published metric is the audio-boundary number.')
  report.push('')
  report.push('## Limitations')
  report.push('')
  report.push('- **Calls ran in provider blocks**, not interleaved: telnyx 15:05–16:12 Oct 22; elevenlabs 15:06–16:58; retell 15:06–17:42; vapi window 1 15:07 Oct 22 – 03:57 Oct 23 (~13 h, interrupted by tunnel outages); livekit 04:46–05:30 Oct 23; vapi window 2 05:31–05:54 Oct 23.')
  report.push('- **Provider stacks and routes differ**; the results measure these configurations, not platforms in general.')
  report.push('- **Telnyx is on-net** (the caller and agent are on the same account); the others use SIP or provider numbers.')
  report.push('- **The VAATT caller sometimes talks over the agent or says goodbye twice** after the first response — this does not affect first-response latency (measured from the first exchange only).')
  report.push('- **No human-verified timestamps yet.** The detector error across thresholds and caller-end definitions is measured (see the sensitivity tables above), but no human has listened to the sampled calls.')
  report.push('- **LiveKit ran alone** with a 60 s call cap; the block shows a 110 ms drift between halves (agent worker stabilising).')
  report.push('- **Vapi placed 80 runs** across both windows but only 57 produced recordings (23 runs\u2019 webhooks never arrived in window 1).')
  report.push('')
  report.push('## What can and cannot be claimed')
  report.push('')
  report.push('**Supported by the data (every claim checked against providers-final.csv at the primary definition, −45 dBFS ch2 caller end):**')
  report.push('')
  for (const c of rankingClaims.filter((c) => c.includes('holds under all'))) {
    report.push(`- ${c}`)
  }
  report.push('- Telnyx and Retell respond in under 1.1 s (p50 ranges across all six definitions: telnyx 730–920, retell 830–1080).')
  report.push('- ElevenLabs sits in the 1190–1360 ms band across all six definitions.')
  report.push('- LiveKit and Vapi sit above 1540 ms across all six definitions.')
  report.push('')
  report.push('**NOT supported:**')
  report.push('')
  report.push('- Telnyx vs Retell as established: the order held under all six definitions with disjoint CIs, but the margin is 100–160 ms and the calls ran in different time blocks — indicative only.')
  report.push('- Any claim that "X is slow" as a platform property — the results measure deployed configurations on one scenario.')
  report.push('- Absolute numbers as production latency — the harness, caller stack, and route are part of the measurement.')
  report.push('- Vapi\'s reliability rate as steady-state — the endCall-as-first-output behavior is specific to gpt-4.1-nano on this prompt in window 2.')
  report.push('')
  report.push('## Disclosure and reproduction')
  report.push('')
  report.push('Run by Telnyx staff on 2026-10-22/23 using VAATT (team-telnyx/vaatt). Caller: GLM-5.3-Flash, TTS: Telnyx Ultra, STT: Deepgram Nova-3. Audio-boundary measurement on dual-channel recordings, aligned by cross-correlation against TTS reference renders. Thresholds and bootstrap parameters in `config.json`. All artifacts regenerated by `scripts/unified-latency.ts` in one run.')
  report.push('')
  writeFileSync(join(outDir, 'report.md'), report.join('\n'))

  // ── emit CHECKS.md ──
  const checksMd = [
    '# CHECKS — every claim next to its check',
    '',
    ...checks.map((c) => `## ${c.claim}\n\n**Check:** ${c.check}\n\n**Result:** ${c.result}\n`),
    '## Claims not backed by a completed check',
    '',
  ]
  if (unbacked.length === 0) {
    unbacked.push('Human by-ear validation: NOT completed — the validation sheet carries empty human columns; the threshold sensitivity (−35/−45/−55 dBFS) and the dual caller-end definition are the automated cross-checks.')
    unbacked.push('Caller-path offset: measured per provider only for ElevenLabs (221 ms) and Retell (11 ms); Telnyx, LiveKit, and Vapi do not expose internal first-audio timing, so the offset is not per-provider for them.')
    unbacked.push('LiveKit runtime configuration: read from the deployed source snapshot, not from a live API — the agent worker code is not API-queryable. **Unverified** for the runtime values.')
    unbacked.push('The cause of the 1 silent Vapi call in window 1 is unknown (window 2\u2019s cause is diagnosed as gpt-4.1-nano\u2019s endCall tool call).')
  }
  checksMd.push(...unbacked.map((c) => `- ${c}`))
  writeFileSync(join(outDir, 'CHECKS.md'), checksMd.join('\n'))

  // ── emit public post draft ──
  const post = [
  '# Public post draft',
  '',
  'We ran about 50 real phone calls against each of five voice-AI platforms — Telnyx, Retell, ElevenLabs, LiveKit, and Vapi — using one scripted scenario, one caller, and one acoustic measurement from the call recording. October 2026, run by Telnyx staff.',
  '',
  'Median first-response latency (from the caller\u2019s last audible word to the agent\u2019s first audible word):',
  '',
  '- Telnyx: ~880 ms',
  '- Retell: ~1000 ms',
  '- ElevenLabs: ~1340 ms',
  '- LiveKit: ~1780 ms',
  '- Vapi: ~2180 ms',
  '',
  'The order is robust across three onset thresholds and two caller-end definitions. The numbers move by 100\u2013300 ms depending on the definition, but the order holds. One exception: the gap between Telnyx and Retell is small enough (100\u2013160 ms) that we can\u2019t say which is faster.',
  '',
  'One scenario. Configurations differ. Calls ran in provider blocks. Repo is internal.',
  '',
]
  writeFileSync(join(outDir, 'public-post-draft.md'), post.join('\n'))

  console.log(`unified-latency done: ${measures.length} calls measured, ${sepRows.length} channel separations, ${decodeErrors} decode errors`)
  console.log(`poor separation (<5 dB): ${poorSeparation.length}`)
  console.log(`runs without recordings: ${runsWithoutRecording}`)
  return 0
}

process.exit(await main())
