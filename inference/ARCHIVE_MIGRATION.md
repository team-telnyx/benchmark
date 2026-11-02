# Historical inference benchmark migration

The public repository promotes final, published benchmark runs rather than mirroring every internal artifact.

## Included

- June 2026 GLM 5.2 provider comparison.
- July 2026 stateful-actor latency comparison using the final 120-row rerun and its earlier baselines.
- August 2026 DeepSeek V4 Flash provider comparison with reasoning disabled.
- September 2026 GLM 5.3 Flash provider comparison.

## Not promoted as standalone runs

| Internal archive | Reason |
|---|---|
| Initial DeepSeek and Qwen combined run | Superseded by the reasoning-controlled DeepSeek rerun; no corresponding published Qwen benchmark report |
| Kimi K3 provider folders | The public Kimi K3 page is a model overview, not a published benchmark report in the benchmark archive |
| Smoke, canary and diagnostic folders | Validation artifacts, not reportable benchmark samples |
| Baseten repair and retry folders | Incorporated into canonical report datasets where applicable |
| current-model and Telnyx-only latency folders | Source baselines for later published reports, not separate current publications |
| exact-overlap, combined and early repaired reports | Unpublished, superseded or incomplete historical analyses |

This file records editorial selection only. Exclusion does not imply the underlying request never ran.
