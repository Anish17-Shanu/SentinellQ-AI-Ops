# Pinned on 2026-10-29; refresh with the digest from `docker buildx imagetools inspect python:3.13-slim`.
FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8081 \
    SENTINELIQ_ENV=production \
    SENTINELIQ_STORAGE=sqlite \
    SENTINELIQ_SQLITE_PATH=/data/sentineliq.db \
    SENTINELIQ_LOG_FORMAT=json

WORKDIR /app

# The runtime is stdlib-only; pip's vendored msgpack, setuptools, and urllib3 are
# never imported and are the image's only HIGH findings in the Trivy release gate.
RUN SITE_PACKAGES="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" \
    && rm -rf "$SITE_PACKAGES"/pip "$SITE_PACKAGES"/pip-*.dist-info

RUN groupadd --system --gid 10001 sentineliq \
    && useradd --system --uid 10001 --gid sentineliq --no-create-home --shell /usr/sbin/nologin sentineliq \
    && mkdir -p /data \
    && chown sentineliq:sentineliq /data

COPY src/ ./src/
COPY dashboard/ ./dashboard/
COPY config/ ./config/
COPY data/incidents.json ./data/incidents.json

USER 10001:10001

EXPOSE 8081
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8081/health', timeout=2).status == 200 else 1)"]

CMD ["python", "src/server.py"]
