# KW Awards Voting Platform: Backend

Django REST API for the KW Awards: public browsing of events, awards and nominees,
phone-verified voting, nomination moderation and staff administration. PostgreSQL 16.
The React frontend lives in a separate repository (`KW_Awards-voting-platfom_frontend`).

> **Status:** core redesigned (Phase 2). Free voting with SMS verification, moderation, results
> and an audit trail are implemented and tested. Not yet built: paid voting (M-Pesa), S3 media
> storage and deployment configuration. See `AUDIT.md` for the findings and roadmap.

## Requirements

| Tool | Version |
|---|---|
| Python | 3.12 |
| Django / DRF | 5.2 LTS / 3.18 |
| PostgreSQL | 16 |

Dependencies are exact-pinned in `backend/voting_platform/requirements/` (`base`, `prod`, `dev`).

## Architecture

```
backend/voting_platform/
├── accounts/      staff User (email login) + roles (Moderator, EventAdmin), JWT auth endpoints
├── events/        Event (status + windows), Category ("genre"), Award; status transitions
├── nominations/   Nominee, Nomination, public submission, moderation, hardened photo uploads
├── voting/        Voter, OTP challenges, Vote (append-only), voter tokens, results/stats
├── payments/      Payment schema only (M-Pesa comes later)
├── audit/         append-only AuditLog and its read API
├── common/        phone/IP/CAPTCHA/SMS/throttle helpers, seed_demo command
└── voting_platform/  settings (base/dev/prod/test), URLs (/api/v1/…), WSGI/ASGI
```

Design rules worth knowing:

- **Votes are rows, never counters.** Results are `SUM(quantity)` over non-voided votes. One free vote
  per voter per award is a PostgreSQL partial unique index; the API inserts and maps the violation to
  HTTP 409. Votes and audit entries are append-only in the ORM *and* in PostgreSQL (triggers). Voting
  opens only when the event status **and** its time window both allow it.
- **Two kinds of token.** Staff use a short-lived JWT access token (rotating, blacklisted refresh
  tokens). Voters get a separate 30-minute *voter token* after SMS OTP verification; each is rejected
  by the other's endpoints. DRF is deny-by-default; public endpoints opt in.
- **Privacy.** Voter phone numbers are stored in E.164, masked (`+2547******12`) in every API
  response, admin page and log line. OTP codes are stored only as a keyed hash.
- **Abuse controls.** Scoped throttles (OTP per phone/IP, votes, nominations, staff login), Cloudflare
  Turnstile on OTP request and nomination submission, client IP through django-ipware with an
  explicit trusted-proxy chain.

## Local setup

All commands run from `backend/voting_platform/`.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt

# PostgreSQL 16 (any local server, or Docker)
docker run -d --name kw-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=vote_app -p 5432:5432 postgres:16

cp .env.example .env                 # dev works with the defaults; set DB_PASSWORD if needed
python manage.py migrate
python manage.py seed_demo           # demo event, nominees and one user per role (DEBUG only)
python manage.py runserver
```

`seed_demo` prints the demo staff logins (`admin@`, `moderator@`, `eventadmin@example.com`). In
development SMS is printed to the server console, so the voter flow works without a provider:

```bash
B=http://127.0.0.1:8000/api/v1
curl -X POST $B/voters/otp/request/ -H 'Content-Type: application/json' \
     -d '{"phone":"0712345678","captcha_token":"dev"}'          # code appears in the server console
curl -X POST $B/voters/otp/verify/  -H 'Content-Type: application/json' \
     -d '{"phone":"0712345678","code":"123456"}'                 # -> {"token": ...}
curl -X POST $B/votes/ -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
     -d '{"nomination_id":"<id from GET /awards/<id>/nominations/>"}'
```

- API: `/api/v1/` · Django admin: `/admin/` · OpenAPI schema and Swagger UI: `/api/schema/`,
  `/api/schema/swagger-ui/` (staff, via a Django admin login)
- The committed `schema.yml` is the contract for the frontend. Regenerate after API changes:
  `python manage.py spectacular --file schema.yml --validate --fail-on-warn`

## Endpoints (v1)

| Area | Endpoints | Who |
|---|---|---|
| Auth | `auth/token/`, `auth/refresh/`, `auth/logout/`, `auth/me/` | staff |
| Public read | `events/`, `events/{slug}/`, `awards/`, `categories/`, `awards/{id}/nominations/`, `nominations/`, `nominees/{id}/`, `events/{slug}/results/` (after publication) | anyone |
| Public write | `nominations/` (multipart, CAPTCHA, throttled) | anyone |
| Voter | `voters/otp/request/`, `voters/otp/verify/`, `votes/`, `votes/mine/` | voter token |
| Moderator | `nominations/` (all statuses), `nominations/{id}/approve\|reject/` | Moderator |
| Event admin | CRUD `events/`, `categories/`, `awards/`; `events/{slug}/set-status/`, `.../stats/`, `.../results/` any time; `votes/`, `votes/{id}/void/`; `audit-logs/` | EventAdmin |

The full request/response contract and the auth requirement of every operation are in `schema.yml`.

## Settings and environment

| Module | Used by | Notes |
|---|---|---|
| `voting_platform.settings.dev` | `manage.py` | local defaults, `DEBUG=True`, console SMS, dummy CAPTCHA |
| `voting_platform.settings.prod` | `wsgi.py`, `asgi.py` | no insecure defaults; **refuses to start** without a shared cache, real SMS and CAPTCHA credentials, hosts, secret key |
| `voting_platform.settings.test` | pytest | in-memory SMS, high rate limits |

Every variable is documented in [`.env.example`](backend/voting_platform/.env.example). Production needs
at least `SECRET_KEY`, `ALLOWED_HOSTS`, `FRONTEND_URL`, database settings, `CACHE_URL` (Redis),
`AFRICASTALKING_*`, `TURNSTILE_SECRET_KEY` and `TRUSTED_PROXIES`. Production assumes a TLS-terminating
proxy that sends `X-Forwarded-Proto` and overwrites `X-Forwarded-For`. Media files are stored on local
disk for now (S3-compatible storage is part of the deployment phase).

## Tests, linting and checks

```bash
pytest                                # 559 tests, coverage reported (fails under 90%)
ruff check . && ruff format --check .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py spectacular --file /tmp/schema.yml --validate --fail-on-warn
pip-audit
```

Tests need PostgreSQL (`DB_*` variables; see Local setup). They include real multi-threaded
double-vote races, time-frozen voting-window boundaries, an endpoint × role permission matrix, and
subprocess checks that the production settings refuse insecure configurations.

Production deployment check (dummy values):

```bash
DJANGO_SETTINGS_MODULE=voting_platform.settings.prod SECRET_KEY=... ALLOWED_HOSTS=vote.example.com \
FRONTEND_URL=https://vote.example.com CACHE_URL=redis://localhost:6379/1 \
AFRICASTALKING_USERNAME=x AFRICASTALKING_API_KEY=x TURNSTILE_SECRET_KEY=x \
DB_NAME=x DB_USER=x DB_PASSWORD=x DB_HOST=localhost \
python manage.py check --deploy --fail-level WARNING
```

The same steps run in GitHub Actions (`.github/workflows/ci.yml`) on every push and pull request.

## Working with Git branches

1. Create a branch: `git checkout -b your-branch-name`
2. Keep it up to date: `git pull --rebase origin main`
3. Commit small, logical changes: `git add -p && git commit`
4. Push the branch and open a pull request; CI must pass before merging.
