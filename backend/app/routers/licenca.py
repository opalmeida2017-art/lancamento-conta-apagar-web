from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import licenca_remota
from backend.app import bootstrap as boot

db = boot.db

router = APIRouter(prefix="/api/licenca", tags=["licenca"])


class LicencaBody(BaseModel):
    razao_social: str = ""


@router.get("/status")
def status_licenca():
    instalacao_id = db.obter_ou_criar_instalacao_id()
    info = db.carregar_instalacao_licenca() or {}
    configurada = licenca_remota.licenca_configurada()
    liberada = True
    if configurada:
        liberada = licenca_remota.arquivo_licenca_existe(instalacao_id)
    return {
        "ok": True,
        "configurada": configurada,
        "liberada": liberada,
        "instalacao_id": instalacao_id,
        "razao_social": info.get("razao_social") or "",
        "status": info.get("status") or "",
        "ultima_verificacao": info.get("ultima_verificacao") or "",
    }


@router.post("/registrar")
def registrar_licenca(body: LicencaBody):
    ok, msg, iid = licenca_remota.registrar_instalacao(body.razao_social)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "mensagem": msg, "instalacao_id": iid}


@router.post("/verificar")
def verificar_licenca():
    iid = db.obter_instalacao_id()
    if not licenca_remota.licenca_configurada():
        return {"ok": True, "liberada": True, "mensagem": "Licenciamento remoto não configurado."}
    liberada = licenca_remota.arquivo_licenca_existe(iid)
    return {
        "ok": True,
        "liberada": liberada,
        "mensagem": "Licença liberada." if liberada else "Sistema bloqueado. Aguarde liberação.",
    }
