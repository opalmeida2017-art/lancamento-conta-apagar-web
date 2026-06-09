from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.app import bootstrap as boot

log_service = boot.log_service

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def listar_logs(
    limite: Optional[str] = "500",
    dt_ini: str = "",
    dt_fim: str = "",
    nota: str = "",
):
    lim = None if str(limite or "").lower() == "todos" else limite
    logs = log_service.listar_logs(limite=lim)
    if dt_ini or dt_fim or nota:
        logs = log_service.filtrar_logs(logs, dt_ini=dt_ini, dt_fim=dt_fim, numero_nota=nota)
    return {"ok": True, "logs": logs, "total": len(logs)}


@router.delete("")
def limpar_logs():
    log_service.limpar_logs()
    return {"ok": True, "mensagem": "Logs limpos."}
