# Scripts

- `start_run.py` builds and starts the benchmark request. The checked-in GLM 5.3 Flash suite requests `reasoning_effort=low` from each provider. Supply `--prompt-dir` with the six Moby-Dick profile files; `--random-seed` enables reproducible provider/profile cell randomization.
- `generate_report.py` sanitizes the export, calculates the published summaries and cost estimates, and renders the report and charts. Set `BENCHMARK_RUN_DIR` to the exported run folder; it defaults to this benchmark folder.
- `actor/` is the hardened Stateful Actor reference implementation compatible with the recorded request schema.

The deployed actor stored provider credentials as secrets. No credentials, private keys or authorization headers are included here.
