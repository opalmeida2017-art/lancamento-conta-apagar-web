#!/usr/bin/env python3
"""Inicializa schema de um tenant existente (uso no servidor)."""
import os
import sys
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_ROOT))

slug = sys.argv[1] if len(sys.argv) > 1 else "demo-transportadora"
import web_tenant_registry as reg

tenant = reg.get_tenant(slug)
if not tenant:
    print(f"Tenant nao encontrado: {slug}")
    sys.exit(1)

env_file = WEB_ROOT / ".env"
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

os.environ["PG_DATABASE"] = tenant["pg_database"]
import database_connection as dc
import database_setup as db

dc.executar_schema()
db.inicializar_banco()
db.garantir_dados_padrao()
print(f"OK tenant {slug} -> {tenant['url']}")
