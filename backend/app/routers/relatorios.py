import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

import relatorio_execucao
import relatorio_itens
from backend.app import bootstrap as boot

db = boot.db

router = APIRouter(prefix="/api/relatorios", tags=["relatorios"])

CABECALHOS_NOTAS = [
    "Inserção", "Cód. Interno", "Status", "Fornecedor", "No.Nota", "Placa", "KM",
    "Data Em.", "Valor", "Sit. NFe", "Chave NFe", "Filial", "Usuário Inserção",
    "Erro Importação", "Observação NFe", "NFe p/ Estoque", "Arquiva",
]

CABECALHOS_ITENS = ["Cód. Item", "Grupo Atual", "Negócio", "Descrição"]


def _linha_nota(nota):
    estoque = "☑" if "☑" in str(nota.get("nfe_estoque") or "") else "☐"
    arquiva = "☑" if "☑" in str(nota.get("nfe_arquiva") or "") else "☐"
    return [
        str(nota.get("data_insercao") or ""),
        str(nota.get("codigo_interno") or ""),
        str(nota.get("status") or ""),
        str(nota.get("fornecedor") or ""),
        str(nota.get("num_nota") or ""),
        str(nota.get("painel_placa") or ""),
        str(nota.get("painel_km") or ""),
        str(nota.get("data_em") or ""),
        str(nota.get("valor") or ""),
        str(nota.get("sit_nfe") or ""),
        str(nota.get("chave_nfe") or ""),
        str(nota.get("filial") or ""),
        str(nota.get("user_ins") or ""),
        str(nota.get("erro_importacao") or ""),
        str(nota.get("observacao_nfe") or ""),
        estoque,
        arquiva,
    ]


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


@router.get("/notas/html")
def relatorio_notas_html(
    dt_ini: str = "",
    dt_fim: str = "",
    cod: str = "",
    status: str = "Todos",
    nota: str = "",
    limite: Optional[str] = "100",
):
    lim = None if str(limite or "").lower() == "todos" else limite
    notas = db.listar_notas_filtradas(dt_ini, dt_fim, cod, status, nota, limite=lim)
    filtros = {
        "Data Emissão Inicial": dt_ini or "Todos",
        "Data Emissão Final": dt_fim or "Todos",
        "Cód. Interno": cod or "Todos",
        "Nº Nota": nota or "Todos",
        "Status": status,
        "Limite": limite,
    }
    linhas = [_linha_nota(n) for n in notas]
    html = relatorio_execucao.gerar_relatorio_html(filtros, CABECALHOS_NOTAS, linhas)
    return HTMLResponse(html)


@router.get("/notas/excel")
def relatorio_notas_excel(
    dt_ini: str = "",
    dt_fim: str = "",
    cod: str = "",
    status: str = "Todos",
    nota: str = "",
    limite: Optional[str] = "100",
):
    lim = None if str(limite or "").lower() == "todos" else limite
    notas = db.listar_notas_filtradas(dt_ini, dt_fim, cod, status, nota, limite=lim)
    filtros = {
        "Data Emissão Inicial": dt_ini or "Todos",
        "Data Emissão Final": dt_fim or "Todos",
        "Cód. Interno": cod or "Todos",
        "Nº Nota": nota or "Todos",
        "Status": status,
    }
    linhas = [_linha_nota(n) for n in notas]
    caminho = Path(tempfile.gettempdir()) / f"relatorio_notas_{Path().cwd().name}.xlsx"
    relatorio_execucao.salvar_relatorio_excel(filtros, CABECALHOS_NOTAS, linhas, caminho_saida=caminho)
    return FileResponse(caminho, filename=caminho.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/itens/html")
def relatorio_itens_html(
    cod: str = "",
    grupo: str = "Todos",
    descricao: str = "",
    limite: Optional[str] = "100",
):
    lim = None if str(limite or "").lower() == "todos" else limite
    itens = _filtrar_itens(cod, grupo, descricao, lim)
    filtros = {
        "Código": cod or "Todos",
        "Grupo": grupo,
        "Descrição": descricao or "Todos",
        "Limite": limite,
    }
    linhas = [
        [
            str(i.get("codItemD") or ""),
            str(i.get("descGrupoImp") or ""),
            str(i.get("descNegocioImp") or ""),
            str(i.get("descricao") or ""),
        ]
        for i in itens
    ]
    html = relatorio_itens.gerar_relatorio_html(filtros, CABECALHOS_ITENS, linhas)
    return HTMLResponse(html)


@router.get("/itens/excel")
def relatorio_itens_excel(
    cod: str = "",
    grupo: str = "Todos",
    descricao: str = "",
    limite: Optional[str] = "100",
):
    lim = None if str(limite or "").lower() == "todos" else limite
    itens = _filtrar_itens(cod, grupo, descricao, lim)
    filtros = {"Código": cod or "Todos", "Grupo": grupo, "Descrição": descricao or "Todos"}
    linhas = [
        [
            str(i.get("codItemD") or ""),
            str(i.get("descGrupoImp") or ""),
            str(i.get("descNegocioImp") or ""),
            str(i.get("descricao") or ""),
        ]
        for i in itens
    ]
    caminho = Path(tempfile.gettempdir()) / "relatorio_itens.xlsx"
    relatorio_itens.salvar_relatorio_excel(filtros, CABECALHOS_ITENS, linhas, caminho_saida=caminho)
    return FileResponse(caminho, filename=caminho.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/grupos")
def listar_grupos():
    itens = db.obter_itens_erp()
    grupos = sorted({str(i.get("descGrupoImp", "")).strip() for i in itens if i.get("descGrupoImp")})
    return {"ok": True, "grupos": ["Todos"] + grupos}
