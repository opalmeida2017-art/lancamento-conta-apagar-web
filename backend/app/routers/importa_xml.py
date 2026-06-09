import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from backend.app import bootstrap as boot
from backend.app.services.robo_service import robo_service

router = APIRouter(prefix="/api/importa-xml", tags=["importa-xml"])

from robo_web.modulo_importa_xml import listar_xmls_da_pasta  # noqa: E402


class PastaBody(BaseModel):
    caminho: str


class IniciarBody(BaseModel):
    caminhos: List[str] = []


@router.post("/listar-pasta")
def listar_pasta(body: PastaBody):
    pasta = Path(body.caminho.strip())
    if not pasta.exists() or not pasta.is_dir():
        raise HTTPException(400, "Pasta não encontrada ou inválida.")
    itens = listar_xmls_da_pasta(str(pasta))
    return {"ok": True, "pasta": str(pasta), "itens": itens}


@router.post("/upload")
async def upload_xmls(arquivos: List[UploadFile] = File(...)):
    pasta = Path(tempfile.mkdtemp(prefix="xml_upload_"))
    itens = []
    for arq in arquivos:
        nome = Path(arq.filename or "nota.xml").name
        if not nome.lower().endswith(".xml"):
            continue
        destino = pasta / nome
        with open(destino, "wb") as f:
            shutil.copyfileobj(arq.file, f)
    itens = listar_xmls_da_pasta(str(pasta))
    return {"ok": True, "pasta": str(pasta), "itens": itens}


@router.post("/iniciar")
def iniciar_importacao(body: IniciarBody):
    if not body.caminhos:
        raise HTTPException(400, "Nenhum XML selecionado.")
    itens = [{"caminho": c, "arquivo": Path(c).name} for c in body.caminhos if c.strip()]
    return robo_service.iniciar_importacao_xml(itens)
