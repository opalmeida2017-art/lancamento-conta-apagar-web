# Automação NFe — Web

Sistema web de automação de NF-e / Contas a Pagar integrado ao ERP via Playwright.

- **Banco:** PostgreSQL (`nfe_web`)
- **Porta padrão:** `8090` (evita conflito com **BIWEB** na porta `5000`)
- **Independente** do projeto desktop (`Lançamento conta apagar`)

## Windows (desenvolvimento)

```powershell
cd "C:\python\Lançamento conta apagar WEB"
copy .env.example .env
# Edite PG_PASSWORD e credenciais

python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chrome
.\run.ps1
```

Acesse: **http://localhost:8090**

## Debian / Ubuntu (produção)

### 1. Criar repositório no GitHub

No PC com Git:

```bash
git init
git add .
git commit -m "Automação NFe Web — deploy Debian porta 8090"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/lancamento-conta-apagar-web.git
git push -u origin main
```

### 2. Instalar no servidor Debian

```bash
# Opção A — clonar do GitHub
export NFE_REPO_URL="https://github.com/SEU_USUARIO/lancamento-conta-apagar-web.git"
curl -fsSL "$NFE_REPO_URL/raw/main/deploy/install-debian.sh" -o /tmp/install-nfe.sh
sudo bash /tmp/install-nfe.sh

# Opção B — copiar pasta e instalar localmente
sudo bash deploy/install-debian.sh
```

### 3. Configurar

```bash
sudo nano /opt/nfe-web/.env
```

| Variável | Descrição |
|----------|-----------|
| `PG_HOST` | Host PostgreSQL |
| `PG_PORT` | `5432` no Debian |
| `PG_DATABASE` | `nfe_web` |
| `PG_PASSWORD` | Senha do PostgreSQL |
| `NFE_WEB_PORT` | `8090` (padrão) |
| `ROBO_HEADLESS` | `true` no servidor |

```bash
sudo systemctl restart nfe-web
sudo systemctl status nfe-web
```

Painel: **http://IP_DO_SERVIDOR:8090**

### Serviços no mesmo servidor

| Sistema | Porta padrão | Pasta |
|---------|--------------|-------|
| BIWEB | 5000 | `/opt/biweb` ou similar |
| NFe Web | **8090** | `/opt/nfe-web` |

### Nginx (opcional)

```bash
sudo cp deploy/nginx-nfe-web.conf /etc/nginx/sites-available/nfe-web
sudo ln -s /etc/nginx/sites-available/nfe-web /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## Estrutura

```
backend/app/           → API FastAPI
frontend/              → Interface web
robo_web/              → Robô Playwright
deploy/                → install-debian.sh, systemd, nginx
pg_schema.sql          → Schema PostgreSQL
database_setup.py      → Lógica de dados
```

## Primeiro uso

1. **Configurações** — link/usuário/senha ERP
2. **Parâmetros ERP** — mês SEFAZ, modelos placa/KM
3. Sincronize **Veículos** e **Itens**
4. **Execução e Notas** — iniciar robô
