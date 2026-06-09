import re
import time
import unicodedata


class ErroServidorIndisponivel(Exception):
    """ERP retornou HTTP 503 (Payara — Service Unavailable)."""


def pagina_servidor_indisponivel(page):
    """Detecta a tela HTTP 503 do Payara (SAT / Intersite)."""
    try:
        if page.is_closed():
            return False
        titulo = page.title() or ''
        try:
            corpo = page.locator('body').inner_text(timeout=5000)
        except Exception:
            corpo = page.content()
    except Exception:
        return False

    if 'HTTP Status 503' in titulo or 'HTTP Status 503' in corpo:
        return True
    if 'Service Unavailable' in corpo and 'Payara Server' in corpo:
        return True
    if 'Service Unavailable' in titulo and '503' in titulo:
        return True
    return False


def normalizar_vinculo_veiculo(vinculo):
    """Remove acentos e padroniza o vínculo vindo da frota (Próprio, Agregado, Terceiro...)."""
    texto = str(vinculo or '').strip().upper()
    texto = unicodedata.normalize('NFD', texto)
    return ''.join(c for c in texto if unicodedata.category(c) != 'Mn')


def vinculo_veiculo_eh_proprio(vinculo):
    return 'PROPRIO' in normalizar_vinculo_veiculo(vinculo)


def vinculo_veiculo_exige_desmarcar_despesa(vinculo):
    """Agregado, Terceiro e demais vínculos não próprios exigem desmarcar Despesa."""
    norm = normalizar_vinculo_veiculo(vinculo)
    if not norm:
        return False
    return not vinculo_veiculo_eh_proprio(vinculo)


def codigo_negocio_por_vinculo(vinculo):
    """1 = Frota (próprio), 2 = Frete/Agenciamento (agregado, terceiro, etc.)."""
    if vinculo_veiculo_exige_desmarcar_despesa(vinculo):
        return '2'
    return '1'


def unificar_codigo_negocio_nota(codigos_por_item):
    """
    Define o negócio da nota inteira:
    - Qualquer item Agregado/Terceiro (2) -> todos Frete/Agenciamento (2)
    - Caso contrário -> todos Frota (1)
    """
    if any(str(codigo or '').strip() == '2' for codigo in (codigos_por_item or [])):
        return '2'
    return '1'


def nome_negocio_erp(codigo_negocio):
    return 'FROTA (1)' if str(codigo_negocio).strip() == '1' else 'FRETE/AGENCIAMENTO (2)'


def verificar_pagina_erp_ok(page, log=None):
    """Lança ErroServidorIndisponivel se a página atual for erro 503."""
    if pagina_servidor_indisponivel(page):
        if log:
            log('⚠️ Servidor ERP indisponível (HTTP 503). Fechando e aguardando 2 min...')
        raise ErroServidorIndisponivel('HTTP 503 - Service Unavailable')


def confirmar_continuar_login_sitesat(page, log=None, espera_total_seg=12):
    """
    Após o login, clica em 'Continuar Login no SiteSAT' se o botão aparecer
    (geralmente no rodapé da página).
    """
    locators = (
        page.locator('input#formCad\\:continuarpopup'),
        page.locator('input[name="formCad:continuarpopup"]'),
        page.locator('input[value="Continuar Login no SiteSAT"]'),
        page.locator('input[type="submit"][value*="Continuar Login"]'),
    )
    fim = time.time() + max(1, int(espera_total_seg))
    while time.time() < fim:
        for loc in locators:
            try:
                if loc.count() == 0:
                    continue
                btn = loc.first
                if not btn.is_visible(timeout=400):
                    continue
                if log:
                    log('→ Confirmando login: clicando em "Continuar Login no SiteSAT"...')
                btn.scroll_into_view_if_needed()
                btn.click(force=True)
                try:
                    page.wait_for_load_state('networkidle', timeout=30000)
                except Exception:
                    page.wait_for_load_state('load', timeout=30000)
                time.sleep(1)
                verificar_pagina_erp_ok(page, log)
                return True
            except Exception:
                continue
        time.sleep(0.4)
    return False


def fazer_login_erp(page, config, log=None, timeout_goto_ms=60000):
    """Login padrão no ERP e confirmação SiteSAT quando o botão aparecer."""
    if log:
        log('Realizando login no sistema...')
    page.goto(config['link'], timeout=timeout_goto_ms)
    try:
        page.wait_for_load_state('networkidle', timeout=30000)
    except Exception:
        page.wait_for_load_state('load', timeout=30000)
    verificar_pagina_erp_ok(page, log)

    page.locator('input[type="text"]').first.fill(config['user_sis'])
    page.locator('input[type="password"]').first.fill(config['senha_sis'])
    page.locator('input[value="Entrar"], button:has-text("Entrar")').first.click(force=True)

    try:
        page.wait_for_load_state('networkidle', timeout=30000)
    except Exception:
        page.wait_for_load_state('load', timeout=30000)
    time.sleep(2)
    verificar_pagina_erp_ok(page, log)
    confirmar_continuar_login_sitesat(page, log=log)
    time.sleep(0.5)
    verificar_pagina_erp_ok(page, log)


def converter_modelo_para_regex(modelo):
    """Transforma 'Placa : AAA-1A11' em regex (comparação exata com a NFe)."""
    match = re.search(r'([A1][A1\-\s]{5,}[A1])', modelo)
    if not match:
        return None

    mascara = match.group(1)
    prefixo = modelo[:match.start()]

    mascara_regex = ''
    for char in mascara:
        if char == 'A':
            mascara_regex += r'[A-Z]'
        elif char == '1':
            mascara_regex += r'\d'
        elif char == ' ':
            mascara_regex += ' '
        elif char == '-':
            mascara_regex += r'-'
        else:
            mascara_regex += re.escape(char)

    return re.escape(prefixo) + f'({mascara_regex})'


def converter_modelo_km_para_regex(modelo):
    """Transforma 'odometro : 111.111,11' em regex (comparação exata com a NFe)."""
    texto = str(modelo or '').strip()
    idx = texto.find('1')
    if idx < 0:
        return None

    prefixo = texto[:idx]
    template = texto[idx:].replace(' ', '')
    if len(template) == 1:
        num_re = r'[\d\.,]+'
    else:
        num_re = ''.join(
            r'\d' if char == '1' else (r'\.' if char == '.' else re.escape(char))
            for char in template
        )

    return re.escape(prefixo) + r'(' + num_re + r')'


def limpar_km_extraido(km_bruto):
    """Preenche no ERP somente dígitos (sem ponto, vírgula ou traço)."""
    texto = str(km_bruto or '').strip()
    if ',' in texto:
        return texto.replace('.', '').split(',')[0]
    return re.sub(r'\D', '', texto)

def obter_mensagem_erro_erp(page):
    """Lê o texto do primeiro <li> de erro visível na tela do ERP."""
    marcadores_erro = (
        'inválido', 'invalido', 'bloqueado', 'favor selecionar',
        'erro', 'não encontrad', 'nao encontrad', 'recusad',
    )
    ignorar = (
        'sucesso', 'alterados com sucesso', 'finalizada com sucesso',
        'reiniciada com sucesso',
    )
    try:
        itens = page.locator('li')
        for i in range(itens.count()):
            li = itens.nth(i)
            try:
                if not li.is_visible(timeout=300):
                    continue
            except Exception:
                continue
            texto = (li.text_content() or '').strip()
            if not texto or len(texto) < 8:
                continue
            tl = texto.lower()
            if any(x in tl for x in ignorar):
                continue
            if any(m in tl for m in marcadores_erro):
                return texto
    except Exception:
        pass
    return ''


def voltar_ao_painel_nfe(page, log):
    """Fecha a tela da nota e volta ao painel NFe."""
    log('Clicando em Voltar e aguardando a página carregar...')
    page.bring_to_front()
    btn = page.locator('input[value="Voltar"]')
    if btn.count() > 0:
        btn.first.click(force=True)
    try:
        page.wait_for_load_state('networkidle', timeout=20000)
    except Exception:
        pass
    time.sleep(1.5)


def abortar_nota_com_erro(page, log, dados, erro_msg):
    """Registra o erro no painel (banco) e volta ao painel NFe."""
    log(f'❌ ERRO ABORTANDO: {erro_msg}')
    import database_setup as db
    db.registrar_erro_nota_painel(dados, erro_msg)
    voltar_ao_painel_nfe(page, log)
    