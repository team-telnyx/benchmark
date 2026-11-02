# GLM 5.3 Flash provider benchmark, September 2026

Part of the [inference benchmarks](../README.md). This run compared GLM 5.3 Flash across Telnyx, Baseten, Together AI and Fireworks using six prompt profiles and ten attempts per provider/profile cell.

| Provider | Successful requests | p50 E2E | p95 E2E | p50 output speed |
|---|---:|---:|---:|---:|
| Telnyx | 60/60 | 5.52 s | 10.77 s | **196.4 tok/s** |
| Baseten | 44/60 | **2.71 s** | 10.21 s | 190.1 tok/s |
| Together AI | 60/60 | 6.93 s | 19.91 s | 102.4 tok/s |
| Fireworks | 60/60 | 9.13 s | 16.96 s | 127.9 tok/s |

Telnyx recorded the highest median output speed and completed all 60 requests. Baseten recorded the lowest median E2E among successful rows but returned 16 rate-limit failures on 100K-input profiles.

Read [the full report](report/REPORT.md) for profile-level results. The public article is [GLM 5.3 Flash inference benchmarks](https://telnyx.com/resources/glm-5-3-flash-benchmarks).

## Layout

- data/raw-measurements.csv: all 240 measured attempts, including failures.
- data/clean-successful-requests.csv: successful rows used in percentile calculations.
- data/failed-requests.csv: failed rows with provider response bodies removed.
- config/run-request.json: exact prompt and provider request configuration.
- scripts/actor/: hardened benchmark-actor source compatible with the request schema.

## Read before quoting

- Baseten's 16 rate-limited requests are retained and excluded from latency percentiles.
- Telnyx throughput used separate non-stream token-usage probes for 59 rows because streamed usage was unavailable.
- TTFT is first streamed activity and may be reasoning rather than visible answer text.
- Provider GPU-serving regions were not independently logged.

## Disclosure

Run by Telnyx staff from a Telnyx stateful actor on September 2, 2026. Telnyx Inference was one of the compared endpoints, and the actor issued calls to every provider.
