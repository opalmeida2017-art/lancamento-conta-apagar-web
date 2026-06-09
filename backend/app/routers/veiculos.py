from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.app import bootstrap as boot
from backend.app.services.robo_service import robo_service

db = boot.db

router = APIRouter(prefix="/api/veiculos", tags=["veiculos"])


@router.get("")
def listar_veiculos(
    limite: Optional[str] = "100",
    placa: str = Query("", alias="placa"),
):
    lim = None if str(limite or "").lower() == "todos" else limite
    veiculos = db.obter_frota_erp(limite=lim, placa_filtro=placa)
    return {
        "ok": True,
        "veiculos": veiculos,
        "ultima_sync": db.obter_ultima_sincronizacao_frota(),
    }


@router.post("/sync")
def sincronizar_frota():
    return robo_service.sincronizar_frota()
