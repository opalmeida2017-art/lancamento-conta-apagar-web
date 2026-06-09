"""
Conexão PostgreSQL — sistema web autônomo (sem SQLite).
Configure via .env ou variáveis PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD.
"""
import os
import re
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg2 import errors as pg_errors

# Campos retornados em camelCase para compatibilidade com o código existente
_ALIASES = {
    'codveiculo': 'codVeiculo',
    'veiculoproprio': 'veiculoProprio',
    'coditemd': 'codItemD',
    'descgrupoimp': 'descGrupoImp',
    'descnegocioimp': 'descNegocioImp',
    'ultima_atualizacao': 'ultima_atualizacao',
}


def _load_dotenv():
    env_path = Path(__file__).resolve().parent / '.env'
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        os.environ[key.strip()] = val.strip().strip('"').strip("'")


_load_dotenv()

# Evita UnicodeDecodeError quando libpq retorna mensagens em PT-BR (Windows)
os.environ.setdefault('PGCLIENTENCODING', 'UTF8')
os.environ.setdefault('LC_MESSAGES', 'C')

OperationalError = pg_errors.OperationalError
DatabaseError = pg_errors.DatabaseError


def caminho_banco():
    host = os.getenv('PG_HOST', 'localhost')
    port = os.getenv('PG_PORT', '5433')
    name = os.getenv('PG_DATABASE', 'nfe_web')
    user = os.getenv('PG_USER', 'postgres')
    return f'postgresql://{user}@{host}:{port}/{name}'


def _dsn():
    return {
        'host': os.getenv('PG_HOST', 'localhost'),
        'port': int(os.getenv('PG_PORT', '5433')),
        'dbname': os.getenv('PG_DATABASE', 'nfe_web'),
        'user': os.getenv('PG_USER', 'postgres'),
        'password': os.getenv('PG_PASSWORD', ''),
        'connect_timeout': 10,
    }


def _conectar_psycopg2(**extra):
    """Conecta ao PostgreSQL."""
    params = {**_dsn(), **extra}
    try:
        return psycopg2.connect(**params)
    except UnicodeDecodeError as exc:
        raise OperationalError(
            'Falha ao conectar ao PostgreSQL. Verifique PG_HOST, PG_PORT, '
            'PG_USER, PG_PASSWORD e se o banco PG_DATABASE existe.'
        ) from exc


def _garantir_database():
    """Cria o banco PG_DATABASE se ainda nao existir."""
    alvo = os.getenv('PG_DATABASE', 'nfe_web')
    try:
        conn = _conectar_psycopg2(dbname='postgres')
    except psycopg2.OperationalError:
        conn = _conectar_psycopg2(dbname='template1')
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', (alvo,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE "{alvo}"')
    cur.close()
    conn.close()


def _to_app_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        src = row
    elif hasattr(row, 'keys'):
        src = dict(row)
    else:
        return row
    out = {}
    for k, v in src.items():
        lk = str(k).lower()
        out[_ALIASES.get(lk, k)] = v
    return out


class PgCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        if params is not None and '?' in query:
            query = query.replace('?', '%s')
        return self._cursor.execute(query, params)

    def executemany(self, query, params_list):
        if '?' in query:
            query = query.replace('?', '%s')
        return self._cursor.executemany(query, params_list)

    def fetchone(self):
        return _to_app_dict(self._cursor.fetchone())

    def fetchall(self):
        return [_to_app_dict(r) for r in self._cursor.fetchall()]

    def __getattr__(self, name):
        attr = getattr(self._cursor, name)
        if name in ('fetchone', 'fetchall'):
            return attr
        return attr


class PgConnection:
    def __init__(self, conn, dict_rows=False):
        object.__setattr__(self, '_conn', conn)
        object.__setattr__(self, '_dict_rows', dict_rows)
        object.__setattr__(self, 'row_factory', None)

    def __setattr__(self, name, value):
        if name == 'row_factory':
            object.__setattr__(self, '_dict_rows', value is not None)
            object.__setattr__(self, 'row_factory', value)
        else:
            object.__setattr__(self, name, value)

    def cursor(self, *args, **kwargs):
        factory = psycopg2.extras.RealDictCursor if self._dict_rows else None
        raw = self._conn.cursor(cursor_factory=factory, *args, **kwargs)
        return PgCursor(raw)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def conectar_banco(**kwargs):
    dict_rows = bool(kwargs.pop('row_factory', None))
    conn = _conectar_psycopg2(**kwargs)
    pg = PgConnection(conn, dict_rows=dict_rows)
    return pg


def _limpar_stmt_sql(stmt):
    """Remove linhas de comentário; não descarta o statement inteiro por causa do cabeçalho."""
    linhas = []
    for line in stmt.splitlines():
        trecho = line.strip()
        if not trecho or trecho.startswith('--'):
            continue
        linhas.append(line)
    return '\n'.join(linhas).strip()


def executar_schema():
    _garantir_database()
    schema_path = Path(__file__).resolve().parent / 'pg_schema.sql'
    if not schema_path.is_file():
        raise FileNotFoundError(f'Schema não encontrado: {schema_path}')
    sql = schema_path.read_text(encoding='utf-8')
    conn = _conectar_psycopg2()
    conn.autocommit = True
    cur = conn.cursor()
    for stmt in re.split(r';\s*(?:\r?\n|$)', sql):
        stmt = _limpar_stmt_sql(stmt.strip())
        if not stmt:
            continue
        try:
            cur.execute(stmt)
        except pg_errors.DuplicateTable:
            pass
        except pg_errors.DuplicateObject:
            pass
        except pg_errors.DuplicateColumn:
            pass
    cur.close()
    conn.close()
