"""API-key authentication and role-based authorization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from errors import AuthError, ConfigError, ForbiddenError
from util import sha256_hex

ROLE_RANK = {"viewer": 1, "responder": 2, "admin": 3}
VALID_ROLES = set(ROLE_RANK)


@dataclass(frozen=True)
class Identity:
    role: str
    tenant: str
    label: str
    authenticated: bool


@dataclass(frozen=True)
class _KeyRecord:
    role: str
    tenant: str
    label: str


class Authenticator:
    def __init__(self, api_keys: str, default_tenant: str, required: bool) -> None:
        self.default_tenant = default_tenant
        self.required = required
        self._keys: dict[str, _KeyRecord] = {}
        self._parse(api_keys)
        if required and not self._keys:
            raise ConfigError("Authentication is required but no API keys are configured")

    def _parse(self, raw: str) -> None:
        for entry in (part.strip() for part in raw.split(",") if part.strip()):
            parts = entry.split(":")
            if len(parts) != 3:
                raise ConfigError(
                    "SENTINELIQ_API_KEYS entries must use key:role:tenant (role is viewer, responder, or admin)"
                )
            key, role, tenant = parts
            role = role.strip().lower()
            tenant = tenant.strip() or self.default_tenant
            if role not in VALID_ROLES:
                raise ConfigError(f"Unknown role in SENTINELIQ_API_KEYS: {role}")
            if len(key.strip()) < 12:
                raise ConfigError("API keys must be at least 12 characters long")
            if not tenant.replace("-", "").replace("_", "").isalnum():
                raise ConfigError("Tenant identifiers must be alphanumeric with - or _")
            digest = sha256_hex(key.strip())
            label = f"key-{digest[:8]}"
            self._keys[digest] = _KeyRecord(role=role, tenant=tenant, label=label)

    @property
    def enabled(self) -> bool:
        return bool(self._keys)

    def identify(self, headers: Mapping[str, str]) -> Identity | None:
        if not self._keys:
            return Identity(role="admin", tenant=self.default_tenant, label="anonymous", authenticated=False)
        presented = self._extract_key(headers)
        if not presented:
            return None
        record = self._keys.get(sha256_hex(presented))
        if record is None:
            return None
        return Identity(role=record.role, tenant=record.tenant, label=record.label, authenticated=True)

    @staticmethod
    def _extract_key(headers: Mapping[str, str]) -> str | None:
        direct = headers.get("x-api-key")
        if direct:
            return direct.strip()
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def require(self, identity: Identity | None, minimum_role: str) -> Identity:
        if minimum_role not in ROLE_RANK:
            raise ConfigError(f"Unknown role requirement: {minimum_role}")
        if identity is None:
            raise AuthError("A valid API key is required")
        if ROLE_RANK[identity.role] < ROLE_RANK[minimum_role]:
            raise ForbiddenError(
                f"Role '{identity.role}' cannot perform this action; '{minimum_role}' or higher is required"
            )
        return identity
