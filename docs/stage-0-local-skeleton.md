# Stage 0: Local Skeleton

## Scope

Implemented:

- Django project under `backend/`
- Settings split into `base`, `local`, and `production`
- PostgreSQL-only database configuration
- Redis cache and Celery broker/result configuration
- Docker Compose local services for `db`, `redis`, `web`, and `worker`
- Liveness and readiness endpoints
- Placeholder app modules for future staged development
- Tests for health endpoints, PostgreSQL setting, Celery Redis setting, and project structure
- Git ignore rules for private corpus data and generated indexes

Not implemented in this stage:

- Corpus intake
- User accounts and approval workflow
- Corpus management
- Processing pipeline
- KWIC, ParaConc, CQPweb, exports, or UI workflows

## Acceptance Checks

```powershell
cd corpus-platform/backend
python manage.py check
pytest
```

```powershell
cd corpus-platform
docker compose -f docker-compose.local.yml up -d db redis
docker compose -f docker-compose.local.yml run --rm web python manage.py migrate
docker compose -f docker-compose.local.yml up -d web worker
```

The local web service is exposed on host port `8010` and container port `8000`.
