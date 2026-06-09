"""Controle de parada do robô NFe (fecha o navegador ao clicar em Parar)."""

_sessao = {
    'browser': None,
    'parar': False,
    'parar_apos_nota': False,
    'rodando': False,
}


class RoboParadoPeloUsuario(Exception):
    """Usuário clicou em Parar — encerrar automação."""


def esta_rodando():
    return bool(_sessao['rodando'])


def registrar_browser(browser):
    _sessao['browser'] = browser


def solicitar_parada():
    """Sinaliza parada e fecha o navegador Chromium aberto pelo robô."""
    _sessao['parar'] = True
    _sessao['parar_apos_nota'] = False
    browser = _sessao.get('browser')
    if not browser:
        return
    try:
        browser.close()
    except Exception:
        pass
    _sessao['browser'] = None


def verificar_parada():
    if _sessao['parar']:
        raise RoboParadoPeloUsuario('Parado pelo usuário')


def solicitar_parada_apos_nota():
    _sessao['parar_apos_nota'] = True


def consumir_parada_apos_nota():
    if not _sessao.get('parar_apos_nota'):
        return False
    _sessao['parar_apos_nota'] = False
    return True


def marcar_rodando(valor):
    _sessao['rodando'] = bool(valor)
    if valor:
        _sessao['parar'] = False
        _sessao['parar_apos_nota'] = False


def encerrar_sessao():
    solicitar_parada()
    _sessao['rodando'] = False
    _sessao['parar_apos_nota'] = False
