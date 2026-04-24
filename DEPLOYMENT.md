# Production deployment use case

This project can run as a real-time conscience guardrail service in front of an AI action executor.

## Reference architecture

- Client app sends proposed actions to `/v1/guardrail/decision`
- Conscience service returns `allowed`, `risk_band`, and controls
- Executor proceeds only if `allowed=true`
- Evaluator endpoint `/v1/evaluate` is used by ops for periodic benchmark health checks

## Deployment options

### Option 1: local process

```bash
cd conscience_research
cp prod.env.example .env
# Edit values if needed
set -a && source .env && set +a
python production_service.py
```

### Option 2: Docker

```bash
cd conscience_research
docker build -t conscience-research:prod .
docker run --rm -p 8080:8080 conscience-research:prod
```

### Option 3: Docker Compose

```bash
cd conscience_research
docker compose up --build -d
```

## API quickstart

### Health

```bash
curl -s http://localhost:8080/health
```

### Evaluate baseline oracle

```bash
curl -s -X POST http://localhost:8080/v1/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"oracle_mode":"baseline"}'
```

### Evaluate third-eye oracle

```bash
curl -s -X POST http://localhost:8080/v1/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"oracle_mode":"third_eye"}'
```

### Guardrail decision (dry-run)

```bash
curl -s -X POST http://localhost:8080/v1/guardrail/decision \
  -H 'Content-Type: application/json' \
  -d '{
    "action": "disclosed_pii_without_consent",
    "context": {"agent_did_this": true, "harm_occurred": true},
    "dry_run": true
  }'
```

### Guardrail decision with state update

```bash
curl -s -X POST http://localhost:8080/v1/guardrail/decision \
  -H 'Content-Type: application/json' \
  -d '{
    "action": "deliberate_moderate_deception",
    "context": {"intent": "deliberate"},
    "dry_run": false,
    "apply_binding_update": true,
    "record_episode": true
  }'
```

## Production hardening checklist

- Put TLS and auth in front of the service (API gateway/ingress)
- Restrict network access to trusted internal clients
- Send request/decision logs to centralized observability
- Alert on drop in `/v1/evaluate` `conscience_score`
- Back up `moral_history.jsonl` if using continuity recording
- Roll out using canary or blue/green and compare decision deltas

## Suggested SLOs

- Availability: 99.9%
- p95 latency for `/v1/guardrail/decision`: < 100ms
- Error rate: < 0.1%
- Score drift alert threshold: conscience_score < 0.90
