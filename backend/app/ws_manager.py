import asyncio
import json
from typing import Dict, Optional, Set

from fastapi import WebSocket
from tenant_context import get_tenant_slug


class LogWebSocketManager:
    def __init__(self):
        self._clients: Dict[Optional[str], Set[WebSocket]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def _tenant_key(self, tenant_slug: Optional[str] = None) -> Optional[str]:
        slug = tenant_slug if tenant_slug is not None else get_tenant_slug()
        return str(slug).strip().lower() if slug else None

    async def connect(self, websocket: WebSocket, tenant_slug: Optional[str] = None):
        key = self._tenant_key(tenant_slug)
        await websocket.accept()
        self._clients.setdefault(key, set()).add(websocket)

    def disconnect(self, websocket: WebSocket):
        empty = []
        for key, clients in self._clients.items():
            clients.discard(websocket)
            if not clients:
                empty.append(key)
        for key in empty:
            self._clients.pop(key, None)

    async def broadcast(self, payload: dict, tenant_slug: Optional[str] = None):
        key = self._tenant_key(tenant_slug)
        dead = []
        text = json.dumps(payload, ensure_ascii=False)
        for ws in list(self._clients.get(key, set())):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def emit_from_thread(self, payload: dict, tenant_slug: Optional[str] = None):
        if not self._loop:
            return
        key = self._tenant_key(tenant_slug)
        asyncio.run_coroutine_threadsafe(self.broadcast(payload, tenant_slug=key), self._loop)


ws_manager = LogWebSocketManager()
