#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from uuid import uuid4

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a synthetic analytics event through the Frigate bridge"
    )
    parser.add_argument(
        "--app-url",
        default="http://127.0.0.1:8080",
        help="Gym Sentry base URL",
    )
    parser.add_argument("--camera", default="gate01")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()

    end_ts = time.time()
    payload = {
        "event_id": f"test-{uuid4().hex}",
        "camera": args.camera,
        "label": "tailgating",
        "event_type": "TAILGATING_TEST",
        "start_ts": end_ts - 2,
        "end_ts": end_ts,
        "severity": "medium",
        "confidence": 0.99,
        "track_ids": [101, 102],
        "bbox": [100, 80, 320, 640],
        "metadata": {"synthetic": True, "source": "test_frigate_event.py"},
    }
    url = f"{args.app_url.rstrip('/')}/api/v1/frigate/events"
    try:
        response = httpx.post(url, json=payload, timeout=args.timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Request failed: {exc}")
        return 1
    print(json.dumps(response.json(), indent=2))
    return 0 if response.json().get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
