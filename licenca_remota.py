# -*- coding: utf-8 -*-
"""Registro e verificação de licença por instalação via GitHub."""
import base64
import json
import os
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import database_setup as db

# Configuração do vendedor (copie licenca_config.example.py -> licenca_config.py)
try:
    import licenca_config as _cfg
except ImportError:
    _cfg = None

LICENCA_REMOTA_ATIVA = getattr(_cfg, 'LICENCA_REMOTA_ATIVA', False) if _cfg else False
GITHUB_OWNER = getattr(_cfg, 'GITHUB_OWNER', '') or os.environ.get('LICENCA_GITHUB_OWNER', '')
GITHUB_REPO = getattr(_cfg, 'GITHUB_REPO', '') or os.environ.get('LICENCA_GITHUB_REPO', '')
GITHUB_BRANCH = getattr(_cfg, 'GITHUB_BRANCH', 'main') if _cfg else os.environ.get('LICENCA_GITHUB_BRANCH', 'main')
GITHUB_TOKEN = getattr(_cfg, 'GITHUB_TOKEN', '') or os.environ.get('LICENCA_GITHUB_TOKEN', '')
PASTA_LICENCAS = getattr(_cfg, 'PASTA_LICENCAS', 'licencas') if _cfg else 'licencas'
INTERVALO_VERIFICACAO_SEG = getattr(_cfg, 'INTERVALO_VERIFICACAO_SEG', 3600) if _cfg else 3600
GRACE_OFFLINE_HORAS = getattr(_cfg, 'GRACE_OFFLINE_HORAS', 72) if _cfg else 72

_ultimo_aviso_rede = 0.0


def licenca_configurada():
    return bool(LICENCA_REMOTA_ATIVA and GITHUB_OWNER and GITHUB_REPO and GITHUB_TOKEN)


def _slug_razao_social(razao_social):
    texto = razao_social.strip()
    texto = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('ascii')
    texto = re.sub(r'[^\w\s-]', '', texto)
    texto = re.sub(r'[\s_\-]+', '_', texto).strip('_')
    return texto[:100] if texto else ''


def gerar_nome_arquivo_github(razao_social):
    slug = _slug_razao_social(razao_social)
    if not slug:
        raise ValueError('Informe a razão social da transportadora.')
    return f'{slug}.json'


def _caminho_arquivo_por_nome(nome_arquivo):
    return f'{PASTA_LICENCAS}/{nome_arquivo}'


def _resolver_caminho_licenca():
    """Caminho do JSON no GitHub (nome da transportadora ou legado por UUID)."""
    info = db.carregar_instalacao_licenca()
    if info.get('nome_arquivo_github'):
        return _caminho_arquivo_por_nome(info['nome_arquivo_github'])
    iid = info.get('instalacao_id')
    if iid:
        return f'{PASTA_LICENCAS}/{iid}.json'
    return None


def _caminho_url(caminho):
    return urllib.parse.quote(caminho, safe='/')


def _headers_api():
    # PAT clássico e fine-grained aceitam Bearer; classic também aceita token
    return {
        'Authorization': f'Bearer {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'User-Agent': 'AutomacaoNFe-Licenca',
        'X-GitHub-Api-Version': '2022-11-28',
    }


def _eh_erro_rede(exc):
    """DNS, timeout ou falha de conexão (não confundir com 404/401 do GitHub)."""
    if isinstance(exc, urllib.error.URLError):
        return not isinstance(getattr(exc, 'reason', None), urllib.error.HTTPError)
    return isinstance(exc, (TimeoutError, socket.timeout, OSError))


def _parse_data_local(texto):
    if not texto:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(texto)[:19], fmt)
        except ValueError:
            continue
    return None


def _pode_continuar_offline():
    """Última verificação OK no GitHub recente — não bloquear por queda de internet."""
    info = db.carregar_instalacao_licenca()
    ult = (info.get('ultimo_ativado_github') or '').strip().lower()
    if ult in ('nao', 'não', 'n', 'no'):
        return False
    if ult in ('sim', 's', 'yes', 'y'):
        ref = info.get('ultima_verificacao') or info.get('ultimo_upload')
    else:
        # Instalações antigas (bloqueio falso por rede): confiar em upload recente + status
        if (info.get('status') or '').lower() == 'bloqueado':
            return False
        ref = info.get('ultima_verificacao') or info.get('ultimo_upload')
    dt = _parse_data_local(ref)
    if not dt:
        return (info.get('status') or '').lower() == 'ativo'
    horas = (datetime.now() - dt).total_seconds() / 3600
    return horas <= GRACE_OFFLINE_HORAS


def _log_aviso_rede(exc):
    global _ultimo_aviso_rede
    agora = time.time()
    if agora - _ultimo_aviso_rede < 300:
        return
    _ultimo_aviso_rede = agora
    print(
        f'[Licença] Sem conexão com GitHub ({exc}). '
        f'Mantendo acesso por até {GRACE_OFFLINE_HORAS}h desde a última verificação OK.'
    )


def _valor_ativado_liberado(valor):
    """True se ativado for sim (texto legível no GitHub). Ausente = sim (arquivos antigos)."""
    if valor is None:
        return True
    v = str(valor).strip().lower()
    if v in ('nao', 'não', 'n', 'no', 'false', '0', 'inativo', 'bloqueado'):
        return False
    return v in ('sim', 's', 'yes', 'y', 'true', '1', 'ativo')


def _montar_payload(instalacao_id, razao_social, ativado='sim'):
    return {
        'instalacao_id': instalacao_id,
        'razao_social': razao_social.strip(),
        'ativado': ativado,
        'data_registro': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'hostname': socket.gethostname(),
    }


def _gravar_json_github(caminho, dados, mensagem_commit, branch=None):
    """Cria ou atualiza o JSON da licença no GitHub."""
    branch = branch or _branch_ativa()
    sha = _obter_sha_existente(caminho, branch)
    conteudo = json.dumps(dados, ensure_ascii=False, indent=2)
    conteudo_b64 = base64.b64encode(conteudo.encode('utf-8')).decode('ascii')
    url = (
        f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
        f'/contents/{_caminho_url(caminho)}'
    )
    corpo = {
        'message': mensagem_commit,
        'content': conteudo_b64,
        'branch': branch,
    }
    if sha:
        corpo['sha'] = sha
    req = urllib.request.Request(
        url,
        data=json.dumps(corpo).encode('utf-8'),
        headers=_headers_api(),
        method='PUT',
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status in (200, 201):
            return True, 'Salvo no GitHub.'
    return False, 'Falha ao salvar no GitHub.'


def _ler_arquivo_licenca(caminho, branch):
    """Baixa e interpreta o JSON da licença no GitHub."""
    url = (
        f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
        f'/contents/{_caminho_url(caminho)}?ref={branch}'
    )
    req = urllib.request.Request(url, headers=_headers_api(), method='GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            meta = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    conteudo_b64 = meta.get('content', '').replace('\n', '')
    if not conteudo_b64:
        return None
    return json.loads(base64.b64decode(conteudo_b64).decode('utf-8'))


def _info_repositorio():
    url = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
    req = urllib.request.Request(url, headers=_headers_api(), method='GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(
                f'Repositório não encontrado ou token sem acesso: {GITHUB_OWNER}/{GITHUB_REPO}. '
                'Confira GITHUB_OWNER, GITHUB_REPO e permissão repo no token.'
            ) from e
        if e.code == 401:
            raise RuntimeError('Token GitHub inválido ou expirado. Gere um novo em github.com/settings/tokens.') from e
        raise RuntimeError(f'Erro ao acessar repositório ({e.code}).') from e


def _branch_ativa():
    info = _info_repositorio()
    return info.get('default_branch') or GITHUB_BRANCH


def _obter_sha_existente(caminho, branch):
    url = (
        f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
        f'/contents/{_caminho_url(caminho)}?ref={branch}'
    )
    req = urllib.request.Request(url, headers=_headers_api(), method='GET')
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            dados = json.loads(resp.read().decode('utf-8'))
            return dados.get('sha')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _apagar_arquivo_github(caminho, branch):
    """Remove arquivo antigo do GitHub (ex.: licença pelo UUID)."""
    sha = _obter_sha_existente(caminho, branch)
    if not sha:
        return
    url = (
        f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
        f'/contents/{_caminho_url(caminho)}'
    )
    corpo = json.dumps({
        'message': f'Remover licença antiga {caminho}',
        'sha': sha,
        'branch': branch,
    })
    req = urllib.request.Request(
        url,
        data=corpo.encode('utf-8'),
        headers=_headers_api(),
        method='DELETE',
    )
    with urllib.request.urlopen(req, timeout=25):
        pass


def _remover_arquivos_licenca_antigos(branch, caminho_novo, instalacao_id, info_antes):
    """Apaga JSON legado (UUID) e nome anterior da transportadora."""
    caminhos = set()
    legado = f'{PASTA_LICENCAS}/{instalacao_id}.json'
    if legado != caminho_novo:
        caminhos.add(legado)
    nome_antigo = (info_antes or {}).get('nome_arquivo_github')
    if nome_antigo:
        antigo = _caminho_arquivo_por_nome(nome_antigo)
        if antigo != caminho_novo:
            caminhos.add(antigo)
    for caminho in caminhos:
        try:
            _apagar_arquivo_github(caminho, branch)
        except Exception as e:
            print(f'[Licença] Não foi possível remover {caminho}: {e}')


def registrar_instalacao(razao_social):
    """
    Registra licença no GitHub. Arquivo: licencas/{Razao_Social}.json
    Retorna (sucesso, mensagem, instalacao_id).
    """
    razao = (razao_social or '').strip()
    if not razao:
        return False, 'Informe a razão social da transportadora.', None

    if not licenca_configurada():
        iid = db.obter_ou_criar_instalacao_id()
        return False, (
            'Serviço de licença indisponível nesta instalação. '
            'Contate o suporte técnico.'
        ), iid

    try:
        nome_arquivo = gerar_nome_arquivo_github(razao)
    except ValueError as e:
        return False, str(e), db.obter_instalacao_id()

    instalacao_id = db.obter_ou_criar_instalacao_id()
    info_antes = db.carregar_instalacao_licenca()
    db.salvar_razao_social_transportadora(razao, nome_arquivo)
    caminho = _caminho_arquivo_por_nome(nome_arquivo)

    try:
        branch = _branch_ativa()
    except RuntimeError as e:
        return False, str(e), instalacao_id

    sha = _obter_sha_existente(caminho, branch)
    ativado = 'não'
    if sha:
        existente = _ler_arquivo_licenca(caminho, branch)
        if existente and existente.get('ativado') is not None:
            ativado = existente.get('ativado')
    elif instalacao_id:
        existente = _ler_arquivo_licenca(f'{PASTA_LICENCAS}/{instalacao_id}.json', branch)
        if existente and existente.get('ativado') is not None:
            ativado = existente.get('ativado')

    try:
        ok_github, msg_github = _gravar_json_github(
            caminho,
            _montar_payload(instalacao_id, razao, ativado=ativado),
            f'Licença {razao}',
            branch,
        )
        if ok_github:
            _remover_arquivos_licenca_antigos(branch, caminho, instalacao_id, info_antes)
            db.registrar_verificacao_licenca(_valor_ativado_liberado(ativado))
            return True, (
                f'Licença salva como {nome_arquivo} com ativado = {ativado}.\n'
                '(Arquivo antigo pelo ID foi removido do GitHub, se existia.)'
            ), instalacao_id
    except urllib.error.HTTPError as e:
        try:
            det = e.read().decode('utf-8')
        except Exception:
            det = str(e)
        msg = f'Falha ao enviar licença ({e.code}): {det}'
        if e.code == 404:
            msg += (
                '\n\nPossíveis causas:\n'
                '• Repositório vazio — faça um commit no GitHub (ex.: README ou licencas/.gitkeep);\n'
                '• Token sem acesso ao repo privado — use token clássico com "repo" ou granular com licencas-clientes;\n'
                '• GITHUB_OWNER errado (seu usuário: opalmeida2017-art).'
            )
        return False, msg, instalacao_id
    except Exception as e:
        return False, f'Erro de rede ao registrar licença: {e}', instalacao_id

    return False, 'Resposta inesperada do GitHub.', instalacao_id


def garantir_registro_inicial_bloqueado():
    """
    Primeira execução: gera o ID local e cria no GitHub um arquivo bloqueado
    (ativado = não). Mantém compatibilidade com instalações já registradas.
    """
    if not licenca_configurada():
        return False, 'Serviço de licença indisponível. Contate o suporte.', db.obter_ou_criar_instalacao_id()

    instalacao_id = db.obter_ou_criar_instalacao_id()
    info = db.carregar_instalacao_licenca()

    try:
        branch = _branch_ativa()
    except Exception as e:
        return False, f'Falha ao preparar licença remota: {e}', instalacao_id

    caminho = _resolver_caminho_licenca() or f'{PASTA_LICENCAS}/{instalacao_id}.json'
    try:
        dados_existentes = _ler_arquivo_licenca(caminho, branch)
        if not dados_existentes and instalacao_id:
            alt_caminho, dados_existentes = _buscar_licenca_por_instalacao_id(instalacao_id, branch)
            if alt_caminho:
                caminho = alt_caminho
        if dados_existentes:
            return True, 'Licença remota já registrada para esta instalação.', instalacao_id

        razao = (info.get('razao_social') or '').strip() or f'Instalação {instalacao_id}'
        ok, msg = _gravar_json_github(
            caminho,
            _montar_payload(instalacao_id, razao, ativado='não'),
            f'Bootstrap licença bloqueada {instalacao_id}',
            branch,
        )
        if ok:
            db.registrar_verificacao_licenca(False)
            return True, 'Licença inicial criada no GitHub com ativado = não.', instalacao_id
        return False, msg, instalacao_id
    except Exception as e:
        return False, f'Erro ao criar licença inicial bloqueada: {e}', instalacao_id


def _recuperar_legado_bloqueio_rede():
    """Corrige bloqueio indevido gravado por falha de rede (versões anteriores)."""
    info = db.carregar_instalacao_licenca()
    if (info.get('status') or '').lower() != 'bloqueado':
        return
    if info.get('ultimo_upload') or info.get('nome_arquivo_github'):
        if (info.get('ultimo_ativado_github') or '').lower() in ('não', 'nao'):
            db.limpar_bloqueio_indevido_rede()
        elif not info.get('ultimo_ativado_github'):
            db.registrar_verificacao_licenca(True)


def _ultima_licenca_conhecida_liberada():
    """Último estado confirmado localmente — usado quando não há internet."""
    info = db.carregar_instalacao_licenca()
    ult = (info.get('ultimo_ativado_github') or '').strip().lower()
    if ult in ('nao', 'não', 'n', 'no'):
        return False
    if ult in ('sim', 's', 'yes', 'y'):
        return True
    return (info.get('status') or '').lower() == 'ativo'


def _retorno_falha_rede(exc):
    """Sem internet: não grava bloqueio; mantém último estado OK."""
    liberada = _ultima_licenca_conhecida_liberada()
    if liberada:
        _log_aviso_rede(exc)
    else:
        print(f'[Licença] Sem conexão ({exc}). Aguardando rede para verificar licença.')
    return liberada


def _buscar_licenca_por_instalacao_id(instalacao_id, branch):
    """Se o caminho salvo estiver errado, acha o JSON pelo ID da instalação."""
    url = (
        f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
        f'/contents/{_caminho_url(PASTA_LICENCAS)}?ref={branch}'
    )
    req = urllib.request.Request(url, headers=_headers_api(), method='GET')
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            itens = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None, None
    for item in itens:
        nome = item.get('name', '')
        if not nome.endswith('.json'):
            continue
        caminho = _caminho_arquivo_por_nome(nome)
        dados = _ler_arquivo_licenca(caminho, branch)
        if dados and str(dados.get('instalacao_id', '')).strip() == str(instalacao_id).strip():
            db.salvar_razao_social_transportadora(
                dados.get('razao_social') or '',
                nome,
            )
            return caminho, dados
    return None, None


def arquivo_licenca_existe(instalacao_id=None):
    """True se o arquivo existir no GitHub e a linha ativado for sim."""
    if not licenca_configurada():
        return False

    if not db.obter_instalacao_id():
        return False

    _recuperar_legado_bloqueio_rede()

    caminho = _resolver_caminho_licenca()
    if not caminho:
        db.registrar_verificacao_licenca(False)
        return False
    iid = instalacao_id or db.obter_instalacao_id()
    branch = GITHUB_BRANCH
    try:
        try:
            branch = _branch_ativa()
        except Exception as e:
            if _eh_erro_rede(e):
                return _retorno_falha_rede(e)
            branch = GITHUB_BRANCH
        dados = _ler_arquivo_licenca(caminho, branch)
        if not dados and iid:
            alt_caminho, dados = _buscar_licenca_por_instalacao_id(iid, branch)
            if alt_caminho:
                caminho = alt_caminho
        if not dados:
            db.registrar_verificacao_licenca(False)
            return False
        ok = _valor_ativado_liberado(dados.get('ativado'))
        db.registrar_verificacao_licenca(ok)
        return ok
    except urllib.error.HTTPError as e:
        if e.code == 404 and iid:
            alt_caminho, dados = _buscar_licenca_por_instalacao_id(iid, branch)
            if dados:
                ok = _valor_ativado_liberado(dados.get('ativado'))
                db.registrar_verificacao_licenca(ok)
                return ok
            db.registrar_verificacao_licenca(False)
            return False
        if e.code >= 500 or _eh_erro_rede(e):
            return _retorno_falha_rede(e)
        print(f'[Licença] Erro HTTP {e.code} na verificação.')
        return _retorno_falha_rede(e)
    except Exception as e:
        if _eh_erro_rede(e):
            return _retorno_falha_rede(e)
        print(f'[Licença] Erro na verificação: {e}')
        return _retorno_falha_rede(e)


def _salvar_json_github(caminho, dados, mensagem_commit, branch=None):
    """Grava JSON no GitHub (atualiza arquivo existente)."""
    branch = branch or _branch_ativa()
    if not _obter_sha_existente(caminho, branch):
        return False, 'Arquivo não encontrado no GitHub.'
    return _gravar_json_github(caminho, dados, mensagem_commit, branch)


def listar_todas_licencas():
    """Lista arquivos .json da pasta licencas no repositório."""
    if not licenca_configurada():
        raise RuntimeError('Configure licenca_config.py (token e repositório).')
    branch = _branch_ativa()
    url = (
        f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
        f'/contents/{_caminho_url(PASTA_LICENCAS)}?ref={branch}'
    )
    req = urllib.request.Request(url, headers=_headers_api(), method='GET')
    with urllib.request.urlopen(req, timeout=25) as resp:
        itens = json.loads(resp.read().decode('utf-8'))
    licencas = []
    for item in sorted(itens, key=lambda x: x.get('name', '')):
        nome = item.get('name', '')
        if not nome.endswith('.json'):
            continue
        caminho = _caminho_arquivo_por_nome(nome)
        dados = _ler_arquivo_licenca(caminho, branch) or {}
        ativado = dados.get('ativado')
        licencas.append({
            'arquivo': nome,
            'caminho': caminho,
            'instalacao_id': dados.get('instalacao_id', '—'),
            'razao_social': dados.get('razao_social', '—'),
            'ativado': str(ativado) if ativado is not None else 'sim',
            'liberado': _valor_ativado_liberado(ativado),
            'data_registro': dados.get('data_registro', '—'),
            'hostname': dados.get('hostname', '—'),
        })

    def _chave_ordenacao(lic):
        dt = _parse_data_local(lic.get('data_registro'))
        return dt or datetime.min

    licencas.sort(key=_chave_ordenacao, reverse=True)
    return licencas


def formatar_data_registro_exibicao(texto):
    """Formata data_registro do JSON para exibição no painel (dd/mm/aaaa HH:MM)."""
    dt = _parse_data_local(texto)
    if dt:
        return dt.strftime('%d/%m/%Y %H:%M')
    t = str(texto or '').strip()
    return t if t and t != '—' else '—'


def excluir_licenca_arquivo(nome_arquivo):
    """Remove o cadastro da licença do repositório GitHub."""
    nome = str(nome_arquivo or '').strip()
    if not nome.endswith('.json'):
        return False, 'Nome de arquivo inválido.'
    if not licenca_configurada():
        return False, 'Licença remota não configurada.'
    try:
        branch = _branch_ativa()
        caminho = _caminho_arquivo_por_nome(nome)
        sha = _obter_sha_existente(caminho, branch)
        if not sha:
            return False, f'Arquivo não encontrado no GitHub: {nome}'
        _apagar_arquivo_github(caminho, branch)
        return True, f'Cadastro removido do GitHub:\n{nome}'
    except urllib.error.HTTPError as e:
        return False, f'Erro ao excluir no GitHub (HTTP {e.code}).'
    except Exception as e:
        return False, str(e)


def definir_ativado_arquivo(nome_arquivo, ativado):
    """Altera ativado para sim ou não no arquivo informado."""
    ativado = str(ativado).strip().lower()
    if ativado not in ('sim', 'não', 'nao'):
        return False, 'Use ativado = sim ou não.'
    valor = 'não' if ativado in ('não', 'nao') else 'sim'
    if not licenca_configurada():
        return False, 'Licença remota não configurada.'
    branch = _branch_ativa()
    caminho = _caminho_arquivo_por_nome(nome_arquivo)
    dados = _ler_arquivo_licenca(caminho, branch)
    if not dados:
        return False, f'Arquivo não encontrado: {nome_arquivo}'
    dados['ativado'] = valor
    razao = dados.get('razao_social', nome_arquivo)
    return _salvar_json_github(
        caminho,
        dados,
        f'Painel: {razao} -> ativado={valor}',
        branch,
    )
