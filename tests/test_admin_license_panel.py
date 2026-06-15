import importlib
import os
from pathlib import Path
import sys
import types
import unittest


class AdminLicensePanelTests(unittest.TestCase):
    def _load_admin_with_stubs(self, licenca_stub):
        fastapi_stub = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class APIRouter:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, *args, **kwargs):
                return lambda fn: fn

            def post(self, *args, **kwargs):
                return lambda fn: fn

            def patch(self, *args, **kwargs):
                return lambda fn: fn

            def delete(self, *args, **kwargs):
                return lambda fn: fn

        fastapi_stub.APIRouter = APIRouter
        fastapi_stub.Header = lambda default="": default
        fastapi_stub.HTTPException = HTTPException

        pydantic_stub = types.ModuleType("pydantic")
        pydantic_stub.BaseModel = type("BaseModel", (), {})

        stubs = {
            "fastapi": fastapi_stub,
            "pydantic": pydantic_stub,
            "licenca_remota": licenca_stub,
        }
        antigos = {nome: sys.modules.get(nome) for nome in stubs}
        modulo_antigo = sys.modules.pop("backend.app.routers.admin", None)
        sys.modules.update(stubs)
        try:
            return importlib.import_module("backend.app.routers.admin"), antigos, modulo_antigo
        except Exception:
            sys.modules.pop("backend.app.routers.admin", None)
            if modulo_antigo is not None:
                sys.modules["backend.app.routers.admin"] = modulo_antigo
            for nome, modulo in antigos.items():
                if modulo is None:
                    sys.modules.pop(nome, None)
                else:
                    sys.modules[nome] = modulo
            raise

    def _restore_stubs(self, antigos, modulo_antigo):
        sys.modules.pop("backend.app.routers.admin", None)
        if modulo_antigo is not None:
            sys.modules["backend.app.routers.admin"] = modulo_antigo
        for nome, modulo in antigos.items():
            if modulo is None:
                sys.modules.pop(nome, None)
            else:
                sys.modules[nome] = modulo

    def test_listar_licencas_formats_registration_date(self):
        licenca_stub = types.ModuleType("licenca_remota")
        licenca_stub.listar_todas_licencas = lambda: [{
            "arquivo": "teste6.json",
            "data_registro": "2026-06-15 05:00:00",
        }]
        licenca_stub.formatar_data_registro_exibicao = lambda texto: "15/06/2026 05:00"

        os.environ["NFE_ADMIN_SECRET"] = "segredo"
        admin, antigos, modulo_antigo = self._load_admin_with_stubs(licenca_stub)
        try:
            resposta = admin.listar_licencas(x_admin_secret="segredo")
        finally:
            self._restore_stubs(antigos, modulo_antigo)

        self.assertTrue(resposta["ok"])
        self.assertEqual(resposta["licenses"][0]["data_registro_formatada"], "15/06/2026 05:00")

    def test_atualizar_licenca_uses_requested_status(self):
        chamadas = []
        licenca_stub = types.ModuleType("licenca_remota")
        licenca_stub.definir_ativado_arquivo = lambda arquivo, ativado: chamadas.append((arquivo, ativado)) or (True, "ok")

        os.environ["NFE_ADMIN_SECRET"] = "segredo"
        admin, antigos, modulo_antigo = self._load_admin_with_stubs(licenca_stub)
        try:
            body = types.SimpleNamespace(ativado="sim")
            resposta = admin.atualizar_licenca("teste6.json", body, x_admin_secret="segredo")
        finally:
            self._restore_stubs(antigos, modulo_antigo)

        self.assertTrue(resposta["ok"])
        self.assertEqual(chamadas, [("teste6.json", "sim")])

    def test_portal_has_requested_access_tabs(self):
        html = Path("frontend/portal.html").read_text(encoding="utf-8")

        self.assertIn("Automação Conta a Pagar", html)
        self.assertIn("Automação Conta a Pagar Web", html)
        self.assertIn("Web BI", html)
        self.assertIn("data-tab=\"conta-apagar\"", html)
        self.assertIn("data-tab=\"conta-apagar-web\"", html)
        self.assertIn("data-tab=\"web-bi\"", html)


if __name__ == "__main__":
    unittest.main()
