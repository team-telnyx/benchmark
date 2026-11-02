#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from url_safety import require_https_url


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def request_json(url: str, timeout: int = 120, resolve_ip: str | None = None):
    url = require_https_url(url)
    if resolve_ip:
        from urllib.parse import urlsplit

        host = urlsplit(url).hostname
        completed = subprocess.run(
            [
                "curl", "-sS", "--fail-with-body", "--max-time", str(timeout),
                "--resolve", f"{host}:443:{resolve_ip}", url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)
    with urllib.request.urlopen(url, timeout=timeout) as response:  # nosemgrep: dynamic-urllib-use-detected
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir")
    parser.add_argument("--resolve-ip", help="Resolve the run hostname to this IP with curl")
    args = parser.parse_args()
    output = Path(args.output_dir)
    manifest_path = output / "launch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_url = manifest["run_url"]
    status = request_json(f"{run_url}/status", resolve_ip=args.resolve_ip)
    (output / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    if not status.get("done"):
        print(json.dumps({"done": False, "status": status}, indent=2))
        return 2

    rows = []
    offset = 0
    while True:
        page = request_json(
            f"{run_url}/results-page.json?offset={offset}&limit=25",
            resolve_ip=args.resolve_ip,
        )
        rows.extend(page["rows"])
        if page.get("nextOffset") is None:
            break
        offset = int(page["nextOffset"])

    with (output / "raw_requests.jsonl").open("w", encoding="utf-8") as handle:
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
    manifest["exported_at"] = utc_now()
    manifest["attempted"] = len(rows)
    manifest["successful_with_visible_answer"] = len(clean)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"done": True, "attempted": len(rows), "successful": len(clean), "output_dir": str(output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
