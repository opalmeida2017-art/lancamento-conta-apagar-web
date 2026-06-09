from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app import bootstrap as boot

db = boot.db

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigBody(BaseModel):
    link: str = ""
    user_sis: str = ""
    senha_sis: str = ""
    smtp: str = ""
    user_email: str = ""
    senha_email: str = ""
    ssl: int = 1
    porta: str = ""
    agendamento_tipo: str = ""
    intervalo_horas: int = 1
    destinatarios: str = ""
    rel_veiculo: str = ""
    rel_item: str = ""
    cod_grupo_item: str = ""


@router.get("")
def obter_config():
    import app_version
    import agendamento_email

    cfg = db.carregar_configuracoes() or {}
    if cfg.get("senha_sis"):
        cfg["senha_sis"] = "********"
    if cfg.get("senha_email"):
        cfg["senha_email"] = "********"
    rel = db.carregar_codigos_relatorios() if hasattr(db, "carregar_codigos_relatorios") else {}
    tipo = cfg.get("agendamento_tipo") or ""
    intervalo = cfg.get("intervalo_horas") or 1
    return {
        "ok": True,
        "config": cfg,
        "relatorios": rel or {},
        "versao": app_version.versao_exibicao(),
        "agendamento_resumo": agendamento_email.resumo_proximo_envio(tipo, intervalo),
    }


@router.put("")
def salvar_config(body: ConfigBody):
    atual = db.carregar_configuracoes() or {}
    senha_sis = body.senha_sis if body.senha_sis and body.senha_sis != "********" else atual.get("senha_sis", "")
    senha_email = body.senha_email if body.senha_email and body.senha_email != "********" else atual.get("senha_email", "")

    ok, msg = db.salvar_configuracoes(
        body.link,
        body.user_sis,
        senha_sis,
        body.smtp,
        body.user_email,
        senha_email,
        body.ssl,
        body.porta,
        body.agendamento_tipo,
        body.intervalo_horas,
        atual.get("proxima_execucao", ""),
        atual.get("ultima_execucao", ""),
        body.destinatarios,
    )
    if not ok:
        raise HTTPException(400, msg)
    if body.rel_veiculo or body.rel_item or body.cod_grupo_item:
        db.salvar_codigos_relatorios(
            body.rel_veiculo,
            body.rel_item,
            body.cod_grupo_item,
        )
    import agendamento_email
    if body.agendamento_tipo:
        proxima = agendamento_email.calcular_proxima_execucao(
            body.agendamento_tipo, body.intervalo_horas,
        )
        db.atualizar_agendamento_email(
            tipo=body.agendamento_tipo,
            intervalo_horas=body.intervalo_horas,
            proxima_execucao=agendamento_email.formatar_data_hora(proxima),
        )
    else:
        db.atualizar_agendamento_email(tipo="", intervalo_horas=body.intervalo_horas)
    return {"ok": True, "mensagem": msg}
