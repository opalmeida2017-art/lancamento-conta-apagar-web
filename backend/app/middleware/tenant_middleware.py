import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import web_tenant_registry as registry
from tenant_context import reset_tenant, set_tenant

_TENANT_RE = re.compile(r"^/t/([a-z0-9-]+)(/.*)?$", re.I)


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.scope.get("path") or ""
        m = _TENANT_RE.match(path)
        if not m:
            tokens = set_tenant(None, None)
            try:
                return await call_next(request)
            finally:
                reset_tenant(tokens)

        slug = m.group(1).lower()
        tenant = registry.get_tenant(slug)
        if not tenant or not tenant.get("ativo", True):
            return JSONResponse({"detail": "Transportadora não encontrada ou inativa."}, status_code=404)

        rest = m.group(2) or "/"
        request.scope["path"] = rest
        request.state.tenant = tenant
        request.state.tenant_slug = slug

        tokens = set_tenant(slug, tenant.get("pg_database"))
        try:
            return await call_next(request)
        finally:
            reset_tenant(tokens)
