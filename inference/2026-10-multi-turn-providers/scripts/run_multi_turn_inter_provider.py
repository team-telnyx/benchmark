#!/usr/bin/env python3
"""Validate, launch, monitor, and export the inter-provider multi-turn benchmark."""

from __future__ import annotations

import argparse
import os
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from url_safety import require_https_url


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "config" / "scenarios-and-models.json"
DEFAULT_BASE_URL = os.environ.get("BENCHMARK_ACTOR_URL", "")
EXPORTS = (
    "results.json",
    "conversations.csv",
    "turns.csv",
    "judges.csv",
    "objective-checks.csv",
)


def load_dataset(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_dataset(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    scenarios = data.get("scenarios")
    models = data.get("models")
    judges = data.get("judges")
    if not isinstance(scenarios, list) or not scenarios:
        return ["scenarios must be a non-empty list"]
    if not isinstance(models, list) or not models:
        errors.append("candidate models must be a non-empty list")
    if not isinstance(judges, list) or len(judges) < 1:
        errors.append("v1 must define at least one judge")

    ids = [scenario.get("id") for scenario in scenarios]
    if len(ids) != len(set(ids)):
        errors.append("scenario IDs must be unique")
    if len(scenarios) != 15:
        errors.append(f"v1 must contain 15 scenarios; found {len(scenarios)}")
    expected_categories = {
        "developer": 5,
        "business": 5,
        "planning_editing_structured": 5,
    }
    categories = Counter(scenario.get("category") for scenario in scenarios)
    if categories != Counter(expected_categories):
        errors.append(f"category counts must be {expected_categories}; found {dict(categories)}")

    for scenario in scenarios:
        scenario_id = scenario.get("id", "<missing>")
        turns = scenario.get("turns")
        if not isinstance(turns, list) or not 4 <= len(turns) <= 6:
            errors.append(f"{scenario_id}: expected 4-6 turns")
        if not scenario.get("judgeContext"):
            errors.append(f"{scenario_id}: judgeContext is required")
        if not isinstance(scenario.get("objectiveChecks"), list):
            errors.append(f"{scenario_id}: objectiveChecks must be a list")

    cells = [(model.get("provider"), model.get("id")) for model in models or []]
    if any(not provider or not model_id for provider, model_id in cells):
        errors.append("every candidate requires provider and id")
    if len(cells) != len(set(cells)):
        errors.append("provider/model cells must be unique")
    providers = Counter(model.get("provider") for model in models or [])
    if providers != Counter({"telnyx": 3, "baseten": 3, "together": 3, "fireworks": 3}):
        errors.append(f"expected three models for each provider; found {dict(providers)}")
    if data.get("repeats") != 3:
        errors.append("inter-provider v1 must use three repeats")
    if data.get("judgeRepeats") != [1]:
        errors.append("inter-provider v1 must judge repeat 1 only")
    return errors


def request(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 120,
) -> bytes:
    url = require_https_url(url)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # nosemgrep: dynamic-urllib-use-detected
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {detail[:1000]}") from error


def json_request(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    return json.loads(request(url, method, payload, timeout).decode("utf-8"))


def output_directory(run_id: str) -> Path:
    return ROOT / "results" / "multi_turn_task_completion" / run_id


def write_manifest(output: Path, args: argparse.Namespace, payload: dict[str, Any]) -> None:
    manifest = {
        "run_id": args.run_id,
        "actor_base_url": args.base_url,
        "dataset_path": str(args.dataset.resolve()),
        "dataset_version": payload["datasetVersion"],
        "candidate_cells": [
            {
                "provider": model["provider"],
                "provider_label": model["providerLabel"],
                "model": model["id"],
                "model_label": model["label"],
                "reasoning_effort": model.get("reasoningEffort"),
                "stream_options": model.get("includeStreamOptions", True),
            }
            for model in payload["models"]
        ],
        "judge_models": [model["id"] for model in payload["judges"]],
        "scenario_count": len(payload["scenarios"]),
        "repeats": payload.get("repeats", 1),
        "judge_repeats": payload.get("judgeRepeats"),
        "task_order_seed": payload.get("taskOrderSeed"),
        "launched_at": datetime.now(timezone.utc).isoformat(),
        "mode": "smoke" if args.smoke else "full",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--run-id", default=f"multi-turn-inter-provider-v1-{datetime.now():%Y%m%d-%H%M%S}")
    parser.add_argument("--smoke", action="store_true", help="Run one scenario across all candidate models and judges")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--max-wait-minutes", type=int, default=240)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.validate_only and not args.base_url:
        raise SystemExit("set BENCHMARK_ACTOR_URL or pass --base-url")
    payload = load_dataset(args.dataset)
    errors = validate_dataset(payload)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print(
        f"Dataset valid: {len(payload['scenarios'])} scenarios, "
        f"{len(payload['models'])} candidates, {len(payload['judges'])} judges"
    )
    if args.validate_only:
        return 0
    if args.smoke:
        payload = {
            **payload,
            "scenarios": payload["scenarios"][:1],
            "repeats": 1,
            "judgeRepeats": [1],
        }

    output = output_directory(args.run_id)
    output.mkdir(parents=True, exist_ok=False)
    (output / "payload.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_manifest(output, args, payload)

    root = f"{args.base_url.rstrip('/')}/multi-turn/runs/{args.run_id}"
    health = request(f"{args.base_url.rstrip('/')}/health/liveness").decode("utf-8", errors="replace")
    if health.strip() != "ok":
        raise RuntimeError(f"Actor health check did not return ok: {health[:200]}")
    started = json_request(f"{root}/start", "POST", payload)
    (output / "start.json").write_text(json.dumps(started, indent=2) + "\n", encoding="utf-8")
    print(f"Started {args.run_id}: {started.get('total')} conversations")

    deadline = time.monotonic() + args.max_wait_minutes * 60
    previous_completed = -1
    while time.monotonic() < deadline:
        try:
            status = json_request(f"{root}/status", timeout=30)
        except (OSError, RuntimeError, TimeoutError) as error:
            print(f"Status temporarily unavailable: {error}")
            time.sleep(args.poll_seconds)
            continue
        completed = int(status.get("completed", 0))
        if completed != previous_completed:
            print(
                f"Progress {completed}/{status.get('total')} "
                f"successes={status.get('successes')} failures={status.get('failures')}"
            )
            previous_completed = completed
        if status.get("done"):
            (output / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
            break
        time.sleep(args.poll_seconds)
    else:
        raise TimeoutError(f"Run did not finish within {args.max_wait_minutes} minutes")

    for name in EXPORTS:
        data = request(f"{root}/{name}")
        (output / name).write_bytes(data)
    print(f"Saved benchmark artifacts to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
