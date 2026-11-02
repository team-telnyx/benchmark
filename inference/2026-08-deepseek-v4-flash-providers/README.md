# DeepSeek V4 Flash provider benchmark, August 2026

Part of the [inference benchmarks](../README.md). This run compared deepseek-ai/DeepSeek-V4-Flash-0731 across Telnyx, Baseten, Together AI and Fireworks with reasoning disabled where supported.

| Provider | Successful requests | p50 E2E | p95 E2E | p50 output speed |
|---|---:|---:|---:|---:|
| Telnyx | 60/60 | 6.64 s | **12.24 s** | **156.5 tok/s** |
| Baseten | 54/60 | **4.01 s** | 12.97 s | 127.0 tok/s |
| Together AI | 60/60 | 7.90 s | 24.91 s | 79.5 tok/s |
| Fireworks | 60/60 | 8.31 s | 14.49 s | 88.8 tok/s |

Telnyx recorded the highest median output speed and the lowest p95 completed-response latency among providers that completed all 60 requests. Baseten had the lowest median E2E among its 54 successful requests; six long-context requests were rate limited.

Read [the full report](report/REPORT.md) for the complete methodology. The public article is [DeepSeek V4 Flash inference benchmarks](https://telnyx.com/resources/deepseek-v4-flash-benchmarks).

## Layout

- data/raw-measurements.csv: all 240 measured attempts, including failures.
- data/clean-successful-requests.csv: 234 successful rows used in percentile calculations.
- data/failed-requests.csv: failed rows with response bodies reduced to error categories.
- config/run-request.json: exact prompt profiles and provider configuration.
- scripts/actor/: hardened benchmark-actor source compatible with the request schema.

## Read before quoting

- All successful rows recorded zero reasoning characters, but provider implementations may still differ.
- Baseten's failed requests are retained and excluded from latency percentiles.
- DSpark or speculative decoding was not toggled independently.
- The actor invoke region is known; provider GPU-serving regions were not independently logged.

## Disclosure

Run by Telnyx staff from a Telnyx stateful actor on August 26, 2026. Telnyx Inference was one of the compared endpoints, and the actor issued calls to every provider.
