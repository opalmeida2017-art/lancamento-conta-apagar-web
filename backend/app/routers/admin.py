import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _check_admin(x_admin_secret: str = Header(default="")):
    esperado = os.getenv("NFE_ADMIN_SECRET", "").strip()
    if not esperado or x_admin_secret != esperado:
        raise HTTPException(403, "Acesso negado.")


class ProvisionBody(BaseModel):
    razao_social: str
    slug: str = ""


@router.get("/tenants")
def listar_tenants(x_admin_secret: str = Header(default="")):
    _check_admin(x_admin_secret)
    import web_tenant_registry as registry
    return {"ok": True, "tenants": registry.list_tenants()}


@router.post("/tenants/provision")
def provisionar_tenant(body: ProvisionBody, x_admin_secret: str = Header(default="")):
    _check_admin(x_admin_secret)
    from deploy.provision_web_tenant import criar_instancia
    try:
        item = criar_instancia(body.razao_social, body.slug or None)
        return {"ok": True, "tenant": item}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
