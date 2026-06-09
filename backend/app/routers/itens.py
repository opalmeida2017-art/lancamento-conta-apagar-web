from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.app import bootstrap as boot
from backend.app.services.robo_service import robo_service

db = boot.db

router = APIRouter(prefix="/api/itens", tags=["itens"])


class MigracaoBody(BaseModel):
    codigos: List[str]
    novo_grupo: str
    grupo_atual: str = "Filtrado"


def _filtrar_itens(cod="", grupo="Todos", descricao="", limite=None):
    itens = db.obter_itens_erp()
    filtrados = []
    cod_l = cod.lower().strip()
    desc_l = descricao.lower().strip()
    for item in itens:
        if cod_l and cod_l not in str(item.get("codItemD", "")).lower():
            continue
        val_grupo = str(item.get("descGrupoImp", "")).strip()
        if grupo != "Todos" and grupo.lower() != val_grupo.lower():
            continue
        if desc_l and desc_l not in str(item.get("descricao", "")).lower():
            continue
        filtrados.append(item)
    if limite in (None, "", "Todos"):
        return filtrados
    try:
        return filtrados[: max(1, int(limite))]
    except Exception:
        return filtrados


@router.get("")
def listar_itens(
    cod: str = "",
    grupo: str = "Todos",
    descricao: str = "",
    limite: Optional[str] = "500",
):
    lim = None if str(limite or "").lower() == "todos" else limite
    return {"ok": True, "itens": _filtrar_itens(cod, grupo, descricao, lim)}


@router.get("/grupos")
def listar_grupos():
    itens = db.obter_itens_erp()
    grupos = sorted({str(i.get("descGrupoImp", "")).strip() for i in itens if i.get("descGrupoImp")})
    return {"ok": True, "grupos": ["Todos"] + grupos}


@router.post("/sync")
def sincronizar_itens():
    return robo_service.sincronizar_itens()


@router.post("/migrar")
def migrar_grupo_lote(body: MigracaoBody):
    if not body.codigos:
        raise HTTPException(400, "Selecione ao menos um item.")
    if not body.novo_grupo.strip():
        raise HTTPException(400, "Informe o novo grupo.")
    return robo_service.iniciar_migracao(
        body.codigos, body.novo_grupo.strip(), body.grupo_atual,
    )
