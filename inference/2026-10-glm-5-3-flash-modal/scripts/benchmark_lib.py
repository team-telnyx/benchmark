from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from url_safety import require_https_url


SYSTEM_PROMPT = (
    "You are participating in a latency benchmark. Follow the requested output format, "
    "do not mention the benchmark, and continue until the requested response is complete."
)


@dataclass(frozen=True)
class Endpoint:
    id: str
    label: str
    endpoint_type: str
    base_url: str
    model: str
    api_key_env: str
    reasoning_effort: str | None
    extra_headers: dict[str, str]
    affinity_header: str | None
    pricing: dict[str, Any] | None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Endpoint":
        return cls(
            id=value["id"],
            label=value["label"],
            endpoint_type=value["endpoint_type"],
            base_url=value["base_url"].rstrip("/"),
            model=value["model"],
            api_key_env=value["api_key_env"],
            reasoning_effort=value.get("reasoning_effort"),
            extra_headers=value.get("extra_headers", {}),
            affinity_header=value.get("affinity_header"),
            pricing=value.get("pricing"),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def enabled_endpoints(config: dict[str, Any]) -> list[Endpoint]:
    return [Endpoint.from_dict(item) for item in config["endpoints"] if item.get("enabled")]


def percentile(values: Iterable[float], p: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * p
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return ordered[low]
    weight = index - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def corpus_text(target_tokens: int, nonce: str, shared_prefix: str | None = None) -> str:
    target_chars = max(100, target_tokens * 4)
    prefix = shared_prefix or f"Request nonce: {nonce}. This nonce is unique and appears before the context.\n"
    lines = [prefix]
    index = 0
    while sum(map(len, lines)) < target_chars:
        digest = hashlib.sha256(f"{nonce}:{index}".encode()).hexdigest()[:16]
        lines.append(
            f"Record {index} ({digest}) describes a software service, its deployment state, "
            "observed behavior, dependencies, and one concrete operational constraint. "
            "Use these records only as context for the final synthesis.\n"
        )
        index += 1
    return "".join(lines)[:target_chars]


def prompt_for(profile: dict[str, Any], nonce: str, shared_prefix: str | None = None) -> list[dict[str, str]]:
    context = corpus_text(int(profile["input_tokens"]), nonce, shared_prefix)
    output_target = int(profile["max_output_tokens"])
    user = (
        f"Read the context and produce a structured technical synthesis of approximately {output_target} tokens. "
        "Include a short overview, three findings, and two implementation recommendations.\n\n"
        f"CONTEXT\n{context}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _usage_detail(usage: dict[str, Any], parent: str, child: str) -> float | None:
    details = usage.get(parent)
    return _number(details.get(child)) if isinstance(details, dict) else None


def _delta_text(chunk: dict[str, Any]) -> tuple[str, str]:
    content = ""
    reasoning = ""
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        content += delta.get("content") or ""
        reasoning += delta.get("reasoning") or delta.get("reasoning_content") or ""
    return content, reasoning


def request_stream(
    endpoint: Endpoint,
    profile: dict[str, Any],
    *,
    run_id: str,
    request_id: str,
    concurrency: int,
    cache_mode: str,
    nonce: str,
    timeout_seconds: int,
    shared_prefix: str | None = None,
    affinity_key: str | None = None,
    warmup: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    start = time.perf_counter()
    key = os.environ.get(endpoint.api_key_env, "")
    base = {
        "run_id": run_id,
        "request_id": request_id,
        "started_at": started_at,
        "endpoint_id": endpoint.id,
        "endpoint_label": endpoint.label,
        "endpoint_type": endpoint.endpoint_type,
        "model": endpoint.model,
        "profile": profile["id"],
        "input_target_tokens": profile["input_tokens"],
        "max_output_tokens": profile["max_output_tokens"],
        "concurrency": concurrency,
        "cache_mode": cache_mode,
        "warmup": warmup,
        "reasoning_effort_requested": endpoint.reasoning_effort,
    }
    if not key:
        return {**base, "ok": False, "status": None, "error": f"Missing env var {endpoint.api_key_env}"}

    messages = prompt_for(profile, nonce, shared_prefix)
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": profile["max_output_tokens"],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if endpoint.reasoning_effort:
        payload["reasoning_effort"] = endpoint.reasoning_effort
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        **endpoint.extra_headers,
    }
    if affinity_key and endpoint.affinity_header:
        headers[endpoint.affinity_header] = affinity_key

    url = require_https_url(f"{endpoint.base_url}/chat/completions")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    first_activity: float | None = None
    first_answer: float | None = None
    last_chunk: float | None = None
    chunk_gaps: list[float] = []
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict[str, Any] = {}
    chunks = 0
    status: int | None = None
    response_request_id: str | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:  # nosemgrep: dynamic-urllib-use-detected
            status = response.status
            response_request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                event = line[5:].strip()
                if not event or event == "[DONE]":
                    continue
                try:
                    parsed = json.loads(event)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed.get("usage"), dict):
                    usage = parsed["usage"]
                content, reasoning = _delta_text(parsed)
                if not content and not reasoning:
                    continue
                now = time.perf_counter()
                chunks += 1
                if first_activity is None:
                    first_activity = now
                if content.strip() and first_answer is None:
                    first_answer = now
                if last_chunk is not None:
                    chunk_gaps.append((now - last_chunk) * 1000)
                last_chunk = now
                content_parts.append(content)
                reasoning_parts.append(reasoning)
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")
        return {
            **base,
            "ok": False,
            "status": status,
            "e2e_ms": (time.perf_counter() - start) * 1000,
            "error": body[:1200] or str(exc),
        }
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "status": status,
            "e2e_ms": (time.perf_counter() - start) * 1000,
            "error": repr(exc)[:1200],
        }

    end = time.perf_counter()
    prompt_tokens = _number(usage.get("prompt_tokens"))
    output_tokens = _number(usage.get("completion_tokens"))
    total_tokens = _number(usage.get("total_tokens"))
    cached_tokens = _usage_detail(usage, "prompt_tokens_details", "cached_tokens")
    reasoning_tokens = _usage_detail(usage, "completion_tokens_details", "reasoning_tokens")
    answer_tokens = None
    if output_tokens is not None and reasoning_tokens is not None:
        answer_tokens = max(0.0, output_tokens - reasoning_tokens)
    generation_seconds = end - first_activity if first_activity is not None else None
    answer_seconds = end - first_answer if first_answer is not None else None
    effective_tok_s = output_tokens / generation_seconds if output_tokens and generation_seconds else None
    answer_tok_s = answer_tokens / answer_seconds if answer_tokens and answer_seconds else None
    estimated_cost = estimate_token_cost(endpoint.pricing, prompt_tokens, cached_tokens, output_tokens)
    return {
        **base,
        "ok": True,
        "status": status,
        "response_request_id": response_request_id,
        "ttft_ms": (first_activity - start) * 1000 if first_activity is not None else None,
        "ttfa_ms": (first_answer - start) * 1000 if first_answer is not None else None,
        "e2e_ms": (end - start) * 1000,
        "median_chunk_gap_ms": statistics.median(chunk_gaps) if chunk_gaps else None,
        "chunks": chunks,
        "prompt_tokens": prompt_tokens,
        "cached_prompt_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "answer_tokens": answer_tokens,
        "total_tokens": total_tokens,
        "effective_output_tok_s": effective_tok_s,
        "answer_tok_s": answer_tok_s,
        "mean_generated_token_ms": 1000 / effective_tok_s if effective_tok_s else None,
        "mean_answer_token_ms": 1000 / answer_tok_s if answer_tok_s else None,
        "full_reasoning_text": "".join(reasoning_parts),
        "full_content_text": "".join(content_parts),
        "estimated_token_cost_usd": estimated_cost,
        "error": None,
    }


def estimate_token_cost(
    pricing: dict[str, Any] | None,
    prompt_tokens: float | None,
    cached_tokens: float | None,
    output_tokens: float | None,
) -> float | None:
    if not pricing or prompt_tokens is None or output_tokens is None:
        return None
    cached = min(prompt_tokens, cached_tokens or 0)
    uncached = prompt_tokens - cached
    return (
        uncached * float(pricing["input_per_million"])
        + cached * float(pricing["cached_input_per_million"])
        + output_tokens * float(pricing["output_per_million"])
    ) / 1_000_000


def run_wave(
    endpoint: Endpoint,
    profile: dict[str, Any],
    *,
    run_id: str,
    concurrency: int,
    attempts: int,
    cache_mode: str,
    timeout_seconds: int,
    nonce_prefix: str,
    shared_prefix: str | None = None,
    affinity_key: str | None = None,
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        for index in range(attempts):
            request_id = f"{run_id}-{endpoint.id}-{profile['id']}-c{concurrency}-{index + 1:04d}"
            nonce = f"{nonce_prefix}-{index:06d}"
            futures.append(
                executor.submit(
                    request_stream,
                    endpoint,
                    profile,
                    run_id=run_id,
                    request_id=request_id,
                    concurrency=concurrency,
                    cache_mode=cache_mode,
                    nonce=nonce,
                    timeout_seconds=timeout_seconds,
                    shared_prefix=shared_prefix,
                    affinity_key=affinity_key,
                )
            )
        for future in as_completed(futures):
            rows.append(future.result())
    wall_seconds = time.perf_counter() - started
    successful = [row for row in rows if row.get("ok")]
    total_output = sum(float(row.get("output_tokens") or 0) for row in successful)
    for row in rows:
        row["cell_wall_seconds"] = wall_seconds
        row["cell_requests_per_second"] = len(successful) / wall_seconds if wall_seconds else None
        row["cell_output_tokens_per_second"] = total_output / wall_seconds if wall_seconds else None
    return rows, wall_seconds


def shuffled_cells(config: dict[str, Any], endpoints: list[Endpoint]) -> list[tuple[Endpoint, dict[str, Any], int]]:
    cells = [
        (endpoint, profile, int(concurrency))
        for endpoint in endpoints
        for profile in config["profiles"]
        for concurrency in config["load"]["concurrency"]
    ]
    random.Random(config["load"].get("random_seed", 5303)).shuffle(cells)
    return cells
