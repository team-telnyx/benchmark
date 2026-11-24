#!/usr/bin/env python3
"""Create deterministic public-safe tables, report, and SVG charts."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import shutil
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


METRICS = ("ttft_ms", "ttfa_ms", "e2e_ms", "effective_tok_s")


def number(row: dict[str, str], key: str) -> float | None:
    try:
        value = float(row.get(key, ""))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def percentile(values: list[float], q: float) -> float | None:
    values = sorted(values)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def ok(row: dict[str, str]) -> bool:
    return row.get("ok", "").lower() == "true"


def summarize(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["provider"], row["profile"])].append(row)
    output: list[dict[str, Any]] = []
    for (provider, profile), cell in sorted(groups.items()):
        successful = [row for row in cell if ok(row)]
        answered = [row for row in successful if number(row, "ttfa_ms") is not None]
        record: dict[str, Any] = {
            "provider": provider,
            "profile": profile,
            "planned_n": len(cell),
            "http_success_n": len(successful),
            "answer_complete_n": len(answered),
            "reliability_pct": round(100 * len(successful) / len(cell), 2),
            "visible_answer_pct": round(100 * len(answered) / len(cell), 2),
        }
        for metric in METRICS:
            sample = answered if metric in {"ttfa_ms", "e2e_ms", "effective_tok_s"} else successful
            values = [value for row in sample if (value := number(row, metric)) is not None]
            record[f"p50_{metric}"] = statistics.median(values) if values else None
            record[f"observed_p95_{metric}"] = percentile(values, 0.95)
            record[f"n_{metric}"] = len(values)
        output.append(record)
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: Any, divisor: float = 1, suffix: str = "") -> str:
    if value is None or value == "":
        return "n/a"
    return f"{float(value) / divisor:.2f}{suffix}"


def svg_chart(path: Path, title: str, subtitle: str, rows: list[dict[str, Any]], key: str, divisor: float, suffix: str, lower: bool) -> None:
    if key == "reliability_pct":
        totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in rows:
            provider = str(row["provider"])
            totals[provider][0] += int(row["http_success_n"])
            totals[provider][1] += int(row["planned_n"])
        values = {
            provider: 100 * successful / planned
            for provider, (successful, planned) in totals.items()
            if planned
        }
    else:
        aggregate: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            value = row.get(key)
            if value is not None:
                aggregate[str(row["provider"])].append(float(value) / divisor)
        values = {provider: statistics.median(items) for provider, items in aggregate.items() if items}
    width, height = 1400, 260 + 105 * len(values)
    max_value = max(max(values.values(), default=0), 1)
    ordered = sorted(values.items(), key=lambda item: item[1], reverse=not lower)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="#050505"/>', '<style>text{font-family:Arial,sans-serif;fill:#fefdf5}.muted{fill:#aaa9a3}.label{font-size:25px}.value{font-size:24px;font-weight:700}</style>', f'<text x="70" y="75" font-size="44" font-weight="700">{html.escape(title)}</text>', f'<text x="70" y="118" class="muted" font-size="22">{html.escape(subtitle)}</text>']
    for index, (provider, value) in enumerate(ordered):
        y = 190 + index * 105
        color = "#00e3aa" if provider == "telnyx" else "#777773"
        bar_width = max(8, 900 * value / max_value)
        parts.extend([f'<text x="70" y="{y + 28}" class="label">{html.escape(provider.title())}</text>', f'<rect x="300" y="{y}" width="{bar_width:.1f}" height="42" rx="4" fill="{color}"/>', f'<text x="{320 + bar_width:.1f}" y="{y + 30}" class="value">{value:.2f}{suffix}</text>'])
    parts.append('</svg>')
    path.write_text("\n".join(parts))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    public = args.run_dir / "public"
    data_dir = public / "data"
    config_dir = public / "config"
    report_dir = public / "report"
    scripts_dir = public / "scripts"
    charts = report_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.run_dir / "run-manifest.json").read_text())
    all_summaries: list[dict[str, Any]] = []
    failures: Counter[tuple[str, str, str, str]] = Counter()
    datasets: list[str] = []
    reasoning_settings: set[str] = set()
    for run in manifest["runs"]:
        if run.get("status") != "complete":
            continue
        source = args.run_dir / run["sanitized_csv"]
        rows = list(csv.DictReader(source.open(newline="", encoding="utf-8")))
        reasoning_settings.update(
            row.get("reasoning_effort_requested", "").strip()
            for row in rows
            if row.get("reasoning_effort_requested", "").strip()
        )
        for row in rows:
            if not ok(row):
                failures[(
                    run["suite"],
                    row.get("provider", "unknown"),
                    row.get("status", "unknown"),
                    row.get("error", "unspecified error").replace("\n", " ")[:240],
                )] += 1
        summary = summarize(rows)
        for row in summary:
            row["suite"] = run["suite"]
        all_summaries.extend(summary)
        public_dataset = data_dir / f"{run['suite']}-sanitized-metrics.csv"
        public_dataset.write_text(source.read_text())
        datasets.append(public_dataset.name)
    if not all_summaries:
        raise SystemExit("No completed measured run was available to summarize")
    write_csv(data_dir / "profile-summary.csv", all_summaries)
    chart_files: list[tuple[str, str]] = []
    for suite in sorted({str(row["suite"]) for row in all_summaries}):
        suite_rows = [row for row in all_summaries if row["suite"] == suite]
        specs = [
            ("median-e2e", "Median completed-response latency", "Median of profile-level p50 E2E values | seconds, lower is better", "p50_e2e_ms", 1000, "s", True),
            ("first-visible-answer", "Time to first visible answer", "Median of profile-level p50 TTFA values | seconds, lower is better", "p50_ttfa_ms", 1000, "s", True),
            ("output-speed", "Output speed", "Median of profile-level p50 output speed | tokens/second, higher is better", "p50_effective_tok_s", 1, " tok/s", False),
            ("reliability", "Request reliability", "HTTP successes across all measured attempts | percent, higher is better", "reliability_pct", 1, "%", False),
        ]
        for slug, title, subtitle, key, divisor, suffix, lower in specs:
            filename = f"{suite}-{slug}.svg"
            svg_chart(charts / filename, f"{title}: {suite}", subtitle, suite_rows, key, divisor, suffix, lower)
            chart_files.append((title, filename))

    lines = [
        f"# Weekly inference benchmark: {manifest['featured']['label']}",
        "",
        f"- Scheduled date: `{manifest['scheduled_date']}`",
        "- Request origin: Telnyx Stateful Actor",
        f"- Featured task: {manifest['featured']['task']}",
        "- Raw reasoning and answer text are retained only in the private workflow artifact. Public datasets contain timing, token, status, and error fields only.",
        "- p95 values are observed sample percentiles, not service-level objectives.",
        "",
        "## Results by provider and profile",
        "",
        "| Suite | Provider | Profile | HTTP success | Visible answers | p50 TTFT | p50 TTFA | p50 E2E | p50 output speed |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in all_summaries:
        lines.append(f"| {row['suite']} | {row['provider']} | {row['profile']} | {row['http_success_n']}/{row['planned_n']} | {row['answer_complete_n']}/{row['planned_n']} | {fmt(row['p50_ttft_ms'], 1000, 's')} | {fmt(row['p50_ttfa_ms'], 1000, 's')} | {fmt(row['p50_e2e_ms'], 1000, 's')} | {fmt(row['p50_effective_tok_s'], 1, ' tok/s')} |")
    lines.extend(["", "## Failures and limitations", ""])
    if not reasoning_settings:
        lines.extend([
            "No explicit reasoning effort was requested, so each endpoint used its provider default. The completion-token allowance includes reasoning tokens; requests that exhausted the allowance before visible content are counted as HTTP successes without a visible answer. Compare TTFA, E2E, and output-speed cells only with their reported visible-answer sample counts.",
            "",
        ])
    if failures:
        lines.extend([
            "Failures remain in the request-level dataset and are excluded from latency percentiles. A rate-limit response can reflect an account quota, so it is not presented as model-serving latency.",
            "",
            "| Suite | Provider | HTTP status | Count | Reported error |",
            "|---|---|---:|---:|---|",
        ])
        for (suite, provider, status, error), count in sorted(failures.items()):
            lines.append(f"| {suite} | {provider} | {status} | {count} | {error.replace('|', '/')} |")
    else:
        lines.append("No measured request failures were recorded.")
    lines.extend(["", "## Charts", ""])
    for title, filename in chart_files:
        lines.extend([f"### {title}", "", f"![{title}](charts/{filename})", ""])
    lines.extend(["## Methodology", "", "The workflow first sends an exact-model smoke request to each configured provider. Telnyx and at least one competitor must return a visible answer; providers that fail qualification are excluded and disclosed before warm-up or measured traffic begins. The workflow then sends five warm-ups and ten measured requests per 1K, 10K, and 100K input profile with short and long requested outputs. Failures remain in the dataset and are excluded only from latency percentiles.", "", "No weighted overall winner is calculated, and no developer blog is published automatically."])
    (report_dir / "REPORT.md").write_text("\n".join(lines) + "\n")
    public_manifest = {key: value for key, value in manifest.items() if key not in {"runs", "recovery"}}
    public_manifest["run_ids"] = [run.get("run_ids") for run in manifest["runs"]]
    public_manifest["disclosure"] = "Run by Telnyx staff; Telnyx Inference was one of the compared endpoints."
    (config_dir / "run-manifest.json").write_text(json.dumps(public_manifest, indent=2))

    (data_dir / "README.md").write_text(
        "# Data\n\n"
        "The per-suite CSV files preserve request identity, timing, token, status, error, and configuration fields. "
        "Generated reasoning and answer text were removed before publication. `profile-summary.csv` contains the exact sample counts and aggregates used in the report. "
        "HTTP failures remain present and are excluded only from latency percentiles.\n"
    )
    source_dir = Path(__file__).resolve().parent
    shutil.copy2(source_dir / "run_weekly.py", scripts_dir / "run_weekly.py")
    shutil.copy2(source_dir / "summarize_weekly.py", scripts_dir / "summarize_weekly.py")
    shutil.copy2(source_dir / "rotation.json", scripts_dir / "rotation.json")
    shutil.copy2(source_dir.parent / "inference-benchmark-actor/scripts/start_run.py", scripts_dir / "start_run.py")
    (scripts_dir / "README.md").write_text(
        "# Scripts\n\n"
        "`run_weekly.py` selects the sentinel and featured suite, performs smoke and warm-up requests, starts measured actor runs, and exports paginated rows. "
        "`summarize_weekly.py` removes generated text and builds the published tables, report, and charts. "
        "`start_run.py` contains the provider endpoint and exact model-ID configuration used by the actor.\n"
    )
    providers = sorted({str(row["provider"]) for row in all_summaries})
    root_readme = [
        f"# Weekly inference benchmark, {manifest['scheduled_date']}",
        "",
        "Part of the [inference benchmarks](../README.md). This run measured streaming inference through the same Telnyx Stateful Actor request origin, using exact model configurations that first passed a provider smoke check.",
        "",
        f"- Featured model: **{manifest['featured']['label']}**",
        f"- Task: {manifest['featured']['task']}",
        f"- Platforms represented: {', '.join(provider.title() for provider in providers)}",
        "- Scope: six Moby-Dick profiles spanning approximately 1K, 10K, and 100K inputs with short and long outputs; ten measured attempts per provider/profile cell.",
        "",
        "Read [`report/REPORT.md`](report/REPORT.md) for sample counts, metric definitions, results, charts, and limitations.",
        "",
        "## Layout",
        "",
        "| Path | Contents |",
        "|---|---|",
        "| `report/` | Full report and generated charts |",
        "| `data/` | Sanitized request-level metrics and profile summary |",
        "| `config/` | Public run manifest and disclosure |",
        "| `scripts/` | Orchestration, model configuration, and report generation code |",
        "",
        "## Read before quoting",
        "",
        "- TTFT is first model activity and may be reasoning; TTFA is first visible answer content.",
        "- p95 values are observed sample percentiles, not provider SLAs.",
        "- Failures are retained and are not silently replaced with retries.",
        "- No weighted overall winner is calculated.",
        "",
        "## Disclosure",
        "",
        "Run by Telnyx staff. Telnyx Inference was one of the compared endpoints. The Telnyx Stateful Actor was the common request origin for all providers.",
        "",
    ]
    (public / "README.md").write_text("\n".join(root_readme))
    print(json.dumps({"public_dir": str(public), "datasets": datasets, "summary_rows": len(all_summaries)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
