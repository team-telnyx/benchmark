# Stateful actor latency comparison, July 2026

Part of the [inference benchmarks](../README.md). This run compared Telnyx model calls initiated from a regional Telnyx stateful actor with earlier Telnyx public-endpoint baselines using the same six prompt profiles.

| Model | Execution path | n | p50 TTFT | p50 completed response |
|---|---|---:|---:|---:|
| MiniMax M3 | Earlier public-endpoint baseline | 60 | 1.87 s | 7.56 s |
| MiniMax M3 | Regional stateful actor | 60 | **0.61 s** | **7.27 s** |
| GLM 5.2 | Earlier public-endpoint baseline | 60 | 1.12 s | 6.00 s |
| GLM 5.2 | Regional stateful actor | 60 | **0.76 s** | **4.54 s** |

The strongest observed change was first-token latency: 67.3% lower for MiniMax M3 and 32.5% lower for GLM 5.2. This is a comparison with earlier baselines, not a same-window controlled network A/B.

Read [the full report](report/REPORT.md) for the complete interpretation. The public article is [How stateful actors affected inference latency](https://telnyx.com/resources/reducing-latency-stateful-actors).

## Layout

- data/stateful-rerun-measurements.csv: 120 successful requests from the actor rerun.
- data/prior-public-endpoint-baselines.csv: the 120 earlier Telnyx rows used as baselines.
- data/comparison-summary.csv: recalculated p50 and p95 metrics for both paths.
- config/stateful-run-request.json: exact actor request, prompts and model configuration.
- scripts/actor/: hardened benchmark-actor source compatible with the recorded request schema.

## Read before quoting

- The actor was invoked through the working us-east-2 regional route.
- The earlier baseline and actor rerun were collected on different dates. The results do not isolate routing from time-of-day, load or serving changes.
- The actor region is known; the final GPU-serving location was not independently logged.
- TTFT is first streamed activity, not necessarily first visible answer text.

## Disclosure

Run by Telnyx staff. Both configurations called Telnyx Inference; the comparison concerns request execution paths, not different inference providers.
