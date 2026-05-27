#!/usr/bin/env python3
"""Генерация тестовых событий MAX для max-metrics."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

ACTION_POOL = [
    "send_message",
    "pay_device",
    "open_chat",
    "read_message",
    "upload_file",
    "call_start",
]
USER_ID_START = 100
USER_ID_END = 120


def random_actions(rng: random.Random) -> dict[str, int | float]:
    count = rng.randint(1, 4)
    actions: dict[str, int | float] = {}
    for action in rng.sample(ACTION_POOL, k=count):
        actions[action] = rng.randint(1, 5)
    return actions


def build_events(
    rng: random.Random,
    *,
    user_count: int,
    events_per_user: int,
) -> list[dict]:
    user_ids = list(range(USER_ID_START, USER_ID_START + user_count))
    events: list[dict] = []
    for user_id in user_ids:
        for _ in range(events_per_user):
            events.append(
                {
                    "USER_ID": user_id,
                    "actions": random_actions(rng),
                }
            )
    rng.shuffle(events)
    return events


def post_event(url: str, payload: dict, api_key: str) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate MAX messenger test events")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:9093/api/v1/events",
        help="max-metrics ingest URL",
    )
    parser.add_argument("--users", type=int, default=15, help="number of distinct USER_IDs")
    parser.add_argument(
        "--per-user",
        type=int,
        default=4,
        help="events per user",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument("--delay", type=float, default=0.02, help="delay between POSTs (sec)")
    parser.add_argument("--api-key", default="", help="MAX_METRICS_API_KEY if set")
    parser.add_argument("--dry-run", action="store_true", help="print events only, no POST")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    events = build_events(rng, user_count=args.users, events_per_user=args.per_user)
    print(
        f"events={len(events)} users={args.users} "
        f"user_ids={USER_ID_START}..{USER_ID_START + args.users - 1}",
        file=sys.stderr,
    )

    ok = 0
    failed = 0
    for i, payload in enumerate(events, 1):
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False))
            ok += 1
            continue
        status, body = post_event(args.url, payload, args.api_key)
        if 200 <= status < 300:
            ok += 1
            if i <= 3 or i == len(events):
                print(f"[{i}/{len(events)}] OK user={payload['USER_ID']} {payload['actions']}")
        else:
            failed += 1
            print(f"[{i}/{len(events)}] FAIL {status}: {body[:200]}", file=sys.stderr)
        if args.delay > 0:
            time.sleep(args.delay)

    print(f"done: ok={ok} failed={failed}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
