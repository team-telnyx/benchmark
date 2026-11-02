#!/usr/bin/env python3
"""Start, poll, and export a GLM 5.3 provider benchmark via the benchmark actor.

Telnyx is intentionally configured against the public OpenAI-compatible
endpoint (`/v2/ai/openai`). Competitors use their provider-native public
OpenAI-compatible endpoints.
"""

from __future__ import annotations

import argparse
import os
import csv
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from url_safety import require_https_url


PROMPT_DIR = Path(__file__).resolve().parents[1] / "config" / "fixtures"
DEFAULT_BASE_URL = os.environ.get("BENCHMARK_ACTOR_URL", "")

PROVIDERS = [
    {
        "name": "telnyx",
        "baseUrl": "https://api.telnyx.com/v2/ai/openai",
        "model": "zai-org/GLM-5.3",
        "apiKeyEnv": "TELNYX_API_KEY",
        "includeStreamOptions": True,
        "retryWithoutStreamOptions": True,
        "reasoningEffort": "high",
    },
    {
        "name": "baseten",
        "baseUrl": "https://inference.baseten.co/v1",
        "model": "zai-org/GLM-5.3",
        "apiKeyEnv": "BASETEN_API_KEY",
        "includeStreamOptions": True,
        "retryWithoutStreamOptions": True,
        "reasoningEffort": "high",
    },
    {
        "name": "together",
        "baseUrl": "https://api.together.xyz/v1",
        "model": "zai-org/GLM-5.3",
        "apiKeyEnv": "TOGETHER_API_KEY",
        "includeStreamOptions": True,
        "retryWithoutStreamOptions": True,
        "reasoningEffort": "high",
    },
    {
        "name": "fireworks",
        "baseUrl": "https://api.fireworks.ai/inference/v1",
        "model": "accounts/fireworks/models/glm-5p3",
        "apiKeyEnv": "FIREWORKS_API_KEY",
        "includeStreamOptions": True,
        "retryWithoutStreamOptions": True,
        "reasoningEffort": "high",
    },
]


def parse_prompt(path: Path) -> dict[str, Any]:
    text = path.read_text()
    name = path.stem
    input_target = "100k" if "_100k_" in name else "10k" if "_10k_" in name else "1k"
    output_target = "1k" if name.endswith("_1k_out") else "100"
    max_tokens = 1200 if output_target == "1k" else 120

    system = text.split("## System", 1)[1].split("## User", 1)[0].strip()
    user = text.split("## User", 1)[1].strip()
    marker_start = user.find("{fixture:")
    marker_end = user.find("}", marker_start)
    if marker_start == -1 or marker_end == -1:
        raise SystemExit(f"No fixture marker found in {path}")
    fixture_name = user[marker_start + len("{fixture:") : marker_end]
    fixture_path = PROMPT_DIR / fixture_name
    if not fixture_path.exists():
        fixture_path = PROMPT_DIR / Path(fixture_name).name
    fixture = fixture_path.read_text()
    user = user[:marker_start] + fixture + user[marker_end + 1 :]

    return {
        "name": name,
        "system": system,
        "user": user,
        "maxTokens": max_tokens,
        "inputTarget": input_target,
        "outputTarget": output_target,
    }


def load_profiles(selected: list[str] | None) -> list[dict[str, Any]]:
    profiles = []
    for path in sorted(PROMPT_DIR.glob("[0-9][0-9]_*.md")):
        if selected and path.stem not in selected:
            continue
        profiles.append(parse_prompt(path))
    if not profiles:
        raise SystemExit("No profiles matched")
    return profiles


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 120) -> Any:
    url = require_https_url(url)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosemgrep: dynamic-urllib-use-detected
            text = response.read().decode("utf-8")
            return json.loads(text) if text else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} from {url}: {detail}") from exc


def request_text(url: str, timeout: float = 120) -> str:
    url = require_https_url(url)
    with urllib.request.urlopen(url, timeout=timeout) as response:  # nosemgrep: dynamic-urllib-use-detected
        return response.read().decode("utf-8")


def write_status(path: Path, status: dict[str, Any]) -> None:
    path.write_text(json.dumps(status, indent=2))


TEXT_FIELDS = {
    "first_delta_text",
    "first_reasoning_text",
    "first_content_text",
    "full_reasoning_text",
    "full_content_text",
}


def write_clean_metrics_csv(raw_csv_path: Path, clean_csv_path: Path) -> int:
    with raw_csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = [field for field in (reader.fieldnames or []) if field not in TEXT_FIELDS]
    with clean_csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fieldnames} for row in rows)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--profiles", nargs="*")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--timeout-ms", type=int, default=900_000)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--poll", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=10.0)
    args = parser.parse_args()
    if not args.base_url:
        parser.error("set BENCHMARK_ACTOR_URL or pass --base-url")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "repeats": args.repeats,
        "timeoutMs": args.timeout_ms,
        "batchSize": args.batch_size,
        "tokenUsageProbe": True,
        "autoStart": True,
        "profiles": load_profiles(args.profiles),
        "suites": [{"suite": "glm_53", "providers": PROVIDERS}],
    }
    (args.out_dir / "payload.json").write_text(json.dumps(payload, indent=2))

    base = args.base_url.rstrip("/")
    start_url = f"{base}/runs/{args.run_id}/start"
    start_response = request_json(start_url, method="POST", payload=payload)
    (args.out_dir / f"{args.run_id}.json").write_text(json.dumps(start_response, indent=2))
    print(json.dumps({"run_id": args.run_id, "start": start_response}, indent=2), flush=True)

    if args.poll:
        last_completed = -1
        while True:
            status = request_json(f"{base}/runs/{args.run_id}/status")
            write_status(args.out_dir / f"{args.run_id}.status.json", status)
            completed = int(status.get("completed", 0))
            if completed != last_completed or status.get("done"):
                print(json.dumps(status, indent=2), flush=True)
                last_completed = completed
            if status.get("done"):
                break
            time.sleep(args.poll_interval)

    csv_text = request_text(f"{base}/runs/{args.run_id}/results.csv")
    csv_path = args.out_dir / f"{args.run_id}.raw-results-with-text.csv"
    csv_path.write_text(csv_text)
    clean_csv_path = args.out_dir / f"{args.run_id}.clean-metrics.csv"
    row_count = write_clean_metrics_csv(csv_path, clean_csv_path)
    results_json = request_json(f"{base}/runs/{args.run_id}/results.json")
    results_json_path = args.out_dir / f"{args.run_id}.raw-results-with-text.json"
    results_json_path.write_text(json.dumps(results_json, indent=2))
    print(
        json.dumps(
            {
                "raw_csv": str(csv_path),
                "clean_csv": str(clean_csv_path),
                "raw_json": str(results_json_path),
                "rows": row_count,
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
