"""Environment-driven configuration for SentinelIQ AI Ops."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.1"

VALID_BACKENDS = {"json", "sqlite", "postgres"}
VALID_ROLES = {"viewer", "responder", "admin"}
VALID_ANONYMOUS_ROLES = VALID_ROLES | {"disabled"}
VALID_PRIORITIES = {"P1", "P2", "P3", "P4"}


def _env(environ: Mapping[str, str], name: str, default: str) -> str:
    value = environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


def _env_int(environ: Mapping[str, str], name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    raw = environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        parsed = int(str(raw).strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ConfigError(f"{name} must be <= {maximum}")
    return parsed


def _env_float(environ: Mapping[str, str], name: str, default: float, minimum: float = 0.0) -> float:
    raw = environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        parsed = float(str(raw).strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return parsed


def _env_bool(environ: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_list(environ: Mapping[str, str], name: str) -> tuple[str, ...]:
    raw = environ.get(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    env: str
    host: str
    port: int
    storage_backend: str
    data_file: Path
    sqlite_path: Path
    database_url: str
    dashboard_dir: Path
    config_dir: Path
    api_keys: str
    auth_required: bool
    anonymous_role: str
    rate_limit_rpm: int
    max_body_bytes: int
    request_timeout_seconds: float
    dedup_window_seconds: int
    notify_webhook_url: str
    notify_min_priority: str
    cron_secret: str
    cors_origins: tuple[str, ...]
    log_format: str
    log_level: str
    sentry_dsn: str
    default_tenant: str
    weights_file: Path
    playbooks_file: Path
    tls_enabled: bool
    auto_seed: bool
    version: str

    @property
    def production(self) -> bool:
        return self.env == "production"


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = environ if environ is not None else os.environ
    profile = _env(env, "SENTINELIQ_ENV", "development").lower()
    if profile not in {"development", "test", "production"}:
        raise ConfigError("SENTINELIQ_ENV must be development, test, or production")

    backend = _env(env, "SENTINELIQ_STORAGE", "json").lower()
    if backend not in VALID_BACKENDS:
        raise ConfigError("SENTINELIQ_STORAGE must be json, sqlite, or postgres")

    data_file = Path(_env(env, "SENTINELIQ_DATA_FILE", str(ROOT / "data" / "incidents.json")))
    sqlite_path = Path(_env(env, "SENTINELIQ_SQLITE_PATH", str(ROOT / "data" / "sentineliq.db")))
    database_url = _env(env, "DATABASE_URL", "")
    if backend == "postgres" and not database_url:
        raise ConfigError("DATABASE_URL is required when SENTINELIQ_STORAGE=postgres")

    min_priority = _env(env, "SENTINELIQ_NOTIFY_MIN_PRIORITY", "P2").upper()
    if min_priority not in VALID_PRIORITIES:
        raise ConfigError("SENTINELIQ_NOTIFY_MIN_PRIORITY must be P1, P2, P3, or P4")

    log_format = _env(env, "SENTINELIQ_LOG_FORMAT", "text" if profile != "production" else "json").lower()
    if log_format not in {"text", "json"}:
        raise ConfigError("SENTINELIQ_LOG_FORMAT must be text or json")

    log_level = _env(env, "SENTINELIQ_LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("SENTINELIQ_LOG_LEVEL is invalid")

    api_keys = _env(env, "SENTINELIQ_API_KEYS", "")
    anonymous_role = _env(env, "SENTINELIQ_ANONYMOUS_ROLE", "").lower()
    if anonymous_role and anonymous_role not in VALID_ANONYMOUS_ROLES:
        raise ConfigError("SENTINELIQ_ANONYMOUS_ROLE must be disabled, viewer, responder, or admin")
    if not anonymous_role:
        anonymous_role = "disabled" if api_keys else "admin"

    auth_required = _env_bool(
        env,
        "SENTINELIQ_AUTH_REQUIRED",
        bool(api_keys) and anonymous_role == "disabled",
    )
    if auth_required and not api_keys:
        raise ConfigError("SENTINELIQ_AUTH_REQUIRED=true requires SENTINELIQ_API_KEYS to be set")
    if auth_required and anonymous_role != "disabled":
        raise ConfigError("SENTINELIQ_ANONYMOUS_ROLE must be disabled when SENTINELIQ_AUTH_REQUIRED=true")
    if anonymous_role == "disabled" and not api_keys:
        raise ConfigError("SENTINELIQ_ANONYMOUS_ROLE=disabled requires SENTINELIQ_API_KEYS to be set")

    settings = Settings(
        env=profile,
        host=_env(env, "HOST", "127.0.0.1"),
        port=_env_int(env, "PORT", 8081, 1, 65535),
        storage_backend=backend,
        data_file=data_file,
        sqlite_path=sqlite_path,
        database_url=database_url,
        dashboard_dir=ROOT / "dashboard",
        config_dir=ROOT / "config",
        api_keys=api_keys,
        auth_required=auth_required,
        anonymous_role=anonymous_role,
        rate_limit_rpm=_env_int(env, "SENTINELIQ_RATE_LIMIT_RPM", 120, 0, 100000),
        max_body_bytes=_env_int(env, "SENTINELIQ_MAX_BODY_BYTES", 262144, 1024, 10485760),
        request_timeout_seconds=_env_float(env, "SENTINELIQ_REQUEST_TIMEOUT", 15.0, 1.0),
        dedup_window_seconds=_env_int(env, "SENTINELIQ_DEDUP_WINDOW_SECONDS", 900, 0, 86400),
        notify_webhook_url=_env(env, "SENTINELIQ_NOTIFY_WEBHOOK_URL", ""),
        notify_min_priority=min_priority,
        cron_secret=_env(env, "SENTINELIQ_CRON_SECRET", ""),
        cors_origins=_env_list(env, "SENTINELIQ_CORS_ORIGINS"),
        log_format=log_format,
        log_level=log_level,
        sentry_dsn=_env(env, "SENTRY_DSN", ""),
        default_tenant=_env(env, "SENTINELIQ_DEFAULT_TENANT", "default") or "default",
        weights_file=Path(_env(env, "SENTINELIQ_WEIGHTS_FILE", str(ROOT / "config" / "weights.json"))),
        playbooks_file=Path(_env(env, "SENTINELIQ_PLAYBOOKS_FILE", str(ROOT / "config" / "playbooks.json"))),
        tls_enabled=_env_bool(env, "SENTINELIQ_TLS", False),
        auto_seed=_env_bool(env, "SENTINELIQ_AUTO_SEED", False),
        version=VERSION,
    )
    return settings
