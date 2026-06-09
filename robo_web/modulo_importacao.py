import re
import time
import database_setup as db
from robo_web.controle_robo import RoboParadoPeloUsuario, consumir_parada_apos_nota
from robo_web.utils import (
    abortar_nota_com_erro,
    codigo_negocio_por_vinculo,
    ErroServidorIndisponivel,
    nome_negocio_erp,
    normalizar_vinculo_veiculo,
    obter_mensagem_erro_erp,
    unificar_codigo_negocio_nota,
    verificar_pagina_erp_ok,
    vinculo_veiculo_exige_desmarcar_despesa,
    voltar_ao_painel_nfe,
)
from robo_web.modulo_veiculo import (
    processar_veiculo,
    MSG_ERRO_PLACA_VEICULO,
    MSG_ERRO_CARRETA_DUPLICADA,
    MSG_ERRO_FALTA_VEICULO_OBS,
)
from robo_web.modulo_km import processar_km
from robo_web.modulo_item import processar_cadastro_item
from robo_web.modulo_fornecedor_cp import validar_e_corrigir_nome_fornecedor_imp_cp
from robo_web.modulo_gravacao import finalizar_gravacao
from robo_web.filial_embarque import (
    localizar_botao_atualizar_painel,
    localizar_observacao_nota_cp,
    localizar_select_filial_lancamento,
    localizar_select_unidade_mestre,
    obter_codigos_para_nota,
)

TIMEOUT_TRAVADO_TENTATIVA_SEG = 90
TIMEOUT_TOTAL_DOWNLOAD_SEG = 600
SELETOR_LINHAS_NFE = "tbody[id='formCad:tablepnfedestinada:tbn'] tr"
MSG_XML_NAO_ENCONTRADO_ERP = 'XML não encontrado ERP por favor importa o xml '


def _desmarcar_checkboxes_painel_nfe(page, log=None):
    """Desmarca todos os checkboxes do painel (só 1 nota por vez no download)."""
    cbs = page.locator(f"{SELETOR_LINHAS_NFE} input[type='checkbox']")
    for i in range(cbs.count()):
        cb = cbs.nth(i)
        try:
            if cb.is_checked():
                cb.uncheck(force=True)
        except Exception:
            pass
    time.sleep(0.4)
    if log:
        log('   -> Checkboxes do painel desmarcados.')


def _marcar_checkbox_linha(linha):
    chk = linha.locator('input[type="checkbox"]').first
    if chk.count() > 0:
        chk.check(force=True)
        time.sleep(0.5)


def _atualizar_painel_nfe(page, log, segundos=4):
    """Atualiza a grade antes de analisar a próxima nota."""
    log('   -> Clicando em Atualizar Painel...')
    btn_atualizar = localizar_botao_atualizar_painel(page)
    if btn_atualizar.count() == 0:
        raise RuntimeError('Botão Atualizar Painel não encontrado.')
    btn_atualizar.click()
    time.sleep(segundos)
    verificar_pagina_erp_ok(page, log)
    try:
        page.wait_for_selector(SELETOR_LINHAS_NFE, state='visible', timeout=15000)
    except Exception:
        pass


def _localizar_filtro_num_nota(page):
    candidatos = [
        page.locator('input#formCad\\:filtroNumNota').first,
        page.locator('input[name="formCad:filtroNumNota"]').first,
        page.locator('input[id^="formCad:"][id$="filtroNumNota"]').first,
        page.locator('tr, td, div').filter(
            has_text=re.compile(r'n[ºo]\.?\s*nota', re.I),
            has=page.locator('input[type="text"]'),
        ).first.locator('input[type="text"]').first,
    ]
    for candidato in candidatos:
        if candidato.count() > 0:
            return candidato
    return candidatos[-1]


def _aplicar_filtro_num_nota(page, log, num_nota):
    num_nota = str(num_nota or '').strip()
    if not num_nota:
        return True

    campo = _localizar_filtro_num_nota(page)
    if campo.count() == 0:
        raise RuntimeError('Campo de filtro Nº Nota não encontrado no ERP.')

    try:
        campo.click(timeout=3000)
    except Exception:
        pass

    try:
        campo.fill(num_nota, timeout=4000)
    except Exception:
        try:
            campo.press('Control+A')
            campo.press('Backspace')
            campo.type(num_nota, delay=80)
        except Exception as e:
            raise RuntimeError(
                f'Não foi possível preencher o filtro Nº Nota no ERP: {e}'
            ) from e

    time.sleep(0.5)
    log(f'🔎 Filtrando painel ERP pela nota {num_nota} antes de atualizar...')
    return True


def _memoria_painel(memoria=None):
    """Estado do robô no painel: notas já finalizadas + controle de atualização."""
    if memoria is None:
        return {'processadas': set(), 'atualizar_agora': True}
    if isinstance(memoria, set):
        return {'processadas': memoria, 'atualizar_agora': True}
    memoria.setdefault('processadas', set())
    memoria.setdefault('atualizar_agora', True)
    return memoria


def _seguir_proxima_nota(page, log, memoria):
    """Atualiza o painel e recomeça pela fila (nº da nota guardado na varredura)."""
    _desmarcar_checkboxes_painel_nfe(page, log)
    _atualizar_painel_nfe(page, log, segundos=5)
    memoria['atualizar_agora'] = False
    return processar_importacao(page, log, memoria)


def _ler_dado_linha(linha, seletor):
    try:
        elemento = linha.locator(seletor)
        if elemento.count() > 0:
            texto = elemento.text_content()
            return texto.strip() if texto else ""
    except Exception:
        # Se a página ou navegador for fechado (TargetClosedError) durante a varredura,
        # captura o erro e retorna string vazia para que o robô encerre suavemente.
        pass
    return ""


def _id_nota_controle(dados, num_nota=''):
    """Identificador único da nota no painel (chave NFe preferencial)."""
    chave = (dados.get('chave_nfe') or '').strip()
    if chave:
        return chave
    return f'num:{num_nota}'


def _texto_status_linha(linha):
    cel_status = linha.locator('td.rf-edt-td-status')
    if cel_status.count() > 0:
        try:
            return (cel_status.first.inner_text(timeout=2000) or '').strip()
        except Exception:
            return (cel_status.first.text_content() or '').strip()
    return (_ler_dado_linha(linha, 'td.rf-edt-td-status') or '').strip()


def _status_eh_abrir_cp(status_texto):
    """Nota já importada no ERP — coluna status exibe link/texto Abrir CP."""
    texto = re.sub(r'\s+', ' ', (status_texto or '').upper().strip())
    return 'ABRIR' in texto and 'CP' in texto


def _linha_tem_link_abrir_cp(linha):
    """Link JSF 'Abrir CP' na coluna status (nota já importada no ERP)."""
    for sel in (
        'td.rf-edt-td-status a:has-text("Abrir CP")',
        'a:has-text("Abrir CP")',
    ):
        loc = linha.locator(sel)
        if loc.count() > 0:
            try:
                if loc.first.is_visible(timeout=800):
                    return True
            except Exception:
                return True
    try:
        if linha.get_by_role('link', name=re.compile(r'^Abrir\s*CP$', re.I)).count() > 0:
            return True
    except Exception:
        pass
    return False


def _linha_status_abrir_cp(linha):
    if _linha_tem_link_abrir_cp(linha):
        return True
    return _status_eh_abrir_cp(_texto_status_linha(linha))


def _extrair_codigo_interno_status(linha):
    """Tenta obter o código interno do texto/link da coluna status (ex.: Abrir CP)."""
    cel_status = linha.locator('td.rf-edt-td-status')
    if cel_status.count() == 0:
        return ''
    blocos = []
    try:
        blocos.append((cel_status.first.inner_text(timeout=2000) or '').strip())
    except Exception:
        pass
    try:
        blocos.append((cel_status.first.text_content() or '').strip())
    except Exception:
        pass
    for link in cel_status.locator('a').all():
        try:
            blocos.append((link.inner_text(timeout=1000) or '').strip())
            blocos.append((link.get_attribute('title') or '').strip())
            blocos.append((link.get_attribute('href') or '').strip())
        except Exception:
            continue
    for bloco in blocos:
        if not bloco:
            continue
        match = re.search(r'\b(\d{4,})\b', bloco)
        if match:
            return match.group(1)
    return ''


def _linha_tem_link_importar(linha):
    """Link/botão Importar na linha (não confundir com link Abrir CP)."""
    if _linha_status_abrir_cp(linha):
        return False
    for sel in (
        'td.rf-edt-td-status a:has-text("Importar")',
        'a:has-text("Importar")',
        'input[value="Importar"]',
        'input[value*="Importar"]',
    ):
        loc = linha.locator(sel)
        if loc.count() > 0:
            try:
                texto = (loc.first.inner_text(timeout=800) or '').strip().upper()
                if 'ABRIR' in texto and 'CP' in texto:
                    continue
                if loc.first.is_visible(timeout=800):
                    return True
            except Exception:
                return True
    try:
        if linha.get_by_role('link', name=re.compile(r'^Importar$', re.I)).count() > 0:
            return True
    except Exception:
        pass
    return False


def _linha_pronta_importar(linha):
    """XML disponível: coluna status = Importar ou link Importar na linha."""
    if _linha_status_abrir_cp(linha):
        return False
    status_texto = _texto_status_linha(linha).upper().strip()
    if status_texto == 'IMPORTAR' or status_texto.startswith('IMPORTAR'):
        return True
    if 'IMPORTAR' in status_texto and 'SEM XML' not in status_texto:
        return True
    if 'SEM XML' in status_texto:
        return _linha_tem_link_importar(linha)
    return _linha_tem_link_importar(linha)


def _linha_precisa_download(linha):
    """Próximo passo é baixar XML (mesma regra do fluxo antigo)."""
    if _linha_status_abrir_cp(linha):
        return False
    if _linha_pronta_importar(linha):
        return False
    status_texto = _texto_status_linha(linha).upper()
    if 'CANCELAD' in status_texto:
        return False
    return True


def _classificar_acao_linha(linha):
    if _linha_status_abrir_cp(linha):
        return 'marcar_importada'
    if _linha_pronta_importar(linha):
        return 'importar'
    if _linha_precisa_download(linha):
        return 'download'
    return 'ignorar'


def _nota_ja_processada(memoria, nid, num_nota):
    processadas = memoria['processadas']
    alvo = str(num_nota).strip()
    return nid in processadas or alvo in processadas


def _marcar_nota_processada(memoria, dados, num_nota):
    nid = _id_nota_controle(dados, num_nota)
    memoria['processadas'].add(nid)
    memoria['processadas'].add(str(num_nota).strip())


def _ir_para_proxima_nota_apos_tratar(page, log, memoria, dados):
    """Marca nota como tratada nesta sessão e segue na ordem da tabela."""
    num = dados.get('num_nota', '')
    _marcar_nota_processada(memoria, dados, num)
    if consumir_parada_apos_nota():
        log(
            f'🛑 Parada programada concluída após finalizar a nota {num}. '
            'Encerrando o robô para iniciar a importação XML.'
        )
        raise RoboParadoPeloUsuario('Parada programada após concluir a nota atual')
    log(f'➡️ Próxima nota (após tratar {num})...')
    return _seguir_proxima_nota(page, log, memoria)


def _falha_nota_registrar_e_proxima(page, log, memoria, dados, erro_msg):
    """Qualquer falha: grava Erro no painel (dashboard), volta ao painel NFe, próxima nota."""
    num = dados.get('num_nota', '?')
    log(f'📝 Registrando erro no painel — nota {num}')
    if erro_msg:
        log(f'   ❌ Motivo: {str(erro_msg)[:220]}')
    db.registrar_erro_nota_painel(dados, erro_msg)
    try:
        if page.locator('input[value="Voltar"]').count() > 0:
            voltar_ao_painel_nfe(page, log)
    except Exception:
        pass
    return _ir_para_proxima_nota_apos_tratar(page, log, memoria, dados)


def _nota_arquivada_no_painel_robo(dados, num_nota, log=None):
    """
    Só pula se o usuário marcou ☑ arquivar no painel do robô (dashboard).
    Erro/importado no banco NÃO impede nova tentativa.
    """
    chave = (dados.get('chave_nfe') or '').strip()
    if chave and db.verificar_nota_arquiva(chave):
        if log:
            log(f'   ⏭️ Nota {num_nota} ignorada (☑ arquivada no painel do robô).')
        return True
    return False


def _localizar_linha_por_num_nota(page, num_nota):
    """Localiza a linha na grade pelo nº da nota (após Atualizar Painel)."""
    alvo = str(num_nota).strip()
    linhas = page.locator(SELETOR_LINHAS_NFE)
    for i in range(linhas.count()):
        linha = linhas.nth(i)
        num = _ler_dado_linha(linha, 'td.rf-edt-td-numNota')
        if num == alvo:
            return linha, i
    return None, -1


def _varrer_linhas_painel(page, memoria, log=None):
    """Uma única varredura da tabela — mesma leitura para resumo e para escolher a próxima."""
    linhas = page.locator(SELETOR_LINHAS_NFE)
    total = linhas.count()
    itens = []

    for i in range(total):
        linha = linhas.nth(i)
        num_nota = _ler_dado_linha(linha, 'td.rf-edt-td-numNota')
        if not num_nota:
            continue
        dados = _montar_dados_nota(linha, num_nota)
        acao = _classificar_acao_linha(linha)
        arquivada = _nota_arquivada_no_painel_robo(dados, num_nota)
        itens.append({
            'indice': i,
            'num_nota': num_nota,
            'dados': dados,
            'acao': acao,
            'arquivada': arquivada,
            'status': _texto_status_linha(linha),
        })

    return total, itens


def _log_resumo_painel(itens, total, log, memoria):
    nums_imp, nums_down, nums_abrir_cp, arquivadas, rodada, outras = [], [], [], [], [], []
    for it in itens:
        n = it['num_nota']
        if it['arquivada']:
            arquivadas.append(n)
            continue
        nid = _id_nota_controle(it['dados'], n)
        if _nota_ja_processada(memoria, nid, n):
            rodada.append(n)
            continue
        if it['acao'] == 'importar':
            nums_imp.append(n)
        elif it['acao'] == 'marcar_importada':
            nums_abrir_cp.append(n)
        elif it['acao'] == 'download':
            nums_down.append(n)
        else:
            outras.append(f"{n}({it['status'][:30]})")

    log(
        f'📋 Painel ({total} linhas, ordem da tabela): '
        f'Importar={nums_imp or "—"} | Abrir CP={nums_abrir_cp or "—"} | '
        f'Sem XML={nums_down or "—"} | '
        f'Arquivadas={arquivadas or "—"} | Já feitas nesta rodada={rodada or "—"} | '
        f'Outras={outras or "—"}'
    )
    return nums_imp, nums_down


def _montar_dados_nota(linha, num_nota):
    return {
        'status': 'Processando',
        'num_nota': num_nota,
        'chave_nfe': _ler_dado_linha(linha, 'td.rf-edt-td-chaveNFe'),
        'fornecedor': _ler_dado_linha(linha, 'td.rf-edt-td-forn'),
        'valor': _ler_dado_linha(linha, 'td.rf-edt-td-valor'),
        'data_em': _ler_dado_linha(linha, 'td.rf-edt-td-data'),
        'sit_nfe': _ler_dado_linha(linha, 'td.rf-edt-td-sitNFe'),
        'filial': _ler_dado_linha(linha, 'td.rf-edt-td-filial'),
        'user_ins': _ler_dado_linha(linha, 'td[id$=":userInsDT"]'),
        'codigo_interno': '',
        'erro_importacao': '',
        'observacao_nfe': '',
        'painel_placa': '',
        'painel_km': '',
    }


def _carregar_dados_nota_alvo(num_nota):
    try:
        notas = db.listar_notas_filtradas(nota=num_nota)
        for nota in notas or []:
            if str((nota or {}).get('num_nota') or '').strip() == str(num_nota).strip():
                return dict(nota)
    except Exception:
        pass

    return {
        'status': '',
        'num_nota': str(num_nota or '').strip(),
        'chave_nfe': '',
        'fornecedor': '',
        'valor': '',
        'data_em': '',
        'sit_nfe': '',
        'filial': '',
        'user_ins': '',
        'codigo_interno': '',
        'erro_importacao': '',
        'observacao_nfe': '',
        'painel_placa': '',
        'painel_km': '',
    }


def _nota_alvo_marcada_como_estoque(memoria, dados=None):
    if not memoria or not memoria.get('nota_alvo_estoque'):
        return False

    nota_alvo = str(memoria.get('nota_alvo') or '').strip()
    if not nota_alvo:
        return False

    if dados is None:
        return True

    return str((dados or {}).get('num_nota') or '').strip() == nota_alvo


def _aplicar_estoque_nota_alvo(dados, memoria, log=None):
    if not _nota_alvo_marcada_como_estoque(memoria, dados):
        return False

    chave_nfe = str((dados or {}).get('chave_nfe') or '').strip()
    if not chave_nfe:
        return False

    if db.atualizar_estoque_nota(chave_nfe, '☑'):
        if log:
            log(f'   📦 Nota {dados.get("num_nota", "")} marcada como ESTOQUE pela confirmação do usuário.')
        return True
    return False


def _finalizar_busca_nota_alvo_sem_resultado(log, memoria, nota_alvo):
    relancamento_manual = str(memoria.get('nota_alvo') or '').strip() == str(nota_alvo).strip()
    if relancamento_manual:
        encerrada, detalhe = False, ''
    else:
        encerrada, detalhe = db.nota_encerrada_robo(num_nota=nota_alvo)
    if encerrada:
        log(f'✅ Nota alvo {nota_alvo} já tratada pelo robô ({detalhe}).')
        memoria['proximo_num_nota'] = None
        memoria['proxima_acao'] = None
        return

    dados = _carregar_dados_nota_alvo(nota_alvo)
    db.registrar_erro_nota_painel(dados, MSG_XML_NAO_ENCONTRADO_ERP)
    _marcar_nota_processada(memoria, dados, nota_alvo)
    memoria['proximo_num_nota'] = None
    memoria['proxima_acao'] = None
    log(f'📝 Erro registrado no painel — nota {nota_alvo}')
    log(f'   ❌ Motivo: {MSG_XML_NAO_ENCONTRADO_ERP}')


def _escolher_proxima_linha_ordem_painel(page, log, memoria):
    """Primeira linha da tabela (de cima para baixo) que ainda precisa ação."""
    total, itens = _varrer_linhas_painel(page, memoria)
    nums_imp, nums_down = _log_resumo_painel(itens, total, log, memoria)

    for it in itens:
        if it['arquivada']:
            continue
        if it['acao'] == 'ignorar':
            continue
        num_nota = it['num_nota']
        nid = _id_nota_controle(it['dados'], num_nota)
        if _nota_ja_processada(memoria, nid, num_nota):
            continue
        acao = it['acao']
        dados = it['dados']

        linha, idx = _localizar_linha_por_num_nota(page, num_nota)
        if not linha:
            log(f'   ⚠️ Nota {num_nota} sumiu da grade; tentando localizar de novo...')
            time.sleep(1)
            linha, idx = _localizar_linha_por_num_nota(page, num_nota)
        if not linha:
            log(f'   ⚠️ Nota {num_nota} não localizada — pulando.')
            _marcar_nota_processada(memoria, dados, num_nota)
            continue

        log(
            f'▶ Próxima nota (linha {(idx + 1) if idx >= 0 else "?"}/{total}): '
            f'{num_nota} ({acao}) | Status: {it["status"][:50]}'
        )
        memoria['proximo_num_nota'] = num_nota
        memoria['proxima_acao'] = acao
        return linha, num_nota, acao, dados

    if nums_down or nums_imp:
        log(
            '⚠️ Havia notas na fila mas nenhuma foi localizada na grade. '
            'Atualize o painel e tente de novo.'
        )
    memoria['proximo_num_nota'] = None
    memoria['proxima_acao'] = None
    return None, None, None, None


def _escolher_nota_alvo(page, log, memoria, nota_alvo):
    total, itens = _varrer_linhas_painel(page, memoria)
    _log_resumo_painel(itens, total, log, memoria)

    for it in itens:
        num_nota = str(it.get('num_nota') or '').strip()
        if num_nota != str(nota_alvo).strip():
            continue

        dados = it['dados']
        if it['arquivada']:
            log(f'⏭️ Nota alvo {nota_alvo} ignorada (☑ arquivada no painel do robô).')
            _marcar_nota_processada(memoria, dados, nota_alvo)
            memoria['proximo_num_nota'] = None
            memoria['proxima_acao'] = None
            return None, None, None, None

        if it['acao'] == 'ignorar':
            log(
                f'⚠️ Nota alvo {nota_alvo} localizada no ERP, mas sem ação '
                f'Importar/Abrir CP/Sem XML. Status: {it["status"][:50]}'
            )
            _marcar_nota_processada(memoria, dados, nota_alvo)
            memoria['proximo_num_nota'] = None
            memoria['proxima_acao'] = None
            return None, None, None, None

        nid = _id_nota_controle(dados, num_nota)
        if _nota_ja_processada(memoria, nid, num_nota):
            memoria['proximo_num_nota'] = None
            memoria['proxima_acao'] = None
            return None, None, None, None

        linha, idx = _localizar_linha_por_num_nota(page, num_nota)
        if not linha:
            log(f'   ⚠️ Nota alvo {num_nota} sumiu da grade; tentando localizar de novo...')
            time.sleep(1)
            linha, idx = _localizar_linha_por_num_nota(page, num_nota)
        if not linha:
            _finalizar_busca_nota_alvo_sem_resultado(log, memoria, nota_alvo)
            return None, None, None, None

        log(
            f'▶ Nota filtrada no ERP (linha {(idx + 1) if idx >= 0 else "?"}/{total}): '
            f'{num_nota} ({it["acao"]}) | Status: {it["status"][:50]}'
        )
        memoria['proximo_num_nota'] = num_nota
        memoria['proxima_acao'] = it['acao']
        return linha, num_nota, it['acao'], dados

    _finalizar_busca_nota_alvo_sem_resultado(log, memoria, nota_alvo)
    return None, None, None, None


def _processar_nota_abrir_cp(page, log, linha, num_nota, dados, memoria):
    """Nota já está no ERP como Conta a Pagar."""
    dados = dict(dados or {})
    dados['num_nota'] = str(num_nota or dados.get('num_nota') or '').strip()
    nota_alvo = str(memoria.get('nota_alvo') or '').strip()
    if nota_alvo and nota_alvo == dados['num_nota']:
        log(
            f'🔄 Nota {dados["num_nota"]} relançada pelo painel (Abrir CP) — '
            'executando finalização na Conta a Pagar...'
        )
        from robo_web.modulo_gravacao import abrir_cp_linha_e_finalizar

        if abrir_cp_linha_e_finalizar(page, log, linha, dados):
            _marcar_nota_processada(memoria, dados, num_nota)
            memoria['proximo_num_nota'] = None
            memoria['proxima_acao'] = None
            return None
        dados_erro = dict(_carregar_dados_nota_alvo(dados['num_nota']))
        db.registrar_erro_nota_painel(
            dados_erro,
            f'Falha ao reprocessar a nota {dados["num_nota"]} via Abrir CP.',
        )
        return None

    log(
        f'📋 Nota {dados["num_nota"]} com status Abrir CP '
        '(já importada no ERP). Registrando como Importada no painel...'
    )
    codigo = _extrair_codigo_interno_status(linha)
    db.salvar_nota_raspada(dados)
    if codigo:
        dados['codigo_interno'] = codigo
        log(f'   -> Código interno identificado: {codigo}')
    dados['erro_importacao'] = ''
    db.marcar_nota_importada_painel(dados)
    log(f'✅ Nota {dados["num_nota"]} marcada como Importada no painel do robô.')
    return _ir_para_proxima_nota_apos_tratar(page, log, memoria, dados)


def _processar_download_nota(page, log, linha, num_nota, dados, memoria):
    log(f'🔍 Nota {num_nota} sem XML. Tentando Download...')
    try:
        _desmarcar_checkboxes_painel_nfe(page)
        _marcar_checkbox_linha(linha)
        log(f'   -> Nota {num_nota} selecionada para download.')

        btn_ciencia = page.locator('input[id="formCad:buttonCiencia"]')
        if btn_ciencia.count() > 0 and btn_ciencia.is_visible(timeout=2000):
            btn_ciencia.click()
            log('   -> Botão Ciência clicado. Aguardando...')
            time.sleep(3)
        else:
            log('   -> Ciência já realizada. Indo para Download...')

        if not _clicar_botao_download_nfe(page, log):
            _marcar_nota_processada(memoria, dados, num_nota)
            return _seguir_proxima_nota(page, log, memoria)

        log('   -> Download solicitado. Monitorando mensagem SEFAZ...')
        resultado, texto_sefaz = aguardar_mensagem_download_nfe(page, log, num_nota)

        if resultado == 'sucesso':
            log('   ✅ Download OK. Atualizando painel para verificar Importar...')
            return _seguir_proxima_nota(page, log, memoria)

        if resultado == 'erro':
            log('   ❌ Erro no download. Próxima nota após atualizar painel.')
            _registrar_erro_download_pular(
                dados, num_nota, texto_sefaz or 'Erro no download NFe', memoria, log,
            )
            return _seguir_proxima_nota(page, log, memoria)

        if resultado in ('timeout_tentativa', 'timeout_total'):
            msg = (
                f'Download travado ({TIMEOUT_TRAVADO_TENTATIVA_SEG}s) — {texto_sefaz[:350]}'
                if texto_sefaz else f'Download travado ({TIMEOUT_TRAVADO_TENTATIVA_SEG}s).'
            )
            _registrar_erro_download_pular(dados, num_nota, msg, memoria, log)
            return _seguir_proxima_nota(page, log, memoria)

        return _seguir_proxima_nota(page, log, memoria)

    except Exception as e:
        log(f'   ⚠️ Falha no download: {e}')
        _marcar_nota_processada(memoria, dados, num_nota)
        return _seguir_proxima_nota(page, log, memoria)


def localizar_link_abrir_cp_linha(linha):
    """Retorna o locator do link Abrir CP na linha do painel NFe, ou None."""
    for sel in (
        'td.rf-edt-td-status a:has-text("Abrir CP")',
        'a:has-text("Abrir CP")',
    ):
        loc = linha.locator(sel)
        if loc.count() > 0:
            try:
                if loc.first.is_visible(timeout=800):
                    return loc.first
            except Exception:
                return loc.first
    try:
        link = linha.get_by_role('link', name=re.compile(r'^Abrir\s*CP$', re.I))
        if link.count() > 0:
            return link.first
    except Exception:
        pass
    return None


def _clicar_importar_linha(linha, log, num_nota):
    for sel in (
        'td.rf-edt-td-status a:has-text("Importar")',
        'a:has-text("Importar")',
        'input[value*="Importar"]',
    ):
        loc = linha.locator(sel)
        if loc.count() > 0:
            loc.first.click()
            return True
    try:
        linha.get_by_role('link', name=re.compile(r'Importar', re.I)).first.click()
        return True
    except Exception:
        pass
    log(f'   ⚠️ Link Importar não encontrado na nota {num_nota}.')
    return False


def _mensagem_download_erro_final(texto_lower):
    marcadores = (
        '10 tentativas',
        'nao foi possivel',
        'não foi possível',
        'consumo indevido',
        'rejeicao',
        'rejeição',
        'indisponivel',
        'indisponível',
        'cancelada',
        'cancelado',
        'nao retornou resposta',
        'não retornou resposta',
    )
    return any(m in texto_lower for m in marcadores)


def _mensagem_download_sucesso(texto_lower):
    return 'sucesso' in texto_lower or 'conclu' in texto_lower


def aguardar_mensagem_download_nfe(page, log, num_nota):
    """
    Aguarda span#formCad:msgEMonitorOp após Download NFe.
    Se a mesma mensagem de 'Tentativa X de 10' ficar 90s, retorna timeout_tentativa.
    """
    msg_span = page.locator('span[id="formCad:msgEMonitorOp"]')
    inicio_total = time.time()
    texto_estavel = ''
    inicio_estavel = time.time()
    ultimo_log = ''

    while time.time() - inicio_total < TIMEOUT_TOTAL_DOWNLOAD_SEG:
        verificar_pagina_erp_ok(page, log)
        try:
            if not msg_span.is_visible(timeout=1000):
                time.sleep(1)
                continue
            texto = (msg_span.text_content() or '').strip()
        except Exception:
            time.sleep(1)
            continue

        if not texto:
            time.sleep(1)
            continue

        if texto != ultimo_log:
            log(f'   -> Retorno SEFAZ: {texto}')
            ultimo_log = texto

        tl = texto.lower()

        if _mensagem_download_erro_final(tl):
            return 'erro', texto

        if _mensagem_download_sucesso(tl):
            return 'sucesso', texto

        if texto != texto_estavel:
            texto_estavel = texto
            inicio_estavel = time.time()
        elif time.time() - inicio_estavel >= TIMEOUT_TRAVADO_TENTATIVA_SEG:
            if 'tentativa' in tl or 'fazendo download' in tl:
                log(
                    f'   ⚠️ Download da nota {num_nota} travou 90s na mesma mensagem. '
                    'Seguindo para a próxima nota.'
                )
                return 'timeout_tentativa', texto

        time.sleep(1.5)

    return 'timeout_total', ultimo_log


def _clicar_botao_download_nfe(page, log):
    btn_download = page.locator('input[id="formCad:downloadNFe"]')
    if btn_download.count() > 0 and btn_download.is_visible(timeout=2000):
        btn_download.click()
        return True
    log('   ⚠️ Botão de Download NFe não encontrado na tela.')
    return False


def _registrar_erro_download_pular(dados, num_nota, msg, memoria, log):
    dados = dict(dados or {})
    dados['num_nota'] = str(num_nota or dados.get('num_nota') or '').strip()
    if db.texto_indica_arquivo_indisponivel(msg):
        db.registrar_erro_nota_painel(dados, db.MSG_ERRO_ARQUIVO_INDISPONIVEL)
        db.atualizar_nota_raspada(dados, arquivar_automatico=True)
    else:
        db.registrar_erro_nota_painel(dados, msg or 'Erro no download NFe')
    log(f'📝 Erro de download registrado no painel — nota {num_nota}')
    _marcar_nota_processada(memoria, dados, num_nota)


def processar_importacao(page, log, memoria=None):
    memoria = _memoria_painel(memoria)
    nota_alvo = str(memoria.get('nota_alvo') or '').strip()
    if nota_alvo:
        dados_painel = _carregar_dados_nota_alvo(nota_alvo)
        status_painel = str(dados_painel.get('status') or '').strip().upper()
        if status_painel in ('IMPORTADO', 'PROCESSADO', 'ERRO'):
            dados_painel['status'] = 'Processando'
            dados_painel['erro_importacao'] = ''
            db.salvar_nota_raspada(dados_painel)
            log(f'🔄 Nota {nota_alvo} liberada para novo processamento no painel.')

    verificar_pagina_erp_ok(page, log)
    if memoria.get('atualizar_agora', True):
        if nota_alvo:
            _aplicar_filtro_num_nota(page, log, nota_alvo)
            log(f'🔄 Atualizando painel filtrado pela nota {nota_alvo}...')
        else:
            log('🔄 Atualizando painel (próxima nota = ordem da tabela)...')
        _atualizar_painel_nfe(page, log, segundos=4)
    memoria['atualizar_agora'] = True

    try:
        page.wait_for_selector(SELETOR_LINHAS_NFE, state='visible', timeout=10000)
    except Exception:
        if nota_alvo:
            _finalizar_busca_nota_alvo_sem_resultado(log, memoria, nota_alvo)
            return
        log('Nenhuma nota encontrada no painel. Fim do processo.')
        return

    if nota_alvo:
        linha, num_nota, acao, dados = _escolher_nota_alvo(page, log, memoria, nota_alvo)
    else:
        linha, num_nota, acao, dados = _escolher_proxima_linha_ordem_painel(page, log, memoria)

    if not acao:
        if nota_alvo:
            log(f'✅ Fluxo da nota {nota_alvo} encerrado.')
        else:
            log('✅ Nenhuma nota pendente na página (todas tratadas ou puladas).')
        return

    verificar_pagina_erp_ok(page, log)

    if acao == 'marcar_importada':
        return _processar_nota_abrir_cp(page, log, linha, num_nota, dados, memoria)

    if acao == 'importar':
        log(f'🚀 Nota {num_nota} — verificando e importando (nota a nota)')
        db.salvar_nota_raspada(dados)
        _aplicar_estoque_nota_alvo(dados, memoria, log)
        if not _clicar_importar_linha(linha, log, num_nota):
            return _falha_nota_registrar_e_proxima(
                page, log, memoria, dados,
                f'Link Importar não encontrado na nota {num_nota}.',
            )
        time.sleep(3)
        try:
            verificar_pagina_erp_ok(page, log)
            sucesso = orquestrar_preenchimento_interno(page, log, dados, memoria)
        except Exception as e:
            return _falha_nota_registrar_e_proxima(
                page, log, memoria, dados, f'Erro inesperado: {e}',
            )
        if not sucesso:
            msg_erro = (dados.get('erro_importacao') or '').strip()
            if not msg_erro:
                msg_erro = obter_mensagem_erro_erp(page)
            if not msg_erro:
                msg_erro = 'Processamento da nota interrompido.'
            if str(dados.get('status', '')).upper() != 'ERRO':
                return _falha_nota_registrar_e_proxima(
                    page, log, memoria, dados, msg_erro,
                )
            return _ir_para_proxima_nota_apos_tratar(page, log, memoria, dados)
        log(f'✅ Nota {num_nota} concluída. Indo para a próxima.')
        return _ir_para_proxima_nota_apos_tratar(page, log, memoria, dados)

    if acao == 'download':
        return _processar_download_nota(page, log, linha, num_nota, dados, memoria)
    
def _garantir_negocio_todos_itens_nfe(page, log, codigo_negocio, total_itens):
    """Antes de importar para CP: todos os itens com o mesmo negócio (Frota ou Frete)."""
    codigo_negocio = str(codigo_negocio or '1').strip()
    if codigo_negocio not in ('1', '2'):
        codigo_negocio = '1'

    nome_negocio = nome_negocio_erp(codigo_negocio)
    log(
        f'   🔒 Garantindo {nome_negocio} em todos os {total_itens} item(ns) '
        'antes de importar para Conta a Pagar...'
    )
    alterados = 0
    for idx in range(total_itens):
        sel_negocio = page.locator(f'select[id="formCad:tableItemNota:{idx}:negocio"]')
        if sel_negocio.count() == 0:
            log(f'      -> Item {idx + 1}: campo Negócio não encontrado.')
            continue
        try:
            valor_atual = str(sel_negocio.input_value() or '').strip()
        except Exception:
            valor_atual = ''
        if valor_atual != codigo_negocio:
            sel_negocio.select_option(value=codigo_negocio)
            alterados += 1
            log(f'      -> Item {idx + 1}: negócio ajustado para {codigo_negocio}')
    if alterados:
        time.sleep(1.5)
    return True


def orquestrar_preenchimento_interno(page, log, dados, memoria=None):
    """Função que gerencia o fluxo de preenchimento dentro da nota"""
    ok_forn, msg_erro_forn = validar_e_corrigir_nome_fornecedor_imp_cp(page, log)
    if not ok_forn:
        abortar_nota_com_erro(
            page,
            log,
            dados,
            msg_erro_forn or 'Falha ao corrigir o nome do fornecedor conforme o XML da NFe.',
        )
        return False

    cod_filial, cod_ue, aplicar_fixo = obter_codigos_para_nota(log)

    select_unid_mestre = localizar_select_unidade_mestre(page)
    if not aplicar_fixo:
        select_empresa = localizar_select_filial_lancamento(page)
        valor_unidade = (
            select_empresa.evaluate('el => el.value')
            if select_empresa.count() > 0 else ''
        )
        if valor_unidade != '-' and select_unid_mestre.count() > 0:
            select_unid_mestre.select_option(value=valor_unidade)
            log(f'Filial e Unid. Emb. Mestre sincronizadas com Unidade {valor_unidade}')
    
    campo_obs = localizar_observacao_nota_cp(page)
    memoria_obs = campo_obs.input_value() if campo_obs.count() > 0 else ''
    dados['observacao_nfe'] = memoria_obs # Guarda a observação da tela

    linhas_item = page.locator('tbody[id="formCad:tableItemNota:tb"] > tr.rf-dt-r')
    total_itens = linhas_item.count()
    dados['desmarcar_despesa_nota'] = False
    dados['codigos_negocio_itens'] = []

    modelos_usuario = db.obter_modelos_placa() or ["PLACA: AAA-1A11", "PLAC: AAA-1A11"]

    painel_info = db.obter_painel_placa_km(
        dados.get('chave_nfe'),
        dados.get('num_nota'),
    )
    painel_placa = str(
        dados.get('painel_placa') or painel_info.get('painel_placa') or ''
    ).strip()
    painel_km = str(dados.get('painel_km') or painel_info.get('painel_km') or '').strip()
    painel_placa = db.normalizar_placa_painel(painel_placa)
    painel_km = db.normalizar_km_painel(painel_km)
    dados['painel_placa'] = painel_placa
    dados['painel_km'] = painel_km
    if painel_placa or painel_km:
        log(
            f'   📋 Painel: placa={painel_placa or "—"} | km={painel_km or "—"}'
        )

    # ==============================================================
    # 0. O ROBÔ PERGUNTA AO BANCO SE A NOTA É PARA O ESTOQUE
    # ==============================================================
    nota_eh_estoque = (
        _nota_alvo_marcada_como_estoque(memoria, dados)
        or db.verificar_nota_estoque(dados['chave_nfe'])
    )

    for idx in range(total_itens):
        log(f"\n========================================================")
        log(f"===> PROCESSANDO ITEM {idx + 1} DE {total_itens}")
        log(f"========================================================")

        item_block = page.locator(f'tr[id="formCad:tableItemNota:{idx}"]')

        # ==============================================================
        # FLUXO 1: NOTA MARCADA PARA ESTOQUE
        # ==============================================================
        if nota_eh_estoque:
            log("   📦 Modo ESTOQUE ativado! Pulando validação de placa e KM...")
            
            # 1. Muda o campo de 'Diversos' (D) para 'Estoque' (E)
            sel_ved = item_block.locator(f'select[id="formCad:tableItemNota:{idx}:VED"]')
            if sel_ved.count() > 0:
                sel_ved.select_option(value="E")
                log("   -> Tipo de item alterado para: [E] Estoque")
            
            # 2. Muda a 'Não Prevista' para 'Não' (N)
            sel_prevista = item_block.locator(f'select[id="formCad:tableItemNota:{idx}:naoPrevista"]')
            if sel_prevista.count() > 0:
                sel_prevista.select_option(value="N")
                log("   -> 'Despesa não prevista' alterado para: [N] Não")

            # 3. Força o Negócio para 'Frota' (1)
            sel_negocio = item_block.locator(f'select[id="formCad:tableItemNota:{idx}:negocio"]')
            if sel_negocio.count() > 0:
                sel_negocio.select_option(value="1")
                log("   -> Ramo de Negócio fixado em: [1] FROTA")
            
            time.sleep(1.5) # Respiro para o ERP processar as mudanças de Select (AJAX)
            
            dados['codigo_negocio_veiculo'] = "1"
            dados['codigos_negocio_itens'].append("1")

            # 4. Pula direto para o preenchimento do código/nome do item e cadastra
            # Passamos "1" como código de negócio para ele carregar isso corretamente na nova aba (se for preciso cadastrar)
            processar_cadastro_item(page, log, idx, item_block, "1")
            
            # Pula para o próximo item, IGNORANDO toda a leitura de placa e KM abaixo!
            continue 

        # ==============================================================
        # FLUXO 2: NOTA NORMAL (VEÍCULO / FROTA / FRETE)
        # ==============================================================
        # 1. PROCESSA O VEÍCULO E RECUPERA O TEXTO FINAL (ex: RRW0H88-13)
        resultado_veiculo, placas_extraidas, erro_veiculo = processar_veiculo(
            page,
            log,
            idx,
            memoria_obs,
            modelos_usuario,
            placa_painel=painel_placa,
        )

        if not resultado_veiculo:
            if erro_veiculo == 'carreta_duplicada':
                msg_erro = MSG_ERRO_CARRETA_DUPLICADA
            elif erro_veiculo == 'sem_placa_observacao':
                msg_erro = MSG_ERRO_FALTA_VEICULO_OBS
            else:
                msg_erro = MSG_ERRO_PLACA_VEICULO
            abortar_nota_com_erro(page, log, dados, msg_erro)
            return False
            
        # 2. INTELIGÊNCIA: DESCOBRE O CÓDIGO E O RAMO DO NEGÓCIO
        codigo_negocio = "1"

        if "-" in resultado_veiculo:
            cod_veiculo = resultado_veiculo.split("-")[-1].strip()
            vinculo = db.obter_vinculo_veiculo(cod_veiculo)
            codigo_negocio = codigo_negocio_por_vinculo(vinculo)
            vinculo_norm = normalizar_vinculo_veiculo(vinculo)

            if vinculo_veiculo_exige_desmarcar_despesa(vinculo):
                dados['desmarcar_despesa_nota'] = True
                if 'AGREG' in vinculo_norm:
                    log(
                        f"-> 🧠 Vínculo ({cod_veiculo}): {vinculo} -> AGREGADO | "
                        f"Negócio FRETE (2) e Despesa será desmarcada antes de finalizar"
                    )
                elif 'TERCEIR' in vinculo_norm:
                    log(
                        f"-> 🧠 Vínculo ({cod_veiculo}): {vinculo} -> TERCEIRO | "
                        f"Negócio FRETE (2) e Despesa será desmarcada antes de finalizar"
                    )
                else:
                    log(
                        f"-> 🧠 Vínculo ({cod_veiculo}): {vinculo} -> "
                        f"Negócio FRETE/AGENCIAMENTO (2) | Despesa será desmarcada antes de finalizar"
                    )
            elif vinculo:
                log(f"-> 🧠 Vínculo do veículo ({cod_veiculo}): {vinculo} -> Negócio: FROTA (1)")
            else:
                log(f"-> ⚠️ Vínculo do veículo ({cod_veiculo}) não encontrado na frota. Usando FROTA (1).")

        # 3. SELECIONA O NEGÓCIO NA TELA PRINCIPAL
        sel_negocio_tela_principal = page.locator(f'select[id="formCad:tableItemNota:{idx}:negocio"]')
        if sel_negocio_tela_principal.count() > 0:
            sel_negocio_tela_principal.select_option(value=codigo_negocio)
            log(f"-> 🎯 Formulário validado: Negócio do Item {idx + 1} alterado na tela para a opção {codigo_negocio}")
        
        dados['codigo_negocio_veiculo'] = codigo_negocio
        dados['codigos_negocio_itens'].append(codigo_negocio)

        # 4. KM obrigatório (exceto modo estoque)
        km_ok = processar_km(page, log, idx, memoria_obs, km_painel=painel_km)
        if not km_ok:
            abortar_nota_com_erro(
                page,
                log,
                dados,
                f"KM não encontrado (Item {idx + 1}). "
                "Preencha a coluna KM no painel ou ajuste o modelo em Parâmetros ERP "
                "para coincidir exatamente com o texto da observação da NFe "
                "(maiúsculas, minúsculas e acentos).",
            )
            return False

        processar_cadastro_item(page, log, idx, item_block, codigo_negocio)

        # =======================================================================
        # 5. NOVA VALIDAÇÃO: VERIFICA SE O SISTEMA RECUSOU/BLOQUEOU O VEÍCULO
        # =======================================================================
        time.sleep(1)
        texto_erro_erp = obter_mensagem_erro_erp(page)
        if texto_erro_erp:
            log(f'   ❌ Erro ERP no Item {idx + 1}: {texto_erro_erp}')
            abortar_nota_com_erro(page, log, dados, texto_erro_erp)
            return False

    # =======================================================================
    # 6. NEGÓCIO ÚNICO NA NOTA: TODOS OS ITENS FROTA OU TODOS FRETE/AGENCIAMENTO
    # =======================================================================
    if nota_eh_estoque:
        codigo_negocio_nota = '1'
        dados['desmarcar_despesa_nota'] = False
    else:
        codigo_negocio_nota = unificar_codigo_negocio_nota(dados.get('codigos_negocio_itens'))
        dados['desmarcar_despesa_nota'] = codigo_negocio_nota == '2'

    dados['codigo_negocio_veiculo'] = codigo_negocio_nota
    log(
        f'   📌 Negócio unificado da nota: {nome_negocio_erp(codigo_negocio_nota)} '
        f'(aplicado em todos os itens)'
    )
    if total_itens > 0:
        _garantir_negocio_todos_itens_nfe(page, log, codigo_negocio_nota, total_itens)

    # FINALIZA A NOTA
    if not finalizar_gravacao(page, log, dados):
        return False
    return True