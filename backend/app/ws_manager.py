import asyncio
import json
from typing import Set

from fastapi import WebSocket


class LogWebSocketManager:
    def __init__(self):
        self._clients: Set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self._clients.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self._clients.discard(websocket)

    async def broadcast(self, payload: dict):
        dead = []
        text = json.dumps(payload, ensure_ascii=False)
        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def emit_from_thread(self, payload: dict):
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(payload), self._loop)


ws_manager = LogWebSocketManager()
