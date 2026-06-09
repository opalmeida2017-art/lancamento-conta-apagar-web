from playwright.sync_api import sync_playwright
import time

from . import modulo_sefaz
from . import modulo_importacao
from .controle_robo import (
    RoboParadoPeloUsuario,
    encerrar_sessao,
    marcar_rodando,
    registrar_browser,
    solicitar_parada,
    verificar_parada,
)
from .erp_lock import ERP_LOCK
from .runtime_config import usar_headless
from .utils import ErroServidorIndisponivel, verificar_pagina_erp_ok

TEMPO_ESPERA_503_SEG = 120
TEMPO_ESPERA_RETOMADA_SEG = 120


def _aguardar_segundos(segundos, log, mensagem):
    log(mensagem)
    for _ in range(segundos):
        verificar_parada()
        time.sleep(1)


def _executar_sessao(
    p,
    config,
    meses,
    anos,
    log,
    nota_alvo=None,
    compra_estoque=False,
    ultimos_30_dias=False,
    hoje_apenas=False, # 🟢 ADICIONADO AQUI
):
    browser = p.chromium.launch(headless=usar_headless(), channel="chrome")
    registrar_browser(browser)
    context = browser.new_context(viewport={"width": 1380, "height": 900})
    page = context.new_page()
    try:
        verificar_parada()
        with ERP_LOCK:
            log('🔒 Sessão ERP exclusiva (robô NFe)')
            # 🟢 PASSAR O PARÂMETRO AQUI NA CHAMADA:
            if not modulo_sefaz.consultar_sefaz(
                page,
                config,
                meses,
                anos,
                log,
                ultimos_30_dias=ultimos_30_dias,
                hoje_apenas=hoje_apenas, # 🟢 ADICIONADO AQUI
            ):
                return False
                
                raise RuntimeError('Consulta SEFAZ não confirmou sucesso.')
            verificar_parada()
            verificar_pagina_erp_ok(page, log)
            time.sleep(2)
            verificar_parada()
            modulo_importacao.processar_importacao(
                page,
                log,
                {
                    'processadas': set(),
                    'atualizar_agora': True,
                    'nota_alvo': nota_alvo,
                    'nota_alvo_estoque': compra_estoque,
                },
            )
        return True
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        registrar_browser(None)


def iniciar_automacao(
    config,
    meses,
    anos,
    progresso_callback=None,
    nota_alvo=None,
    compra_estoque=False,
    ultimos_30_dias=False,
    hoje_apenas=False, # 🟢 ADICIONADO AQUI
):
    def log(msg):
        if progresso_callback:
            progresso_callback(msg)
        print(f'[ROBÔ]: {msg}')

    marcar_rodando(True)
    modo_continuo = not str(nota_alvo or '').strip()
    try:
        while True:
            verificar_parada()
            try:
                with sync_playwright() as p:
                    _executar_sessao(
                        p,
                        config,
                        meses,
                        anos,
                        log,
                        nota_alvo=nota_alvo,
                        compra_estoque=compra_estoque,
                        ultimos_30_dias=ultimos_30_dias,
                        hoje_apenas=hoje_apenas, # 🟢 ADICIONADO AQUI
                    )
                if not modo_continuo:
                    log('Automação concluída.')
                    return
                _aguardar_segundos(
                    TEMPO_ESPERA_RETOMADA_SEG,
                    log,
                    '✅ Ciclo concluído. Aguardando 2 min para verificar novas notas...',
                )
                log('🔄 Retomando monitoramento automático do painel...')
            except RoboParadoPeloUsuario:
                log('Robô parado pelo usuário. Navegador fechado.')
                raise
            except ErroServidorIndisponivel:
                if not modo_continuo:
                    raise
                _aguardar_segundos(
                    TEMPO_ESPERA_503_SEG,
                    log,
                    f'Servidor ERP em manutenção (503). Aguardando {TEMPO_ESPERA_503_SEG // 60} min para tentar novamente...',
                )
                log('Reiniciando robô (novo navegador)...')
            except Exception as e:
                from .controle_robo import _sessao
                if _sessao.get('parar'):
                    log('Robô parado pelo usuário. Navegador fechado.')
                    raise RoboParadoPeloUsuario() from e
                if not modo_continuo:
                    log(f'ERRO: {e}')
                    raise
                _aguardar_segundos(
                    TEMPO_ESPERA_RETOMADA_SEG,
                    log,
                    f'⚠️ Erro de tela/processamento: {e}. Aguardando 2 min para retomar...',
                )
                log('🔄 Retomando monitoramento automático após erro...')
    finally:
        encerrar_sessao()