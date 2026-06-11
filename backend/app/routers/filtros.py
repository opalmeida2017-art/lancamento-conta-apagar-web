from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app import bootstrap as boot

db = boot.db

router = APIRouter(prefix="/api/filtros", tags=["filtros"])


class FiltrosBody(BaseModel):
    mes: str = "01 - Janeiro"
    ano: str = "2026"
    cod_filial: str = ""
    cod_unidade_embarque: str = ""
    ultimos_30_dias: bool = False
    hoje_apenas: bool = False
    ultimos_15_dias: bool = False
    fornecedores_fatura_afaturar: str = ""
    cod_tipo_fornecedor: str = ""
    modelos_placa: str = ""
    modelos_km: str = ""
    cod_etanol: str = ""
    cod_gasolina: str = ""
    cod_s10: str = ""
    cod_s500: str = ""
    cod_arla: str = ""
    rel_veiculo: str = ""
    rel_item: str = ""
    cod_grupo_item: str = ""


@router.get("")
def obter_filtros():
    filtros = db.carregar_filtros() or {}
    rel = db.carregar_codigos_relatorios() if hasattr(db, "carregar_codigos_relatorios") else {}
    comb = db.carregar_codigos_combustiveis()
    return {
        "ok": True,
        "filtros": filtros,
        "placas": db.obter_modelos_placa_string(),
        "km": db.obter_modelos_km_string(),
        "combustiveis": comb,
        "relatorios": rel or {},
    }


@router.put("")
def salvar_filtros(body: FiltrosBody):
    db.salvar_filtros(
        body.mes,
        body.ano,
        body.cod_filial,
        body.cod_unidade_embarque,
        int(body.ultimos_30_dias),
        int(body.hoje_apenas),
        int(body.ultimos_15_dias),
        body.fornecedores_fatura_afaturar,
        body.cod_tipo_fornecedor,
    )
    if body.modelos_placa:
        ok, msg, _ = db.validar_lista_modelos_placa(body.modelos_placa)
        if not ok:
            raise HTTPException(400, msg)
        db.salvar_modelos_placa(body.modelos_placa)
    if body.modelos_km:
        ok, msg, _ = db.validar_lista_modelos_km(body.modelos_km)
        if not ok:
            raise HTTPException(400, msg)
        db.salvar_modelos_km(body.modelos_km)
    if any([body.cod_etanol, body.cod_gasolina, body.cod_s10, body.cod_s500, body.cod_arla]):
        db.salvar_codigos_combustiveis(
            body.cod_etanol, body.cod_gasolina, body.cod_s10, body.cod_s500, body.cod_arla,
        )
    if body.rel_veiculo or body.rel_item or body.cod_grupo_item:
        db.salvar_codigos_relatorios(body.rel_veiculo, body.rel_item, body.cod_grupo_item)
    return {"ok": True}
