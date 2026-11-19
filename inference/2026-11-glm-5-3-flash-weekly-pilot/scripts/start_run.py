#!/usr/bin/env python3
"""Start a Stateful Actor inference benchmark run."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

SUITES: dict[str, list[dict[str, Any]]] = {
    "deepseek_v4_flash_0731": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "baseten",
            "baseUrl": "https://inference.baseten.co/v1",
            "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "apiKeyEnv": "BASETEN_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/deepseek-v4-flash-0731",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
    ],
    "deepseek_v4_flash_0731_no_thinking": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "extraBody": {"enable_thinking": False},
        },
        {
            "name": "baseten",
            "baseUrl": "https://inference.baseten.co/v1",
            "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "apiKeyEnv": "BASETEN_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "extraBody": {"enable_thinking": False},
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "extraBody": {"enable_thinking": False},
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/deepseek-v4-flash-0731",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "extraBody": {"enable_thinking": False},
        },
    ],
    "deepseek_v4_flash_0731_reasoning_disabled": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "extraBody": {"thinking": {"type": "disabled"}},
        },
        {
            "name": "baseten",
            "baseUrl": "https://inference.baseten.co/v1",
            "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "apiKeyEnv": "BASETEN_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "extraBody": {"thinking": {"type": "disabled"}},
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "extraBody": {"reasoning": {"enabled": False}},
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/deepseek-v4-flash-0731",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "extraBody": {"reasoning_effort": "none"},
        },
    ],
    "qwen38_27b": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "Qwen/Qwen3.8-27B",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "baseten",
            "baseUrl": "https://inference.baseten.co/v1",
            "model": "Qwen/Qwen3.8-27B",
            "apiKeyEnv": "BASETEN_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "Qwen/Qwen3.8-27B",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/qwen3p8-27b",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
    ],
    "qwen38_27b_telnyx_only": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "Qwen/Qwen3.8-27B",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
    ],
    "kimi_k3": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "moonshotai/Kimi-K3",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "baseten",
            "baseUrl": "https://inference.baseten.co/v1",
            "model": "moonshotai/Kimi-K3",
            "apiKeyEnv": "BASETEN_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "moonshotai/Kimi-K3",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/kimi-k3",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
    ],
    "kimi_k31_pro": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "moonshotai/Kimi-K3.1-Pro",
            "apiKeyEnv": "TELNYX_API_KEY",
            "maxTokensField": "max_completion_tokens",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "reasoningEffort": "max",
            "extraBody": {"temperature": 1, "top_p": 0.95},
        },
        {
            "name": "baseten",
            "baseUrl": "https://inference.baseten.co/v1",
            "model": "moonshotai/Kimi-K3.1-Pro",
            "apiKeyEnv": "BASETEN_API_KEY",
            "maxTokensField": "max_completion_tokens",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "reasoningEffort": "max",
            "extraBody": {"temperature": 1, "top_p": 0.95},
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "moonshotai/Kimi-K3.1-Pro",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "maxTokensField": "max_completion_tokens",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "reasoningEffort": "max",
            "extraBody": {"temperature": 1, "top_p": 0.95},
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/kimi-k3p1-pro",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "maxTokensField": "max_completion_tokens",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "reasoningEffort": "max",
            "extraBody": {"temperature": 1, "top_p": 0.95},
        },
    ],
    "minimax_m3": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "MiniMaxAI/MiniMax-M3-MXFP8",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "MiniMaxAI/MiniMax-M3",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/minimax-m3",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
    ],
    "glm_52": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "zai-org/GLM-5.2",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "baseten",
            "baseUrl": "https://inference.baseten.co/v1",
            "model": "zai-org/GLM-5.2",
            "apiKeyEnv": "BASETEN_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "zai-org/GLM-5.2",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/glm-5p2",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
    ],
    "glm_53_flash": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "zai-org/GLM-5.3-Flash",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "reasoningEffort": "low",
        },
        {
            "name": "baseten",
            "baseUrl": "https://inference.baseten.co/v1",
            "model": "zai-org/GLM-5.3-Flash",
            "apiKeyEnv": "BASETEN_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "reasoningEffort": "low",
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "zai-org/GLM-5.3-Flash",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "reasoningEffort": "low",
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/glm-5p3-flash",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
            "reasoningEffort": "low",
        },
    ],
    "glm_53": [
        {
            "name": "telnyx",
            "baseUrl": "https://api.telnyx.com/v2/ai",
            "model": "zai-org/GLM-5.3",
            "apiKeyEnv": "TELNYX_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "baseten",
            "baseUrl": "https://inference.baseten.co/v1",
            "model": "zai-org/GLM-5.3",
            "apiKeyEnv": "BASETEN_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "together",
            "baseUrl": "https://api.together.xyz/v1",
            "model": "zai-org/GLM-5.3",
            "apiKeyEnv": "TOGETHER_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
        {
            "name": "fireworks",
            "baseUrl": "https://api.fireworks.ai/inference/v1",
            "model": "accounts/fireworks/models/glm-5p3",
            "apiKeyEnv": "FIREWORKS_API_KEY",
            "includeStreamOptions": True,
            "retryWithoutStreamOptions": True,
        },
    ],
}


def parse_prompt(path: Path, prompt_dir: Path) -> dict[str, Any]:
    text = path.read_text()
    name = path.stem
    input_target = "100k" if "_100k_" in name else "10k" if "_10k_" in name else "1k"
    output_target = "1k" if name.endswith("_1k_out") else "100"
    max_tokens = 1200 if output_target == "1k" else 120

    try:
        system = text.split("## System", 1)[1].split("## User", 1)[0].strip()
        user = text.split("## User", 1)[1].strip()
    except IndexError as exc:
        raise SystemExit(f"Could not parse prompt file: {path}") from exc

    marker_start = user.find("{fixture:")
    marker_end = user.find("}", marker_start)
    if marker_start == -1 or marker_end == -1:
        raise SystemExit(f"No fixture marker found in {path}")
    fixture_name = user[marker_start + len("{fixture:") : marker_end]
    fixture_path = prompt_dir / fixture_name
    if not fixture_path.exists():
        fixture_path = prompt_dir / Path(fixture_name).name
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


def load_profiles(prompt_dir: Path, selected: list[str] | None) -> list[dict[str, Any]]:
    profiles = []
    for path in sorted(prompt_dir.glob("[0-9][0-9]_*.md")):
        if selected and path.stem not in selected:
            continue
        profiles.append(parse_prompt(path, prompt_dir))
    if not profiles:
        raise SystemExit("No prompt profiles loaded")
    return profiles


def post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="Deployed actor base URL")
    parser.add_argument("--run-id", default=f"inference-{int(time.time())}")
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPT_DIR)
    parser.add_argument("--profiles", nargs="*")
    parser.add_argument("--suites", nargs="+", choices=sorted(SUITES), default=sorted(SUITES))
    parser.add_argument("--providers", nargs="*", help="Limit each selected suite to these provider names")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--short-max-tokens", type=int)
    parser.add_argument("--long-max-tokens", type=int)
    parser.add_argument("--timeout-ms", type=int, default=900_000)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--random-seed", type=int, help="Seed used to randomize comparable provider/profile cells")
    parser.add_argument("--token-usage-probe", action="store_true", help="Fill missing streamed token usage with a matched non-streamed usage probe")
    parser.add_argument("--manual", action="store_true", help="Start the run without scheduling actor alarms")
    parser.add_argument("--write-payload", type=Path)
    args = parser.parse_args()

    profiles = load_profiles(args.prompt_dir, args.profiles)
    for profile in profiles:
        if profile["outputTarget"] == "1k" and args.long_max_tokens:
            profile["maxTokens"] = args.long_max_tokens
        elif profile["outputTarget"] == "100" and args.short_max_tokens:
            profile["maxTokens"] = args.short_max_tokens

    suites = []
    for suite in args.suites:
        providers = SUITES[suite]
        if args.providers:
            providers = [provider for provider in providers if provider["name"] in args.providers]
        if not providers:
            raise SystemExit(f"No providers selected for suite {suite}")
        suites.append({"suite": suite, "providers": providers})

    payload = {
        "repeats": args.repeats,
        "timeoutMs": args.timeout_ms,
        "batchSize": args.batch_size,
        "tokenUsageProbe": args.token_usage_probe,
        "autoStart": not args.manual,
        "profiles": profiles,
        "suites": suites,
    }
    if args.random_seed is not None:
        payload["phases"] = [{
            "name": "weekly-randomized",
            "repeats": args.repeats,
            "batchSize": args.batch_size,
            "randomSeed": args.random_seed,
            "profiles": profiles,
            "suites": suites,
        }]
    if args.write_payload:
        args.write_payload.parent.mkdir(parents=True, exist_ok=True)
        args.write_payload.write_text(json.dumps(payload, indent=2))

    url = f"{args.base_url.rstrip('/')}/runs/{args.run_id}/start"
    response = post_json(url, payload, timeout=120)
    print(json.dumps({"run_id": args.run_id, "response": response}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
