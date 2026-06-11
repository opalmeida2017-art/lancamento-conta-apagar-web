from fastapi import APIRouter

import web_tenant_registry as registry

router = APIRouter(prefix="/api/portal", tags=["portal"])


@router.get("/tenants")
def listar_portal():
    items = []
    for t in registry.list_tenants():
        if not t.get("ativo", True):
            continue
        items.append({
            "slug": t.get("slug"),
            "razao_social": t.get("razao_social"),
            "url": t.get("url"),
        })
    return {"ok": True, "tenants": items}
