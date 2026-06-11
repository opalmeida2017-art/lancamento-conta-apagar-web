#!/usr/bin/env bash
# Instalação no Debian/Ubuntu — porta 8090 (não conflita com BIWEB :5000)
set -euo pipefail

APP_USER="nfe-web"
APP_DIR="/opt/nfe-web"
REPO_URL="${NFE_REPO_URL:-}"
BRANCH="${NFE_REPO_BRANCH:-main}"
NFE_PORT="${NFE_WEB_PORT:-8090}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Execute como root: sudo bash deploy/install-debian.sh"
  exit 1
fi

echo "=== Instalação Automação NFe Web (porta ${NFE_PORT}) ==="

apt-get update
apt-get install -y \
  python3 python3-venv python3-pip \
  git curl \
  libpq-dev \
  nginx \
  chromium \
  fonts-liberation libnss3 libatk-bridge2.0-0 libgtk-3-0 libx11-xcb1 \
  libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2

if ! id "$APP_USER" &>/dev/null; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"

if [ -n "$REPO_URL" ]; then
  if [ -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git -C "$APP_DIR" pull origin "$BRANCH"
  else
    sudo -u "$APP_USER" git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  fi
else
  echo "Copiando arquivos do diretório atual para ${APP_DIR}..."
  rsync -a --exclude venv --exclude __pycache__ --exclude .git --exclude '*.db' \
    "$(cd "$(dirname "$0")/.." && pwd)/" "$APP_DIR/"
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

cd "$APP_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^PG_PORT=.*/PG_PORT=5432/" .env
  sed -i "s/^NFE_WEB_PORT=.*/NFE_WEB_PORT=${NFE_PORT}/" .env
  grep -q '^NFE_ALLOW_START_WITHOUT_DB=' .env || echo 'NFE_ALLOW_START_WITHOUT_DB=1' >> .env
  grep -q '^NFE_ADMIN_SECRET=' .env || echo 'NFE_ADMIN_SECRET=altere-esta-chave-secreta' >> .env
  IP_PUBLIC=$(hostname -I | awk '{print $1}')
  sed -i "s/^NFE_PUBLIC_HOST=.*/NFE_PUBLIC_HOST=${IP_PUBLIC}/" .env 2>/dev/null || echo "NFE_PUBLIC_HOST=${IP_PUBLIC}" >> .env
  echo ""
  echo ">>> Edite ${APP_DIR}/.env (PG_PASSWORD, credenciais ERP)"
  echo ""
fi

sudo -u "$APP_USER" python3 -m venv venv
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r requirements.txt
sudo -u "$APP_USER" "$APP_DIR/venv/bin/playwright" install chromium

# Banco PostgreSQL (ajuste senha conforme seu servidor)
if command -v psql &>/dev/null; then
  sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'nfe_web'" | grep -q 1 \
    || sudo -u postgres createdb nfe_web || true
fi

# systemd
sed "s|/opt/nfe-web|${APP_DIR}|g; s|--port 8090|--port ${NFE_PORT}|" \
  "$APP_DIR/deploy/nfe-web.service" > /etc/systemd/system/nfe-web.service

systemctl daemon-reload
systemctl enable nfe-web
systemctl restart nfe-web

echo ""
echo "=== Instalação concluída ==="
echo "Painel:  http://$(hostname -I | awk '{print $1}'):${NFE_PORT}"
echo "Status:  systemctl status nfe-web"
echo "Logs:    journalctl -u nfe-web -f"
echo ""
echo "BIWEB usa porta 5000 por padrão — este painel usa ${NFE_PORT}."
echo "Proxy nginx (opcional): deploy/nginx-nfe-web.conf"
