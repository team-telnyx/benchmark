# Inference benchmarks

Inference model and provider benchmarks run by Telnyx staff. Each run is self-contained and follows the layout in the [root README](../README.md).

## Runs

| Run | Measured | Headline | Platforms |
|---|---|---|---|
| [2026-06-glm-5-2-providers/](2026-06-glm-5-2-providers/) | GLM 5.2 latency across six prompt profiles | Telnyx had the lowest p50 completed-response latency; Baseten had the lowest p50 first activity | Telnyx, Baseten, Together AI, Fireworks |
| [2026-07-stateful-actor-latency/](2026-07-stateful-actor-latency/) | Earlier public-endpoint baselines against a regional stateful-actor rerun | p50 TTFT fell 67.3% for MiniMax M3 and 32.5% for GLM 5.2 | Telnyx |
| [2026-08-deepseek-v4-flash-providers/](2026-08-deepseek-v4-flash-providers/) | DeepSeek V4 Flash latency, throughput and reliability | Telnyx had the highest p50 output speed and lowest p95 E2E among 60/60 completers | Telnyx, Baseten, Together AI, Fireworks |
| [2026-09-glm-5-3-flash-providers/](2026-09-glm-5-3-flash-providers/) | GLM 5.3 Flash latency, throughput and completion | Telnyx had the highest p50 output speed and completed 60/60 requests | Telnyx, Baseten, Together AI, Fireworks |
| [`2026-09-glm-5-3-fresh-prompts/`](2026-09-glm-5-3-fresh-prompts/) | GLM 5.3 latency across four providers using distinct prompts | Telnyx had the lowest median completed-response latency in 5 of 6 profiles | Telnyx, Baseten, Together AI, Fireworks |
| [`2026-10-multi-turn-models/`](2026-10-multi-turn-models/) | Three open-weight models on fixed multi-turn workflows | All models completed 15/15 conversations; speed and judged completion differed | Telnyx Inference |
| [`2026-10-multi-turn-providers/`](2026-10-multi-turn-providers/) | Three model families across four providers on multi-turn workflows | Telnyx recorded the lowest median and observed p95 conversation response time for all three model families | Telnyx, Baseten, Together AI, Fireworks |
| [`2026-10-glm-5-3-flash-modal/`](2026-10-glm-5-3-flash-modal/) | GLM 5.3 Flash managed endpoint comparison | Telnyx completed responses faster; Modal Shared had stronger p95 first-visible-answer latency in most cells | Telnyx, Modal Shared |

See [ARCHIVE_MIGRATION.md](ARCHIVE_MIGRATION.md) for the historical inclusion and exclusion record.
