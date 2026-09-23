# KW Awards Voting Platform: Backend

Django REST API for the KW Awards voting platform, backed by PostgreSQL.
The React frontend lives in a separate repository
(`KW_Awards-voting-platfom_frontend`).

> **Status:** early development. The API is not yet safe to expose publicly
> (no authorization on most endpoints, no vote-integrity rules). See
> `AUDIT.md` for the current findings and the roadmap.

## Requirements

| Tool | Version |
|---|---|
| Python | 3.12 |
| Django | 5.2 LTS |
| Django REST framework | 3.18 |
| PostgreSQL | 16 |

Dependencies are exact-pinned in `backend/voting_platform/requirements/`:

- `base.txt`: runtime dependencies
- `prod.txt`: base + gunicorn + WhiteNoise (what `requirements.txt` points at)
- `dev.txt`: base + pytest, pytest-django, ruff, pip-audit

## Local setup

All commands run from `backend/voting_platform/`.

1. **Clone and enter the project**

   ```bash
   git clone https://github.com/Giddy-K/KW_Awards-voting-platfom.git
   cd KW_Awards-voting-platfom/backend/voting_platform
   ```

2. **Create a virtual environment and install dev dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements/dev.txt
   ```

3. **Start PostgreSQL 16 and create a database**

   Use any local Postgres 16, or Docker:

   ```bash
   docker run -d --name kw-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=vote_app \
     -p 5432:5432 postgres:16
   ```

   Or, with an existing server, `createdb vote_app`.

4. **Configure the environment**

   ```bash
   cp .env.example .env
   ```

   `manage.py` uses `voting_platform.settings.dev`, which has safe local
   defaults (database `vote_app` as user `postgres` on `localhost`, emails
   printed to the console, CORS for `localhost:3000/4500/5173`). Set
   `DB_PASSWORD` (and any other values that differ) in `.env`.

5. **Migrate, create an admin user, run**

   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   python manage.py runserver
   ```

   - API: <http://127.0.0.1:8000/api/>
   - Django admin: <http://127.0.0.1:8000/admin/>
   - Swagger UI: <http://127.0.0.1:8000/swagger/>

## Settings and environment variables

| Module | Used by | Notes |
|---|---|---|
| `voting_platform.settings.dev` | `manage.py` | local defaults, `DEBUG=True` |
| `voting_platform.settings.prod` | `wsgi.py`, `asgi.py` | no insecure defaults; HTTPS/HSTS, secure cookies, WhiteNoise, stdout logging |
| `voting_platform.settings.test` | pytest | dev settings + fast hasher + in-memory email |

Override with `DJANGO_SETTINGS_MODULE`. Real environment variables take
precedence over `.env`. **Every variable is documented in
[`.env.example`](backend/voting_platform/.env.example).** Required in
production (no defaults): `SECRET_KEY`, `ALLOWED_HOSTS`, `FRONTEND_URL`, and
either `DATABASE_URL` or `DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_HOST`.
Also set `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` and the `EMAIL_*`
values for real deployments.

The production settings assume a TLS-terminating reverse proxy that sends
`X-Forwarded-Proto: https`. Collect static files with
`python manage.py collectstatic --noinput`.

## Tests, linting and checks

```bash
pytest                                   # smoke tests (needs Postgres, see above)
ruff check .                             # lint
ruff format --check .                    # formatting (use `ruff format .` to fix)
python manage.py check
python manage.py makemigrations --check --dry-run
pip-audit                                # dependency vulnerabilities
```

Deployment check (uses dummy values, prod settings):

```bash
DJANGO_SETTINGS_MODULE=voting_platform.settings.prod \
SECRET_KEY="$(python -c 'from django.core.management.utils import get_random_secret_key as k; print(k())')" \
ALLOWED_HOSTS=vote.example.com FRONTEND_URL=https://vote.example.com \
python manage.py check --deploy --fail-level WARNING
```

The same steps run in GitHub Actions (`.github/workflows/ci.yml`) on every
push and pull request.

## Working with Git branches

1. Create a branch: `git checkout -b your-branch-name`
2. Keep it up to date: `git pull --rebase origin main`
3. Commit small, logical changes: `git add -p && git commit`
4. Push the branch and open a pull request on GitHub; CI must pass before merging.
