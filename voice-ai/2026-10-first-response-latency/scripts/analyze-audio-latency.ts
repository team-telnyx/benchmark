#!/usr/bin/env bun
/**
 * Audio-boundary first-response latency over the benchmark recordings.
 *
 * The batch's recordings have inconsistent channel layouts and epochs (one
 * channel silent, both speakers on one channel, files running past hangup), so
 * fixed channel assumptions and transcript-time alignment cannot be trusted.
 * The opening utterance, though, is scripted and always rendered by the same
 * caller voice — so each recording is aligned by cross-correlating against a
 * reference render of the same text with the same voice. The opening's start
 * and end land exactly in the file's own timeline, and the agent's first
 * speech onset after it gives first-response latency:
 *
 *   first_response_ms = agent_speech_onset_ms - caller_opening_end_ms
 *
 * measured from the mono mix of the two channels. Greetings spoken before the
 * opening are flagged; agent speech overlapping the opening is an
 * early-response event and yields no sample, per the benchmark plan.
 *
 * Usage:
 *   bun run scripts/analyze-audio-latency.ts \
 *     [--recordings <dir>] [--runs <dir>] [--reference <mp3>] [--out <dir>]
 */
import { readdirSync, readFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs'
import { basename, join } from 'node:path'

interface CallResult {
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
  agentSpoke: boolean
  transcriptFirstResponseMs: number | null
  fileDurationS: number
  callDurationS: number | null
}

const RUN_ID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/
const SR = 8000

function decodeStereo(file: string): Promise<{ ch1: Float32Array; ch2: Float32Array }> {
  return new Promise((resolve, reject) => {
    const child = Bun.spawn(
      ['ffmpeg', '-v', 'error', '-i', file, '-f', 's16le', '-acodec', 'pcm_s16le', '-ar', String(SR), '-ac', '2', '-'],
      { stdout: 'pipe', stderr: 'ignore' },
    )
    void new Response(child.stdout).arrayBuffer().then((buf) => {
      void child.exited.then((code) => {
        if (code !== 0) {
          reject(new Error(`ffmpeg exit ${code}`))
          return
        }
        const pcm = Buffer.from(buf)
        const frames = Math.floor(pcm.length / 4)
        const ch1 = new Float32Array(frames)
        const ch2 = new Float32Array(frames)
        for (let i = 0; i < frames; i++) {
          ch1[i] = pcm.readInt16LE(i * 4) / 32768
          ch2[i] = pcm.readInt16LE(i * 4 + 2) / 32768
        }
        resolve({ ch1, ch2 })
      })
    })
  })
}

/** Max-absence mono mix: whichever channel carries audio wins each frame. */
function monoMix(ch1: Float32Array, ch2: Float32Array): Float32Array {
  const out = new Float32Array(ch1.length)
  for (let i = 0; i < out.length; i++) out[i] = Math.max(Math.abs(ch1[i]!), Math.abs(ch2[i]!))
  return out
}

/**
 * Normalized cross-correlation of `ref` sliding over `signal`, with prefix
 * sums carrying the window mean/variance so each offset costs one dot pass.
 */
function bestAlignment(signal: Float32Array, ref: Float32Array): { offset: number; score: number } {
  const m = ref.length
  const n = signal.length
  if (n < m) return { offset: -1, score: 0 }
  let rMean = 0
  for (const v of ref) rMean += v
  rMean /= m
  let rVar = 0
  for (const v of ref) rVar += (v - rMean) * (v - rMean)
  const rNorm = Math.sqrt(rVar)
  if (rNorm === 0) return { offset: -1, score: 0 }

  const S = new Float64Array(n + 1)
  const Q = new Float64Array(n + 1)
  for (let i = 0; i < n; i++) {
    S[i + 1] = S[i]! + signal[i]!
    Q[i + 1] = Q[i]! + signal[i]! * signal[i]!
  }
  let best = { offset: -1, score: 0 }
  for (let o = 0; o + m <= n; o++) {
    const sum = S[o + m]! - S[o]!
    const sq = Q[o + m]! - Q[o]!
    const wMean = sum / m
    const wVar = sq - m * wMean * wMean
    if (wVar <= 0) continue
    let acc = 0
    for (let i = 0; i < m; i++) acc += signal[o + i]! * ref[i]!
    const dot = acc - m * wMean * rMean
    const score = dot / (Math.sqrt(wVar) * rNorm)
    if (score > best.score) best = { offset: o, score }
  }
  return best
}

function decimate(signal: Float32Array, factor: number): Float32Array {
  const out = new Float32Array(Math.floor(signal.length / factor))
  for (let i = 0; i < out.length; i++) out[i] = signal[i * factor]!
  return out
}

/** Sustained speech onset at/after `fromSample`: `attack` loud 20 ms frames above -35 dBFS. */
function speechOnset(signal: Float32Array, fromSample: number): number | null {
  const frame = SR / 50
  const threshold = Math.pow(10, -35 / 20)
  const attack = 3
  let loud = 0
  for (let offset = Math.max(0, fromSample); offset + frame <= signal.length; offset += frame) {
    let sum = 0
    for (let i = offset; i < offset + frame; i++) sum += signal[i]! * signal[i]!
    const rms = Math.sqrt(sum / frame)
    if (rms >= threshold) {
      loud += 1
      if (loud >= attack) return offset - (attack - 1) * frame
    } else {
      loud = 0
    }
  }
  return null
}

function transcriptFirstResponse(turns: Array<{ speaker: string; atMs: number; endMs?: number }>): number | null {
  const caller = turns
    .filter((t) => t.speaker === 'caller' && t.endMs !== undefined)
    .sort((a, b) => a.atMs - b.atMs)[0]
  if (!caller?.endMs) return null
  const agent = turns
    .filter((t) => t.speaker === 'agent' && t.atMs > caller.endMs!)
    .sort((a, b) => a.atMs - b.atMs)[0]
  return agent ? agent.atMs - caller.endMs : null
}

async function main(): Promise<number> {
  const argv = Bun.argv.slice(2)
  let recordings = 'results/backup-2026-10-22/recordings'
  let runsDir = 'results/backup-2026-10-22/runs'
  let referencesDir = 'results/audio-latency/references'
  let outDir = 'results/audio-latency'
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--recordings') recordings = argv[++i] ?? recordings
    else if (argv[i] === '--runs') runsDir = argv[++i] ?? runsDir
    else if (argv[i] === '--references-dir') referencesDir = argv[++i] ?? referencesDir
    else if (argv[i] === '--out') outDir = argv[++i] ?? outDir
  }
  if (!existsSync(recordings) || !existsSync(referencesDir)) {
    console.error(`missing input: ${recordings} or ${referencesDir}`)
    return 2
  }
  mkdirSync(outDir, { recursive: true })
  const quiet = argv.includes('--quiet')
  const debug = argv.includes('--debug')
  const log = (msg: string) => {
    if (!quiet) console.log(msg)
  }
  log(
    `[analyze] recordings=${recordings} runs=${runsDir} out=${outDir} thresholds: corr>=0.40, searchWindow=90s, fineTolerance=±150ms, onsetAttack=60ms@-35dBFS`,
  )

  const runsById = new Map<string, { scenarioId: string; durationSeconds: number | null; turns: Array<{ speaker: string; atMs: number; endMs?: number }> }>()
  for (const f of readdirSync(runsDir)) {
    if (!f.endsWith('.json')) continue
    const run = JSON.parse(readFileSync(join(runsDir, f), 'utf8'))
    runsById.set(run.id, run)
  }

  const references: Array<{ name: string; signal: Float32Array }> = []
  for (const f of readdirSync(referencesDir).filter((x) => x.endsWith('.mp3')).sort()) {
    const stereo = await decodeStereo(join(referencesDir, f))
    references.push({ name: f, signal: monoMix(stereo.ch1, stereo.ch2) })
  }
  console.log(`references: ${references.map((r) => `${r.name} (${(r.signal.length / SR).toFixed(2)}s)`).join(', ')}`)

  const files = readdirSync(recordings).filter((f) => f.endsWith('.mp3')).sort()
  const results: CallResult[] = []
  for (const file of files) {
    const m = file.match(RUN_ID)
    const run = m ? runsById.get(m[0]) : undefined
    try {
      const stereo = await decodeStereo(join(recordings, file))
      const signal = monoMix(stereo.ch1, stereo.ch2)
      const searchLen = Math.min(signal.length, 90 * SR)
      const coarseSig = decimate(signal.subarray(0, searchLen), 10)

      // Try every reference; the best correlation wins (the caller model
      // paraphrased the script on some calls, so one render is not enough).
      let best: { ref: { name: string; signal: Float32Array }; coarse: { offset: number; score: number } } | null = null
      for (const ref of references) {
        const coarse = bestAlignment(coarseSig, decimate(ref.signal, 10))
        if (best === null || coarse.score > best.coarse.score) best = { ref, coarse }
      }
      const ref = best!.ref
      const coarse = best!.coarse
      let result: CallResult
      if (coarse.offset < 0 || coarse.score < 0.4) {
        result = {
          file,
          runId: m?.[0] ?? null,
          scenarioId: run?.scenarioId ?? null,
          provider: run?.scenarioId.split('-')[2] ?? null,
          correlation: coarse.score > 0 ? Math.round(coarse.score * 1000) / 1000 : null,
          openingStartMs: null,
          openingEndMs: null,
          greetingMs: null,
          agentOnsetMs: null,
          firstResponseMs: null,
          earlyResponse: false,
          agentSpoke: (run?.turns ?? []).some((t) => t.speaker === 'agent'),
          transcriptFirstResponseMs: transcriptFirstResponse(run?.turns ?? []),
          fileDurationS: Math.round((signal.length / SR) * 10) / 10,
          callDurationS: run?.durationSeconds ?? null,
        }
      } else {
        const coarse8k = coarse.offset * 10
        const tolerance = 1200 // ±150 ms
        const windowStart = Math.max(0, coarse8k - tolerance)
        const windowEnd = Math.min(signal.length, windowStart + ref.signal.length + 2 * tolerance)
        const fine = bestAlignment(signal.subarray(windowStart, windowEnd), ref.signal)
        const offset = windowStart + fine.offset
        const openingEndSample = offset + ref.signal.length
        const onset = speechOnset(signal, openingEndSample + Math.round(0.15 * SR))
        const preOnset = offset > 0 ? speechOnset(signal.subarray(0, offset), 0) : null
        // The mono mix cannot attribute speakers: in a call where the agent
        // never spoke, the first "onset" after the opening is the caller's own
        // re-prompt. The run's transcript is the ground truth — no agent turn
        // means the detected onset is ours, not theirs.
        const agentSpoke = (run?.turns ?? []).some((t) => t.speaker === 'agent')
        // Agent speech during the opening adds power beyond the reference's own.
        // Recording gain differs per call, so calibrate per file: the median
        // power excess over speech frames sets the baseline, and a sustained
        // excess well above it is an early-response event. Max-abs mixing hides
        // an agent under a louder caller, so this uses the power sum.
        const frame = SR / 50
        const diffs: number[] = []
        for (let s = offset; s + frame <= openingEndSample; s += frame) {
          let powSum = 0
          for (let i = s; i < s + frame; i++) powSum += stereo.ch1[i]! ** 2 + stereo.ch2[i]! ** 2
          let refSum = 0
          for (let i = s - offset; i < s - offset + frame; i++) refSum += ref.signal[i]! ** 2
          const powDb = 10 * Math.log10(Math.max(powSum / frame, 1e-12))
          const refDb = 10 * Math.log10(Math.max(refSum / frame, 1e-12))
          if (refDb > -40) diffs.push(powDb - refDb)
        }
        diffs.sort((a, b) => a - b)
        const medianExcess = diffs.length > 0 ? diffs[Math.floor(diffs.length / 2)]! : 0
        let excessFrames = 0
        let overlap = false
        for (let s = offset; s + frame <= openingEndSample; s += frame) {
          let powSum = 0
          for (let i = s; i < s + frame; i++) powSum += stereo.ch1[i]! ** 2 + stereo.ch2[i]! ** 2
          let refSum = 0
          for (let i = s - offset; i < s - offset + frame; i++) refSum += ref.signal[i]! ** 2
          const powDb = 10 * Math.log10(Math.max(powSum / frame, 1e-12))
          const refDb = 10 * Math.log10(Math.max(refSum / frame, 1e-12))
          if (refDb > -40 && powDb - refDb > medianExcess + 8) {
            excessFrames += 1
            if (excessFrames >= 3) overlap = true
          } else {
            excessFrames = 0
          }
        }
        result = {
          file,
          runId: m?.[0] ?? null,
          scenarioId: run?.scenarioId ?? null,
          provider: run?.scenarioId.split('-')[2] ?? null,
          correlation: Math.round(fine.score * 1000) / 1000,
          openingStartMs: Math.round((offset / SR) * 1000),
          openingEndMs: Math.round((openingEndSample / SR) * 1000),
          greetingMs: preOnset === null ? null : Math.round((preOnset / SR) * 1000),
          agentOnsetMs: onset === null ? null : Math.round((onset / SR) * 1000),
          firstResponseMs: onset === null || !agentSpoke ? null : Math.round(((onset - openingEndSample) / SR) * 1000),
          earlyResponse: overlap,
          agentSpoke,
          transcriptFirstResponseMs: transcriptFirstResponse(run?.turns ?? []),
          fileDurationS: Math.round((signal.length / SR) * 10) / 10,
          callDurationS: run?.durationSeconds ?? null,
        }
      }
      results.push(result)
    } catch (err) {
      console.error(`skip ${basename(file)}: ${(err as Error).message} :: ${(err as Error).stack?.split('\n').slice(1, 3).join(' | ')}`)
    }
  }

  writeFileSync(join(outDir, 'calls.json'), JSON.stringify(results, null, 2))
  const aligned = results.filter((r) => r.firstResponseMs !== null)
  console.log(`analyzed ${results.length}: aligned ${aligned.length}, unaligned ${results.length - aligned.length}`)
  return 0
}

process.exit(await main())
