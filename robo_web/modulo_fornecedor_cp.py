"""Regras de fornecedor e parcelas na nota de Conta a Pagar."""
import re
import time

import database_setup as db
from robo_web.filial_embarque import (
    _aguardar_ajax_erp,
    clicar_gravar_nota,
    desmarcar_despesa_dados_gerais_nota,
    garantir_nota_cp_editavel,
    localizar_checkbox_despesa_nota,
    pagina_eh_nota_cp,
    preparar_tela_dados_gerais_nota_cp,
    preencher_filial_ue_dados_gerais_nota,
    _checkbox_despesa_marcado,
)

# Pausas curtas entre ações na nota CP
_PAUSA_CURTA = 0.18
_PAUSA_ABA = 0.28
_PAUSA_AJAX = 0.35
_PAUSA_FLAG_CP = 2.0  # ERP: aguardar após NF Fatura / A Faturar (nota duplicata)


def parsear_codigos_fornecedores(texto):
    """Converte '243, 094284, 12' em lista de códigos únicos."""
    codigos = []
    vistos = set()
    for parte in re.split(r'[,;\s]+', str(texto or '')):
        codigo = re.sub(r'\D', '', parte.strip())
        if not codigo or codigo in vistos:
            continue
        vistos.add(codigo)
        codigos.append(codigo)
    return codigos


def carregar_codigos_fornecedores_parametro():
    dados = db.carregar_filtros() or {}
    return parsear_codigos_fornecedores(dados.get('fornecedores_fatura_afaturar', ''))


def fornecedor_nota_na_lista_parametro(page):
    codigos_param = carregar_codigos_fornecedores_parametro()
    if not codigos_param:
        return False, ''
    codigo_forn = extrair_codigo_fornecedor_nota(page)
    if not codigo_forn:
        return False, ''
    return codigo_forn in codigos_param, codigo_forn


def extrair_codigo_fornecedor_nota(page):
    """Extrai o código do fornecedor do autocomplete (ex.: ...-243)."""
    candidatos = (
        page.locator('input#formnota\\:Nota_fornInput').first,
        page.locator('input[name="formnota:Nota_fornInput"]').first,
        page.locator('input[id^="formnota:"][id$="Nota_fornInput"]').first,
    )
    for campo in candidatos:
        try:
            if campo.count() == 0:
                continue
            valor = str(campo.input_value(timeout=2000) or '').strip()
            if not valor:
                continue
            match = re.search(r'-(\d+)\s*$', valor)
            if match:
                return match.group(1)
            match = re.search(r'\[(\d+)\]\s*$', valor)
            if match:
                return match.group(1)
        except Exception:
            continue
    return ''


def _checkbox_marcado(locator):
    try:
        if locator.count() == 0:
            return False
        return bool(locator.evaluate('el => !!el.checked'))
    except Exception:
        return False


def localizar_checkbox_nf_fatura(page):
    return page.locator('input#formnota\\:Nota_nfFatura').first


def localizar_checkbox_a_faturar(page):
    return page.locator('input#formnota\\:Nota_afaturar').first


def _aplicar_flags_nf_desmarcada_af_marcada(page):
    """
    Define estado final sem clique (evita marcar/desmarcar duas vezes no readonly).
    NF Fatura desmarcada + A Faturar marcada; dispara AJAX só uma vez.
    """
    return page.evaluate(
        """() => {
            const nf = document.getElementById('formnota:Nota_nfFatura');
            const af = document.getElementById('formnota:Nota_afaturar');
            if (!nf || !af) {
                return { ok: false, alterou: false, motivo: 'campos_nao_encontrados' };
            }
            if (!nf.checked && af.checked) {
                return { ok: true, alterou: false, motivo: 'ja_correto' };
            }
            nf.removeAttribute('readonly');
            af.removeAttribute('readonly');
            const alterou = nf.checked || !af.checked;
            nf.checked = false;
            af.checked = true;
            if (alterou) {
                nf.dispatchEvent(new Event('change', { bubbles: true }));
                nf.dispatchEvent(new Event('blur', { bubbles: true }));
                af.dispatchEvent(new Event('change', { bubbles: true }));
                af.dispatchEvent(new Event('blur', { bubbles: true }));
            }
            return {
                ok: !nf.checked && af.checked,
                alterou,
                motivo: 'ajustado',
            };
        }""",
    )


def _flags_nf_af_corretas(page):
    chk_nf = localizar_checkbox_nf_fatura(page)
    chk_af = localizar_checkbox_a_faturar(page)
    return (
        chk_nf.count() > 0
        and chk_af.count() > 0
        and not _checkbox_marcado(chk_nf)
        and _checkbox_marcado(chk_af)
    )


def _clicar_flag_ate_estado(checkbox, desejado_marcado, log, nome, max_cliques=4):
    """
    Clica na flag até o estado bater com o esperado (marcada/desmarcada).
    Após cada clique aguarda 2s e verifica; repete se estiver diferente.
    """
    if checkbox.count() == 0:
        log(f'   ⚠️ {nome}: checkbox não encontrado.')
        return False

    desejado_marcado = bool(desejado_marcado)
    esperado_txt = 'marcada' if desejado_marcado else 'desmarcada'

    def _ler_estado():
        return _checkbox_marcado(checkbox)

    if _ler_estado() == desejado_marcado:
        return True

    for clique in range(max_cliques):
        estado_atual = _ler_estado()
        atual_txt = 'marcada' if estado_atual else 'desmarcada'
        if estado_atual == desejado_marcado:
            log(f'   ✅ {nome}: {atual_txt} (condição atendida).')
            return True

        log(
            f'   ⚠️ {nome}: atual {atual_txt}, esperado {esperado_txt} '
            f'— clique {clique + 1}/{max_cliques}...'
        )
        try:
            checkbox.scroll_into_view_if_needed(timeout=2000)
            checkbox.click(force=True, timeout=2500)
        except Exception:
            pass

        time.sleep(_PAUSA_FLAG_CP)
        estado_depois = _ler_estado()
        depois_txt = 'marcada' if estado_depois else 'desmarcada'
        if estado_depois == desejado_marcado:
            log(f'   ✅ {nome}: {depois_txt} após clique.')
            return True

        if clique < max_cliques - 1:
            log(f'   🔁 {nome}: ainda {depois_txt} — repetindo clique...')

    estado_final = _ler_estado()
    final_txt = 'marcada' if estado_final else 'desmarcada'
    log(f'   ❌ {nome}: permanece {final_txt} (esperado {esperado_txt}).')
    return False


def _aplicar_flags_nf_af_com_retry(page, log, max_tentativas=3):
    """Aplica NF desmarcada + AF marcada com retry e clique de fallback."""
    if _flags_nf_af_corretas(page):
        return {'ok': True, 'alterou': False, 'motivo': 'ja_correto'}

    alterou_alguma = False
    for tentativa in range(max_tentativas):
        try:
            resultado = _aplicar_flags_nf_desmarcada_af_marcada(page) or {}
            alterou_alguma = alterou_alguma or bool(resultado.get('alterou'))
        except Exception:
            resultado = {}

        if resultado.get('alterou'):
            time.sleep(_PAUSA_FLAG_CP)
        else:
            time.sleep(_PAUSA_AJAX)

        if _flags_nf_af_corretas(page):
            return {'ok': True, 'alterou': alterou_alguma, 'motivo': 'ajustado'}

        chk_nf = localizar_checkbox_nf_fatura(page)
        chk_af = localizar_checkbox_a_faturar(page)

        nf_ok = True
        af_ok = True
        if _checkbox_marcado(chk_nf):
            nf_ok = _clicar_flag_ate_estado(
                chk_nf, False, log, 'NF Fatura (nota duplicata)',
            )
        if not _checkbox_marcado(chk_af):
            af_ok = _clicar_flag_ate_estado(
                chk_af, True, log, 'A Faturar',
            )

        if nf_ok and af_ok and _flags_nf_af_corretas(page):
            return {'ok': True, 'alterou': True, 'motivo': 'clique'}

        if tentativa < max_tentativas - 1:
            log(f'   🔁 Retentando flags NF/AF ({tentativa + 2}/{max_tentativas})...')

    return {'ok': False, 'alterou': alterou_alguma, 'motivo': 'falha'}


def ajustar_flags_fornecedor_na_nota(page, log):
    """
    Se o fornecedor estiver no parâmetro e NF Fatura estiver marcada:
    desmarca NF Fatura (duplicata) e marca A Faturar.
    """
    codigos_param = carregar_codigos_fornecedores_parametro()
    if not codigos_param:
        log('   ℹ️ Nenhum fornecedor configurado em Parâmetros ERP para regra A Faturar.')
        return False

    codigo_forn = extrair_codigo_fornecedor_nota(page)
    if not codigo_forn:
        log('   ⚠️ Código do fornecedor não identificado na nota CP.')
        return False

    if codigo_forn not in codigos_param:
        log(
            f'   ℹ️ Fornecedor {codigo_forn} fora da lista do parâmetro '
            f'({", ".join(codigos_param)}).'
        )
        return False

    chk_nf = localizar_checkbox_nf_fatura(page)
    chk_af = localizar_checkbox_a_faturar(page)
    if chk_nf.count() == 0 or chk_af.count() == 0:
        log('   ⚠️ Checkboxes NF Fatura / A Faturar não encontrados.')
        return False

    nf_marcado = _checkbox_marcado(chk_nf)
    af_marcado = _checkbox_marcado(chk_af)

    if not nf_marcado and af_marcado:
        log(f'   ℹ️ Fornecedor {codigo_forn}: NF Fatura desmarcada e A Faturar marcada.')
        return False

    log(
        f'   🔄 Fornecedor {codigo_forn}: NF Fatura desmarcada + A Faturar marcada '
        f'(estado atual: NF={"sim" if nf_marcado else "não"}, AF={"sim" if af_marcado else "não"})...'
    )
    try:
        resultado = _aplicar_flags_nf_af_com_retry(page, log) or {}
    except Exception as e:
        log(f'   ⚠️ Erro ao ajustar flags: {e}')
        return False

    if resultado.get('ok'):
        if resultado.get('alterou'):
            log('   ✅ Flags NF Fatura / A Faturar ajustadas.')
        else:
            log('   ✅ Flags NF Fatura / A Faturar confirmadas.')
        return bool(resultado.get('alterou'))

    log('   ⚠️ Não foi possível confirmar as flags NF Fatura / A Faturar.')
    return bool(resultado.get('alterou'))


def _verificar_e_corrigir_passo1_apos_gravar(page, log, desmarcar_despesa, fornecedor_na_lista):
    """Reaplica flags/Despesa se o ERP reverteu após gravar Dados Gerais."""
    precisa_regravar = False

    if fornecedor_na_lista and not _flags_nf_af_corretas(page):
        log('   🔁 Flags NF/AF revertidas após gravar — reaplicando...')
        if _aplicar_flags_nf_af_com_retry(page, log).get('ok'):
            precisa_regravar = True

    if desmarcar_despesa:
        checkbox_despesa = localizar_checkbox_despesa_nota(page)
        if checkbox_despesa.count() > 0 and _checkbox_despesa_marcado(checkbox_despesa):
            log('   🔁 Flag Despesa voltou após gravar — desmarcando novamente...')
            if desmarcar_despesa_dados_gerais_nota(page, log):
                precisa_regravar = True

    if precisa_regravar:
        if not clicar_gravar_nota(page, log):
            log('   ⚠️ Regravação após correção de flags não confirmada.')
    return precisa_regravar


def extrair_codigo_interno_nota_cp(page, fallback=''):
    """Obtém o código interno da nota CP na tela do ERP."""
    fallback = str(fallback or '').strip()
    candidatos = (
        page.locator('input#formnota\\:Nota_codigo').first,
        page.locator('input[name="formnota:Nota_codigo"]').first,
        page.locator('input[id^="formnota:"][id*="codigo"]').first,
        page.locator('input[name^="formnota:"][name*="codigo"]').first,
    )
    for campo in candidatos:
        try:
            if campo.count() == 0:
                continue
            valor = str(campo.input_value(timeout=1500) or '').strip()
            if valor and re.fullmatch(r'\d+', valor):
                return valor
        except Exception:
            continue

    try:
        url = page.url or ''
        for pattern in (
            r'[?&]cod(?:igo)?=(\d+)',
            r'[?&]nota=(\d+)',
            r'/(\d{4,})(?:[/?#]|$)',
        ):
            match = re.search(pattern, url, re.I)
            if match:
                return match.group(1)
    except Exception:
        pass
    return fallback


def verificar_e_corrigir_antes_finalizar(page, log, desmarcar_despesa=False):
    """
    Checagem final na aba Dados Gerais antes de Finalizar:
    - Terceiro/Agregado: Despesa desmarcada
    - Fornecedor na lista: NF Fatura (duplicata) desmarcada + A Faturar marcada
    """
    if page.is_closed() or not pagina_eh_nota_cp(page):
        log('   ⚠️ Tela da nota CP indisponível na verificação pré-finalização.')
        return False

    if not preparar_tela_dados_gerais_nota_cp(page, log):
        return False

    if not garantir_nota_cp_editavel(page, log, contexto='pré-finalização'):
        return False

    log('   🔍 Pré-finalização — verificando flags Despesa / NF Fatura / A Faturar...')
    precisa_gravar = False

    na_lista, cod_forn = fornecedor_nota_na_lista_parametro(page)
    if na_lista:
        if _flags_nf_af_corretas(page):
            log(
                f'   ✅ Fornecedor {cod_forn}: NF Fatura desmarcada e A Faturar marcada.'
            )
        else:
            log(
                f'   🔄 Fornecedor {cod_forn}: corrigindo NF Fatura (duplicata) / A Faturar...'
            )
            if not _aplicar_flags_nf_af_com_retry(page, log).get('ok'):
                log('   ❌ Flags NF Fatura / A Faturar incorretas antes de finalizar.')
                return False
            precisa_gravar = True
    else:
        log('   ℹ️ Fornecedor fora da lista — sem checagem NF Fatura / A Faturar.')

    if desmarcar_despesa:
        checkbox_despesa = localizar_checkbox_despesa_nota(page)
        if checkbox_despesa.count() == 0:
            log('   ⚠️ Checkbox Despesa não encontrado na pré-finalização.')
            return False
        if _checkbox_despesa_marcado(checkbox_despesa):
            log("   🔄 Terceiro/Agregado: flag Despesa ainda marcada — desmarcando...")
            if not desmarcar_despesa_dados_gerais_nota(page, log):
                log('   ❌ Não foi possível desmarcar Despesa antes de finalizar.')
                return False
            precisa_gravar = True
        else:
            log('   ✅ Flag Despesa OK (desmarcada).')
    else:
        log('   ℹ️ Nota própria — sem checagem de Despesa.')

    if precisa_gravar:
        log('   💾 Gravando correções da pré-finalização...')
        if not clicar_gravar_nota(page, log):
            log('   ❌ Gravação pré-finalização não confirmada.')
            return False
        garantir_nota_cp_editavel(page, log, contexto='após gravar pré-finalização')

        if na_lista and not _flags_nf_af_corretas(page):
            log('   ❌ Flags NF/A Faturar revertidas após gravar pré-finalização.')
            return False
        if desmarcar_despesa:
            checkbox_despesa = localizar_checkbox_despesa_nota(page)
            if checkbox_despesa.count() > 0 and _checkbox_despesa_marcado(checkbox_despesa):
                log('   ❌ Flag Despesa voltou após gravar pré-finalização.')
                return False
    else:
        log('   ✅ Pré-finalização — flags já corretas, seguindo para Finalizar.')

    return True


def ajustar_dados_gerais_pos_importacao_completo(
    page,
    log,
    cod_filial='',
    cod_ue='',
    aplicar_fixo=False,
    desmarcar_despesa=False,
):
    """Passo 1: fornecedor (se aplicável), Filial/UE, Despesa e grava."""
    if page.is_closed() or not pagina_eh_nota_cp(page):
        log('   ⚠️ Tela da nota CP indisponível no Passo 1.')
        return False

    if not preparar_tela_dados_gerais_nota_cp(page, log):
        return False

    if not garantir_nota_cp_editavel(page, log, contexto='antes do Passo 1'):
        return False

    log('   📋 Passo 1 — Aba 1. Dados Gerais (somente gravar; finalizar só no fim)...')
    precisa_gravar = False
    fornecedor_na_lista = False

    if ajustar_flags_fornecedor_na_nota(page, log):
        precisa_gravar = True
        fornecedor_na_lista = True
    else:
        na_lista, _ = fornecedor_nota_na_lista_parametro(page)
        fornecedor_na_lista = na_lista

    if aplicar_fixo:
        log('   🏢 Filial e UE na nota (Dados Gerais)...')
        if not preencher_filial_ue_dados_gerais_nota(page, log, cod_filial, cod_ue):
            return False
        precisa_gravar = True

    if desmarcar_despesa:
        log("   🚚 Veículo Agregado/Terceiro: desmarcando flag 'Despesa'...")
        if not desmarcar_despesa_dados_gerais_nota(page, log):
            return False
        precisa_gravar = True

    if precisa_gravar:
        if not clicar_gravar_nota(page, log):
            log('   ⚠️ Gravação dos Dados Gerais não confirmada.')
        else:
            garantir_nota_cp_editavel(page, log, contexto='após gravar Passo 1')
            _verificar_e_corrigir_passo1_apos_gravar(
                page, log, desmarcar_despesa, fornecedor_na_lista,
            )
    else:
        log('   ℹ️ Dados Gerais sem alteração no Passo 1.')

    log('   ➡️ Passo 1 concluído — seguindo para aba 2 Itens (sem finalizar agora).')
    return True


def abrir_aba_parcelas_nota(page, log):
    """Abre a aba 3 direto (sem passar pela aba 1)."""
    seletores_parcelas = (
        'tbody[id*="tableduplicataPagar"]',
        'input#formduplicataPagar\\:DuplicataPagar_parcela',
    )

    def _parcelas_visiveis():
        for seletor in seletores_parcelas:
            try:
                page.locator(seletor).first.wait_for(state='visible', timeout=2000)
                return True
            except Exception:
                continue
        return False

    if _parcelas_visiveis():
        log('   📑 Aba 3. Parcelas já visível.')
        return True

    candidatos = (
        page.locator('span.rf-tab-lbl', has_text=re.compile(r'^\s*3\.\s*Parcelas', re.I)).first,
        page.locator('span.rf-tab-lbl', has_text=re.compile(r'Parcelas', re.I)).first,
        page.locator('td, a, span').filter(has_text=re.compile(r'^\s*3\.\s*Parcelas', re.I)).first,
    )
    for aba in candidatos:
        if aba.count() == 0:
            continue
        try:
            aba.click(force=True)
            if _parcelas_visiveis():
                log('   📑 Aba 3. Parcelas aberta.')
                return True
        except Exception:
            continue

    log('   ⚠️ Não foi possível abrir a aba 3. Parcelas.')
    return False


def _links_parcelas(page):
    return page.locator('a[id^="formtableduplicataPagar:tableduplicataPagar:"]')


def _aceitar_dialogo_confirmacao(dialog):
    """Diálogo nativo do Chrome (confirm) — não existe botão OK no HTML."""
    try:
        msg = (dialog.message or '').lower()
        if dialog.type == 'confirm' and ('remo' in msg or 'confirma' in msg):
            dialog.accept()
        elif dialog.type == 'confirm':
            dialog.accept()
        else:
            dialog.dismiss()
    except Exception:
        try:
            dialog.accept()
        except Exception:
            pass


def _registrar_aceite_dialogo(context):
    """Registra aceite de confirm() em todas as abas do contexto."""
    try:
        context.on('dialog', _aceitar_dialogo_confirmacao)
    except Exception:
        pass


def _remover_aceite_dialogo(context):
    try:
        context.remove_listener('dialog', _aceitar_dialogo_confirmacao)
    except Exception:
        pass


def _clicar_botao_remover_parcela(page):
    """Clica em remover via JS (confirm automático) — diálogo nativo não tem seletor HTML."""
    page.evaluate(
        """() => {
            window.confirm = function() { return true; };
            window.alert = function() {};
            const btn = document.getElementById('formduplicataPagar:remduplicataPagar');
            if (!btn) {
                throw new Error('Botão remover parcela não encontrado');
            }
            btn.click();
        }""",
    )


def remover_todas_parcelas_nota(page, log):
    """Remove todas as parcelas da aba 3 (confirma o diálogo nativo do navegador)."""
    if not abrir_aba_parcelas_nota(page, log):
        return False

    log('   💳 Passo 3 — Removendo parcelas da nota CP...')
    contexto = page.context
    _registrar_aceite_dialogo(contexto)
    page.evaluate('() => { window.confirm = function() { return true; }; }')

    try:
        return _remover_parcelas_loop(page, log)
    finally:
        _remover_aceite_dialogo(contexto)


def _remover_parcelas_loop(page, log):
    for tentativa in range(30):
        if page.is_closed():
            return False

        links = _links_parcelas(page)
        total = links.count()
        if total == 0:
            log('   ✅ Nenhuma parcela restante na nota.')
            return True

        try:
            codigo_parcela = (links.first.inner_text(timeout=2000) or '').strip()
        except Exception:
            codigo_parcela = '?'

        log(f'   -> Parcela {tentativa + 1}: abrindo {codigo_parcela}...')
        try:
            links.first.click(force=True)
        except Exception:
            log(f'   ⚠️ Não foi possível abrir a parcela {codigo_parcela}.')
            return False

        time.sleep(_PAUSA_ABA)

        campo_parcela = page.locator('input#formduplicataPagar\\:DuplicataPagar_parcela').first
        try:
            campo_parcela.wait_for(state='visible', timeout=8000)
            valor_parcela = (campo_parcela.input_value(timeout=2000) or '').strip()
        except Exception:
            log(f'   ⚠️ Campo da parcela não apareceu após abrir {codigo_parcela}.')
            return False

        if not valor_parcela:
            log('   ⚠️ Número da parcela não carregou; tentando próximo link...')
            time.sleep(_PAUSA_CURTA)
            continue

        btn_remover = page.locator('input#formduplicataPagar\\:remduplicataPagar').first
        if btn_remover.count() == 0:
            log('   ⚠️ Botão remover parcela não encontrado.')
            return False

        log(f'   -> Removendo parcela {valor_parcela} (confirmando OK automaticamente)...')
        qtd_antes = total
        try:
            btn_remover.scroll_into_view_if_needed(timeout=3000)
            _clicar_botao_remover_parcela(page)
        except Exception as e:
            log(f'   ⚠️ Falha ao remover parcela {valor_parcela}: {e}')
            return False

        removida = False
        for _ in range(20):
            time.sleep(0.12)
            if _links_parcelas(page).count() < qtd_antes:
                removida = True
                break
            try:
                if not (campo_parcela.input_value(timeout=400) or '').strip():
                    removida = True
                    break
            except Exception:
                pass

        if not removida:
            log(f'   ⚠️ Parcela {valor_parcela} ainda visível após remover; tentando de novo...')
            continue

        log(f'   ✅ Parcela {valor_parcela} removida.')
        _aguardar_ajax_erp(page, timeout_ms=2000)

    log('   ⚠️ Limite de tentativas ao remover parcelas.')
    return False


# --- Validação do nome do fornecedor na importação CP (formCad) ---

_MSG_FORNECEDOR_ALTERADO = re.compile(r'Dados alterados com sucesso', re.I)


def carregar_cod_tipo_fornecedor_parametro():
    dados = db.carregar_filtros() or {}
    return str(dados.get('cod_tipo_fornecedor', '') or '').strip()


def extrair_nome_filtro_fornecedor_imp_cp(valor):
    """Extrai o nome do fornecedor (texto antes da vírgula) do autocomplete CP."""
    valor = str(valor or '').strip()
    if not valor:
        return ''
    return valor.split(',')[0].strip()


def _normalizar_nome_fornecedor_comparacao(nome):
    nome = str(nome or '').upper()
    nome = nome.replace('ª', '')
    return re.sub(r'[^A-Z0-9]', '', nome)


def nomes_fornecedor_equivalentes(nome_erp, nome_xml):
    """
    Compara nome exibido no ERP (antes da vírgula) com xNome do XML.
    Ignora apenas maiúsculas, pontuação e o caractere ª da fonte do ERP.
    """
    if not nome_erp or not nome_xml:
        return True
    return _normalizar_nome_fornecedor_comparacao(nome_erp) == _normalizar_nome_fornecedor_comparacao(nome_xml)


def _localizar_campo_filtro_forn_imp_cp(page):
    candidatos = (
        page.locator('input#formCad\\:filtroFornImpCPInput').first,
        page.locator('input[name="formCad:filtroFornImpCPInput"]').first,
    )
    for campo in candidatos:
        if campo.count() > 0:
            return campo
    return candidatos[0]


def _localizar_botao_inserir_fornecedor_imp_cp(page):
    campo = _localizar_campo_filtro_forn_imp_cp(page)
    if campo.count() > 0:
        xpaths = (
            'xpath=ancestor::tr[1]//img[@title="Inserir/Alterar"]',
            'xpath=ancestor::td[1]//img[@title="Inserir/Alterar"]',
            'xpath=ancestor::*[contains(@class,"rf-au")][1]//img[@title="Inserir/Alterar"]',
            'xpath=../..//img[@title="Inserir/Alterar"]',
            'xpath=following::img[@title="Inserir/Alterar"][1]',
        )
        for xpath in xpaths:
            botao = campo.locator(xpath)
            if botao.count() > 0:
                return botao.first

    candidatos = (
        page.locator('form#formCad img[src*="inserir.gif"][title="Inserir/Alterar"]').first,
        page.locator('input[id*="filtroFornImpCP"]')
        .locator('xpath=ancestor::tr[1]//img[@title="Inserir/Alterar"]')
        .first,
        page.locator('img[src*="inserir.gif"][title="Inserir/Alterar"]').first,
        page.locator('img[title="Inserir/Alterar"]').first,
    )
    for botao in candidatos:
        if botao.count() > 0:
            return botao
    return candidatos[-1]


def ler_xnome_emitente_xml(page, log):
    """Lê a tag xNome do emitente na aba Arquivo XML da NFe."""
    from robo_web.modulo_item import _voltar_aba_principal

    try:
        aba_xml = page.locator('span.rf-tab-lbl', has_text='Arquivo XML da NFe')
        if not aba_xml.is_visible(timeout=3000):
            log('   ⚠️ Aba XML não encontrada para validar emitente.')
            return ''
        aba_xml.click()
        time.sleep(1.5)

        xml_texto = page.locator('div, pre, td, span').filter(has_text='nfeProc').last.text_content()
        bloco_emit = re.search(r'<emit>.*?</emit>', xml_texto or '', re.DOTALL)
        if bloco_emit:
            match = re.search(r'<xNome>(.*?)</xNome>', bloco_emit.group(0), re.DOTALL)
            if match:
                nome = match.group(1).strip()
                log(f'   📄 xNome emitente no XML: {nome}')
                _voltar_aba_principal(page)
                return nome

        match = re.search(r'<xNome>(.*?)</xNome>', xml_texto or '', re.DOTALL)
        if match:
            nome = match.group(1).strip()
            log(f'   📄 xNome no XML: {nome}')
            _voltar_aba_principal(page)
            return nome

        log('   ⚠️ Tag xNome não encontrada no XML.')
        _voltar_aba_principal(page)
    except Exception as e:
        log(f'   ⚠️ Erro ao ler xNome do XML: {e}')
        try:
            from robo_web.modulo_item import _voltar_aba_principal
            _voltar_aba_principal(page)
        except Exception:
            pass
    return ''


def _campo_tipo_fornecedor_vazio(campo_tipo):
    try:
        return not str(campo_tipo.input_value(timeout=2000) or '').strip()
    except Exception:
        return True


def _preencher_tipo_fornecedor_autocomplete(aba_forn, cod_tipo, log):
    """Preenche autocomplete Tipo de Fornecedor (código dos Parâmetros ERP)."""
    cod_tipo = str(cod_tipo or '').strip()
    if not cod_tipo:
        return False

    campo_tipo = aba_forn.locator('input#formforn\\:Forn_tipoFornecedorInput')
    if campo_tipo.count() == 0:
        log('   ⚠️ Campo Tipo de Fornecedor não encontrado no cadastro.')
        return False

    if not _campo_tipo_fornecedor_vazio(campo_tipo):
        valor_atual = str(campo_tipo.input_value() or '').strip()
        log(f'   ℹ️ Tipo de fornecedor já preenchido: {valor_atual}')
        return True

    log(f'   → Digitando tipo de fornecedor: {cod_tipo}')
    campo_tipo.scroll_into_view_if_needed()
    campo_tipo = campo_tipo.first
    campo_tipo.click()
    time.sleep(0.25)
    try:
        campo_tipo.press('Control+A')
        campo_tipo.press('Backspace')
    except Exception:
        campo_tipo.fill('')
    campo_tipo.press_sequentially(cod_tipo, delay=80)
    time.sleep(1.2)
    campo_tipo.press('Enter')
    time.sleep(1.2)
    campo_tipo.press('Enter')
    time.sleep(0.4)
    campo_tipo.press('Tab')
    time.sleep(1.5)

    valor_final = str(campo_tipo.input_value() or '').strip()
    if valor_final:
        log(f'   ✅ Tipo de fornecedor selecionado: {valor_final}')
        return True

    sugestoes = (
        aba_forn.locator('tr.rf-au-opt').first,
        aba_forn.locator('table.rf-au-lst-scrl tbody tr').first,
        aba_forn.locator('div.rf-au-lst-scrl tr').first,
    )
    for sugestao in sugestoes:
        try:
            if sugestao.count() > 0 and sugestao.is_visible(timeout=1500):
                sugestao.click()
                time.sleep(1)
                valor_final = str(campo_tipo.input_value() or '').strip()
                if valor_final:
                    log(f'   ✅ Tipo de fornecedor selecionado (lista): {valor_final}')
                    return True
        except Exception:
            continue

    log(
        f'   ❌ Tipo de fornecedor não confirmado após digitar {cod_tipo}. '
        'Verifique o código em Parâmetros ERP.'
    )
    return False


def _ler_erros_gravacao_fornecedor(aba_forn):
    try:
        return aba_forn.evaluate(
            """() => Array.from(document.querySelectorAll('li.fontErrorMessages'))
                .map(el => (el.textContent || '').trim())
                .filter(Boolean)""",
        )
    except Exception:
        return []


def _aguardar_gravacao_fornecedor(aba_forn, log, timeout_seg=20):
    """Aguarda sucesso ou erro após gravar fornecedor. Retorna (ok, mensagem_erro)."""
    inicio = time.time()
    while time.time() - inicio < timeout_seg:
        try:
            textos_info = aba_forn.evaluate(
                """() => Array.from(document.querySelectorAll('li.fontInfoMessages'))
                    .map(el => (el.textContent || '').trim())
                    .filter(Boolean)""",
            )
            for texto in textos_info or []:
                if _MSG_FORNECEDOR_ALTERADO.search(texto):
                    log(f'   ✅ {texto}')
                    time.sleep(1)
                    return True, ''
        except Exception:
            pass

        erros = _ler_erros_gravacao_fornecedor(aba_forn)
        if erros:
            msg = erros[0]
            log(f'   ❌ Erro ERP ao gravar fornecedor: {msg}')
            return False, msg

        time.sleep(0.4)

    erros = _ler_erros_gravacao_fornecedor(aba_forn)
    if erros:
        msg = erros[0]
        log(f'   ❌ Erro ERP ao gravar fornecedor: {msg}')
        return False, msg

    log('   ⚠️ Mensagem "Dados alterados com sucesso" não apareceu a tempo.')
    return False, 'Gravação do fornecedor não confirmada pelo ERP.'


def _corrigir_cadastro_fornecedor_imp_cp(page, log, nome_xml):
    cod_tipo = carregar_cod_tipo_fornecedor_parametro()
    botao_inserir = _localizar_botao_inserir_fornecedor_imp_cp(page)
    aba_forn = None

    if botao_inserir.count() == 0:
        msg = 'Botão Inserir/Alterar do fornecedor não encontrado na tela.'
        log(f'   ❌ {msg}')
        return False, msg

    log('   → Clicando em Inserir/Alterar do fornecedor...')
    try:
        with page.context.expect_page() as nova_aba_info:
            botao_inserir.click()
        aba_forn = nova_aba_info.value
        aba_forn.wait_for_load_state('networkidle')
    except Exception as e:
        msg = f'Falha ao abrir cadastro do fornecedor: {e}'
        log(f'   ❌ {msg}')
        return False, msg

    try:
        campo_nome = aba_forn.locator('input#formforn\\:Forn_nome')
        campo_nome.wait_for(state='visible', timeout=10000)
        campo_nome.click(click_count=3)
        campo_nome.fill('')
        campo_nome.fill(str(nome_xml).upper())
        log(f'   ✏️ Nome do fornecedor atualizado para: {nome_xml.upper()}')

        campo_tipo = aba_forn.locator('input#formforn\\:Forn_tipoFornecedorInput').first
        if campo_tipo.count() > 0 and _campo_tipo_fornecedor_vazio(campo_tipo):
            if not cod_tipo:
                msg = (
                    'Tipo de fornecedor vazio — configure '
                    'Cod. Tipo Fornecedor em Parâmetros ERP.'
                )
                log(f'   ❌ {msg}')
                aba_forn.close()
                page.bring_to_front()
                return False, msg
            if not _preencher_tipo_fornecedor_autocomplete(aba_forn, cod_tipo, log):
                aba_forn.close()
                page.bring_to_front()
                return False, 'Tipo de fornecedor não confirmado no cadastro.'

        log('   → Gravando cadastro do fornecedor...')
        aba_forn.locator('input#formforn\\:gravarforn').click()
        try:
            aba_forn.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass

        ok, msg_erro = _aguardar_gravacao_fornecedor(aba_forn, log)
        aba_forn.close()
        page.bring_to_front()
        if not ok:
            return False, msg_erro

        time.sleep(1)
        return True, ''
    except Exception as e:
        msg = f'Erro ao gravar fornecedor: {e}'
        log(f'   ❌ {msg}')
        try:
            if aba_forn and not aba_forn.is_closed():
                aba_forn.close()
        except Exception:
            pass
        page.bring_to_front()
        return False, msg


def validar_e_corrigir_nome_fornecedor_imp_cp(page, log):
    """
    Após clicar em Importar no painel NFe:
    compara o fornecedor CP com xNome do XML; corrige cadastro se divergir.
    Retorna (ok, mensagem_erro).
    """
    campo = _localizar_campo_filtro_forn_imp_cp(page)
    if campo.count() == 0:
        return True, ''

    try:
        valor = str(campo.input_value(timeout=3000) or '').strip()
    except Exception:
        valor = ''

    if not valor:
        log(
            '   ℹ️ Fornecedor CP vazio — seguindo fluxo normal '
            'sem validar emitente do XML.'
        )
        return True, ''

    nome_erp = extrair_nome_filtro_fornecedor_imp_cp(valor)
    log(f'   🔎 Fornecedor no ERP: {nome_erp}')
    nome_xml = ler_xnome_emitente_xml(page, log)
    if not nome_xml:
        return True, ''

    norm_erp = _normalizar_nome_fornecedor_comparacao(nome_erp)
    norm_xml = _normalizar_nome_fornecedor_comparacao(nome_xml)
    if nomes_fornecedor_equivalentes(nome_erp, nome_xml):
        log(f'   ✅ Nome do fornecedor confere com o XML ({nome_xml}).')
        return True, ''

    log(
        f'   ⚠️ Nome ERP ({nome_erp}) difere do XML ({nome_xml}). '
        f'Normalizado ERP={norm_erp} | XML={norm_xml}. '
        'Abrindo cadastro para corrigir...'
    )
    return _corrigir_cadastro_fornecedor_imp_cp(page, log, nome_xml)
