# Telnyx benchmarks

Benchmarks run by Telnyx staff. Each section holds a family of benchmarks, and each run lives in its own dated folder with its report, data, configuration and scripts.

## Sections

| Section | What it covers | Runs |
|---|---|---|
| [`voice-ai/`](voice-ai/) | Voice-AI agent platforms measured over real phone calls | 1 |
| [`inference/`](inference/) | Inference benchmarks | none yet |

## Adding a run

Create a folder under the right section named `YYYY-MM-short-name` and follow the layout used by the existing run:

```
<section>/<YYYY-MM-short-name>/
  README.md     what was measured, headline results, how to read them, disclosure
  report/       the full write-up
  data/         results and per-call measurements
  config/       measurement settings and the configurations that were tested
  scripts/      the code used (copies are fine if it lives elsewhere)
```

Then add the run to its section's table.

Every run should state, in its README:

- what was measured and how, in one definition;
- the configurations tested, so results aren't read as statements about whole platforms;
- the scope (scenario, sample size, dates) and any known limits;
- that it was run by Telnyx staff, and whether Telnyx's own product was in the comparison.

Don't commit raw recordings, call logs or anything containing phone numbers, call identifiers, account IDs or keys.
