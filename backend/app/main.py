import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import backend.app.bootstrap as boot
from backend.app.ws_manager import ws_manager
from backend.app.routers import (
    notas, veiculos, itens, filtros, config, robo, logs,
    parametros, relatorios, importa_xml, suporte, licenca, email,
)
from backend.app.services.background_service import background_service

FRONTEND_DIR = boot.WEB_ROOT / "frontend"

app = FastAPI(
    title="Sistema Automação NFe — Web",
    description="Sistema web autônomo de Contas a Pagar / NFe",
    version="1.0.0",
)


@app.on_event("startup")
async def startup():
    ws_manager.bind_loop(asyncio.get_running_loop())
    try:
        boot.db.inicializar_banco()
        boot.db.gerar_chave_seguranca()
        boot.db.configurar_usuario_master()
        boot.log_service.garantir_tabelas()
        background_service.iniciar()
    except Exception as exc:
        print("")
        print("=" * 60)
        print("ERRO: nao foi possivel conectar ao PostgreSQL.")
        print(f"  {exc}")
        print("")
        print("Verifique:")
        print("  1. PostgreSQL em execucao")
        print("  2. Banco criado (CREATE DATABASE nfe_web;)")
        print("  3. Arquivo .env com PG_HOST, PG_USER, PG_PASSWORD")
        print("=" * 60)
        print("")
        raise


app.include_router(notas.router)
app.include_router(veiculos.router)
app.include_router(itens.router)
app.include_router(filtros.router)
app.include_router(config.router)
app.include_router(robo.router)
app.include_router(logs.router)
app.include_router(parametros.router)
app.include_router(relatorios.router)
app.include_router(importa_xml.router)
app.include_router(suporte.router)
app.include_router(licenca.router)
app.include_router(email.router)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "sistema": "web",
        "raiz": str(boot.WEB_ROOT),
        "banco": boot.db.caminho_banco(),
    }


@app.websocket("/ws")
async def websocket_logs(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


app.mount("/assets/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
app.mount("/assets/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")


@app.get("/")
def pagina_inicial():
    return FileResponse(FRONTEND_DIR / "index.html")
