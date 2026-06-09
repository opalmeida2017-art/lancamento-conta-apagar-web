from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.app import bootstrap as boot

db = boot.db

router = APIRouter(prefix="/api/notas", tags=["notas"])


class PlacaKmBody(BaseModel):
    chave_nfe: str = ""
    num_nota: str = ""
    valor: str


class FlagBody(BaseModel):
    chave_nfe: str
    valor: bool


@router.get("")
def listar_notas(
    dt_ini: str = "",
    dt_fim: str = "",
    cod: str = "",
    status: str = "Todos",
    nota: str = "",
    limite: Optional[str] = "100",
):
    lim = None if str(limite or "").lower() == "todos" else limite
    try:
        notas = db.listar_notas_filtradas(dt_ini, dt_fim, cod, status, nota, limite=lim)
        return {"ok": True, "notas": notas or []}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.patch("/estoque")
def atualizar_estoque(body: FlagBody):
    db.atualizar_estoque_nota(body.chave_nfe, body.valor)
    return {"ok": True}


@router.patch("/arquiva")
def atualizar_arquiva(body: FlagBody):
    db.atualizar_arquiva_nota(body.chave_nfe, body.valor)
    return {"ok": True}


@router.patch("/placa")
def atualizar_placa(body: PlacaKmBody):
    ok, msg = db.atualizar_painel_placa_km(
        chave_nfe=body.chave_nfe,
        num_nota=body.num_nota,
        placa=body.valor,
    )
    if not ok:
        raise HTTPException(400, msg or "Placa inválida")
    return {"ok": True}


@router.patch("/km")
def atualizar_km(body: PlacaKmBody):
    ok, msg = db.atualizar_painel_placa_km(
        chave_nfe=body.chave_nfe,
        num_nota=body.num_nota,
        km=body.valor,
    )
    if not ok:
        raise HTTPException(400, msg or "KM inválido")
    return {"ok": True}
