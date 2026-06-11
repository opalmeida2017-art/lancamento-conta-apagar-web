"""Importação de tarifas bancárias a partir de extratos XLS do Sicredi."""

import csv
import json
import os
import re
import time
from datetime import datetime
from io import StringIO

import pandas as pd

import database_setup as db

NOMES_ARQUIVO_MAPA = (
    'mapa_contas.csv',
    'mapa_contas_cnpj.csv',
    'contas_cnpj.csv',
    'mapa_contas.json',
)

PALAVRAS_TARIFA = (
    'TARIFA', 'TAXA', ' IOF', 'IOF ', 'MANUTEN', 'PACOTE', 'COBRANCA', 'COBRANÇA',
    'SERVICO', 'SERVIÇO', 'ANUIDADE', 'MENSALIDADE', 'CESTA', 'TAR.', 'TAR ',
    'DOC/TED', 'TED DOC', 'TARIF', 'TAXAS',
)

EXCLUSOES_TARIFA = (
    'TED RECEB', 'PIX RECEB', 'DEPOSITO', 'DEPÓSITO', 'TRANSFERENCIA RECEB',
    'TRANSFERÊNCIA RECEB', 'RECEBIMENTO', 'CREDITO', 'CRÉDITO',
)

MAPA_COLUNAS = {
    'data': ('data', 'dt', 'dtmovimento', 'datamovimento', 'datalancamento'),
    'descricao': (
        'historico', 'histórico', 'descricao', 'descrição', 'lancamento',
        'lançamento', 'complemento', 'operacao', 'operação',
    ),
    'valor': ('valor', 'valorr', 'vl', 'valormovimento'),
    'debito': ('debito', 'débito', 'valordebito', 'saida', 'saída'),
    'credito': ('credito', 'crédito', 'valorcredito', 'entrada'),
}


def _log_tarifa(msg, log_callback=None):
    texto = f"[TARIFA BANCARIA]: {msg}"
    try:
        print(texto)
    except UnicodeEncodeError:
        print(texto.encode('ascii', 'replace').decode('ascii'))
    if log_callback:
        log_callback(msg)


def sincronizar_tarifas_erp(log_callback=None):
    pasta = db.obter_pasta_tarifas_bancarias()
    if not pasta:
        if log_callback:
            log_callback('Nenhuma pasta de planilhas configurada.')
        return False
    return importar_tarifas_pasta(pasta, log_callback=log_callback)


def importar_tarifas_pasta(pasta, log_callback=None):
    def log(msg):
        _log_tarifa(msg, log_callback=log_callback)

    pasta = os.path.abspath(str(pasta or '').strip())
    if not pasta or not os.path.isdir(pasta):
        log('Pasta invalida ou inexistente.')
        return False

    contas_mapa = carregar_mapa_contas_pasta(pasta)
    if contas_mapa:
        total_mapa = db.sincronizar_mapa_contas_sicredi(contas_mapa)
        log(f'Mapa de contas: {total_mapa} vinculo(s) CNPJ/conta carregado(s).')

    lookup_contas = db.obter_mapa_contas_sicredi_dict()
    planilhas = listar_planilhas_extrato(pasta)
    if not planilhas:
        log('Nenhuma planilha XLS/XLSX encontrada na pasta.')
        return bool(contas_mapa)

    log(f'Lendo {len(planilhas)} planilha(s) em: {pasta}')

    resumo = _importar_lista_planilhas(planilhas, lookup_contas, log_callback)
    db.registrar_ultima_importacao_tarifas()
    db.salvar_pasta_tarifas_bancarias(pasta)

    log(
        f'Importacao concluida: {resumo["arquivos_ok"]}/{len(planilhas)} planilha(s) com tarifas, '
        f'{resumo["contas"]} conta(s) identificada(s), {resumo["tarifas"]} tarifa(s) lidas, '
        f'{resumo["novas"]} gravada(s), {resumo["duplicadas"]} duplicada(s).'
    )
    return True


def obter_snapshot_planilhas(pasta):
    """Retorna {caminho_absoluto: data_modificacao} das planilhas da pasta."""
    snapshot = {}
    pasta = os.path.abspath(str(pasta or '').strip())
    if not pasta or not os.path.isdir(pasta):
        return snapshot
    for caminho, _cnpj_pasta in listar_planilhas_extrato(pasta):
        try:
            snapshot[caminho] = os.path.getmtime(caminho)
        except OSError:
            continue
    return snapshot


def detectar_planilhas_pendentes(snapshot_anterior, pasta):
    """Retorna lista de (caminho, tipo) para arquivos novos ou modificados."""
    snapshot_atual = obter_snapshot_planilhas(pasta)
    if not snapshot_anterior:
        return [], snapshot_atual

    pendentes = []
    for caminho, mtime in snapshot_atual.items():
        if caminho not in snapshot_anterior:
            pendentes.append((caminho, 'novo'))
        elif snapshot_anterior.get(caminho) != mtime:
            pendentes.append((caminho, 'atualizado'))
    return pendentes, snapshot_atual


def _arquivo_planilha_pronto(caminho):
    """Aguarda o Sicredi terminar de gravar o arquivo antes de importar."""
    caminho = os.path.abspath(str(caminho or '').strip())
    if not os.path.isfile(caminho):
        return False
    try:
        if os.path.getsize(caminho) <= 0:
            return False
        tamanho_1 = os.path.getsize(caminho)
        time.sleep(0.4)
        tamanho_2 = os.path.getsize(caminho)
        if tamanho_1 != tamanho_2:
            return False
        with open(caminho, 'rb'):
            pass
        return True
    except OSError:
        return False


def importar_planilhas_alteradas(pasta, snapshot_anterior, log_callback=None):
    """Importa planilhas novas ou com data de modificacao alterada."""
    def log(msg):
        _log_tarifa(msg, log_callback=log_callback)

    pasta = os.path.abspath(str(pasta or '').strip())
    if not pasta or not os.path.isdir(pasta):
        return dict(snapshot_anterior or {}), []

    pendentes, snapshot_atual = detectar_planilhas_pendentes(snapshot_anterior, pasta)
    if not pendentes:
        return dict(snapshot_anterior), []

    contas_mapa = carregar_mapa_contas_pasta(pasta)
    if contas_mapa:
        db.sincronizar_mapa_contas_sicredi(contas_mapa)

    lookup_contas = db.obter_mapa_contas_sicredi_dict()
    mapa_cnpj_pasta = {
        caminho: cnpj for caminho, cnpj in listar_planilhas_extrato(pasta)
    }

    qtd_novos = sum(1 for _c, tipo in pendentes if tipo == 'novo')
    qtd_atualizados = len(pendentes) - qtd_novos
    log(
        f'Monitor: {len(pendentes)} planilha(s) pendente(s) '
        f'({qtd_novos} nova(s), {qtd_atualizados} atualizada(s)).'
    )

    novo_snapshot = dict(snapshot_anterior)
    importados = []
    for caminho, tipo in pendentes:
        nome = os.path.basename(caminho)
        if not _arquivo_planilha_pronto(caminho):
            log(f'Monitor: {nome} ainda sendo gravado, aguardando...')
            continue

        if tipo == 'novo':
            log(f'Monitor: arquivo novo detectado: {nome}')

        resultado = importar_arquivo_extrato(
            caminho,
            cnpj_pasta=mapa_cnpj_pasta.get(caminho, ''),
            lookup_contas=lookup_contas,
            log_callback=log_callback,
        )
        if resultado.get('importado'):
            importados.append(caminho)
            lookup_contas = db.obter_mapa_contas_sicredi_dict()

        if _deve_marcar_planilha_processada(resultado):
            try:
                novo_snapshot[caminho] = os.path.getmtime(caminho)
            except OSError:
                pass

    for caminho in list(novo_snapshot.keys()):
        if caminho not in snapshot_atual:
            del novo_snapshot[caminho]

    if importados:
        db.registrar_ultima_importacao_tarifas()

    return novo_snapshot, importados


def _deve_marcar_planilha_processada(resultado):
    if resultado.get('importado'):
        return True
    if resultado.get('tarifas', 0) == 0 and not resultado.get('erro'):
        return True
    erro = str(resultado.get('erro') or '').strip()
    if erro in (
        'conta sem CNPJ no mapa',
        'nome fora do padrao agencia_conta',
        'arquivo nao encontrado',
    ):
        return True
    return False


def importar_arquivo_extrato(
    caminho,
    cnpj_pasta='',
    lookup_contas=None,
    log_callback=None,
):
    def log(msg):
        _log_tarifa(msg, log_callback=log_callback)

    caminho = os.path.abspath(str(caminho or '').strip())
    nome = os.path.basename(caminho)
    resultado = {
        'arquivo': nome,
        'importado': False,
        'tarifas': 0,
        'novas': 0,
        'duplicadas': 0,
        'erro': '',
    }

    if not os.path.isfile(caminho):
        resultado['erro'] = 'arquivo nao encontrado'
        return resultado

    agencia, conta = _parse_conta_do_nome_arquivo(nome)
    if not agencia or not conta:
        resultado['erro'] = 'nome fora do padrao agencia_conta'
        log(f'{nome}: nome do arquivo fora do padrao agencia_conta.')
        return resultado

    if lookup_contas is None:
        lookup_contas = db.obter_mapa_contas_sicredi_dict()

    contexto = resolver_contexto_conta(
        agencia,
        conta,
        cnpj_pasta=cnpj_pasta,
        lookup_contas=lookup_contas,
    )
    if not contexto.get('cnpj'):
        resultado['erro'] = 'conta sem CNPJ no mapa'
        log(
            f'{nome}: conta {agencia}/{conta} sem CNPJ. '
            f'Cadastre em mapa_contas.csv ou use subpasta com CNPJ.'
        )
        return resultado

    db.sincronizar_mapa_contas_sicredi([contexto])
    db.registrar_data_arquivo_conta(agencia, conta, caminho_arquivo=caminho)

    try:
        tarifas, meta = extrair_tarifas_extrato_sicredi(
            caminho,
            meta_override=contexto,
        )
        resultado['tarifas'] = len(tarifas)
        if not tarifas:
            log(
                f'{nome}: sem tarifas no periodo '
                f'(CNPJ {meta.get("cnpj")} | Conta {conta}).'
            )
            return resultado

        novas, duplicadas = db.importar_tarifas_bancarias_lote(tarifas)
        resultado.update({
            'importado': True,
            'novas': novas,
            'duplicadas': duplicadas,
        })
        log(
            f'{nome}: {len(tarifas)} tarifa(s) | CNPJ {meta.get("cnpj")} | '
            f'Conta {conta} | {novas} nova(s), {duplicadas} ja existente(s).'
        )
    except Exception as erro:
        resultado['erro'] = str(erro)[:120]
        log(f'{nome}: {resultado["erro"]}')

    return resultado


def _importar_lista_planilhas(planilhas, lookup_contas, log_callback=None):
    resumo = {
        'novas': 0,
        'duplicadas': 0,
        'tarifas': 0,
        'arquivos_ok': 0,
        'contas': 0,
    }
    contas_vistas = set()

    for caminho, cnpj_pasta in planilhas:
        item = importar_arquivo_extrato(
            caminho,
            cnpj_pasta=cnpj_pasta,
            lookup_contas=lookup_contas,
            log_callback=log_callback,
        )
        if item.get('importado'):
            resumo['arquivos_ok'] += 1
            resumo['novas'] += item.get('novas', 0)
            resumo['duplicadas'] += item.get('duplicadas', 0)
            resumo['tarifas'] += item.get('tarifas', 0)
            agencia, conta = _parse_conta_do_nome_arquivo(item.get('arquivo', ''))
            contas_vistas.add(f'{agencia}|{conta}')
            lookup_contas = db.obter_mapa_contas_sicredi_dict()

    resumo['contas'] = len(contas_vistas)
    return resumo


def listar_planilhas_extrato(pasta):
    """Lista planilhas na pasta e subpastas; subpasta com CNPJ vira contexto."""
    encontradas = []
    for raiz, _, arquivos in os.walk(pasta):
        cnpj_pasta = _cnpj_do_nome_pasta(os.path.basename(raiz))
        for nome in sorted(arquivos):
            if not nome.lower().endswith(('.xls', '.xlsx')):
                continue
            if nome.lower() in NOMES_ARQUIVO_MAPA:
                continue
            encontradas.append((os.path.join(raiz, nome), cnpj_pasta))
    return encontradas


def carregar_mapa_contas_pasta(pasta):
    """Carrega mapa CNPJ/conta de CSV ou JSON na pasta."""
    pasta = os.path.abspath(str(pasta or '').strip())
    for nome in NOMES_ARQUIVO_MAPA:
        caminho = os.path.join(pasta, nome)
        if not os.path.isfile(caminho):
            continue
        try:
            if nome.endswith('.json'):
                return _parsear_mapa_contas_json(caminho)
            return _parsear_mapa_contas_csv(caminho)
        except Exception as erro:
            print(f'Erro ao ler {nome}: {erro}')
    return []


def _parsear_mapa_contas_csv(caminho):
    contas = []
    for encoding in ('utf-8-sig', 'latin-1', 'cp1252'):
        try:
            with open(caminho, 'r', encoding=encoding, newline='') as arquivo:
                amostra = arquivo.read(2048)
                arquivo.seek(0)
                delimitador = ';' if amostra.count(';') >= amostra.count(',') else ','
                leitor = csv.reader(arquivo, delimiter=delimitador)
                linhas = list(leitor)
            break
        except Exception:
            linhas = []
    if not linhas:
        return []

    cabecalho = [str(c).strip().lower() for c in linhas[0]]
    mapa_idx = _mapear_colunas_mapa_contas(cabecalho)
    inicio = 1 if mapa_idx else 0
    if not mapa_idx:
        mapa_idx = {'cnpj': 0, 'agencia': 1, 'conta': 2, 'razao_social': 3}

    for linha in linhas[inicio:]:
        if not linha or not any(str(v).strip() for v in linha):
            continue
        item = _montar_item_mapa_conta(linha, mapa_idx)
        if item:
            contas.append(item)
    return contas


def _parsear_mapa_contas_json(caminho):
    with open(caminho, 'r', encoding='utf-8') as arquivo:
        dados = json.load(arquivo)
    if isinstance(dados, dict):
        dados = dados.get('contas', [])
    contas = []
    for item in dados or []:
        if not isinstance(item, dict):
            continue
        montado = _montar_item_mapa_conta_dict(item)
        if montado:
            contas.append(montado)
    return contas


def _mapear_colunas_mapa_contas(cabecalho):
    aliases = {
        'cnpj': ('cnpj', 'cnpjempresa', 'documento'),
        'agencia': ('agencia', 'ag', 'agencia_banco'),
        'conta': ('conta', 'numeroconta', 'contacorrente', 'cc'),
        'razao_social': ('razaosocial', 'razao', 'empresa', 'nome', 'titular'),
        'cod_filial': ('codfilial', 'filial', 'codigofilial'),
        'cod_conta_erp': ('codcontaerp', 'contaerp', 'codigocontaerp', 'contaerp'),
    }
    mapa = {}
    for idx, nome in enumerate(cabecalho):
        chave = re.sub(r'[^a-z0-9]', '', nome)
        for destino, opcoes in aliases.items():
            if chave in opcoes and destino not in mapa:
                mapa[destino] = idx
    if 'cnpj' in mapa and 'agencia' in mapa and 'conta' in mapa:
        return mapa
    return {}


def _montar_item_mapa_conta(linha, mapa_idx):
    def valor(campo):
        idx = mapa_idx.get(campo)
        if idx is None or idx >= len(linha):
            return ''
        return str(linha[idx]).strip()

    return _montar_item_mapa_conta_dict({
        'cnpj': valor('cnpj'),
        'agencia': valor('agencia'),
        'conta': valor('conta'),
        'razao_social': valor('razao_social'),
        'cod_filial': valor('cod_filial'),
        'cod_conta_erp': valor('cod_conta_erp'),
    })


def _montar_item_mapa_conta_dict(item):
    agencia = str(item.get('agencia') or '').strip()
    conta = str(item.get('conta') or '').strip().replace('_', '-')
    cnpj = _formatar_cnpj(item.get('cnpj') or '')
    if not agencia or not conta or not cnpj:
        return None
    razao = str(item.get('razao_social') or item.get('razao') or '').strip().upper()
    return {
        'cnpj': cnpj,
        'razao_social': razao,
        'agencia': agencia,
        'conta': conta,
        'cod_filial': str(item.get('cod_filial') or '').strip(),
        'cod_conta_erp': str(item.get('cod_conta_erp') or '').strip(),
    }


def resolver_contexto_conta(agencia, conta, cnpj_pasta='', lookup_contas=None):
    """Descobre CNPJ/razão social pelo número da conta."""
    agencia = str(agencia or '').strip()
    conta = str(conta or '').strip().replace('_', '-')
    chave = f'{agencia}|{conta}'
    lookup_contas = lookup_contas or {}

    contexto = {
        'cnpj': _formatar_cnpj(cnpj_pasta),
        'razao_social': '',
        'agencia': agencia,
        'conta': conta,
    }

    if lookup_contas.get(chave):
        salvo = lookup_contas[chave]
        contexto['cnpj'] = salvo.get('cnpj') or contexto['cnpj']
        contexto['razao_social'] = salvo.get('razao_social') or ''

    if contexto['cnpj']:
        return contexto

    for item in lookup_contas.values():
        if str(item.get('agencia') or '') == agencia and str(item.get('conta') or '') == conta:
            contexto['cnpj'] = item.get('cnpj') or ''
            contexto['razao_social'] = item.get('razao_social') or ''
            break

    return contexto


def extrair_tarifas_extrato_sicredi(caminho_arquivo, meta_override=None):
    conteudo_bruto, tabelas = _ler_conteudo_planilha(caminho_arquivo)
    meta = _extrair_metadados_extrato(conteudo_bruto, os.path.basename(caminho_arquivo))
    if meta_override:
        for chave, valor in meta_override.items():
            if valor:
                meta[chave] = valor

    df_mov = _localizar_tabela_movimentacoes(tabelas)
    if df_mov is None or df_mov.empty:
        raise ValueError('planilha sem tabela de movimentacoes')

    colunas = _mapear_colunas_extrato(df_mov)
    if 'data' not in colunas or 'descricao' not in colunas:
        raise ValueError('colunas Data/Historico nao encontradas')

    tarifas = []
    for _, linha in df_mov.iterrows():
        descricao = str(linha.get(colunas['descricao'], '') or '').strip()
        if not descricao or not _eh_linha_tarifa(descricao):
            continue

        data_raw = linha.get(colunas['data'], '')
        data_fmt = _formatar_data_extrato(data_raw)
        if not data_fmt:
            continue

        valor_tarifa = _extrair_valor_tarifa(linha, colunas)
        if not valor_tarifa:
            continue

        tarifas.append({
            'cnpj': meta.get('cnpj', ''),
            'razao_social': meta.get('razao_social', ''),
            'agencia': meta.get('agencia', ''),
            'conta': meta.get('conta', ''),
            'data_movimento': data_fmt,
            'descricao': descricao,
            'valor': valor_tarifa,
            'status': 'Pendente',
            'codigo_interno': os.path.basename(caminho_arquivo),
        })

    return tarifas, meta


def _ler_conteudo_planilha(caminho):
    conteudo_bruto = ''
    tabelas = []

    try:
        df = pd.read_excel(caminho, header=None, dtype=str)
        df = df.fillna('')
        tabelas.append(df)
        conteudo_bruto = '\n'.join(
            ' '.join(str(v) for v in row if str(v).strip())
            for _, row in df.head(20).iterrows()
        )
        return conteudo_bruto, tabelas
    except Exception:
        pass

    for encoding in ('latin-1', 'utf-8', 'cp1252'):
        try:
            with open(caminho, 'r', encoding=encoding, errors='ignore') as arquivo:
                conteudo_bruto = arquivo.read()
            break
        except Exception:
            continue

    if conteudo_bruto:
        try:
            tabelas_html = pd.read_html(
                StringIO(conteudo_bruto), decimal=',', thousands='.',
            )
            tabelas.extend(tabelas_html)
        except Exception:
            pass

    if not tabelas:
        raise ValueError('nao foi possivel ler a planilha')

    return conteudo_bruto, tabelas


def _localizar_tabela_movimentacoes(tabelas):
    melhor = None
    melhor_pontos = -1

    for tabela in tabelas:
        if tabela is None or tabela.empty:
            continue

        df = tabela.copy().fillna('')
        cabecalho_idx = _encontrar_linha_cabecalho(df)
        if cabecalho_idx is None:
            continue

        cabecalho = [str(v).strip() for v in df.iloc[cabecalho_idx].tolist()]
        mapa = _mapear_colunas_por_nomes(cabecalho)
        if 'data' not in mapa or 'descricao' not in mapa:
            continue

        corpo = df.iloc[cabecalho_idx + 1:].copy()
        corpo.columns = [
            str(c).strip() if str(c).strip() else f'col_{i}'
            for i, c in enumerate(cabecalho)
        ]
        corpo = corpo[
            corpo.astype(str).apply(
                lambda row: any(str(v).strip() for v in row), axis=1,
            )
        ]
        pontos = len(corpo)
        if 'valor' in mapa or 'debito' in mapa:
            pontos += 5
        if pontos > melhor_pontos:
            melhor_pontos = pontos
            melhor = corpo

    return melhor


def _encontrar_linha_cabecalho(df):
    for idx in range(min(30, len(df))):
        valores = [str(v).strip() for v in df.iloc[idx].tolist()]
        mapa = _mapear_colunas_por_nomes(valores)
        if 'data' in mapa and 'descricao' in mapa:
            return idx
    return None


def _normalizar_nome_coluna(nome):
    texto = str(nome or '').strip().lower()
    texto = texto.replace('r$', '').replace('(r$)', '')
    return re.sub(r'[^a-z0-9]', '', texto)


def _mapear_colunas_por_nomes(nomes):
    mapa = {}
    for nome in nomes:
        chave = _normalizar_nome_coluna(nome)
        if not chave:
            continue
        for destino, aliases in MAPA_COLUNAS.items():
            if chave in aliases and destino not in mapa:
                mapa[destino] = nome
    return mapa


def _mapear_colunas_extrato(df):
    return _mapear_colunas_por_nomes(list(df.columns))


def _extrair_metadados_extrato(conteudo_bruto, nome_arquivo):
    agencia_arq, conta_arq = _parse_conta_do_nome_arquivo(nome_arquivo)
    razao = _extrair_razao_social_texto(conteudo_bruto)
    agencia_txt, conta_txt = _extrair_conta_texto(conteudo_bruto)
    return {
        'cnpj': '',
        'razao_social': razao,
        'agencia': agencia_txt or agencia_arq,
        'conta': (conta_txt or conta_arq).replace('_', '-'),
    }


def _extrair_razao_social_texto(texto):
    padroes = (
        r'(?:Raz[aã]o Social|Nome(?:\s+do\s+Cliente)?|Titular|Empresa|Cooperado)\s*[:\-]\s*([^\n\r<;]+)',
        r'(?:Cliente)\s*[:\-]\s*([^\n\r<;]+)',
    )
    for padrao in padroes:
        match = re.search(padrao, str(texto or ''), flags=re.IGNORECASE)
        if match:
            valor = re.sub(r'\s+', ' ', match.group(1)).strip(' "\'')
            if len(valor) >= 3:
                return valor.upper()
    return ''


def _extrair_conta_texto(texto):
    match = re.search(
        r'Conta(?:\s+Corrente)?\s*[:\-]?\s*(\d{4})\s*[\s/_-]+\s*([\d\-]+)',
        str(texto or ''),
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1), match.group(2).replace('_', '-')
    return '', ''


def _formatar_cnpj(cnpj):
    digits = re.sub(r'\D', '', str(cnpj or ''))
    if len(digits) != 14:
        return str(cnpj or '').strip()
    return f'{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:14]}'


def _cnpj_do_nome_pasta(nome_pasta):
    return _formatar_cnpj(nome_pasta)


def _parse_conta_do_nome_arquivo(nome_arquivo):
    base = os.path.splitext(os.path.basename(nome_arquivo))[0]
    match = re.match(r'^(\d{4})[_-](.+)$', base)
    if match:
        return match.group(1), match.group(2).replace('_', '-')
    return '', base.replace('_', '-')


def _formatar_data_extrato(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ''
    if isinstance(valor, datetime):
        return valor.strftime('%d/%m/%Y')

    texto = str(valor).strip()
    if not texto:
        return ''

    if re.match(r'^\d{4}-\d{2}-\d{2}', texto):
        try:
            return datetime.strptime(texto[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
        except ValueError:
            pass

    match = re.search(r'(\d{2}/\d{2}/\d{4})', texto)
    if match:
        return match.group(1)

    match = re.search(r'(\d{2}/\d{2}/\d{2})', texto)
    if match:
        dia, mes, ano = match.group(1).split('/')
        ano = f'20{ano}' if len(ano) == 2 else ano
        return f'{dia}/{mes}/{ano}'

    return ''


def _extrair_valor_tarifa(linha, colunas):
    if 'debito' in colunas:
        debito = _normalizar_valor_monetario(linha.get(colunas['debito'], ''))
        if debito:
            return debito

    if 'valor' in colunas:
        valor = _normalizar_valor_monetario(linha.get(colunas['valor'], ''))
        if valor:
            if valor.startswith('-'):
                return valor.lstrip('-')
            if 'credito' in colunas:
                credito = _normalizar_valor_monetario(linha.get(colunas['credito'], ''))
                if credito:
                    return ''
            return valor

    return ''


def _normalizar_valor_monetario(valor):
    texto = str(valor or '').strip()
    if not texto or texto in ('-', '—', 'nan'):
        return ''

    texto = texto.replace('R$', '').replace('(', '').replace(')', '').strip()
    texto = re.sub(r'[^\d,.-]', '', texto)
    if not texto:
        return ''

    if ',' in texto and '.' in texto:
        if texto.rfind(',') > texto.rfind('.'):
            texto = texto.replace('.', '').replace(',', '.')
        else:
            texto = texto.replace(',', '')
    elif ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')

    try:
        numero = abs(float(texto))
    except ValueError:
        return ''

    if numero <= 0:
        return ''

    formatado = f'{numero:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return formatado


def _eh_linha_tarifa(descricao):
    texto = str(descricao or '').strip().upper()
    if not texto:
        return False
    if any(exc in texto for exc in EXCLUSOES_TARIFA):
        return False
    return any(palavra in texto for palavra in PALAVRAS_TARIFA)


def processar_tarifas_pendentes(config=None, log_callback=None):
    from robo_web.modulo_lancamento_tarifa import executar_lancamento_tarifas

    return executar_lancamento_tarifas(config=config, log_callback=log_callback)
