# Voice-agent first-response latency, October 2026

Part of the [voice AI benchmarks](../README.md). Run with [VAATT](https://github.com/team-telnyx/vaatt), Telnyx's scenario-based test harness for voice agents.

How long five platforms take to start talking after a caller finishes a sentence. About 50 real phone calls per platform, one scenario (an appointment reschedule), measured from the call recording.

| Platform | Calls measured | Median (ms) | 95% CI on median | p95 (ms) |
|---|---|---|---|---|
| Telnyx | 51 | 880 | 860–900 | 1020 |
| Retell | 49 | 1000 | 940–1080 | 1420 |
| ElevenLabs | 51 | 1340 | 1180–1500 | 2040 |
| LiveKit | 50 | 1780 | 1700–1900 | 2211 |
| Vapi | 49 | 2180 | 2120–2220 | 2704 |

First-response latency is the time from the caller's last audible sound to the agent's first audible sound. The order Telnyx, Retell, ElevenLabs, LiveKit, Vapi held under every definition tested. Exact values move by about 100–150 ms depending on the definition. Telnyx's lead over Retell is small (about 0.1 s).

Read [`report/REPORT.md`](report/REPORT.md) (or the [PDF](report/Telnyx-Voice-Agent-Latency-Benchmark.pdf)) for the method, configurations tested, scope and what the data does and doesn't support.

**Read before quoting:** each platform was measured in its own configuration, calls ran in provider blocks (not interleaved), and Telnyx is on the same network as the caller. The results describe these configurations on this scenario, not the platforms in general.

## Layout

| Path | What it is |
|---|---|
| `report/` | The report (Markdown and PDF) |
| `data/calls.csv` | One row per measured call (250): first-response latency under each of the six definitions. Call IDs are anonymized. |
| `data/providers-final.csv` | Median, p95, mean, min, max and 95% CI per platform for each of the six definitions (three onset thresholds × two caller-end methods), computed from `calls.csv` |
| `data/block-timings.json` | When each platform's calls ran |
| `data/README.md` | Column definitions |
| `config/measurement-config.json` | Thresholds, seeds and definitions used |
| `config/provider-configurations.json` | Each platform's configuration at test time (identifiers redacted) |
| `scripts/` | The analysis scripts, copied from VAATT for reference |

## Reproducing

- The scripts import from and expect the layout of the VAATT repository (`@vaatt/core`, `results/...` paths). Run them from a VAATT checkout with Bun; they are included here so the method is readable alongside the results.
- `scripts/unified-latency.ts` recomputes the per-call values and per-platform statistics from the recordings. `data/calls.csv` reproduces every figure in `data/providers-final.csv`, and re-running the script reproduces `providers-final.csv` exactly. The report text itself is written by hand; its numbers come from `providers-final.csv`.
- Raw call recordings and run records are not in this repository: they contain test phone numbers, call identifiers and expiring recording links. Ask the benchmark owner if you need them.
- Re-running requires live provider accounts and test phone numbers and places real, billed calls.

## Disclosure

Run by Telnyx staff on a Telnyx-built test harness, October 22–23, 2026. The simulated caller uses Telnyx services, and the Telnyx agent is on the same network as the caller.
