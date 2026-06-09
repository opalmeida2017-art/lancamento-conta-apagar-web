from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app import bootstrap as boot

db = boot.db

router = APIRouter(prefix="/api/parametros", tags=["parametros"])


class ModelosBody(BaseModel):
    modelos: str = ""


class CombustiveisBody(BaseModel):
    etanol: str = ""
    gasolina: str = ""
    s10: str = ""
    s500: str = ""
    arla: str = ""


@router.get("")
def obter_parametros():
    return {
        "ok": True,
        "placas": db.obter_modelos_placa_string(),
        "km": db.obter_modelos_km_string(),
        "combustiveis": db.carregar_codigos_combustiveis(),
        "relatorios": db.carregar_codigos_relatorios() if hasattr(db, "carregar_codigos_relatorios") else {},
    }


@router.put("/placas")
def salvar_placas(body: ModelosBody):
    ok, msg, _ = db.validar_lista_modelos_placa(body.modelos)
    if not ok:
        raise HTTPException(400, msg)
    db.salvar_modelos_placa(body.modelos)
    return {"ok": True}


@router.put("/km")
def salvar_km(body: ModelosBody):
    ok, msg, _ = db.validar_lista_modelos_km(body.modelos)
    if not ok:
        raise HTTPException(400, msg)
    db.salvar_modelos_km(body.modelos)
    return {"ok": True}


@router.put("/combustiveis")
def salvar_combustiveis(body: CombustiveisBody):
    db.salvar_codigos_combustiveis(
        body.etanol, body.gasolina, body.s10, body.s500, body.arla,
    )
    return {"ok": True}
