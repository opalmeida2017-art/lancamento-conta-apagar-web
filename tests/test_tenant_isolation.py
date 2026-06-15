import asyncio
import importlib
import sys
import threading
import types
import unittest

from tenant_context import get_tenant_db, get_tenant_slug, preserve_tenant_context, tenant_scope


class TenantContextThreadTests(unittest.TestCase):
    def test_preserve_tenant_context_keeps_slug_and_database_in_thread(self):
        seen = []

        def target():
            seen.append((get_tenant_slug(), get_tenant_db()))

        with tenant_scope("teste6", "nfe_web_teste6"):
            thread = threading.Thread(target=preserve_tenant_context(target))
            thread.start()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(seen, [("teste6", "nfe_web_teste6")])


class WebSocketTenantIsolationTests(unittest.TestCase):
    def test_broadcast_only_reaches_clients_from_same_tenant(self):
        fastapi_stub = types.ModuleType("fastapi")
        fastapi_stub.WebSocket = object
        sys.modules.setdefault("fastapi", fastapi_stub)
        ws_module = importlib.import_module("backend.app.ws_manager")
        manager = ws_module.LogWebSocketManager()

        class FakeWebSocket:
            def __init__(self):
                self.sent = []

            async def accept(self):
                pass

            async def send_text(self, text):
                self.sent.append(text)

        async def scenario():
            teste6 = FakeWebSocket()
            wcarlos = FakeWebSocket()
            await manager.connect(teste6, tenant_slug="teste6")
            await manager.connect(wcarlos, tenant_slug="wcarlos")

            await manager.broadcast({"tipo": "log", "mensagem": "log wcarlos"}, tenant_slug="wcarlos")
            return teste6, wcarlos

        teste6, wcarlos = asyncio.run(scenario())

        self.assertEqual(teste6.sent, [])
        self.assertEqual(len(wcarlos.sent), 1)
        self.assertIn("log wcarlos", wcarlos.sent[0])


if __name__ == "__main__":
    unittest.main()
