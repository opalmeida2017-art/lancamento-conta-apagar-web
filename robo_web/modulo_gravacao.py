import time
import re
import database_setup as db
from robo_web.filial_embarque import (
    abrir_aba_dados_gerais_nota,
    clicar_finalizar_nota,
    continuar_pagina_nota_cp,
    garantir_nota_cp_editavel,
    localizar_botao_importar_cp,
    localizar_link_abrir_nota,
    nota_cp_esta_finalizada,
    obter_codigos_para_nota,
    preparar_tela_dados_gerais_nota_cp,
    processar_itens_nota_cp_completo,
)
from robo_web.modulo_fornecedor_cp import (
    ajustar_dados_gerais_pos_importacao_completo,
    extrair_codigo_interno_nota_cp,
    fornecedor_nota_na_lista_parametro,
    remover_todas_parcelas_nota,
    verificar_e_corrigir_antes_finalizar,
)

_MSG_CP_FINALIZADA = re.compile(
    r'Conta\s+a\s+Pagar\s+finalizada\s+com\s+sucesso', re.I,
)
_MSG_CP_REINICIADA = re.compile(
    r'Conta\s+a\s+Pagar\s+reiniciada\s+com\s+sucesso', re.I,
)


def _nota_exige_desmarcar_despesa(dados):
    return bool(
        dados.get('desmarcar_despesa_nota')
        or str(dados.get('codigo_negocio_veiculo') or '').strip() == '2'
    )


def _fechar_nota_cp_e_voltar_painel(page, nova_aba, log):
    try:
        nova_aba.close()
    except Exception:
        pass
    page.bring_to_front()
    btn_voltar = page.locator('input[value="Voltar"]')
    if btn_voltar.count() > 0 and btn_voltar.first.is_visible():
        btn_voltar.first.click(force=True)


def _aguardar_tela_nota_cp(page, log):
    """Aguarda o formulário da nota CP carregar (sem pausa fixa longa)."""
    try:
        page.wait_for_load_state('domcontentloaded', timeout=8000)
        page.locator('form#formnota, form[name="formnota"]').first.wait_for(
            state='visible', timeout=10000,
        )
    except Exception as e:
        log(f'   ⚠️ Formulário da nota CP demorou a carregar: {e}')
        time.sleep(0.6)


def _ler_mensagens_cp_pagina(page):
    """Lê mensagens li.fontErrorMessages relacionadas à Conta a Pagar."""
    textos = []
    try:
        loc = page.locator('li.fontErrorMessages, li').filter(
            has_text=re.compile(r'Conta\s+a\s+Pagar', re.I),
        )
        total = min(loc.count(), 8)
        for i in range(total):
            texto = (loc.nth(i).text_content() or '').strip()
            if texto:
                textos.append(texto)
    except Exception:
        pass
    return textos


def _aguardar_finalizacao_nota(page, log, codigo_interno=''):
    """
    Confirma finalização somente com a mensagem
    'Conta a Pagar finalizada com sucesso!'.
    Ignora 'Conta a Pagar reiniciada com sucesso!' (desfinalização).
    """
    codigo_interno = str(codigo_interno or '').strip()
    prazo = time.time() + 12

    while time.time() < prazo:
        for texto in _ler_mensagens_cp_pagina(page):
            if _MSG_CP_REINICIADA.search(texto):
                log(f'   ℹ️ ERP: {texto} (nota desfinalizada para edição)')
                continue
            if _MSG_CP_FINALIZADA.search(texto):
                cod = extrair_codigo_interno_nota_cp(page, codigo_interno)
                log(f'   ✅ {texto}')
                if cod:
                    log(f'   📋 Código interno CP registrado: {cod}')
                return True, cod

        if nota_cp_esta_finalizada(page):
            cod = extrair_codigo_interno_nota_cp(page, codigo_interno)
            log('   ✅ Nota CP finalizada (estado bloqueado confirmado no ERP).')
            if cod:
                log(f'   📋 Código interno CP: {cod}')
            return True, cod

        time.sleep(0.25)

    for texto in _ler_mensagens_cp_pagina(page):
        if _MSG_CP_REINICIADA.search(texto) and not _MSG_CP_FINALIZADA.search(texto):
            log(
                '   ⚠️ ERP informou reinício da nota, mas não confirmou finalização '
                f'("{texto}").'
            )
            break

    return False, codigo_interno


def _registrar_nota_finalizada_painel(dados, codigo_interno, log):
    """Registra no painel somente após finalização confirmada."""
    codigo_interno = str(codigo_interno or '').strip()
    if not codigo_interno:
        log('   ⚠️ Finalização OK, mas código interno não identificado para o painel.')
        return False

    dados = dict(dados or {})
    dados['status'] = 'Importado'
    dados['codigo_interno'] = codigo_interno
    dados['erro_importacao'] = ''
    db.marcar_nota_importada_painel(dados)
    log(f'   📋 Painel atualizado — nota finalizada (código interno {codigo_interno}).')
    return True


def _executar_finalizacao_nota_cp(
    nova_aba,
    page_painel,
    log,
    dados,
    cod_filial,
    cod_ue,
    aplicar_fixo,
    codigo_negocio_nota,
    codigo_interno,
):
    """
    Fluxo após importar para CP:
    1) Dados Gerais: fornecedor (lista), Filial, UE, Despesa e salvar
    2) Itens: UE + Negócio item a item (grava só se alterar)
    3) Parcelas: remove todas (somente fornecedor na lista)
    4) Dados Gerais: Finalizar
    """
    cod_ue_itens = cod_ue if aplicar_fixo else ''
    page_nota = nova_aba

    if not garantir_nota_cp_editavel(page_nota, log, contexto='ao abrir nota CP'):
        return None

    if not ajustar_dados_gerais_pos_importacao_completo(
        page_nota,
        log,
        cod_filial=cod_filial,
        cod_ue=cod_ue,
        aplicar_fixo=aplicar_fixo,
        desmarcar_despesa=_nota_exige_desmarcar_despesa(dados),
    ):
        return None

    page_nota = continuar_pagina_nota_cp(
        page_nota, page_nota.context, page_painel, codigo_interno, log,
    )
    if not page_nota:
        return None

    log('   📦 Passo 2 — Aba 2 Itens...')
    if not processar_itens_nota_cp_completo(
        page_nota,
        log,
        cod_unidade=cod_ue_itens,
        codigo_negocio=codigo_negocio_nota,
    ):
        return None

    page_nota = continuar_pagina_nota_cp(
        page_nota, page_nota.context, page_painel, codigo_interno, log,
    )
    if not page_nota:
        return None

    if not garantir_nota_cp_editavel(page_nota, log, contexto='antes das Parcelas'):
        return None

    na_lista, cod_forn = fornecedor_nota_na_lista_parametro(page_nota)
    if na_lista:
        log(f'   📌 Fornecedor {cod_forn} na lista: removendo parcelas (Passo 3)...')
        if not remover_todas_parcelas_nota(page_nota, log):
            return None
        page_nota = continuar_pagina_nota_cp(
            page_nota, page_nota.context, page_painel, codigo_interno, log,
        )
        if not page_nota:
            return None

    log('   🏁 Passo final — Voltando para Dados Gerais e finalizando a nota...')
    if not abrir_aba_dados_gerais_nota(page_nota, log):
        if not preparar_tela_dados_gerais_nota_cp(page_nota, log):
            return None

    if not garantir_nota_cp_editavel(page_nota, log, contexto='antes de Finalizar'):
        return None

    desmarcar_despesa = _nota_exige_desmarcar_despesa(dados)
    if not verificar_e_corrigir_antes_finalizar(
        page_nota, log, desmarcar_despesa=desmarcar_despesa,
    ):
        log('   ❌ Pré-finalização falhou — flags Despesa/NF/A Faturar não confirmadas.')
        return None

    if not clicar_finalizar_nota(page_nota, log):
        return None

    finalizado, codigo_interno_final = _aguardar_finalizacao_nota(
        page_nota, log, codigo_interno,
    )
    if not finalizado:
        log('   🔁 Finalização não confirmada — tentando Finalizar novamente...')
        if verificar_e_corrigir_antes_finalizar(
            page_nota, log, desmarcar_despesa=desmarcar_despesa,
        ) and clicar_finalizar_nota(page_nota, log):
            finalizado, codigo_interno_final = _aguardar_finalizacao_nota(
                page_nota, log, codigo_interno,
            )

    if not finalizado:
        log('   ❌ Conta a Pagar não confirmou "finalizada com sucesso".')
        return None

    _registrar_nota_finalizada_painel(dados, codigo_interno_final, log)
    time.sleep(0.3)
    return page_nota


def abrir_cp_linha_e_finalizar(page, log, linha, dados):
    """
    Abre a nota pelo link Abrir CP no painel NFe e executa o fluxo de finalização na CP.
    Usado quando o usuário relança manualmente uma nota já importada no ERP.
    """
    from robo_web.modulo_importacao import (
        _carregar_dados_nota_alvo,
        _extrair_codigo_interno_status,
        localizar_link_abrir_cp_linha,
    )

    num_nota = str((dados or {}).get('num_nota') or '').strip()
    dados = dict(_carregar_dados_nota_alvo(num_nota) or {})
    dados['num_nota'] = num_nota
    dados['status'] = 'Processando'
    dados['erro_importacao'] = ''
    db.salvar_nota_raspada(dados)

    cod_filial, cod_ue, aplicar_fixo = obter_codigos_para_nota(log)
    codigo_negocio_nota = str(dados.get('codigo_negocio_veiculo') or '1').strip()
    if codigo_negocio_nota not in ('1', '2'):
        codigo_negocio_nota = '1'

    codigo_interno = str(dados.get('codigo_interno') or '').strip()
    if not codigo_interno:
        codigo_interno = _extrair_codigo_interno_status(linha)

    link_abrir = localizar_link_abrir_cp_linha(linha)
    if not link_abrir:
        log(f'   ⚠️ Link Abrir CP não encontrado na nota {num_nota}.')
        return False

    log(f'📝 Abrindo Conta a Pagar da nota {num_nota} (Abrir CP)...')
    try:
        with page.context.expect_page() as nova_aba_info:
            link_abrir.click()
        nova_aba = nova_aba_info.value
        _aguardar_tela_nota_cp(nova_aba, log)
    except Exception as e:
        log(f'   ❌ Falha ao abrir a nota CP: {e}')
        return False

    if not codigo_interno:
        try:
            match = re.search(r'\b(\d{4,})\b', nova_aba.url or '')
            if match:
                codigo_interno = match.group(1)
        except Exception:
            pass

    page_nota = _executar_finalizacao_nota_cp(
        nova_aba,
        page,
        log,
        dados,
        cod_filial,
        cod_ue,
        aplicar_fixo,
        codigo_negocio_nota,
        codigo_interno or num_nota,
    )
    if not page_nota:
        msg = 'Falha ao finalizar a nota na Conta a Pagar (Abrir CP).'
        log(f'   ❌ {msg}')
        db.registrar_erro_nota_painel(dados, msg)
        _fechar_nota_cp_e_voltar_painel(page, nova_aba, log)
        return False

    log(f'✅ Nota {num_nota} reprocessada e finalizada (Abrir CP).')

    try:
        if not page_nota.is_closed():
            page_nota.close()
    except Exception:
        pass
    page.bring_to_front()
    return True


def finalizar_gravacao(page, log, dados):
    cod_filial, cod_ue, aplicar_fixo = obter_codigos_para_nota(log)
    codigo_negocio_nota = str(dados.get('codigo_negocio_veiculo') or '1').strip()
    if codigo_negocio_nota not in ('1', '2'):
        codigo_negocio_nota = '1'

    log("💾 Clicando em 'Importar para uma Conta a Pagar'...")
    
    btn_importar = localizar_botao_importar_cp(page)
    btn_importar.click()
    
    log("⏳ Aguardando processamento do ERP (Sucesso ou Erro)...")
    
    for tentativa in range(15):
        time.sleep(1)
        
        erros_li = page.locator("li")
        if erros_li.count() > 0:
            for i in range(erros_li.count()):
                texto_erro = erros_li.nth(i).text_content().strip()
                texto_lower = texto_erro.lower()
                
                if "gerencia estoque" in texto_lower and "inválido" in texto_lower:
                    log("⚠️ Erro detectado: Item não gerencia estoque! Iniciando correção em todos os itens...")
                    
                    linhas_item = page.locator('tbody[id="formCad:tableItemNota:tb"] > tr.rf-dt-r')
                    total_itens = linhas_item.count()
                    
                    for idx_fix in range(total_itens):
                        log(f"   -> Ajustando gerenciamento do Item {idx_fix + 1}...")
                        linha_item = page.locator(f'tr[id="formCad:tableItemNota:{idx_fix}"]')
                        
                        with page.context.expect_page() as nova_aba_item:
                            linha_item.locator('img[title="Inserir/Alterar"]').click()
                        
                        aba_item = nova_aba_item.value
                        aba_item.wait_for_load_state("networkidle")
                        
                        sel_estoque = aba_item.locator('select[id="formitemD:ItemD_gerenciaEstoque"]')
                        if sel_estoque.count() > 0:
                            sel_estoque.select_option(value="S")
                            log(f"      - Item {idx_fix + 1}: Alterado para 'Gerencia' [S]")
                            aba_item.locator('input[id="formitemD:gravaritemD"]').click()
                            time.sleep(2)
                        
                        aba_item.close()
                    
                    page.bring_to_front()
                    log("✅ Todos os itens foram corrigidos! Tentando importar a nota novamente...")
                    btn_importar.click()
                    time.sleep(2)
                    break
                
                if "bloqueado" in texto_lower or "inválido" in texto_lower or "selecionar novamente" in texto_lower:
                    log(f"⚠️ ERRO DETECTADO: {texto_erro}")
                    db.registrar_erro_nota_painel(dados, texto_erro)
                    log("⬅️ Clicando em Voltar...")
                    btn_voltar = page.locator('input[value="Voltar"]')
                    if btn_voltar.count() > 0:
                        btn_voltar.first.click(force=True)
                    return False

        link_sucesso = localizar_link_abrir_nota(page)
        if link_sucesso.count() > 0 and link_sucesso.first.is_visible():
            texto_sucesso = link_sucesso.first.text_content()
            codigo_interno = re.search(r'\d+', texto_sucesso).group()
            log(f"⭐⭐ SUCESSO! Código Gerado: {codigo_interno}")

            dados['codigo_interno'] = codigo_interno
            dados['erro_importacao'] = ""

            log(f"📝 Abrindo a nota {codigo_interno} para finalização...")
            
            with page.context.expect_page() as nova_aba_info:
                link_sucesso.first.click()
            
            nova_aba = nova_aba_info.value
            _aguardar_tela_nota_cp(nova_aba, log)

            page_nota = _executar_finalizacao_nota_cp(
                nova_aba,
                page,
                log,
                dados,
                cod_filial,
                cod_ue,
                aplicar_fixo,
                codigo_negocio_nota,
                codigo_interno,
            )
            if not page_nota:
                msg = 'Falha ao finalizar a nota na Conta a Pagar após importação.'
                log(f'   ❌ {msg}')
                db.registrar_erro_nota_painel(dados, msg)
                _fechar_nota_cp_e_voltar_painel(page, nova_aba, log)
                return False
            
            try:
                if not page_nota.is_closed():
                    page_nota.close()
            except Exception:
                pass
            page.bring_to_front()
            
            btn_voltar_extra = page.locator('input[value="Voltar"]')
            if btn_voltar_extra.count() > 0 and btn_voltar_extra.first.is_visible():
                btn_voltar_extra.first.click(force=True)
            return True

    db.registrar_erro_nota_painel(
        dados, 'Falha ao importar para Conta a Pagar (tempo esgotado ou sem resposta do ERP).',
    )
    log('⬅️ Voltando ao painel após falha na importação...')
    btn_voltar = page.locator('input[value="Voltar"]')
    if btn_voltar.count() > 0 and btn_voltar.first.is_visible():
        btn_voltar.first.click(force=True)
    return False
