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


class LicenseToggleBody(BaseModel):
    ativado: str


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


@router.get("/licenses")
def listar_licencas(x_admin_secret: str = Header(default="")):
    _check_admin(x_admin_secret)
    import licenca_remota
    try:
        licencas = licenca_remota.listar_todas_licencas()
        for item in licencas:
            item["data_registro_formatada"] = licenca_remota.formatar_data_registro_exibicao(
                item.get("data_registro"),
            )
        return {"ok": True, "licenses": licencas}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.patch("/licenses/{arquivo}")
def atualizar_licenca(
    arquivo: str,
    body: LicenseToggleBody,
    x_admin_secret: str = Header(default=""),
):
    _check_admin(x_admin_secret)
    import licenca_remota
    ok, msg = licenca_remota.definir_ativado_arquivo(arquivo, body.ativado)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "mensagem": msg}


@router.delete("/licenses/{arquivo}")
def excluir_licenca(arquivo: str, x_admin_secret: str = Header(default="")):
    _check_admin(x_admin_secret)
    import licenca_remota
    ok, msg = licenca_remota.excluir_licenca_arquivo(arquivo)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "mensagem": msg}
