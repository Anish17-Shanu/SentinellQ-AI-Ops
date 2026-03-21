# Deployment

## Local

```bash
python src/server.py
```

Dashboard:

```text
http://127.0.0.1:8081/dashboard/
```

## Docker

```bash
docker build -t sentineliq-ai-ops .
docker run -p 8081:8081 sentineliq-ai-ops
```

## GitHub Actions

The CI workflow:

- compiles the Python sources
- runs the unit tests in `tests/`

## Production notes

- move incident storage from JSON to Postgres or a managed database
- add authentication before exposing the dashboard publicly
- place the app behind Nginx or a cloud load balancer
