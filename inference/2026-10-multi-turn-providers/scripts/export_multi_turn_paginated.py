#!/usr/bin/env python3
"""Export a completed multi-turn actor run in pages and rebuild audit CSVs."""

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


DEFAULT_BASE_URL = os.environ.get("BENCHMARK_ACTOR_URL", "")
SCORE_FIELDS = (
    "task_completion",
    "correctness",
    "state_tracking",
    "correction_following",
    "final_answer_usability",
)


def get_json(url: str, attempts: int = 5) -> dict[str, Any]:
    url = require_https_url(url)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=180) as response:  # nosemgrep: dynamic-urllib-use-detected
                return json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.HTTPError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not export {url}: {last_error}")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def flatten(conversations: list[dict[str, Any]], output: Path) -> None:
    conversation_fields = [
        "conversation_id", "dataset_version", "scenario_id", "scenario_title", "category", "provider",
        "provider_label", "model", "model_label", "repeat", "started_at", "finished_at", "ok",
        "completed_turns", "expected_turns", "request_failures", "total_response_ms", "input_tokens",
        "output_tokens", "total_tokens", "objective_checks_passed", "objective_checks_total",
        "judge_completion_votes", "judge_results", "average_judge_score",
    ]
    turn_fields = [
        "conversation_id", "dataset_version", "scenario_id", "category", "provider", "provider_label",
        "model", "model_label", "repeat", "turn_id", "turn_index", "user_text", "ok", "status",
        "started_at", "e2e_ms", "ttft_ms", "ttfr_ms", "ttfa_ms", "first_delta_kind",
        "reasoning_text", "content_text", "prompt_tokens", "output_tokens", "total_tokens",
        "reasoning_effort_requested", "stream_options_requested", "finish_reason", "error",
    ]
    judge_fields = [
        "conversation_id", "dataset_version", "scenario_id", "category", "provider", "provider_label",
        "model", "model_label", "repeat", "judge_model", "judge_label", "ok", "status",
        "finish_reason", "completed", *SCORE_FIELDS, "summary", "strengths", "issues", "raw_response", "error",
    ]
    objective_fields = [
        "conversation_id", "dataset_version", "scenario_id", "category", "provider", "provider_label",
        "model", "model_label", "repeat", "label", "type", "passed", "detail",
    ]
    conversation_rows: list[dict[str, Any]] = []
    turn_rows: list[dict[str, Any]] = []
    judge_rows: list[dict[str, Any]] = []
    objective_rows: list[dict[str, Any]] = []
    for conversation in conversations:
        base = {key: conversation.get(key) for key in conversation_fields}
        judges = [judge for judge in conversation.get("judges", []) if judge.get("ok")]
        scores = [value for judge in judges for value in (judge.get("scores") or {}).values()]
        base.update(
            objective_checks_passed=sum(bool(check.get("passed")) for check in conversation.get("objective_checks", [])),
            objective_checks_total=len(conversation.get("objective_checks", [])),
            judge_completion_votes=sum(judge.get("completed") is True for judge in judges),
            judge_results=len(judges),
            average_judge_score=(sum(scores) / len(scores)) if scores else None,
        )
        conversation_rows.append(base)
        shared = {field: conversation.get(field) for field in (
            "conversation_id", "dataset_version", "scenario_id", "category", "provider", "provider_label",
            "model", "model_label", "repeat",
        )}
        for turn in conversation.get("turns", []):
            turn_rows.append({**shared, **turn})
        for judge in conversation.get("judges", []):
            judge_rows.append({
                **shared,
                **judge,
                **(judge.get("scores") or {}),
                "strengths": " | ".join(judge.get("strengths") or []),
                "issues": " | ".join(judge.get("issues") or []),
            })
        for check in conversation.get("objective_checks", []):
            objective_rows.append({**shared, **check})
    write_csv(output / "conversations.csv", conversation_fields, conversation_rows)
    write_csv(output / "turns.csv", turn_fields, turn_rows)
    write_csv(output / "judges.csv", judge_fields, judge_rows)
    write_csv(output / "objective-checks.csv", objective_fields, objective_rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--page-size", type=int, default=5)
    args = parser.parse_args()
    if not args.base_url:
        parser.error("set BENCHMARK_ACTOR_URL or pass --base-url")
    root = f"{args.base_url.rstrip('/')}/multi-turn/runs/{args.run_id}"
    status = get_json(f"{root}/status")
    if not status.get("done"):
        raise RuntimeError(f"Run is not complete: {status}")
    total = int(status["total"])
    conversations: list[dict[str, Any]] = []
    state: dict[str, Any] | None = None
    for offset in range(0, total, args.page_size):
        page = get_json(f"{root}/results-page.json?offset={offset}&limit={args.page_size}")
        state = state or page.get("state")
        conversations.extend(page.get("conversations") or [])
        print(f"Exported {len(conversations)}/{total}")
    if len(conversations) != total:
        raise RuntimeError(f"Expected {total} conversations, exported {len(conversations)}")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    (args.output / "results.json").write_text(
        json.dumps({"state": state, "conversations": conversations}, indent=2) + "\n",
        encoding="utf-8",
    )
    flatten(conversations, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
