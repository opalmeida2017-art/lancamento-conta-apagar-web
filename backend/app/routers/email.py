from fastapi import APIRouter, HTTPException

import agendamento_email
from backend.app import bootstrap as boot

db = boot.db

router = APIRouter(prefix="/api/email", tags=["email"])


@router.get("/agendamento")
def obter_agendamento():
    cfg = db.carregar_configuracoes() or {}
    tipo = cfg.get("agendamento_tipo") or ""
    intervalo = cfg.get("intervalo_horas") or 1
    return {
        "ok": True,
        "agendamento_tipo": tipo,
        "intervalo_horas": intervalo,
        "proxima_execucao": cfg.get("proxima_execucao") or "",
        "ultima_execucao": cfg.get("ultima_execucao") or "",
        "resumo": agendamento_email.resumo_proximo_envio(tipo, intervalo),
        "descricao": agendamento_email.descricao_agendamento(tipo, intervalo),
    }


@router.post("/enviar-manual")
def enviar_relatorios_manual():
    cfg = db.carregar_configuracoes() or {}
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
