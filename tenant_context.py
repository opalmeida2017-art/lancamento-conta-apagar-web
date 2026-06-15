"""Contexto do tenant ativo (banco PostgreSQL isolado por transportadora)."""
from contextlib import contextmanager
from functools import wraps
from contextvars import ContextVar
from typing import Callable, Optional, Tuple, TypeVar

_tenant_slug: ContextVar[Optional[str]] = ContextVar("tenant_slug", default=None)
_tenant_db: ContextVar[Optional[str]] = ContextVar("tenant_db", default=None)
_F = TypeVar("_F", bound=Callable)


def get_tenant_slug() -> Optional[str]:
    return _tenant_slug.get()


def get_tenant_db() -> Optional[str]:
    return _tenant_db.get()


def set_tenant(slug: Optional[str], pg_database: Optional[str] = None) -> Tuple:
    t1 = _tenant_slug.set(slug)
    t2 = _tenant_db.set(pg_database)
    return t1, t2


def reset_tenant(tokens):
    t1, t2 = tokens
    _tenant_slug.reset(t1)
    _tenant_db.reset(t2)


@contextmanager
def tenant_scope(slug: str | None, pg_database: str | None = None):
    tokens = set_tenant(slug, pg_database)
    try:
        yield
    finally:
        reset_tenant(tokens)


def preserve_tenant_context(func: _F) -> _F:
    slug = get_tenant_slug()
    pg_database = get_tenant_db()

    @wraps(func)
    def wrapped(*args, **kwargs):
        with tenant_scope(slug, pg_database):
            return func(*args, **kwargs)

    return wrapped
