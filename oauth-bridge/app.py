"""
OAuth2-мост: Bitrix24 → Grafana и oauth2-proxy (Prometheus UI).

Реализует минимальный OIDC-провайдер поверх Bitrix24 OAuth:
  GET  /.well-known/openid-configuration, /.well-known/jwks.json
  GET  /oauth/authorize  — редирект на Bitrix24
  GET  /oauth/callback   — возврат кода в Grafana / oauth2-proxy
  POST /oauth/token      — обмен code на токены Bitrix + id_token для proxy
  GET  /userinfo         — профиль по Bearer access_token
  GET  /health           — проверка работы сервиса

Публичный URL задаётся переменной OAUTH_BRIDGE_PUBLIC_URL в .env.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _require_env(name: str) -> str:
    """Читает обязательную переменную окружения; при отсутствии завершает процесс."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"Переменная окружения {name} обязательна. "
            "Задайте её в .env и запускайте: docker compose --env-file .env up -d"
        )
    return value


BRIDGE_PUBLIC_URL = _require_env("OAUTH_BRIDGE_PUBLIC_URL").rstrip("/")
BRIDGE_CALLBACK_URL = f"{BRIDGE_PUBLIC_URL}/oauth/callback"
CLIENT_ID = os.environ.get("OAUTH_BRIDGE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("OAUTH_BRIDGE_CLIENT_SECRET", "")
BITRIX_CLIENT_ID = os.environ.get("BITRIX_CLIENT_ID", CLIENT_ID)
BITRIX_CLIENT_SECRET = os.environ.get("BITRIX_CLIENT_SECRET", CLIENT_SECRET)
BITRIX_AUTH_BASE_URL = _require_env("BITRIX_AUTH_BASE_URL")
BITRIX_TOKEN_URL = _require_env("BITRIX_TOKEN_URL")
BITRIX_OAUTH_SCOPE = os.environ.get("BITRIX_OAUTH_SCOPE", "user,user_brief,profile,auth")
OAUTH_EMAIL_DOMAIN = os.environ.get("OAUTH_EMAIL_DOMAIN", "local")
DEFAULT_BITRIX_REST_BASE = _require_env("BITRIX_REST_BASE").rstrip("/") + "/"
_grafana_root = _require_env("GRAFANA_ROOT_URL").rstrip("/")
GRAFANA_OAUTH_REDIRECT_URL = os.environ.get(
    "GRAFANA_OAUTH_REDIRECT_URL", f"{_grafana_root}/login/generic_oauth"
)
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
PENDING_COOKIE = "oauth_bridge_pending"
BITRIX_TOKEN_CACHE_TTL = int(os.environ.get("BITRIX_TOKEN_CACHE_TTL", "3600"))

_bitrix_userinfo_cache: dict[str, tuple[dict, float]] = {}
_bitrix_rest_endpoints: dict[str, tuple[list[str], float]] = {}
_bitrix_token_payloads: dict[str, tuple[dict, float]] = {}

_JWT_KID = "oauth-bridge-1"
_JWT_ALG = "RS256"


def _load_jwt_private_key():
    """Загружает RSA-ключ из PEM или генерирует временный ключ при старте."""
    pem = os.environ.get("OAUTH_BRIDGE_JWT_PRIVATE_KEY_PEM", "").strip()
    if pem:
        return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    print(
        "oauth-bridge: OAUTH_BRIDGE_JWT_PRIVATE_KEY_PEM not set, generating ephemeral RSA key",
        flush=True,
    )
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


_JWT_PRIVATE_KEY = _load_jwt_private_key()
_JWT_PUBLIC_KEY = _JWT_PRIVATE_KEY.public_key()


def _jwks_document() -> dict:
    """Формирует JWKS для проверки id_token (oauth2-proxy)."""
    numbers = _JWT_PUBLIC_KEY.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": _JWT_ALG,
                "kid": _JWT_KID,
                "n": jwt.utils.base64url_encode(
                    numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
                ).decode("ascii"),
                "e": jwt.utils.base64url_encode(
                    numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
                ).decode("ascii"),
            }
        ]
    }


def _make_id_token(userinfo: dict, *, client_id: str, expires_in: int) -> str:
    """Подписывает JWT id_token с claims пользователя для OIDC-клиента."""
    now = int(time.time())
    claims = {
        "iss": BRIDGE_PUBLIC_URL,
        "sub": str(userinfo.get("sub") or ""),
        "aud": client_id or CLIENT_ID,
        "iat": now,
        "exp": now + max(int(expires_in), 60),
        "email": userinfo.get("email"),
        "email_verified": userinfo.get("email_verified", True),
        "name": userinfo.get("name"),
        "preferred_username": userinfo.get("preferred_username"),
        "locale": userinfo.get("locale", "ru-RU"),
    }
    if userinfo.get("given_name"):
        claims["given_name"] = userinfo["given_name"]
    if userinfo.get("family_name"):
        claims["family_name"] = userinfo["family_name"]
    return jwt.encode(
        claims,
        _JWT_PRIVATE_KEY,
        algorithm=_JWT_ALG,
        headers={"kid": _JWT_KID},
    )


def _enrich_token_response(token_payload: dict, *, client_id: str) -> dict:
    """Добавляет id_token в ответ /oauth/token, если его нет (нужно oauth2-proxy)."""
    if token_payload.get("id_token"):
        return token_payload
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return token_payload
    userinfo = _get_cached_userinfo(access_token)
    if not userinfo:
        try:
            userinfo = _cache_bitrix_userinfo(access_token, token_payload)
        except (HTTPError, URLError, ValueError, json.JSONDecodeError, TimeoutError):
            userinfo = None
    if not userinfo:
        return token_payload
    try:
        expires_in = int(token_payload.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    enriched = dict(token_payload)
    enriched["id_token"] = _make_id_token(
        userinfo, client_id=client_id or CLIENT_ID, expires_in=expires_in
    )
    enriched.setdefault("token_type", "Bearer")
    return enriched


def _normalize_redirect(uri: str) -> str:
    """Убирает query и хвостовой слэш для сравнения redirect_uri."""
    return uri.split("?", 1)[0].rstrip("/")


def _allowed_redirects() -> frozenset[str]:
    """Список разрешённых redirect_uri: Grafana и Prometheus (oauth2-proxy)."""
    allowed = {_normalize_redirect(GRAFANA_OAUTH_REDIRECT_URL)}
    prom = os.environ.get("PROMETHEUS_PUBLIC_URL", "").strip().rstrip("/")
    if prom:
        allowed.add(_normalize_redirect(f"{prom}/oauth2/callback"))
    return allowed


def _redirect_allowed(uri: str) -> bool:
    """Проверяет, что redirect_uri клиента OAuth разрешён."""
    return _normalize_redirect(uri) in _allowed_redirects()


def _decode_pending_cookie(raw: str) -> dict | None:
    """Декодирует cookie с сохранённым redirect_uri между authorize и callback."""
    try:
        pad = "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw + pad).decode("utf-8"))
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _client_secret_configured() -> bool:
    """True, если задан реальный BITRIX_CLIENT_SECRET (не placeholder)."""
    secret = (BITRIX_CLIENT_SECRET or "").strip()
    return bool(secret) and secret not in ("changeme", "unused")


def _normalize_rest_endpoint(endpoint: str) -> str:
    """Приводит URL REST Bitrix к виду .../rest/."""
    value = (endpoint or "").strip() or DEFAULT_BITRIX_REST_BASE
    if not value.endswith("/"):
        value += "/"
    if not value.endswith("rest/"):
        value = value.rstrip("/") + "/rest/"
    return value


def _remember_token_payload(access_token: str, token_payload: dict) -> None:
    """Кэширует ответ Bitrix token и REST-endpoints для последующих вызовов."""
    _bitrix_token_payloads[access_token] = (
        token_payload,
        time.time() + BITRIX_TOKEN_CACHE_TTL,
    )
    expires = time.time() + BITRIX_TOKEN_CACHE_TTL
    endpoints: list[str] = []
    for key in ("client_endpoint", "server_endpoint"):
        raw = str(token_payload.get(key) or "").strip()
        if raw:
            ep = _normalize_rest_endpoint(raw)
            if ep not in endpoints:
                endpoints.append(ep)
    if DEFAULT_BITRIX_REST_BASE not in endpoints:
        endpoints.append(DEFAULT_BITRIX_REST_BASE)
    _bitrix_rest_endpoints[access_token] = (endpoints, expires)


def _get_token_payload(access_token: str) -> dict | None:
    """Возвращает кэшированный payload токена Bitrix или None при истечении TTL."""
    entry = _bitrix_token_payloads.get(access_token)
    if not entry:
        return None
    payload, expires = entry
    if time.time() > expires:
        _bitrix_token_payloads.pop(access_token, None)
        return None
    return payload


def _rest_endpoints_for_token(access_token: str) -> list[str]:
    """Список REST URL для токена (из token response или DEFAULT_BITRIX_REST_BASE)."""
    entry = _bitrix_rest_endpoints.get(access_token)
    if entry:
        endpoints, expires = entry
        if time.time() <= expires:
            return endpoints
        _bitrix_rest_endpoints.pop(access_token, None)
    return [DEFAULT_BITRIX_REST_BASE]


def _bitrix_rest_payload(
    rest_base: str,
    method: str,
    access_token: str,
    params: dict | None = None,
    *,
    use_get: bool = False,
) -> dict:
    """Выполняет один HTTP-запрос к Bitrix REST и возвращает JSON."""
    query = {"auth": access_token, **(params or {})}
    url = f"{rest_base.rstrip('/')}/{method}.json?{urlencode(query)}"
    if use_get:
        req = Request(url, method="GET", headers={"Accept": "application/json"})
    else:
        req = Request(
            url,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise ValueError(f"HTTP {exc.code} on {method}@{rest_base}: {body}") from exc


def _bitrix_rest_call(
    rest_base: str, method: str, access_token: str, params: dict | None = None
) -> dict:
    """Вызывает REST-метод Bitrix; пробует POST, затем GET при ошибке."""
    last_error: ValueError | None = None
    for use_get in (False, True):
        try:
            payload = _bitrix_rest_payload(
                rest_base, method, access_token, params, use_get=use_get
            )
        except ValueError as exc:
            last_error = exc
            continue
        if "error" in payload:
            raise ValueError(payload.get("error_description") or payload["error"])
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return result[0]
        raise ValueError(f"Bitrix {method} returned no result")
    raise last_error or ValueError(f"Bitrix {method} failed")


def _bitrix_rest_list(
    rest_base: str, method: str, access_token: str, params: dict | None = None
) -> list:
    """REST-вызов, ожидающий list в поле result (например scope)."""
    payload = _bitrix_rest_payload(rest_base, method, access_token, params)
    if "error" in payload:
        raise ValueError(payload.get("error_description") or payload["error"])
    result = payload.get("result")
    if isinstance(result, list):
        return result
    raise ValueError(f"Bitrix {method} returned no list")


def _log_bitrix_token_diagnostics(access_token: str, token_payload: dict) -> None:
    """Пишет в лог scope() Bitrix для диагностики прав приложения."""
    scopes: list[str] = []
    for rest_base in _rest_endpoints_for_token(access_token):
        try:
            scopes = _bitrix_rest_list(rest_base, "scope", access_token, {"full": True})
            print(f"bitrix scope() on {rest_base}: {scopes}", flush=True)
            break
        except ValueError as exc:
            print(f"bitrix scope() failed on {rest_base}: {exc}", flush=True)
    if scopes and "user" not in scopes and "user_brief" not in scopes:
        print(
            "bitrix: в выданных правах нет user/user_brief — "
            "включите «Пользователи» в настройках приложения Bitrix24 и переустановите приложение",
            flush=True,
        )


def _format_oidc_userinfo(
    *,
    sub: str,
    email: str,
    login: str,
    first: str = "",
    last: str = "",
    full_name: str = "",
    picture: str = "",
) -> dict:
    """OpenID Connect UserInfo (RFC 7519 / OIDC Core §5.3)."""
    name = full_name or " ".join(part for part in (first, last) if part).strip() or login
    claims: dict = {
        "sub": sub,
        "name": name,
        "preferred_username": login,
        "email": email,
        "email_verified": True,
        "locale": "ru-RU",
        "updated_at": int(time.time()),
        # Grafana Generic OAuth (не OIDC-стандарт, но нужен мосту)
        "login": login,
    }
    if first:
        claims["given_name"] = first
    if last:
        claims["family_name"] = last
    if picture:
        claims["picture"] = picture
    return claims


def _bitrix_user_to_userinfo(user: dict) -> dict:
    """Преобразует объект user из Bitrix REST в OIDC UserInfo."""
    user_id = str(user.get("ID") or user.get("id") or "")
    email = str(user.get("EMAIL") or user.get("email") or "").strip()
    first = str(user.get("NAME") or user.get("name") or "").strip()
    last = str(user.get("LAST_NAME") or user.get("last_name") or "").strip()
    login = str(user.get("LOGIN") or user.get("login") or "").strip()
    picture = str(user.get("PERSONAL_PHOTO") or user.get("personal_photo") or "").strip()
    if not email:
        email = f"user{user_id}@{OAUTH_EMAIL_DOMAIN}" if user_id else f"{login}@{OAUTH_EMAIL_DOMAIN}"
    if not login:
        login = email.split("@")[0]
    return _format_oidc_userinfo(
        sub=user_id or login,
        email=email,
        login=login,
        first=first,
        last=last,
        picture=picture,
    )


def _userinfo_from_bitrix_token_payload(token_payload: dict) -> dict:
    """Минимальный UserInfo из полей token response, если REST profile недоступен."""
    member_id = str(token_payload.get("member_id") or "").strip()
    user_id = str(token_payload.get("user_id") or "").strip()
    scope = str(token_payload.get("scope") or "").strip()
    sub = user_id or member_id or secrets.token_hex(8)
    login = f"bitrix_{sub[:24]}"
    email = f"{login}@{OAUTH_EMAIL_DOMAIN}"
    name = login.replace("_", " ").title()
    if scope:
        name = f"{name} ({scope.split(',')[0]})"
    return _format_oidc_userinfo(sub=sub, email=email, login=login, full_name=name)


def _cache_bitrix_userinfo(
    access_token: str, token_payload: dict | None = None
) -> dict | None:
    """Загружает профиль из Bitrix REST и сохраняет в кэше по access_token."""
    if token_payload:
        _remember_token_payload(access_token, token_payload)

    info: dict | None = None
    try:
        for rest_base in _rest_endpoints_for_token(access_token):
            for method in ("profile", "user.current"):
                try:
                    info = _bitrix_user_to_userinfo(
                        _bitrix_rest_call(rest_base, method, access_token)
                    )
                    print(f"bitrix {method} ok on {rest_base}", flush=True)
                    break
                except (HTTPError, URLError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
                    print(f"bitrix {method} failed on {rest_base}: {exc}", flush=True)
            if info:
                break
    except (HTTPError, URLError, ValueError, json.JSONDecodeError, TimeoutError):
        pass

    if not info and token_payload:
        user_id = str(token_payload.get("user_id") or "").strip()
        if user_id:
            for rest_base in _rest_endpoints_for_token(access_token):
                try:
                    user = _bitrix_rest_call(
                        rest_base, "user.get", access_token, {"ID": user_id}
                    )
                    info = _bitrix_user_to_userinfo(user)
                    break
                except (HTTPError, URLError, ValueError, json.JSONDecodeError, TimeoutError):
                    continue

    if not info:
        payload = token_payload or _get_token_payload(access_token)
        if payload:
            info = _userinfo_from_bitrix_token_payload(payload)
            print(
                "fallback userinfo from token "
                f"(member_id={payload.get('member_id')}, scope={payload.get('scope')})",
                flush=True,
            )

    if not info:
        return None

    _bitrix_userinfo_cache[access_token] = (info, time.time() + BITRIX_TOKEN_CACHE_TTL)
    return info


def _get_cached_userinfo(access_token: str) -> dict | None:
    """Возвращает UserInfo из кэша или None при истечении TTL."""
    entry = _bitrix_userinfo_cache.get(access_token)
    if not entry:
        return None
    info, expires = entry
    if time.time() > expires:
        _bitrix_userinfo_cache.pop(access_token, None)
        return None
    return info


def userinfo_from_token(access_token: str) -> dict:
    """Публичная точка: UserInfo по access_token Bitrix (с кэшем)."""
    cached = _get_cached_userinfo(access_token)
    if cached:
        return cached
    info = _cache_bitrix_userinfo(access_token)
    if info:
        return info
    raise ValueError("invalid_token")


class Handler(BaseHTTPRequestHandler):
    """HTTP-обработчик OIDC-моста Bitrix24."""

    server_version = "OAuthBridge/2.0"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _json(
        self,
        status: HTTPStatus,
        body: dict | list,
        *,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        """Отправляет JSON-ответ с no-store заголовками."""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bearer_userinfo(self) -> dict | None:
        """Извлекает Bearer-токен и возвращает UserInfo или отправляет 401/502."""
        auth = self.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            self._json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "invalid_token", "error_description": "Bearer access token required"},
                extra_headers=[("WWW-Authenticate", 'Bearer error="invalid_token"')],
            )
            return None
        token = auth[7:].strip()
        if not token:
            self._json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "invalid_token", "error_description": "Empty bearer token"},
                extra_headers=[("WWW-Authenticate", 'Bearer error="invalid_token"')],
            )
            return None
        try:
            return userinfo_from_token(token)
        except ValueError:
            self._json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "invalid_token", "error_description": "Access token not accepted"},
                extra_headers=[("WWW-Authenticate", 'Bearer error="invalid_token"')],
            )
            return None
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as exc:
            self._json(HTTPStatus.BAD_GATEWAY, {"error": "userinfo_failed", "detail": str(exc)})
            return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path in ("/health", "/healthz"):
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "callback": BRIDGE_CALLBACK_URL,
                    "client_secret_configured": _client_secret_configured(),
                },
            )
            return

        if path == "/.well-known/jwks.json":
            self._json(HTTPStatus.OK, _jwks_document())
            return

        if path == "/.well-known/openid-configuration":
            self._json(
                HTTPStatus.OK,
                {
                    "issuer": BRIDGE_PUBLIC_URL,
                    "authorization_endpoint": f"{BRIDGE_PUBLIC_URL}/oauth/authorize",
                    "token_endpoint": f"{BRIDGE_PUBLIC_URL}/oauth/token",
                    "userinfo_endpoint": f"{BRIDGE_PUBLIC_URL}/userinfo",
                    "jwks_uri": f"{BRIDGE_PUBLIC_URL}/.well-known/jwks.json",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code"],
                    "scopes_supported": [
                        "openid",
                        "profile",
                        "email",
                        "user",
                        "user_brief",
                    ],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": [_JWT_ALG],
                    "claims_supported": [
                        "sub",
                        "name",
                        "given_name",
                        "family_name",
                        "preferred_username",
                        "email",
                        "email_verified",
                        "picture",
                        "locale",
                        "updated_at",
                    ],
                },
            )
            return

        if path == "/oauth/authorize":
            self._authorize(parse_qs(urlparse(self.path).query))
            return

        if path == "/oauth/callback":
            self._oauth_callback(parse_qs(urlparse(self.path).query))
            return

        if path == "/userinfo/emails":
            info = self._bearer_userinfo()
            if info is None:
                return
            email = str(info.get("email") or "").strip()
            if not email:
                self._json(HTTPStatus.OK, [])
                return
            self._json(
                HTTPStatus.OK,
                [{"email": email, "primary": True, "is_primary": True, "verified": True}],
            )
            return

        if path == "/userinfo":
            info = self._bearer_userinfo()
            if info is not None:
                self._json(HTTPStatus.OK, info)
            return

        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path.rstrip("/") != "/oauth/token":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        self._exchange_token(parse_qs(raw))

    def _authorize(self, query: dict[str, list[str]]) -> None:
        """Старт OAuth: сохраняет redirect клиента и перенаправляет на Bitrix24."""
        client_redirect = (query.get("redirect_uri") or [""])[0]
        state = (query.get("state") or [""])[0]
        client_id = (query.get("client_id") or [BITRIX_CLIENT_ID])[0]
        if not client_redirect:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "missing_redirect_uri"})
            return
        if not _redirect_allowed(client_redirect):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_redirect_uri"})
            return

        pending = json.dumps(
            {"redirect_uri": client_redirect, "state": state, "created": time.time()}
        )
        cookie_value = base64.urlsafe_b64encode(pending.encode("utf-8")).decode("ascii")
        params = {
            "user_lang": "ru",
            "client_id": client_id,
            "redirect_uri": BRIDGE_CALLBACK_URL,
            "scope": BITRIX_OAUTH_SCOPE,
            "response_type": "code",
            "mode": "page",
        }
        if state:
            params["state"] = state
        base = BITRIX_AUTH_BASE_URL.split("?", 1)[0].rstrip("/")
        location = f"{base}/?{urlencode(params)}"
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header(
            "Set-Cookie",
            f"{PENDING_COOKIE}={cookie_value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=600",
        )
        self.end_headers()

    def _pending_from_cookie(self) -> dict | None:
        """Читает pending redirect из HttpOnly cookie."""
        prefix = f"{PENDING_COOKIE}="
        for part in self.headers.get("Cookie", "").split(";"):
            part = part.strip()
            if part.startswith(prefix):
                return _decode_pending_cookie(part[len(prefix) :])
        return None

    def _oauth_callback(self, query: dict[str, list[str]]) -> None:
        """Callback Bitrix: передаёт code обратно в Grafana или oauth2-proxy."""
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        if not code:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "missing_code"})
            return

        redirect_target = GRAFANA_OAUTH_REDIRECT_URL
        pending = self._pending_from_cookie()
        if pending:
            candidate = str(pending.get("redirect_uri") or "").strip()
            if candidate and _redirect_allowed(candidate):
                redirect_target = candidate

        params: dict[str, str] = {"code": code}
        if state:
            params["state"] = state
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", f"{redirect_target}?{urlencode(params)}")
        self.send_header(
            "Set-Cookie",
            f"{PENDING_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )
        self.end_headers()

    def _exchange_token(self, params: dict[str, list[str]]) -> None:
        """POST /oauth/token: обмен code на токены Bitrix и дополнение id_token."""
        if not _client_secret_configured():
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_client", "error_description": "BITRIX_CLIENT_SECRET not set"},
            )
            return

        grant_type = (params.get("grant_type") or [""])[0]
        code = (params.get("code") or [""])[0]
        if grant_type != "authorization_code" or not code:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return

        body = urlencode(
            {
                "grant_type": grant_type,
                "client_id": (params.get("client_id") or [BITRIX_CLIENT_ID])[0],
                "client_secret": (params.get("client_secret") or [BITRIX_CLIENT_SECRET])[0],
                "code": code,
                "redirect_uri": BRIDGE_CALLBACK_URL,
            }
        ).encode("utf-8")
        req = Request(
            BITRIX_TOKEN_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        oauth_client_id = (params.get("client_id") or [CLIENT_ID])[0]
        try:
            with urlopen(req, timeout=20) as resp:
                raw_payload = resp.read()
                try:
                    token_payload = json.loads(raw_payload.decode("utf-8"))
                    access_token = str(token_payload.get("access_token") or "").strip()
                    if access_token:
                        print(
                            "bitrix token: "
                            f"scope={token_payload.get('scope')!r} "
                            f"user_id={token_payload.get('user_id')!r} "
                            f"member_id={token_payload.get('member_id')!r} "
                            f"client_endpoint={token_payload.get('client_endpoint')!r}",
                            flush=True,
                        )
                        _log_bitrix_token_diagnostics(access_token, token_payload)
                        _cache_bitrix_userinfo(access_token, token_payload)
                        token_payload = _enrich_token_response(
                            token_payload, client_id=oauth_client_id
                        )
                        if token_payload.get("id_token"):
                            print("oauth-bridge: id_token added for oauth2-proxy", flush=True)
                        raw_payload = json.dumps(token_payload, ensure_ascii=False).encode(
                            "utf-8"
                        )
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw_payload)))
                self.end_headers()
                self.wfile.write(raw_payload)
        except HTTPError as exc:
            err_body = exc.read()
            print(f"bitrix token error {exc.code}: {err_body[:500]!r}", flush=True)
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)


if __name__ == "__main__":
    print(
        f"oauth-bridge public={BRIDGE_PUBLIC_URL} callback={BRIDGE_CALLBACK_URL} "
        f"grafana_redirect={GRAFANA_OAUTH_REDIRECT_URL} "
        f"listen={LISTEN_HOST}:{LISTEN_PORT}",
        flush=True,
    )
    HTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
