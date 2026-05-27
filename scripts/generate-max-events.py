#!/usr/bin/env python3
"""
Генератор тестовых событий для max-metrics.

Отправляет POST на /api/v1/events с случайными USER_ID и actions.
Использование: python3 scripts/generate-max-events.py --users 10 --per-user 5
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

# Типы действий для случайной генерации
ACTION_POOL = (
    "send_message",
    "pay_device",
    "open_chat",
    "read_message",
    "upload_file",
    "call_start",
)
USER_ID_START = 100


def random_actions(rng: random.Random) -> dict[str, int]:
    """Случайный набор 1–4 действий с целочисленными значениями."""
    actions: dict[str, int] = {}
    for action in rng.sample(ACTION_POOL, k=rng.randint(1, 4)):
        actions[action] = rng.randint(1, 5)
    return actions


def build_events(rng: random.Random, *, user_count: int, events_per_user: int) -> list[dict]:
    """Формирует список событий для user_count пользователей."""
    events = [
        {"USER_ID": uid, "actions": random_actions(rng)}
        for uid in range(USER_ID_START, USER_ID_START + user_count)
        for _ in range(events_per_user)
    ]
    rng.shuffle(events)
    return events


def post_event(url: str, payload: dict, api_key: str) -> tuple[int, str]:
    """POST одного события; возвращает (HTTP-код, тело ответа)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate MAX messenger test events")
    parser.add_argument("--url", default="http://127.0.0.1:9093/api/v1/events")
    parser.add_argument("--users", type=int, default=15)
    parser.add_argument("--per-user", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--delay", type=float, default=0.02)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    events = build_events(rng, user_count=args.users, events_per_user=args.per_user)
    print(
        f"events={len(events)} users={args.users} "
        f"ids={USER_ID_START}..{USER_ID_START + args.users - 1}",
        file=sys.stderr,
    )

    ok = failed = 0
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
