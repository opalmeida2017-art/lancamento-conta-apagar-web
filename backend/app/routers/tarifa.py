from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app import bootstrap as boot
from backend.app.services.robo_service import robo_service

db = boot.db

router = APIRouter(prefix="/api/tarifa", tags=["tarifa"])


class PastaBody(BaseModel):
    pasta: str


class ConfigTarifaBody(BaseModel):
    cod_fornecedor_sicredi: str = "640"
    cod_grupo_item_tarifa: str = "44"
    nome_item_tarifa: str = ""


@router.get("")
def listar_tarifas(
    cnpj: str = "",
    data_ini: str = "",
    data_fim: str = "",
    status: str = "Todos",
):
    grupos = db.obter_tarifas_agrupadas_por_cnpj_conta(
        cnpj_filtro=cnpj,
        data_ini=data_ini,
        data_fim=data_fim,
        status=status,
    )
    return {
        "ok": True,
        "grupos": grupos,
        "total": db.contar_tarifas_bancarias(),
        "ultima_importacao": db.obter_ultima_importacao_tarifas()
        or db.obter_ultima_atualizacao_tarifas(),
        "pasta": db.obter_pasta_tarifas_bancarias(),
        "config": db.obter_config_tarifa_erp(),
        "cnpjs": _listar_cnpjs(),
        "monitor_ativo": robo_service.tarifa_monitor_ativo(),
    }


@router.get("/cnpjs")
def listar_cnpjs():
    return {"ok": True, "cnpjs": _listar_cnpjs()}


def _listar_cnpjs():
    vistos = set()
    opcoes = ["Todos"]
    for item in db.obter_mapa_contas_sicredi():
        cnpj = str(item.get("cnpj") or "").strip()
        if cnpj and cnpj not in vistos:
            vistos.add(cnpj)
            opcoes.append(cnpj)
    for tarifa in db.listar_tarifas_bancarias():
        cnpj = str(tarifa.get("cnpj") or "").strip()
        if cnpj and cnpj not in vistos:
            vistos.add(cnpj)
            opcoes.append(cnpj)
    return opcoes


@router.put("/pasta")
def salvar_pasta(body: PastaBody):
    pasta = str(body.pasta or "").strip()
    if not pasta:
        raise HTTPException(400, "Informe o caminho da pasta.")
    db.salvar_pasta_tarifas_bancarias(pasta)
    return {"ok": True, "pasta": pasta}


@router.put("/config")
def salvar_config(body: ConfigTarifaBody):
    db.salvar_config_tarifa_erp(
        cod_fornecedor_sicredi=body.cod_fornecedor_sicredi,
        cod_grupo_item_tarifa=body.cod_grupo_item_tarifa,
        nome_item_tarifa=body.nome_item_tarifa,
    )
    return {"ok": True, "config": db.obter_config_tarifa_erp()}


@router.post("/importar")
def importar_planilhas():
    pasta = db.obter_pasta_tarifas_bancarias()
    if not pasta:
        raise HTTPException(400, "Configure a pasta de planilhas XLS primeiro.")
    return robo_service.importar_tarifas_pasta(pasta)


@router.post("/lancar")
def lancar_pendentes():
    return robo_service.lancar_tarifas_pendentes()
