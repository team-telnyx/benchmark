# GLM 5.2 provider benchmark, June 2026

Part of the [inference benchmarks](../README.md). This run compared streamed GLM 5.2 responses across Telnyx, Baseten, Together AI and Fireworks using six prompt profiles and ten measured requests per provider/profile cell.

| Provider | Successful requests | p50 first activity | p50 completed response |
|---|---:|---:|---:|
| Telnyx | 60/60 | 1.12 s | **6.00 s** |
| Baseten | 60/60 | **0.76 s** | 6.19 s |
| Together AI | 60/60 | 0.79 s | 7.49 s |
| Fireworks | 60/60 | 1.28 s | 14.98 s |

Telnyx recorded the lowest median completed-response latency. Baseten recorded the lowest median time to first streamed activity. Because reasoning was enabled, first activity may be reasoning rather than visible answer text.

Read [the full report](report/REPORT.md) for profile-level results. The public article is [GLM 5.2 inference benchmarks](https://telnyx.com/resources/glm-5-2-inference-benchmarks-provider).

## Layout

- report/: full report and charts.
- data/raw-measurements.csv: 246 original attempts, including six failed attempts and their successful retries, with provider error text reduced to categories.
- data/clean-successful-requests.csv: deduplicated successful rows used in the report.
- config/run-manifest.json: providers, model IDs and request settings.

## Read before quoting

- The run used repeated profile prompts, not fresh text for every request.
- Telnyx used zai-org/GLM-5.2-FP8 in the published article; the historical raw file records zai-org/GLM-5.2. This identifier asymmetry is preserved rather than silently rewritten.
- TTFT means first streamed model activity and may include reasoning. TTFA was not measured.
- Some Baseten responses ended before the requested output budget. Read the profile table before treating E2E as a serving-only comparison.

## Disclosure

Run by Telnyx staff on June 23, 2026. Telnyx Inference was one of the compared endpoints.
