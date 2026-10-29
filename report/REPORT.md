# Voice-agent first-response latency: five platforms, October 2026

Telnyx and Retell responded fastest (about 0.9 s and 1.0 s), then ElevenLabs (about 1.3 s), LiveKit (about 1.8 s) and Vapi (about 2.2 s). That order held under every way we measured it.

## Headline

First-response latency is the time from the caller's last audible sound to the agent's first audible sound, measured on the call recording. Median (p50) with a 95% confidence interval, from about 50 real phone calls per platform.

| Platform | Calls measured | p50 (ms) | 95% CI on p50 | p95 (ms) | Quote as |
|---|---|---|---|---|---|
| Telnyx | 51 | 880 | 860–900 | 1020 | about 0.9 s |
| Retell | 49 | 1000 | 940–1080 | 1420 | about 1.0 s |
| ElevenLabs | 51 | 1340 | 1180–1500 | 2040 | about 1.3 s |
| LiveKit | 50 | 1780 | 1700–1900 | 2211 | about 1.8 s |
| Vapi | 49 | 2180 | 2120–2220 | 2704 | about 2.2 s |

Definition used: the caller's end is the last frame above -45 dBFS on the caller's channel; the agent's start is the first 3 consecutive 20 ms frames above -45 dBFS on the agent's channel. Confidence intervals: 10,000 bootstrap resamples, seed 42.

## How the numbers were checked

- **Separate channels.** In every recording the caller is on one channel and the agent on the other, with at least 15 dB of separation in 247 of 254 recordings. Each boundary is read from its own channel, so the agent's audio and the caller's audio are never mixed up.
- **Six definitions, same order.** We measured every call six ways (three onset thresholds, two ways of marking the caller's end). The order Telnyx, Retell, ElevenLabs, LiveKit, Vapi held in all six. The exact values move by about 100-150 ms depending on the definition, which is the precision to expect:

| Platform | Lowest p50 | Highest p50 |
|---|---|---|
| Telnyx | 730 | 920 |
| Retell | 830 | 1080 |
| ElevenLabs | 1190 | 1360 |
| LiveKit | 1540 | 1820 |
| Vapi | 2010 | 2200 |

- **Listening check.** Four calls (Retell, Telnyx, ElevenLabs, Vapi) were cut into short clips with a marker at each measured boundary. The caller-end marker landed just after the caller's last line, and the agent-start marker landed between just before and right on the agent's first sound. This was a by-ear judgment on four calls, not a timed measurement.
- **Provider timings.** Retell reports its own first-response timing (about 0.94 s median, derived from our numbers and the measured offset) and ElevenLabs reports its own time to first audio (about 0.98 s median). Ours read higher: by 10-60 ms for Retell and 235-365 ms for ElevenLabs depending on definition. The platforms likely start their clocks at their own end-of-speech detection, later than the caller's last sound, but we did not confirm that.
- **Every call accounted for.** 264 recordings on disk: 250 measured, 14 excluded (3 where the Telnyx agent's speech overlapped the caller's opening, 10 where the agent never spoke, 1 with weak alignment).

## Results in context

- **Telnyx vs Retell:** Telnyx was ahead in all six definitions, by about 100-160 ms, with non-overlapping intervals under the primary definition. Treat it as a small lead.
- **Larger gaps:** Retell to ElevenLabs about 340 ms, ElevenLabs to LiveKit about 440 ms, LiveKit to Vapi about 400 ms. All have non-overlapping intervals.
- **ElevenLabs' own breakdown** (median per call): about 290 ms is the turn-taking wait, then speech recognition 67 ms, language model 233 ms and voice generation 98 ms. The turn-taking wait is a setting, and this test used Custom mode, not the managed Realtime mode.

## Configurations tested

Each platform was measured in its own configuration.

| Platform | Model | Speech recognition | Voice | Greeting | Route |
|---|---|---|---|---|---|
| Telnyx | GLM-5.3-Flash | Deepgram Nova-3 | Telnyx Ultra | none | same network as the caller |
| Retell | GPT-4.1 (version 4) | Retell native, fast | retell-Cimo | none | Telnyx number forwarded by SIP |
| ElevenLabs | Qwen, Custom mode | Scribe v2 Realtime | Flash v2 | none | Telnyx number forwarded by SIP |
| Vapi | gpt-4.1-nano | Deepgram Nova-2, 150 ms endpointing | Elliot | none | provider number |
| LiveKit | Gemma 4 31B (from the deployed source) | Deepgram Nova-3 | Deepgram Aura-2 | none | provider number |

Configurations come from provider snapshots taken after the runs. Vapi's model was changed on Oct 24, after both windows, so the runs used gpt-4.1-nano.

## Scope

- One scenario (an appointment reschedule), English, clean audio, one caller setup, calls placed one at a time, about 50 per platform.
- Platforms ran in blocks, one after another (UTC): Telnyx 15:16-16:11 Oct 22; ElevenLabs 16:12-16:57; Retell 16:58-17:42; Vapi 17:42 Oct 22 to 03:56 Oct 23; LiveKit 04:46-05:29 Oct 23; Vapi again 05:31-05:54. Vapi's two windows, about 12 hours apart, agree within 100 ms (1950 and 2070 ms).
- Results describe these configurations on this path. Telnyx is on the same network as the caller; the others are reached over SIP or provider numbers.
- The simulated caller occasionally talks over the agent or says goodbye twice after the first exchange. This happens after the first response and does not affect these numbers.

## Reliability (supporting context)

| Platform | Window | Runs | Passed | Failed | Errored |
|---|---|---|---|---|---|
| Telnyx | Oct 22 | 55 | 51 | 3 | 1 |
| ElevenLabs | Oct 22 | 52 | 49 | 0 | 3 |
| Retell | Oct 22 | 52 | 48 | 1 | 3 |
| Vapi | Oct 22 | 42 | 10 | 9 | 22 |
| Vapi | Oct 23 | 38 | 27 | 4 | 7 |
| LiveKit | Oct 22 (smoke) | 1 | 1 | 0 | 0 |
| LiveKit | Oct 23 | 51 | 48 | 0 | 2 |

Most errors came from the test setup, not the platforms: 22 of Vapi's 42 Oct 22 runs errored when the webhook tunnel dropped, and 2 LiveKit runs failed to connect media to the test line. On Oct 23, 7 of 38 Vapi runs had no agent speech; in the ones inspected, the assistant called its end-call tool as its first action. This reliability picture is incomplete and should not be read as a platform reliability rate.

## What the data supports

- Telnyx and Retell are the fastest at around 1 second, ElevenLabs about 1.3 s, LiveKit about 1.8 s, Vapi about 2.2 s, in this setup.
- The order Telnyx, Retell, ElevenLabs, LiveKit, Vapi, under every definition tested.
- Telnyx slightly ahead of Retell, by about 0.1 s.

Not claimed: exact numbers as production latency, anything about other scenarios, languages or noisy audio, platform reliability rates, or that any platform is slow in general.

## Disclosure

Run by Telnyx staff on a Telnyx-built test harness, October 22-23, 2026. The simulated caller uses Telnyx services, and the Telnyx agent is on the same network as the caller. The harness repository is internal to Telnyx.
