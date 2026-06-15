"""Controle de parada do robô NFe (fecha o navegador ao clicar em Parar)."""
import threading

from tenant_context import get_tenant_slug


def _nova_sessao():
    return {
        'browser': None,
        'parar': False,
        'parar_apos_nota': False,
        'rodando': False,
    }


_LOCK = threading.RLock()
_sessoes = {}


class RoboParadoPeloUsuario(Exception):
    """Usuário clicou em Parar — encerrar automação."""


def _tenant_key():
    return get_tenant_slug() or '__default__'


def _obter_sessao():
    with _LOCK:
        return _sessoes.setdefault(_tenant_key(), _nova_sessao())


def esta_rodando():
    return bool(_obter_sessao()['rodando'])


def registrar_browser(browser):
    with _LOCK:
        _obter_sessao()['browser'] = browser


def solicitar_parada():
    """Sinaliza parada e fecha o navegador Chromium aberto pelo robô."""
    with _LOCK:
        sessao = _obter_sessao()
        sessao['parar'] = True
        sessao['parar_apos_nota'] = False
        browser = sessao.get('browser')
    if not browser:
        return
    try:
        browser.close()
    except Exception:
        pass
    with _LOCK:
        _obter_sessao()['browser'] = None


def verificar_parada():
    if _obter_sessao()['parar']:
        raise RoboParadoPeloUsuario('Parado pelo usuário')


def solicitar_parada_apos_nota():
    with _LOCK:
        _obter_sessao()['parar_apos_nota'] = True


def consumir_parada_apos_nota():
    with _LOCK:
        sessao = _obter_sessao()
        if not sessao.get('parar_apos_nota'):
            return False
        sessao['parar_apos_nota'] = False
        return True


def parada_solicitada():
    return bool(_obter_sessao().get('parar'))


def marcar_rodando(valor):
    with _LOCK:
        sessao = _obter_sessao()
        sessao['rodando'] = bool(valor)
        if valor:
            sessao['parar'] = False
            sessao['parar_apos_nota'] = False


def encerrar_sessao():
    solicitar_parada()
    with _LOCK:
        sessao = _obter_sessao()
        sessao['rodando'] = False
        sessao['parar_apos_nota'] = False
