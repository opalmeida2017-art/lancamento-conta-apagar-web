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


class TenantRobotControlTests(unittest.TestCase):
    def test_robot_control_state_is_independent_per_tenant(self):
        from robo_web import controle_robo

        try:
            with tenant_scope("wcarlos", "nfe_web_wcarlos"):
                controle_robo.marcar_rodando(True)

            with tenant_scope("teste6", "nfe_web_teste6"):
                self.assertFalse(controle_robo.esta_rodando())
                controle_robo.marcar_rodando(True)
                controle_robo.solicitar_parada()

            with tenant_scope("wcarlos", "nfe_web_wcarlos"):
                self.assertTrue(controle_robo.esta_rodando())
                controle_robo.verificar_parada()

            with tenant_scope("teste6", "nfe_web_teste6"):
                with self.assertRaises(controle_robo.RoboParadoPeloUsuario):
                    controle_robo.verificar_parada()
        finally:
            for slug, db_name in (
                ("wcarlos", "nfe_web_wcarlos"),
                ("teste6", "nfe_web_teste6"),
            ):
                with tenant_scope(slug, db_name):
                    controle_robo.encerrar_sessao()


class TenantERPLockTests(unittest.TestCase):
    def test_erp_lock_does_not_block_other_tenant(self):
        from robo_web.erp_lock import ERP_LOCK

        acquired = threading.Event()

        def acquire_as_teste6():
            with tenant_scope("teste6", "nfe_web_teste6"):
                with ERP_LOCK:
                    acquired.set()

        with tenant_scope("wcarlos", "nfe_web_wcarlos"):
            with ERP_LOCK:
                thread = threading.Thread(target=acquire_as_teste6)
                thread.start()
                independent = acquired.wait(timeout=0.5)

        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(independent)


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


class RoboServiceTenantStatusTests(unittest.TestCase):
    def test_start_and_stop_are_independent_per_tenant(self):
        controle = {"rodando": {"wcarlos"}, "paradas": []}

        boot = types.ModuleType("backend.app.bootstrap")
        boot.db = types.SimpleNamespace(
            obter_pasta_tarifas_bancarias=lambda: "",
            carregar_configuracoes=lambda: {"link": "https://erp.example"},
            carregar_filtros=lambda: {},
        )
        boot.log_service = types.SimpleNamespace(
            adicionar_listener=lambda callback: None,
            remover_listener=lambda callback: None,
            iniciar_sessao=lambda origem="ROBO", descricao="": "sessao",
            finalizar_sessao=lambda *args, **kwargs: None,
            registrar_log=lambda *args, **kwargs: None,
        )

        ws_module = types.ModuleType("backend.app.ws_manager")
        ws_module.ws_manager = types.SimpleNamespace(emit_from_thread=lambda payload: None)

        controle_module = types.ModuleType("robo_web.controle_robo")
        controle_module.RoboParadoPeloUsuario = type("RoboParadoPeloUsuario", (Exception,), {})
        controle_module.esta_rodando = lambda: get_tenant_slug() in controle["rodando"]

        def solicitar_parada():
            controle["paradas"].append(get_tenant_slug())

        controle_module.solicitar_parada = solicitar_parada
        controle_module.solicitar_parada_apos_nota = lambda: None

        robo_pkg = types.ModuleType("robo_web")
        for nome in (
            "automacao",
            "modulo_frota",
            "modulo_importa_xml",
            "modulo_item_sync",
            "modulo_migracao",
            "modulo_tarifa_bancaria",
        ):
            setattr(robo_pkg, nome, types.SimpleNamespace())

        stubs = {
            "backend.app.bootstrap": boot,
            "backend.app.ws_manager": ws_module,
            "robo_web": robo_pkg,
            "robo_web.controle_robo": controle_module,
        }
        antigos = {nome: sys.modules.get(nome) for nome in stubs}
        modulo_servico_antigo = sys.modules.pop("backend.app.services.robo_service", None)
        sys.modules.update(stubs)
        try:
            service_module = importlib.import_module("backend.app.services.robo_service")
            service = service_module.RoboService()
            service._iniciar_thread = lambda target, *args: None
            service._agendar_monitor_tarifa = lambda: None

            with tenant_scope("wcarlos", "nfe_web_wcarlos"):
                service._active_tenants.add(service._tenant_key())
                state = service._tenant_state()
                state["status"] = "Executando wcarlos"
                state["sessao_log"] = "sessao-wcarlos"

            with tenant_scope("teste6", "nfe_web_teste6"):
                status_antes = service.status()
                resultado_inicio = service.iniciar()
                controle["rodando"].add("teste6")
                status_depois = service.status()
                resultado_parada = service.iniciar()

            with tenant_scope("wcarlos", "nfe_web_wcarlos"):
                status_wcarlos = service.status()

            self.assertFalse(status_antes["rodando"])
            self.assertEqual(status_antes["status"], "Parado")
            self.assertTrue(resultado_inicio["ok"])
            self.assertTrue(status_depois["rodando"])
            self.assertEqual(resultado_parada["acao"], "parada_solicitada")
            self.assertEqual(controle["paradas"], ["teste6"])
            self.assertTrue(status_wcarlos["rodando"])
            self.assertEqual(status_wcarlos["sessao_id"], "sessao-wcarlos")
        finally:
            sys.modules.pop("backend.app.services.robo_service", None)
            if modulo_servico_antigo is not None:
                sys.modules["backend.app.services.robo_service"] = modulo_servico_antigo
            for nome, modulo in antigos.items():
                if modulo is None:
                    sys.modules.pop(nome, None)
                else:
                    sys.modules[nome] = modulo


if __name__ == "__main__":
    unittest.main()
