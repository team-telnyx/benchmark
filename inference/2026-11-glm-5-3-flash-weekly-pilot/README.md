# GLM 5.3 Flash weekly benchmark pilot, November 2026

Part of the [inference benchmarks](../README.md). This pilot compared GLM 5.3 Flash on Telnyx, Baseten, Together AI and Fireworks using six Moby-Dick prompt profiles, ten planned attempts per provider/profile cell, explicit low reasoning effort and larger completion limits that allowed every HTTP-successful stream to reach visible answer content.

| Provider | HTTP success | Visible answers | Pooled p50 E2E | Pooled observed p95 E2E | Pooled p50 output speed | Estimated successful-token cost |
|---|---:|---:|---:|---:|---:|---:|
| Telnyx | 60/60 | 60/60 | 5.73 s | 13.56 s | 156.7 tok/s | $0.289 |
| Baseten | 41/60 | 41/60 | N/A* | N/A* | N/A* | N/A* |
| Together AI | 60/60 | 60/60 | 5.51 s | 9.23 s | 195.8 tok/s | $0.322 |
| Fireworks | 60/60 | 60/60 | 7.76 s | 13.54 s | 123.5 tok/s | $0.321 |

Together AI had the lowest median first-visible-answer time and highest median output speed in all six complete workload comparisons, and the lowest median E2E latency in five of six. Telnyx led median E2E on the 10K-input/long-answer workload, completed 60/60 requests, and had the lowest estimated successful-token cost among the three providers with complete profile coverage.

`*` Baseten's pooled values are withheld because 19 requests in its 100K-input cells returned HTTP 429. Its valid 1K, 10K and available 100K measurements remain in the full report.

Read [`report/REPORT.md`](report/REPORT.md) for profile-level results, metric definitions, methodology and limitations. The publication-ready article draft is in [`blog/DEVELOPER_BLOG.md`](blog/DEVELOPER_BLOG.md).

## Layout

| Path | What it is |
|---|---|
| `report/` | Full report and Telnyx-branded charts |
| `blog/DEVELOPER_BLOG.md` | Developer blog draft referencing the verified report charts |
| `data/raw-measurements.csv` | All 240 original attempts, including failures, with generated text removed |
| `data/clean-successful-requests.csv` | The 221 answer-complete rows used for TTFA, E2E and output-speed summaries |
| `data/http-successful-requests.csv` | All 221 HTTP-successful planned slots |
| `data/unresolved-failures.csv` | The 19 unresolved Baseten rate-limit failures |
| `data/cell-summary.csv` | Provider/profile sample counts and percentile inputs |
| `config/` | Sanitized run request, manifest and pricing snapshot |
| `scripts/` | Runner configuration, actor reference source and report generator |

## Read before quoting

- TTFT is first model activity; TTFA is first visible answer content.
- p95 values are observed sample percentiles, not provider SLAs.
- Baseten's 100K-input cells are incomplete and must not be quoted as balanced ten-request comparisons.
- Requests ran sequentially with fixed provider order. The weekly automation will randomize comparable cells to reduce time-of-run bias.
- Estimated costs use successful rows' provider-reported token counts and published uncached list rates. They are not invoices.

## Disclosure

Run by Telnyx staff from a Telnyx Stateful Actor on November 19, 2026. Telnyx Inference was one of the compared endpoints, and the actor issued calls to every provider.
