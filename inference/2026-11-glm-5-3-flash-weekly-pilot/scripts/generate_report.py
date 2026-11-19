#!/usr/bin/env python3
"""Build public-safe artifacts for the corrected weekly GLM 5.3 Flash pilot."""

from __future__ import annotations

import csv
import html
import json
import math
import re
import statistics
from collections import Counter, defaultdict
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = Path(os.environ.get("BENCHMARK_RUN_DIR", ROOT)).resolve()
PRIMARY_ID = "weekly-glm53-flash-corrected-full-20261119-001"
PROVIDERS = ("telnyx", "baseten", "together", "fireworks")
NAMES = {"telnyx": "Telnyx", "baseten": "Baseten", "together": "Together AI", "fireworks": "Fireworks"}
MODELS = {
    "telnyx": "zai-org/GLM-5.3-Flash",
    "baseten": "zai-org/GLM-5.3-Flash",
    "together": "zai-org/GLM-5.3-Flash",
    "fireworks": "accounts/fireworks/models/glm-5p3-flash",
}
PROFILES = (
    "01_1k_in_100_out",
    "02_1k_in_1k_out",
    "03_10k_in_100_out",
    "04_10k_in_1k_out",
    "05_100k_in_100_out",
    "06_100k_in_1k_out",
)
PROFILE_LABELS = {
    "01_1k_in_100_out": "1K input / short answer",
    "02_1k_in_1k_out": "1K input / long answer",
    "03_10k_in_100_out": "10K input / short answer",
    "04_10k_in_1k_out": "10K input / long answer",
    "05_100k_in_100_out": "100K input / short answer",
    "06_100k_in_1k_out": "100K input / long answer",
}
RATES = {
    "telnyx": (0.135, 0.450, "https://telnyx.com/pricing/inference-api"),
    "baseten": (0.150, 0.500, "https://www.baseten.co/pricing/"),
    "together": (0.150, 0.500, "https://www.together.ai/models/glm-5-3-flash"),
    "fireworks": (0.150, 0.500, "https://fireworks.ai/models/fireworks/glm-5p3-flash"),
}

BLACK = "#050505"
CREAM = "#FEFDF5"
MUTED = "#AAA9A3"
GRID = "#343430"
TRACK = "#20201E"
COLORS = {"telnyx": "#00E3AA", "baseten": "#F77442", "together": "#E6E3D3", "fireworks": "#8850F9"}
LOGO_CANDIDATES = (ROOT / "report" / "assets" / "telnyx-logo.png",)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = fields or (list(rows[0].keys()) if rows else [])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def number(row: dict[str, object], key: str) -> float | None:
    try:
        value = float(row.get(key, ""))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def percentile(values: list[float], q: float) -> float | None:
    values = sorted(v for v in values if math.isfinite(v))
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def metric(rows: list[dict[str, object]], key: str) -> dict[str, float | int | None]:
    values = [value for row in rows if (value := number(row, key)) is not None]
    return {
        "n": len(values),
        "p50": statistics.median(values) if values else None,
        "p95": percentile(values, 0.95),
    }


def is_ok(row: dict[str, object]) -> bool:
    return str(row.get("ok", "")).lower() == "true"


def is_answered(row: dict[str, object]) -> bool:
    return is_ok(row) and number(row, "ttfa_ms") is not None and bool(str(row.get("full_content_text", "")).strip())


def sanitize(row: dict[str, object]) -> dict[str, object]:
    excluded = {"first_delta_text", "first_reasoning_text", "first_content_text", "full_reasoning_text", "full_content_text"}
    return {key: value for key, value in row.items() if key not in excluded}


def reconcile(primary: list[dict[str, str]], retries: list[tuple[str, list[dict[str, str]]]]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    primary_rows: list[dict[str, object]] = []
    clean_by_slot: dict[tuple[str, str, int], dict[str, object]] = {}
    failed_slots: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
    for row in primary:
        slot = (row["provider"], row["profile"], int(row["iteration"]))
        enriched: dict[str, object] = dict(row, source_run_id=PRIMARY_ID, row_origin="primary", planned_iteration=int(row["iteration"]))
        primary_rows.append(enriched)
        if is_ok(row):
            clean_by_slot[slot] = enriched
        else:
            failed_slots[(row["provider"], row["profile"])].append(slot)

    retry_rows: list[dict[str, object]] = []
    successes: dict[tuple[str, str], list[tuple[str, dict[str, str]]]] = defaultdict(list)
    for run_id, rows in retries:
        for row in rows:
            enriched: dict[str, object] = dict(row, source_run_id=run_id, row_origin="retry", planned_iteration="")
            retry_rows.append(enriched)
            if is_ok(row):
                successes[(row["provider"], row["profile"])].append((run_id, row))

    audit: list[dict[str, object]] = []
    for cell, slots in failed_slots.items():
        candidates = successes.get(cell, [])
        for index, slot in enumerate(slots):
            if index >= len(candidates):
                break
            run_id, row = candidates[index]
            replacement: dict[str, object] = dict(
                row,
                source_run_id=run_id,
                row_origin="retry_replacement",
                retry_iteration=int(row["iteration"]),
                iteration=slot[2],
                planned_iteration=slot[2],
            )
            clean_by_slot[slot] = replacement
            audit.append(replacement)
        for run_id, row in candidates[len(slots):]:
            audit.append(dict(row, source_run_id=run_id, row_origin="extra_retry_success", planned_iteration=""))
    audit.extend(row for row in retry_rows if not is_ok(row))

    clean = [clean_by_slot[key] for key in sorted(clean_by_slot)]
    unresolved = [
        row for row in primary_rows
        if not is_ok(row) and (str(row["provider"]), str(row["profile"]), int(row["iteration"])) not in clean_by_slot
    ]
    return primary_rows + retry_rows, clean, unresolved, audit


def summarize(primary: list[dict[str, str]], clean: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    cells: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in clean:
        cells[(str(row["provider"]), str(row["profile"]))].append(row)
    original: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in primary:
        original[(row["provider"], row["profile"])].append(row)

    cell_summary: list[dict[str, object]] = []
    for profile in PROFILES:
        for provider in PROVIDERS:
            rows = cells[(provider, profile)]
            answered = [row for row in rows if is_answered(row)]
            cell_summary.append({
                "profile": profile,
                "profile_label": PROFILE_LABELS[profile],
                "provider": provider,
                "provider_label": NAMES[provider],
                "planned_n": 10,
                "original_http_success_n": sum(is_ok(row) for row in original[(provider, profile)]),
                "reconciled_http_success_n": len(rows),
                "answered_n": len(answered),
                "p50_ttft_ms": metric(rows, "ttft_ms")["p50"],
                "observed_p95_ttft_ms": metric(rows, "ttft_ms")["p95"],
                "p50_ttfa_ms": metric(answered, "ttfa_ms")["p50"],
                "observed_p95_ttfa_ms": metric(answered, "ttfa_ms")["p95"],
                "p50_e2e_ms": metric(answered, "e2e_ms")["p50"],
                "observed_p95_e2e_ms": metric(answered, "e2e_ms")["p95"],
                "p50_output_tok_s": metric(answered, "effective_tok_s")["p50"],
                "p50_inter_token_ms": metric(answered, "median_itl_ms")["p50"],
                "prompt_tokens": int(sum(number(row, "prompt_tokens") or 0 for row in rows)),
                "output_tokens": int(sum(number(row, "output_tokens") or 0 for row in rows)),
            })

    provider_summary: list[dict[str, object]] = []
    for provider in PROVIDERS:
        rows = [row for row in clean if row["provider"] == provider]
        answered = [row for row in rows if is_answered(row)]
        original_rows = [row for row in primary if row["provider"] == provider]
        input_tokens = int(sum(number(row, "prompt_tokens") or 0 for row in rows))
        output_tokens = int(sum(number(row, "output_tokens") or 0 for row in rows))
        input_rate, output_rate, _ = RATES[provider]
        provider_summary.append({
            "provider": provider,
            "provider_label": NAMES[provider],
            "planned_n": 60,
            "original_http_success_n": sum(is_ok(row) for row in original_rows),
            "reconciled_http_success_n": len(rows),
            "answered_n": len(answered),
            "p50_ttft_ms": metric(rows, "ttft_ms")["p50"],
            "observed_p95_ttft_ms": metric(rows, "ttft_ms")["p95"],
            "p50_ttfa_ms": metric(answered, "ttfa_ms")["p50"],
            "observed_p95_ttfa_ms": metric(answered, "ttfa_ms")["p95"],
            "p50_e2e_ms": metric(answered, "e2e_ms")["p50"],
            "observed_p95_e2e_ms": metric(answered, "e2e_ms")["p95"],
            "p50_output_tok_s": metric(answered, "effective_tok_s")["p50"],
            "p50_inter_token_ms": metric(answered, "median_itl_ms")["p50"],
            "prompt_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_uncached_cost_usd": input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate,
        })
    return cell_summary, provider_summary


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def header(image: Image.Image, title: str, subtitle: str) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(image)
    logo = next((path for path in LOGO_CANDIDATES if path.exists()), None)
    if logo:
        mark = Image.open(logo).convert("RGBA")
        mark.thumbnail((250, 70), Image.Resampling.LANCZOS)
        image.paste(mark, (76, 42), mark)
    else:
        draw.text((76, 48), "telnyx", font=font(42, True), fill=CREAM)
    draw.text((76, 150), title, font=font(54, True), fill=CREAM)
    draw.text((76, 222), subtitle, font=font(24), fill=MUTED)
    return draw


def metric_chart(path: Path, rows: list[dict[str, object]], key: str, title: str, subtitle: str, suffix: str, lower: bool) -> None:
    image = Image.new("RGB", (2300, 2140), BLACK)
    draw = header(image, title, subtitle)
    keyed = {(str(row["profile"]), str(row["provider"])): row for row in rows}
    values = [float(row[key]) for row in rows if row[key] is not None]
    maximum = max(values) * 1.08 if values else 1
    x0, width, y = 820, 1160, 330
    for p_index, profile in enumerate(PROFILES):
        draw.text((76, y), PROFILE_LABELS[profile], font=font(30, True), fill=CREAM)
        eligible = [float(keyed[(profile, provider)][key]) for provider in PROVIDERS if keyed[(profile, provider)][key] is not None]
        best = (min if lower else max)(eligible) if eligible else None
        y += 54
        for provider in PROVIDERS:
            row = keyed[(profile, provider)]
            value = row[key]
            n = int(row["reconciled_http_success_n"] if key == "p50_ttft_ms" else row["answered_n"])
            draw.text((110, y + 5), NAMES[provider], font=font(22, provider == "telnyx"), fill=COLORS[provider])
            draw.rounded_rectangle((x0, y, x0 + width, y + 36), radius=5, fill=TRACK)
            if value is not None:
                numeric = float(value)
                draw.rounded_rectangle((x0, y, x0 + max(3, int(width * numeric / maximum)), y + 36), radius=5, fill=COLORS[provider])
                shown = numeric / 1000 if suffix == "s" else numeric
                label = f"{shown:.2f}{suffix}  n={n}"
                if best is not None and abs(numeric - best) < 1e-9:
                    label += "  best"
                draw.text((x0 + width + 24, y + 1), label, font=font(21, True), fill=CREAM)
            else:
                draw.text((x0 + width + 24, y + 1), f"Insufficient data  n={n}", font=font(21, True), fill=MUTED)
            y += 50
        if p_index < len(PROFILES) - 1:
            draw.line((76, y + 10, 2220, y + 10), fill=GRID, width=2)
        y += 40
    draw.text((76, 2075), "Observed sample values. Failed requests remain in reliability counts and are excluded from latency percentiles.", font=font(19), fill=MUTED)
    image.save(path, optimize=True)


def reliability_chart(path: Path, rows: list[dict[str, object]]) -> None:
    image = Image.new("RGB", (2100, 1050), BLACK)
    draw = header(image, "GLM 5.3 Flash request reliability", "Original HTTP success and visible-answer completion | 60 planned requests per provider")
    x0, width, y = 720, 1050, 345
    for row in rows:
        provider = str(row["provider"])
        draw.text((86, y + 20), NAMES[provider], font=font(28, provider == "telnyx"), fill=COLORS[provider])
        for label, value, fill in (
            ("Original HTTP success", int(row["original_http_success_n"]), MUTED),
            ("Visible answer completed", int(row["answered_n"]), COLORS[provider]),
        ):
            draw.text((340, y + 5), label, font=font(19), fill=CREAM)
            draw.rounded_rectangle((x0, y, x0 + width, y + 34), radius=5, fill=TRACK)
            draw.rounded_rectangle((x0, y, x0 + int(width * value / 60), y + 34), radius=5, fill=fill)
            draw.text((x0 + width + 22, y), f"{value}/60 ({100 * value / 60:.1f}%)", font=font(21, True), fill=CREAM)
            y += 48
        y += 28
    draw.text((86, 985), "HTTP success and visible-answer completion are reported separately.", font=font(20), fill=MUTED)
    image.save(path, optimize=True)


def pricing_chart(path: Path) -> None:
    image = Image.new("RGB", (2100, 1100), BLACK)
    draw = header(image, "Published GLM 5.3 Flash pricing", "USD per 1 million tokens | pay-as-you-go list rates retrieved November 19, 2026")
    x0, width, y = 690, 1110, 340
    maximum = max(rate for input_rate, output_rate, _ in RATES.values() for rate in (input_rate, output_rate))
    for provider in PROVIDERS:
        draw.text((86, y + 38), NAMES[provider], font=font(28, provider == "telnyx"), fill=COLORS[provider])
        for label, value in (("Input", RATES[provider][0]), ("Output", RATES[provider][1])):
            draw.text((360, y + 5), label, font=font(20), fill=CREAM)
            draw.rounded_rectangle((x0, y, x0 + width, y + 34), radius=5, fill=TRACK)
            draw.rounded_rectangle((x0, y, x0 + max(3, int(width * value / maximum)), y + 34), radius=5, fill=COLORS[provider])
            draw.text((x0 + width + 22, y), f"${value:.3f}", font=font(21, True), fill=CREAM)
            y += 48
        y += 28
    draw.text((86, 1030), "Cached-input rates, discounts, credits, and failed-attempt charges are not shown.", font=font(20), fill=MUTED)
    image.save(path, optimize=True)


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def seconds(value: object) -> str:
    return "N/A" if value is None else f"{float(value) / 1000:.2f}s"


def report(cell_summary: list[dict[str, object]], provider_summary: list[dict[str, object]], primary: list[dict[str, str]], unresolved: list[dict[str, object]], retry_rows: list[dict[str, object]]) -> str:
    overall_rows = []
    for row in provider_summary:
        complete = int(row["answered_n"]) == 60
        overall_rows.append([
            str(row["provider_label"]),
            f"{int(row['original_http_success_n'])}/60",
            f"{int(row['answered_n'])}/60",
            seconds(row["p50_ttft_ms"]) if complete else "N/A*",
            seconds(row["p50_ttfa_ms"]) if complete else "N/A*",
            seconds(row["p50_e2e_ms"]) if complete else "N/A*",
            seconds(row["observed_p95_e2e_ms"]) if complete else "N/A*",
            (f"{float(row['p50_output_tok_s']):.1f} tok/s" if row["p50_output_tok_s"] is not None else "N/A") if complete else "N/A*",
            f"${float(row['estimated_uncached_cost_usd']):.3f}" if complete else "N/A*",
        ])
    profile_rows = [
        [
            str(row["profile_label"]),
            str(row["provider_label"]),
            f"{int(row['reconciled_http_success_n'])}/10",
            f"{int(row['answered_n'])}/10",
            seconds(row["p50_ttfa_ms"]),
            seconds(row["p50_e2e_ms"]),
            seconds(row["observed_p95_e2e_ms"]),
            "N/A" if row["p50_output_tok_s"] is None else f"{float(row['p50_output_tok_s']):.1f}",
        ]
        for row in cell_summary
    ]
    failure_counts = Counter((str(row["provider"]), str(row["profile"]), str(row.get("status", ""))) for row in primary if not is_ok(row))
    failure_lines = [
        f"- {NAMES[provider]} {PROFILE_LABELS[profile]}: {count} original HTTP {status or 'unknown'} failure(s)."
        for (provider, profile, status), count in sorted(failure_counts.items())
    ] or ["- No original request failures."]
    unresolved_text = ", ".join(f"{NAMES[str(row['provider'])]} {PROFILE_LABELS[str(row['profile'])]} iteration {row['iteration']}" for row in unresolved) or "None"
    retry_failures = sum(not is_ok(row) for row in retry_rows)
    total_answered = sum(int(row["answered_n"]) for row in provider_summary)
    total_success = sum(int(row["reconciled_http_success_n"]) for row in provider_summary)

    return f"""# GLM 5.3 Flash weekly provider benchmark

## Executive summary

This pilot compared GLM 5.3 Flash on Telnyx, Baseten, Together AI, and Fireworks from the same Telnyx Stateful Actor request origin. Every provider received the same six Moby-Dick prompt profiles, explicit `reasoning_effort=low`, deterministic sampling, streaming, and the same completion limits.

The primary run recorded **{sum(is_ok(row) for row in primary)}/240 HTTP-successful requests**. After any targeted retries, **{total_success}/240 planned slots** had an HTTP-successful response and **{total_answered}/240** reached visible answer content. Transport reliability, first model activity, first visible answer, completed-response latency, output speed, and estimated list-price cost are reported separately.

## Overall results

{markdown_table(
    ["Provider", "Original HTTP", "Visible answers", "p50 TTFT", "p50 TTFA", "p50 E2E", "Observed p95 E2E", "p50 output speed", "Est. token cost"],
    overall_rows,
)}

**Definitions:** TTFT is time to first model activity. TTFA is time to first visible answer content. E2E ends when the streamed response completes. TTFA, E2E, and output-speed percentiles use answer-complete requests only. The p95 values are observed sample percentiles, not provider SLAs.

`*` Baseten's pooled metrics and total cost are withheld because its 100K cells were incomplete. Its valid per-workload measurements remain in the detailed table.

![Median completed-response latency](assets/glm-5-3-flash-p50-e2e.png)

![Observed sample p95 completed-response latency](assets/glm-5-3-flash-observed-p95-e2e.png)

![Median time to first visible answer](assets/glm-5-3-flash-p50-ttfa.png)

![Median output speed](assets/glm-5-3-flash-output-speed.png)

![Request reliability](assets/glm-5-3-flash-reliability.png)

![Published pricing](assets/glm-5-3-flash-pricing.png)

## Results by workload

{markdown_table(
    ["Workload", "Provider", "HTTP n", "Answered n", "p50 TTFA", "p50 E2E", "Observed p95 E2E", "p50 tok/s"],
    profile_rows,
)}

## Reliability and retry accounting

- Planned primary attempts: **240**
- Primary HTTP successes: **{sum(is_ok(row) for row in primary)}**
- Primary failures retained: **{sum(not is_ok(row) for row in primary)}**
- Retry attempts retained: **{len(retry_rows)}**
- Failed retry attempts: **{retry_failures}**
- Reconciled HTTP-successful planned slots: **{total_success}**
- Answer-complete planned slots: **{total_answered}**
- Unresolved planned failures: **{len(unresolved)}**
- Unresolved slots: **{unresolved_text}**

{chr(10).join(failure_lines)}

Retries fill only failed planned slots in the reconciled dataset. They do not alter the original reliability rate, and every original failure remains in `raw-measurements.csv`.

## Cost scope

Published pay-as-you-go list rates were retrieved on November 19, 2026. The cost column multiplies successful rows' provider-reported input and output tokens by those rates. It assumes uncached input because this run did not expose verified cached-token counts. It excludes credits, discounts, failed-attempt charges without usage, and non-token fees, so it is an estimate rather than an invoice.

## Methodology

- **Model parity:** Telnyx, Baseten, and Together used `zai-org/GLM-5.3-Flash`; Fireworks used its provider-specific endpoint for the same published checkpoint, `accounts/fireworks/models/glm-5p3-flash`.
- **Request origin:** all provider requests originated from one Telnyx Stateful Actor invoked through its `us-east-2` regional path. This identifies the runner location, not any provider's inference-serving data center.
- **Prompts:** six public-domain Moby-Dick profiles targeting approximately 1K, 10K, and 100K input with short and long answers; ten planned repetitions per provider/profile cell.
- **Settings:** `reasoning_effort=low`, temperature 0, streaming enabled, 1,024 completion tokens for short-answer profiles, and 4,096 for long-answer profiles.
- **Execution:** requests ran sequentially (`batchSize=1`). Provider order was fixed in this pilot rather than randomized, so changing provider load over time is a possible confounder. The scheduled weekly runner will randomize comparable cells.
- **Percentiles:** p50 is the median. Observed p95 uses linear interpolation over eligible requests in each cell. Failed/no-answer requests remain in reliability counts and are excluded from latency percentiles.
- **Throughput:** output tokens per second uses provider-reported token counts where available. Check `token_usage_source` before detailed cross-provider interpretation.

## Limitations

- This is a point-in-time observational benchmark, not an SLA.
- Cells with fewer than ten answered requests have weaker tail estimates and show their sample sizes in the table and charts.
- Provider load, routing, implementation, and pricing can change independently.
- The Moby-Dick prompts measure serving behavior across controlled context sizes; they are not a general model-quality benchmark.

## Reproduction artifacts

- `config/run-request.json`: sanitized request configuration.
- `config/pricing-snapshot.json`: rates and source URLs used for cost estimates.
- `data/raw-measurements.csv`: all original and retry attempts with generated text removed.
- `data/clean-successful-requests.csv`: answer-complete planned slots used for TTFA, E2E, and output-speed summaries.
- `data/http-successful-requests.csv`: reconciled HTTP-successful planned slots, including any no-answer streams.
- `data/unresolved-failures.csv`: planned slots not recovered by retries.
- `data/cell-summary.csv` and `data/provider-summary.csv`: report inputs.
- `scripts/start_run.py`: run configuration builder.

## Sources

- [Telnyx inference pricing](https://telnyx.com/pricing/inference-api)
- [Telnyx inference models](https://developers.telnyx.com/docs/inference/models)
- [Telnyx Stateful Actors](https://developers.telnyx.com/docs/edge-compute/stateful-actors)
- [Baseten pricing](https://www.baseten.co/pricing/)
- [Together AI GLM 5.3 Flash](https://www.together.ai/models/glm-5-3-flash)
- [Fireworks GLM 5.3 Flash](https://fireworks.ai/models/fireworks/glm-5p3-flash)
"""


def render_html(markdown: str) -> str:
    body: list[str] = []
    table: list[list[str]] = []
    in_list = False

    def inline(value: str) -> str:
        value = html.escape(value)
        value = re.sub(r"!\[([^]]+)\]\(([^)]+)\)", r"<img src='\2' alt='\1'>", value)
        value = re.sub(r"\[([^]]+)\]\((https?://[^)]+)\)", r"<a href='\2'>\1</a>", value)
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
        return value

    def flush_table() -> None:
        nonlocal table
        if not table:
            return
        header_row, _, *rows = table
        body.append("<div class='table-wrap'><table><thead><tr>" + "".join(f"<th>{inline(cell)}</th>" for cell in header_row) + "</tr></thead><tbody>")
        for row in rows:
            body.append("<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>")
        body.append("</tbody></table></div>")
        table = []

    for line in markdown.splitlines():
        if line.startswith("|"):
            table.append([cell.strip() for cell in line.strip("|").split("|")])
            continue
        flush_table()
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("# "):
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("!["):
            body.append(inline(line))
        else:
            body.append(f"<p>{inline(line)}</p>")
    flush_table()
    if in_list:
        body.append("</ul>")
    css = "body{margin:0;background:#050505;color:#fefdf5;font:17px/1.6 Arial,sans-serif}main{max-width:1180px;margin:auto;padding:64px 40px 100px}h1{font-size:48px;line-height:1.08}h2{font-size:30px;margin-top:58px;border-top:1px solid #343430;padding-top:28px}a{color:#00e3aa}code{background:#20201e;padding:2px 5px}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:12px;border-bottom:1px solid #343430;white-space:nowrap}th{color:#00e3aa}img{width:100%;height:auto;margin:28px 0;border:1px solid #343430}"
    return "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>GLM 5.3 Flash weekly provider benchmark</title><style>" + css + "</style></head><body><main>" + "".join(body) + "</main></body></html>"


def main() -> int:
    primary_path = RUN_DIR / f"{PRIMARY_ID}.csv"
    if not primary_path.exists():
        raise SystemExit(f"Missing completed primary run: {primary_path}")
    primary = read_csv(primary_path)
    retry_paths = sorted((RUN_DIR / "retries").glob("weekly-glm53-flash-corrected-*.csv")) if (RUN_DIR / "retries").exists() else []
    retries = [(path.stem, read_csv(path)) for path in retry_paths]
    all_rows, clean_http, unresolved, retry_audit = reconcile(primary, retries)
    answered = [row for row in clean_http if is_answered(row)]
    cells, providers = summarize(primary, clean_http)

    data = RUN_DIR / "data"
    report_dir = RUN_DIR / "report"
    assets = report_dir / "assets"
    config = RUN_DIR / "config"
    for directory in (data, assets, config):
        directory.mkdir(parents=True, exist_ok=True)

    sanitized_all = [sanitize(row) for row in all_rows]
    sanitized_http = [sanitize(row) for row in clean_http]
    sanitized_answered = [sanitize(row) for row in answered]
    write_csv(data / "raw-measurements.csv", sanitized_all)
    write_csv(data / "http-successful-requests.csv", sanitized_http)
    write_csv(data / "clean-successful-requests.csv", sanitized_answered)
    write_csv(data / "unresolved-failures.csv", [sanitize(row) for row in unresolved], list(sanitized_all[0].keys()))
    write_csv(data / "retry-audit.csv", [sanitize(row) for row in retry_audit], list(sanitized_all[0].keys()))
    write_csv(data / "cell-summary.csv", cells)
    write_csv(data / "provider-summary.csv", providers)

    pricing = {
        "retrieved_on": "2026-11-19",
        "currency": "USD",
        "unit": "per 1 million tokens",
        "rates": {
            provider: {"input": rate[0], "output": rate[1], "source": rate[2]}
            for provider, rate in RATES.items()
        },
        "notes": ["Pay-as-you-go list rates", "Cached-input prices excluded from estimates because cache usage was not verified"],
    }
    (config / "pricing-snapshot.json").write_text(json.dumps(pricing, indent=2) + "\n", encoding="utf-8")
    run_request = json.loads((RUN_DIR / "run-payload.json").read_text(encoding="utf-8"))
    for suite in run_request.get("suites", []):
        for provider in suite.get("providers", []):
            provider.pop("apiKeyEnv", None)
            provider.pop("baseUrl", None)
    (config / "run-request.json").write_text(json.dumps(run_request, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "benchmark": "Weekly GLM 5.3 Flash inter-provider pilot",
        "date": "2026-11-19",
        "primary_run_id": PRIMARY_ID,
        "retry_run_ids": [run_id for run_id, _ in retries],
        "planned_attempts": 240,
        "primary_http_successes": sum(is_ok(row) for row in primary),
        "reconciled_http_successes": len(clean_http),
        "answer_complete": len(answered),
        "unresolved_failures": len(unresolved),
        "reasoning_effort": "low",
        "short_completion_limit": 1024,
        "long_completion_limit": 4096,
        "runner": "Telnyx Stateful Actor",
        "actor_region": "us-east-2",
        "inference_serving_datacenter": "not established",
        "models": MODELS,
        "profiles": list(PROFILES),
    }
    (config / "run-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    metric_chart(assets / "glm-5-3-flash-p50-e2e.png", cells, "p50_e2e_ms", "GLM 5.3 Flash completed-response latency", "Median E2E by workload | seconds, lower is better", "s", True)
    metric_chart(assets / "glm-5-3-flash-observed-p95-e2e.png", cells, "observed_p95_e2e_ms", "GLM 5.3 Flash tail response latency", "Observed sample p95 E2E by workload | seconds, lower is better", "s", True)
    metric_chart(assets / "glm-5-3-flash-p50-ttfa.png", cells, "p50_ttfa_ms", "GLM 5.3 Flash time to first visible answer", "Median TTFA by workload | seconds, lower is better", "s", True)
    metric_chart(assets / "glm-5-3-flash-output-speed.png", cells, "p50_output_tok_s", "GLM 5.3 Flash generation speed", "Median effective output tokens per second | higher is better", " tok/s", False)
    reliability_chart(assets / "glm-5-3-flash-reliability.png", providers)
    pricing_chart(assets / "glm-5-3-flash-pricing.png")

    markdown = report(cells, providers, primary, unresolved, [row for _, rows in retries for row in rows])
    (report_dir / "REPORT.md").write_text(markdown, encoding="utf-8")
    (report_dir / "REPORT.html").write_text(render_html(markdown), encoding="utf-8")
    print(json.dumps({"primary": len(primary), "all_attempts": len(all_rows), "http_successful_slots": len(clean_http), "answer_complete": len(answered), "unresolved": len(unresolved), "output": str(RUN_DIR)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
