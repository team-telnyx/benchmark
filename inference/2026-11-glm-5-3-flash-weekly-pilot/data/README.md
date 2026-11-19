# Data

`raw-measurements.csv` preserves all 240 original attempts, including HTTP failures. Generated answer and reasoning text were removed before publication; timings, token counts, statuses, error categories and request identities remain.

`http-successful-requests.csv` contains one row for every HTTP-successful planned slot. `clean-successful-requests.csv` is the answer-complete subset used for TTFA, E2E and output-speed summaries. `unresolved-failures.csv` contains the 19 Baseten HTTP 429 rows. `cell-summary.csv` and `provider-summary.csv` contain the aggregates used by the report.

The empty `retry-audit.csv` records that no corrected-run retry was used to replace a planned slot. Earlier calibration requests used different completion limits and are intentionally excluded from this public run.
