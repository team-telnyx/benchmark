# Weekly inference benchmark, 2026-11-23

Part of the [inference benchmarks](../README.md). This run measured streaming inference through the same Telnyx Stateful Actor request origin, using exact model configurations that first passed a provider smoke check.

- Featured model: **DeepSeek V4.1 Flash 1017**
- Task: Moby-Dick serving and long-context performance
- Platforms represented: Baseten, Fireworks, Telnyx, Together
- Scope: six Moby-Dick profiles spanning approximately 1K, 10K, and 100K inputs with short and long outputs; ten measured attempts per provider/profile cell.

Read [`report/REPORT.md`](report/REPORT.md) for sample counts, metric definitions, results, charts, and limitations.

## Layout

| Path | Contents |
|---|---|
| `report/` | Full report and generated charts |
| `data/` | Sanitized request-level metrics and profile summary |
| `config/` | Public run manifest and disclosure |
| `scripts/` | Orchestration, model configuration, and report generation code |

## Read before quoting

- TTFT is first model activity and may be reasoning; TTFA is first visible answer content.
- p95 values are observed sample percentiles, not provider SLAs.
- Failures are retained and are not silently replaced with retries.
- No weighted overall winner is calculated.

## Disclosure

Run by Telnyx staff. Telnyx Inference was one of the compared endpoints. The Telnyx Stateful Actor was the common request origin for all providers.
