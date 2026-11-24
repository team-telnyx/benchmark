#!/usr/bin/env python3
"""Run the weekly exact-model benchmark through the protected Telnyx actor."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
ACTOR_DIR = BENCHMARK_ROOT / "inference-benchmark-actor"
PROMPT_DIR = BENCHMARK_ROOT / "benchmarking-files"
DEFAULT_CONFIG = Path(__file__).with_name("rotation.json")
PRIVATE_TEXT_FIELDS = {
    "first_delta_text",
    "first_reasoning_text",
    "first_content_text",
    "full_reasoning_text",
    "full_content_text",
}


def request_json(method: str, url: str, token: str, payload: dict[str, Any] | None = None, timeout: float = 120) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc


def validate_actor_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".telnyxcompute.com")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Actor URL must be an HTTPS telnyxcompute.com origin without credentials, query, or fragment")
    return value.rstrip("/")


def week_index(anchor: str, today: date, size: int) -> int:
    anchor_date = date.fromisoformat(anchor)
    return max(0, (today - anchor_date).days // 7) % size


def run_start(base_url: str, token: str, run_id: str, suite: str, profiles: list[str], repeats: int, prompt_dir: Path, payload_path: Path) -> None:
    env = os.environ.copy()
    env["INFERENCE_BENCHMARK_CONTROL_TOKEN"] = token
    command = [
        sys.executable,
        str(ACTOR_DIR / "scripts/start_run.py"),
        "--base-url", base_url,
        "--run-id", run_id,
        "--prompt-dir", str(prompt_dir),
        "--suites", suite,
        "--profiles", *profiles,
        "--repeats", str(repeats),
        "--random-seed", str(int(datetime.now(timezone.utc).strftime("%Y%m%d"))),
        "--fresh-prompt-prefix", run_id,
        "--write-payload", str(payload_path),
    ]
    subprocess.run(command, check=True, env=env)


def poll(base_url: str, token: str, run_id: str, timeout_hours: float = 8) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_hours * 3600
    last_completed = -1
    last_progress = time.monotonic()
    while time.monotonic() < deadline:
        status = request_json("GET", f"{base_url}/runs/{run_id}/status", token)
        completed = int(status.get("completed") or status.get("cursor") or 0)
        if completed != last_completed:
            print(json.dumps({"run_id": run_id, "completed": completed, "total": status.get("total"), "done": status.get("done")}), flush=True)
            last_completed = completed
            last_progress = time.monotonic()
        if status.get("done"):
            return status
        if time.monotonic() - last_progress > 1800:
            raise RuntimeError(f"Run {run_id} made no progress for 30 minutes: {json.dumps(status)}")
        time.sleep(30)
    raise RuntimeError(f"Run {run_id} exceeded {timeout_hours} hours")


def export_rows(base_url: str, token: str, run_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = request_json("GET", f"{base_url}/runs/{run_id}/results-page.json?offset={offset}&limit=100", token)
        rows.extend(page.get("rows", []))
        next_offset = page.get("nextOffset")
        if next_offset is None:
            if len(rows) != int(page.get("total", len(rows))):
                raise RuntimeError(f"Export count mismatch for {run_id}: {len(rows)} rows")
            return rows
        offset = int(next_offset)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sanitize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if key not in PRIVATE_TEXT_FIELDS} for row in rows]


def successful(row: dict[str, Any]) -> bool:
    return row.get("ok") is True or str(row.get("ok", "")).lower() == "true"


def smoke_is_valid(rows: list[dict[str, Any]]) -> tuple[bool, str]:
    providers = {str(row.get("provider")) for row in rows}
    failures = [row for row in rows if not successful(row)]
    no_answer = [row for row in rows if successful(row) and row.get("ttfa_ms") in (None, "")]
    if len(providers) < 2:
        return False, f"only {len(providers)} provider returned a row"
    if failures:
        details = "; ".join(f"{row.get('provider')}: {row.get('status')} {row.get('error')}" for row in failures)
        return False, f"provider smoke failure: {details}"
    if no_answer:
        return False, "one or more providers returned no visible answer"
    return True, "ok"


def run_suite(base_url: str, token: str, suite: str, profiles: list[str], smoke_profile: str, warmups: int, repeats: int, out_dir: Path, prompt_dir: Path) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ids = {
        "smoke": f"weekly-{suite}-smoke-{stamp}",
        "warmup": f"weekly-{suite}-warmup-{stamp}",
        "measured": f"weekly-{suite}-measured-{stamp}",
    }
    result: dict[str, Any] = {"suite": suite, "run_ids": ids, "status": "started"}

    run_start(base_url, token, ids["smoke"], suite, [smoke_profile], 1, prompt_dir, out_dir / f"{ids['smoke']}.payload.json")
    result["smoke_status"] = poll(base_url, token, ids["smoke"])
    smoke_rows = export_rows(base_url, token, ids["smoke"])
    (out_dir / f"{ids['smoke']}.json").write_text(json.dumps(smoke_rows, indent=2))
    valid, reason = smoke_is_valid(smoke_rows)
    result["smoke_valid"] = valid
    result["smoke_reason"] = reason
    if not valid:
        result["status"] = "stopped_after_smoke"
        return result

    run_start(base_url, token, ids["warmup"], suite, [smoke_profile], warmups, prompt_dir, out_dir / f"{ids['warmup']}.payload.json")
    result["warmup_status"] = poll(base_url, token, ids["warmup"])

    run_start(base_url, token, ids["measured"], suite, profiles, repeats, prompt_dir, out_dir / f"{ids['measured']}.payload.json")
    result["measured_status"] = poll(base_url, token, ids["measured"])
    rows = export_rows(base_url, token, ids["measured"])
    expected = len(profiles) * repeats * len({str(row.get("provider")) for row in rows})
    if len(rows) != expected:
        raise RuntimeError(f"Measured export for {suite} has {len(rows)} rows; expected {expected}")
    (out_dir / f"{ids['measured']}.raw.json").write_text(json.dumps(rows, indent=2))
    write_csv(out_dir / f"{ids['measured']}.raw.csv", rows)
    public_rows = sanitize(rows)
    (out_dir / f"{ids['measured']}.sanitized.json").write_text(json.dumps(public_rows, indent=2))
    write_csv(out_dir / f"{ids['measured']}.sanitized.csv", public_rows)
    result.update({"status": "complete", "row_count": len(rows), "success_count": sum(successful(row) for row in rows), "sanitized_csv": f"{ids['measured']}.sanitized.csv"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--suite", help="Override rotation and run one featured suite")
    args = parser.parse_args()

    actor_url = os.environ.get("INFERENCE_BENCHMARK_ACTOR_URL", "")
    token = os.environ.get("INFERENCE_BENCHMARK_CONTROL_TOKEN", "")
    if not actor_url or not token:
        raise SystemExit("INFERENCE_BENCHMARK_ACTOR_URL and INFERENCE_BENCHMARK_CONTROL_TOKEN are required")
    base_url = validate_actor_url(actor_url)
    config = json.loads(args.config.read_text())
    rotation = config["featured_rotation"]
    selected = next((item for item in rotation if item["suite"] == args.suite), None) if args.suite else rotation[week_index(config["anchor_sunday"], args.date, len(rotation))]
    if selected is None:
        raise SystemExit(f"Unknown suite override: {args.suite}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "scheduled_date": args.date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "actor_origin": "Telnyx Stateful Actor",
        "featured": selected,
        "sentinel_suite": config["sentinel_suite"],
        "profiles": config["profiles"],
        "runs": [],
    }
    suites = [config["sentinel_suite"]]
    if selected["suite"] not in suites:
        suites.append(selected["suite"])
    try:
        for suite in suites:
            manifest["runs"].append(run_suite(base_url, token, suite, config["profiles"], config["smoke_profile"], config["warmup_repeats"], config["measured_repeats"], args.out_dir, PROMPT_DIR))
    finally:
        (args.out_dir / "run-manifest.json").write_text(json.dumps(manifest, indent=2))
    if any(run["status"] != "complete" for run in manifest["runs"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
