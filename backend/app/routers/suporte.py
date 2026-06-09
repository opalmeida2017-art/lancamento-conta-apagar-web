from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import agendamento_email
import database_setup as db
import relatorio_suporte

router = APIRouter(prefix="/api/suporte", tags=["suporte"])


class SuporteBody(BaseModel):
    dt_ini: str
    dt_fim: str


class EmailManualBody(BaseModel):
    destinatarios: str = ""


@router.post("/enviar-log")
def enviar_log_suporte(body: SuporteBody):
    try:
        resultado = relatorio_suporte.enviar_log_suporte_por_email(body.dt_ini, body.dt_fim)
        return {"ok": True, **{k: str(v) if hasattr(v, "strftime") else v for k, v in resultado.items()}}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/enviar-relatorios")
def enviar_relatorios_manual(body: EmailManualBody):
    cfg = db.carregar_configuracoes() or {}
    if body.destinatarios:
        cfg = dict(cfg)
        cfg["destinatarios"] = body.destinatarios
    try:
        resultado = agendamento_email.enviar_relatorios_agendados(cfg)
        proxima = resultado.get("proxima_execucao")
        return {
            "ok": True,
            "total_notas": resultado.get("total_notas"),
            "total_itens": resultado.get("total_itens"),
            "proxima_execucao": proxima.strftime("%d/%m/%Y %H:%M") if proxima else "",
        }
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
