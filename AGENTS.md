# AGENTS.md

## Cursor Cloud specific instructions

Project: **Automação NFe — Web** — a FastAPI backend that also serves a static
HTML/JS frontend, plus a Playwright robot (`robo_web/`) that drives an external
ERP. Single product, multi-tenant ("multi-transportadora"): each tenant gets its
own PostgreSQL database. See `README.md` for the full overview.

### Services & how to run them
- **PostgreSQL** must be running before the app. It is NOT auto-started on VM
  boot — start it with `sudo pg_ctlcluster 16 main start` (or
  `sudo service postgresql start`). The local `postgres` role password is
  `postgres` and that matches `.env`.
- **Backend + frontend (one process):** `bash run.sh` starts uvicorn with
  `--reload` on port **8090** (dev). The frontend is static files served by
  FastAPI — there is no separate frontend build/server.
- Health check: `curl http://127.0.0.1:8090/api/health`.

### Non-obvious gotchas
- `.env` is gitignored. A working dev `.env` (copied from `.env.example` with
  `PG_PASSWORD=postgres` and `PG_PORT=5432`) is already present in the VM. If it
  is ever missing, recreate it from `.env.example`; without it `PG_PORT` defaults
  to **5433** in `database_connection.py` (mismatch vs `.env.example`'s 5432), so
  always set `PG_PORT=5432` explicitly.
- `NFE_ALLOW_START_WITHOUT_DB=1` (in `.env`) lets the server boot even if the
  default DB is unavailable; the app then runs in portal/multi-tenant mode.
- The app auto-creates and migrates databases on demand: the default `nfe_web`
  DB at startup, and a per-tenant DB (`nfe_web_<slug>`) when a tenant is
  provisioned. You do not run migrations manually.
- URLs: `/` serves the tenant **portal** (`portal.html`); the actual app for a
  tenant is at `/t/<slug>/` (e.g. `/t/demo/`). The tenant slug is stripped by
  `TenantMiddleware`, so API calls under a tenant look like `/t/<slug>/api/...`.
- Provision a tenant via the admin API (header `X-Admin-Secret` must equal
  `NFE_ADMIN_SECRET` from `.env`):
  `curl -X POST http://127.0.0.1:8090/api/admin/tenants/provision -H "X-Admin-Secret: <secret>" -H "Content-Type: application/json" -d '{"razao_social":"...","slug":"..."}'`.
  This writes `tenants.json` (runtime state at repo root, not committed).
- The **robot** (`robo_web/`) launches Chrome via Playwright and needs a real
  external ERP + credentials to do anything useful, so it cannot be exercised
  end-to-end in this environment. Playwright's Chromium is installed in the VM
  (`playwright install chromium`); reinstall after bumping the `playwright`
  version.

### Lint / tests / build
- There is **no lint config and no automated test suite** in this repo
  (no ruff/flake8/pytest config, no `package.json`). The frontend is plain
  static files with no build step. "Build" = installing Python deps and running
  the uvicorn dev server.
