"""Composition root: wires settings, storage, scoring, auth, and the router."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from auth import Authenticator
from config import Settings, load_settings
from engine import assess_incident, load_playbooks, load_weights
from logging_utils import configure_logging
from metrics import Metrics
from notifier import WebhookNotifier
from ratelimit import TokenBucketLimiter
from repository import IncidentRepository, build_repository
from router import Router
from seed import seed_demo
from service import IncidentService

logger = logging.getLogger("sentineliq.bootstrap")


@dataclass
class AppContext:
    settings: Settings
    repository: IncidentRepository
    service: IncidentService
    authenticator: Authenticator
    limiter: TokenBucketLimiter
    metrics: Metrics
    notifier: WebhookNotifier
    router: Router


def build_context(
    settings: Settings | None = None,
    repository=None,
    notifier: WebhookNotifier | None = None,
    configure_logs: bool = False,
) -> AppContext:
    settings = settings or load_settings()
    if configure_logs:
        configure_logging(settings)

    metrics = Metrics(settings.version)
    score_config = load_weights(settings.weights_file)
    playbooks = load_playbooks(settings.playbooks_file)
    assessor = lambda record: assess_incident(record, score_config, playbooks)  # noqa: E731

    repository = repository or build_repository(settings, assessor)
    notifier = notifier if notifier is not None else WebhookNotifier(settings.notify_webhook_url)
    service = IncidentService(repository, settings, score_config, playbooks, metrics, notifier)
    authenticator = Authenticator(settings.api_keys, settings.default_tenant, settings.auth_required)
    limiter = TokenBucketLimiter(settings.rate_limit_rpm)
    router = Router(settings, service, authenticator, limiter, metrics)

    if settings.auto_seed:
        try:
            if not repository.all_incidents(settings.default_tenant):
                seed_demo(service, settings.default_tenant)
                logger.info("demo data seeded", extra={"fields": {"tenant": settings.default_tenant}})
        except Exception as exc:  # noqa: BLE001 - seeding must not block startup
            logger.warning("demo seeding skipped: %s", exc)

    if settings.production and not authenticator.enabled:
        logger.warning("authentication is disabled; writes are open to anyone who can reach the service")

    return AppContext(
        settings=settings,
        repository=repository,
        service=service,
        authenticator=authenticator,
        limiter=limiter,
        metrics=metrics,
        notifier=notifier,
        router=router,
    )
