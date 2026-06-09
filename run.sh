#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${NFE_WEB_HOST:-0.0.0.0}"
PORT="${NFE_WEB_PORT:-8090}"

if [ ! -d venv ]; then
  echo "Criando ambiente virtual..."
  python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -r requirements.txt

export PYTHONPATH="$(pwd)"

echo ""
echo "=== Automação NFe — Web ==="
echo "Banco: PostgreSQL (${PG_DATABASE:-nfe_web})"
echo "URL:   http://127.0.0.1:${PORT}"
echo ""

exec python -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT" --reload
