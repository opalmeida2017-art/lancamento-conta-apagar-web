"""Cria banco PostgreSQL + registro para uma transportadora web."""
import os
import sys
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parent.parent
if str(WEB_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_ROOT))

import web_tenant_registry as registry


def _criar_banco(pg_database: str):
    import psycopg2

    params = {
        "host": os.getenv("PG_HOST", "localhost"),
        "port": int(os.getenv("PG_PORT", "5432")),
        "user": os.getenv("PG_USER", "postgres"),
        "password": os.getenv("PG_PASSWORD", ""),
        "dbname": "postgres",
    }
    conn = psycopg2.connect(**params)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (pg_database,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE "{pg_database}"')
    cur.close()
    conn.close()


def _aplicar_schema(pg_database: str):
    import database_connection as dc

    old = os.environ.get("PG_DATABASE")
    os.environ["PG_DATABASE"] = pg_database
    try:
        dc.executar_schema()
    finally:
        if old:
            os.environ["PG_DATABASE"] = old
        elif "PG_DATABASE" in os.environ:
            del os.environ["PG_DATABASE"]


def _init_dados(pg_database: str, slug: str):
    from tenant_context import tenant_scope
    import database_setup as db

    with tenant_scope(slug, pg_database):
        db.inicializar_banco()
        db.gerar_chave_seguranca()
        db.garantir_dados_padrao()


def criar_instancia(razao_social: str, slug: str | None = None) -> dict:
    host = os.getenv("NFE_PUBLIC_HOST", "")
    port = os.getenv("NFE_WEB_PORT", "8090")
    item = registry.register_tenant(razao_social, slug, host, port)
    pg_db = item["pg_database"]
    _criar_banco(pg_db)
    _aplicar_schema(pg_db)
    _init_dados(pg_db, item["slug"])

    return item


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Provisiona instância web por transportadora")
    parser.add_argument("razao_social", help="Nome da transportadora")
    parser.add_argument("--slug", default="", help="Slug opcional na URL")
    args = parser.parse_args()
    item = criar_instancia(args.razao_social, args.slug or None)
    print(f"OK slug={item['slug']}")
    print(f"URL: {item['url']}")
    print(f"Banco: {item['pg_database']}")


if __name__ == "__main__":
    main()
