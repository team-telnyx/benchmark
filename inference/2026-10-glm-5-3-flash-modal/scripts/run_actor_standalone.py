#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import time
import urllib.error
import urllib.request
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path

from url_safety import require_https_url


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT = (
    "You are participating in a latency benchmark. Follow the requested output format, "
    "do not mention the benchmark, and continue until the requested response is complete."
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def request_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: int = 60):
    url = require_https_url(url)
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"content-type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosemgrep: dynamic-urllib-use-detected
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {text[:1000]}") from exc


def actor_provider(endpoint: dict, concurrency: int, cache_mode: str) -> dict:
    return {
        "name": endpoint["actor_provider"],
        "baseUrl": endpoint["base_url"],
        "model": endpoint["model"],
        "apiKeyEnv": endpoint["api_key_env"],
        "reasoningEffort": endpoint["reasoning_effort"],
        "includeStreamOptions": endpoint.get("include_stream_options", True),
        "retryWithoutStreamOptions": True,
        "concurrency": concurrency,
        "cacheMode": cache_mode,
    }


def actor_profile(profile: dict, nonce_prefix: str) -> dict:
    return {
        "name": profile["id"],
        "system": SYSTEM_PROMPT,
        "user": "Generated inside the benchmark actor.",
        "maxTokens": profile["max_output_tokens"],
        "inputTarget": str(profile["input_tokens"]),
        "outputTarget": str(profile["max_output_tokens"]),
        "inputTokens": profile["input_tokens"],
        "noncePrefix": nonce_prefix,
    }


def run_cell(actor_base: str, endpoint: dict, profile: dict, *, attempts: int, concurrency: int, cache_mode: str) -> tuple[list[dict], dict]:
    run_id = (
        f"modal-{endpoint['id']}-{profile['id']}-c{concurrency}-"
        f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    )
    run_url = f"{actor_base.rstrip('/')}/runs/{run_id}"
    payload = {
        "repeats": attempts,
        "profiles": [actor_profile(profile, run_id)],
        "suites": [
            {
                "suite": "glm_53_flash",
                "providers": [actor_provider(endpoint, concurrency, cache_mode)],
            }
        ],
        "timeoutMs": 900000,
        "batchSize": concurrency,
        "autoStart": True,
        "tokenUsageProbe": False,
    }
    wall_start = time.perf_counter()
    started = request_json(f"{run_url}/start", method="POST", body=payload, timeout=120)
    while True:
        status = request_json(f"{run_url}/status", timeout=60)
        if status.get("done"):
            break
        time.sleep(2)
    wall_seconds = time.perf_counter() - wall_start
    rows = request_json(f"{run_url}/results.json", timeout=120)
    successes = [row for row in rows if row.get("ok")]
    total_output = sum(float(row.get("output_tokens") or 0) for row in successes)
    for row in rows:
        row.update(
            {
                "actor_run_id": run_id,
                "endpoint_id": endpoint["id"],
                "endpoint_label": endpoint["label"],
                "endpoint_type": endpoint["endpoint_type"],
                "cell_wall_seconds": wall_seconds,
                "cell_requests_per_second": len(successes) / wall_seconds if wall_seconds else None,
                "cell_output_tokens_per_second": total_output / wall_seconds if wall_seconds else None,
            }
        )
    return rows, {"run_id": run_id, "started": started, "status": status, "wall_seconds": wall_seconds}


def write_outputs(output: Path, rows: list[dict], manifest: dict) -> None:
    raw_jsonl = output / "raw_requests.jsonl"
    with raw_jsonl.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    fields = sorted({key for row in rows for key in row})
    with (output / "raw_requests.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    clean = [row for row in rows if row.get("ok") and str(row.get("full_content_text") or "").strip()]
    with (output / "clean_successful_requests.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(clean)
    manifest["completed_at"] = utc_now()
    manifest["attempted"] = len(rows)
    manifest["successful_with_visible_answer"] = len(clean)
    (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "benchmark-config.json"))
    parser.add_argument("--actor-base-url", default=os.environ.get("BENCHMARK_ACTOR_URL", ""))
    parser.add_argument("--mode", choices=["smoke", "pilot", "full"], default="smoke")
    parser.add_argument("--output-dir")
    parser.add_argument("--endpoint", action="append", dest="endpoint_ids")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.endpoint_ids:
        selected = set(args.endpoint_ids)
        config["endpoints"] = [item for item in config["endpoints"] if item["id"] in selected]
        missing = selected - {item["id"] for item in config["endpoints"]}
        if missing:
            raise SystemExit(f"Unknown endpoint id(s): {', '.join(sorted(missing))}")
    actor_base = args.actor_base_url
    if not actor_base:
        parser.error("set BENCHMARK_ACTOR_URL or pass --actor-base-url")
    output = Path(
        args.output_dir
        or ROOT / "results" / f"actor_{args.mode}_{time.strftime('%Y%m%d-%H%M%S')}"
    )
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "started_at": utc_now(),
        "mode": args.mode,
        "execution_origin": "Telnyx Stateful Actor",
        "actor_base_url": actor_base,
        "config": config,
        "cells": [],
        "limitations": [],
    }
    rows: list[dict] = []

    if args.mode == "smoke":
        smoke_profile = {"id": "smoke_256_256", "input_tokens": 256, "max_output_tokens": 256}
        cells = [(endpoint, smoke_profile, 1, 1, "smoke") for endpoint in config["endpoints"]]
    else:
        allowed = [1, 10] if args.mode == "pilot" else config["load"]["concurrency"]
        if args.mode == "full":
            blockers = [
                endpoint for endpoint in config["endpoints"]
                if endpoint.get("documented_concurrency_limit") is not None
                and max(allowed) > int(endpoint["documented_concurrency_limit"])
            ]
            if blockers:
                labels = ", ".join(
                    f"{item['label']} ({item['documented_concurrency_limit']})" for item in blockers
                )
                raise SystemExit(f"Refusing full run: documented concurrency limit blocks c={max(allowed)}: {labels}")
        cells = []
        for endpoint in config["endpoints"]:
            for profile in config["profiles"]:
                warmups = int(config["load"]["warmups_per_endpoint_profile"])
                cells.append((endpoint, profile, warmups, 1, "warmup"))
                for concurrency in allowed:
                    attempts = int(config["load"]["attempts"][str(concurrency)])
                    cells.append((endpoint, profile, attempts, concurrency, "fresh"))
        random.Random(config["load"]["random_seed"]).shuffle(cells)

    for endpoint, profile, attempts, concurrency, cache_mode in cells:
        cell_rows, metadata = run_cell(
            actor_base,
            endpoint,
            profile,
            attempts=attempts,
            concurrency=concurrency,
            cache_mode=cache_mode,
        )
        rows.extend(cell_rows)
        manifest["cells"].append(metadata)
        ok = sum(bool(row.get("ok") and str(row.get("full_content_text") or "").strip()) for row in cell_rows)
        print(f"{endpoint['id']} {profile['id']} c={concurrency} {cache_mode}: {ok}/{len(cell_rows)}")

    write_outputs(output, rows, manifest)
    print(json.dumps({"output_dir": str(output), "attempted": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
