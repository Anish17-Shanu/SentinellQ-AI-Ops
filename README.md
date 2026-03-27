# SentinelIQ AI Ops

## Creator

This project was created, written, and maintained by **ANISH KUMAR**.
All primary documentation in this README is presented as the work of **ANISH KUMAR**.

SentinelIQ AI Ops is an explainable incident-prioritization service for platform teams. It takes live incident signals, stores incidents locally, scores business risk, recommends response actions, and returns a confidence-backed explanation layer that is portfolio-ready and easy to extend.

## Features

- Incident severity scoring using service criticality, user impact, error rate, latency, and alert volume
- Explainable output showing why a priority was assigned
- Suggested playbook actions by category
- Local incident persistence with `POST /incidents`
- Incident filtering by owner, environment, and priority hint
- Summary analytics endpoint for operations reporting
- Unit tests for scoring and summary behavior

## Run locally

```bash
cd "d:\Project\SentinelIQ-AI-Ops"
python src/server.py
```

The server starts on `http://127.0.0.1:8081`.

Dashboard:

```text
http://127.0.0.1:8081/dashboard/
```

## Endpoints

- `GET /health`
- `GET /incidents`
- `GET /summary`
- `GET /playbooks`
- `POST /incidents`
- `POST /score`

## Testing

```bash
python -m unittest discover -s tests
```

## Example

```bash
curl -X POST http://127.0.0.1:8081/score ^
  -H "Content-Type: application/json" ^
  -d "{\"service\":\"payments-api\",\"criticality\":\"critical\",\"impacted_users\":18500,\"error_rate\":17.2,\"latency_ms\":1850,\"alerts\":23,\"category\":\"availability\"}"
```
