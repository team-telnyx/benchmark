# Data

## `calls.csv`

One row per measured call (250 calls: Telnyx 51, Retell 49, ElevenLabs 51, LiveKit 50, Vapi 49). Calls the analysis excluded are not listed (see the report for the exclusion counts and reasons).

| Column | Meaning |
|---|---|
| `provider` | Platform tested |
| `window` | Run window: `batch-2026-10-22` or `completion-2026-10-23` |
| `call` | Anonymized call ID: provider, window, then a running number |
| `alignment_correlation` | How closely the recorded opening matched the reference clip used to locate it (0 to 1) |
| `channel_separation_db` | How much louder the caller is on its own channel than the agent is on it, in dB; higher means cleaner separation |
| `fr_ch2_35_ms`, `fr_ch2_45_ms`, `fr_ch2_55_ms` | First-response latency in milliseconds, with the caller's end taken from the caller channel and the agent's start taken at −35, −45 or −55 dBFS on the agent channel |
| `fr_ref_35_ms`, `fr_ref_45_ms`, `fr_ref_55_ms` | The same, with the caller's end taken from the reference clip alignment instead |

The headline figures in the report use `fr_ch2_45_ms`. See the report for how the two caller-end methods differ.

## `providers-final.csv`

Per-platform statistics for each of the six definitions, computed from `calls.csv`: `n`, `p50`, `p95`, `mean`, `min`, `max` (milliseconds) and the 95% bootstrap confidence interval on the median (10,000 resamples, seed 42).

## `block-timings.json`

When each platform's calls ran (UTC). Platforms ran in blocks, not interleaved.

## Not included

Raw recordings, run records and platform-side timing exports are not published. They contain test phone numbers and call identifiers, or belong to the platforms. The report quotes ElevenLabs' and Retell's own timing figures for context; the per-call values are not in this repository.
