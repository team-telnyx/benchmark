#!/usr/bin/env python3
"""Repeat only failed judge evaluations for an existing actor run."""

from __future__ import annotations

import argparse
import os
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from url_safety import require_https_url


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "config" / "scenarios-and-models.json"
DEFAULT_BASE_URL = os.environ.get("BENCHMARK_ACTOR_URL", "")
EXPORTS = ("results.json", "conversations.csv", "turns.csv", "judges.csv", "objective-checks.csv")


def request(url: str, method: str = "GET", payload: dict[str, Any] | None = None) -> bytes:
    url = require_https_url(url)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:  # nosemgrep: dynamic-urllib-use-detected
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {detail[:1000]}") from error


def json_request(url: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return json.loads(request(url, method, payload).decode("utf-8"))


def preserve_exports(run_directory: Path) -> None:
    archive = run_directory / "pre_rejudge"
    archive.mkdir(exist_ok=True)
    for name in EXPORTS:
        source = run_directory / name
        if source.exists():
            shutil.copy2(source, archive / name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--judge-id", default="MiniMaxAI/MiniMax-M3-MXFP8")
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--max-wait-minutes", type=int, default=60)
    args = parser.parse_args()
    if not args.base_url:
        parser.error("set BENCHMARK_ACTOR_URL or pass --base-url")

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    judges = [judge for judge in dataset["judges"] if judge["id"] == args.judge_id]
    if len(judges) != 1:
        raise ValueError(f"Expected exactly one judge config for {args.judge_id}")
    run_directory = ROOT / "results" / "multi_turn_task_completion" / args.run_id
    if not run_directory.exists():
        raise FileNotFoundError(run_directory)
    preserve_exports(run_directory)

    root = f"{args.base_url.rstrip('/')}/multi-turn/runs/{args.run_id}"
    started = json_request(
        f"{root}/rejudge/start",
        "POST",
        {"onlyFailed": True, "judges": judges},
    )
    (run_directory / "rejudge-start.json").write_text(json.dumps(started, indent=2) + "\n", encoding="utf-8")
    print(f"Started targeted rejudge: {started.get('total')} evaluations")

    deadline = time.monotonic() + args.max_wait_minutes * 60
    previous = -1
    while time.monotonic() < deadline:
        status = json_request(f"{root}/rejudge/status")
        completed = int(status.get("completed", 0))
        if completed != previous:
            print(
                f"Rejudge {completed}/{status.get('total')} "
                f"successes={status.get('successes')} failures={status.get('failures')}"
            )
            previous = completed
        if status.get("done"):
            (run_directory / "rejudge-status.json").write_text(
                json.dumps(status, indent=2) + "\n",
                encoding="utf-8",
            )
            break
        time.sleep(args.poll_seconds)
    else:
        raise TimeoutError(f"Rejudge did not finish within {args.max_wait_minutes} minutes")

    for name in EXPORTS:
        (run_directory / name).write_bytes(request(f"{root}/{name}"))
    print(f"Updated exports in {run_directory}; original exports are under pre_rejudge/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
