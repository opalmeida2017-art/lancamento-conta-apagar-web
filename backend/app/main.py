import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import backend.app.bootstrap as boot
from backend.app.middleware.tenant_middleware import TenantMiddleware
from backend.app.ws_manager import ws_manager
from backend.app.routers import (
    notas, veiculos, itens, filtros, config, robo, logs,
    parametros, relatorios, importa_xml, suporte, licenca, email, tarifa,
    admin, portal,
)
from backend.app.services.background_service import background_service

FRONTEND_DIR = boot.WEB_ROOT / "frontend"

app = FastAPI(
    title="Sistema Automação NFe — Web",
    description="Sistema web autônomo de Contas a Pagar / NFe (multi-transportadora)",
    version="1.1.0",
)

app.add_middleware(TenantMiddleware)


@app.on_event("startup")
async def startup():
    ws_manager.bind_loop(asyncio.get_running_loop())
    try:
        import web_tenant_registry as registry
        registry.load_registry()
    except Exception:
        pass
    try:
        boot.db.inicializar_banco()
        boot.db.gerar_chave_seguranca()
        boot.db.configurar_usuario_master()
        boot.log_service.garantir_tabelas()
        background_service.iniciar()
    except Exception as exc:
        if not os.getenv("NFE_ALLOW_START_WITHOUT_DB"):
            print("")
            print("=" * 60)
            print("AVISO: banco padrao indisponivel (modo multi-tenant OK).")
            print(f"  {exc}")
            print("=" * 60)
            print("")


app.include_router(portal.router)
app.include_router(admin.router)
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
app.include_router(tarifa.router)


@app.get("/api/health")
def health():
    from tenant_context import get_tenant_slug
    return {
        "ok": True,
        "sistema": "web",
        "tenant": get_tenant_slug(),
        "raiz": str(boot.WEB_ROOT),
        "banco": boot.db.caminho_banco(),
    }


@app.websocket("/ws")
async def websocket_logs(websocket: WebSocket):
    await _websocket_logs(websocket)


@app.websocket("/t/{tenant_slug}/ws")
async def tenant_websocket_logs(websocket: WebSocket, tenant_slug: str):
    import web_tenant_registry as registry

    slug = str(tenant_slug or "").strip().lower()
    tenant = registry.get_tenant(slug)
    if not tenant or not tenant.get("ativo", True):
        await websocket.close(code=1008)
        return
    await _websocket_logs(websocket, tenant_slug=slug)


async def _websocket_logs(websocket: WebSocket, tenant_slug: str | None = None):
    await ws_manager.connect(websocket, tenant_slug=tenant_slug)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


app.mount("/assets/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
app.mount("/assets/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")


@app.get("/")
def pagina_root(request: Request):
    if getattr(request.state, "tenant", None):
        return FileResponse(FRONTEND_DIR / "index.html")
    return FileResponse(FRONTEND_DIR / "portal.html")
