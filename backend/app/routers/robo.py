from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.services.robo_service import robo_service

router = APIRouter(prefix="/api/robo", tags=["robo"])


class IniciarRoboBody(BaseModel):
    nota_alvo: Optional[str] = None
    compra_estoque: bool = False
    notas_lote: Optional[List[str]] = None


@router.get("/status")
def status_robo():
    return {"ok": True, **robo_service.status()}


@router.post("/start")
def iniciar_robo(body: IniciarRoboBody):
    if body.notas_lote:
        return robo_service.iniciar(notas_lote=body.notas_lote)
    return robo_service.iniciar(
        nota_alvo=body.nota_alvo or None,
        compra_estoque=body.compra_estoque,
    )


@router.post("/lote")
def iniciar_lote(body: IniciarRoboBody):
    return robo_service.iniciar(notas_lote=body.notas_lote or [])


@router.post("/stop")
def parar_robo():
    return robo_service.iniciar()
