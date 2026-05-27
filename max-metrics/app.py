"""Приём событий MAX-мессенджера и экспорт метрик Prometheus."""

from __future__ import annotations

import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Info, generate_latest

LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
API_KEY = os.environ.get("MAX_METRICS_API_KEY", "").strip()
MAX_BODY_BYTES = int(os.environ.get("MAX_METRICS_MAX_BODY_BYTES", "65536"))

EVENTS = Counter(
    "max_messenger_events_total",
    "Счётчик действий пользователя в MAX (метки: user_id, action). "
    "Увеличивается при POST /api/v1/events.",
    ["user_id", "action"],
)
LAST_EVENT_TS = Gauge(
    "max_messenger_last_event_timestamp_seconds",
    "Метка времени (Unix, сек) последнего успешно принятого события",
)
SERVICE = Info(
    "max_messenger_service",
    "HTTP-эндпоинты сервиса приёма событий MAX (справка для Prometheus/Grafana)",
)
SERVICE.info(
    {
        "opisanie": "Приём событий MAX-мессенджера и экспорт метрик",
        "post_events": "POST /api/v1/events или /events — JSON: USER_ID, actions",
        "get_health": "GET /health — проверка сервиса",
        "get_metrics": "GET /metrics — только для Prometheus (скрейп)",
        "vneshniy_port": "9093 на хосте (переменная MAX_METRICS_PORT в .env)",
    }
)


def _parse_user_id(payload: dict[str, Any]) -> str:
    for key in ("USER_ID", "user_id", "UserId"):
        if key in payload and payload[key] is not None:
            return str(payload[key]).strip()
    raise ValueError("missing USER_ID")


def _parse_actions(payload: dict[str, Any]) -> dict[str, float]:
    raw = payload.get("actions")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("missing or empty actions")
    actions: dict[str, float] = {}
    for name, value in raw.items():
        action = str(name).strip()
        if not action:
            continue
        try:
            amount = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid action value for {action!r}") from exc
        if amount < 0:
            raise ValueError(f"negative value for action {action!r}")
        if amount > 0:
            actions[action] = amount
    if not actions:
        raise ValueError("no positive action values")
    return actions


def ingest_event(payload: dict[str, Any]) -> dict[str, Any]:
    user_id = _parse_user_id(payload)
    actions = _parse_actions(payload)
    for action, amount in actions.items():
        EVENTS.labels(user_id=user_id, action=action).inc(amount)
    LAST_EVENT_TS.set(time.time())
    return {"status": "ok", "user_id": user_id, "actions": actions}


class Handler(BaseHTTPRequestHandler):
    server_version = "MaxMetrics/1.0"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _authorized(self) -> bool:
        if not API_KEY:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer ") and auth[7:].strip() == API_KEY:
            return True
        if self.headers.get("X-API-Key", "").strip() == API_KEY:
            return True
        return False

    def _json(self, status: HTTPStatus, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "empty_body"})
            return None
        if length > MAX_BODY_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large"})
            return None
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return None
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "json_must_be_object"})
            return None
        return payload

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/health", "/healthz"):
            self._json(
                HTTPStatus.OK,
                {"status": "ok", "auth_required": bool(API_KEY)},
            )
            return
        if path == "/metrics":
            body = generate_latest()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path not in ("/api/v1/events", "/events"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            result = ingest_event(payload)
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._json(HTTPStatus.OK, result)


if __name__ == "__main__":
    print(
        f"max-metrics listen={LISTEN_HOST}:{LISTEN_PORT} "
        f"auth={'on' if API_KEY else 'off'}",
        flush=True,
    )
    HTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
