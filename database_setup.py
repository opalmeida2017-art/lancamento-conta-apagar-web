import re
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
import bcrypt
import secrets
import string
import json

from database_connection import (
    conectar_banco,
    caminho_banco,
    executar_schema,
    OperationalError,
    DatabaseError,
)


# ==========================================
# 1. MÓDULO DE SEGURANÇA E CRIPTOGRAFIA
# ==========================================
def gerar_chave_seguranca():
    if not os.path.exists("secret.key"):
        chave = Fernet.generate_key()
        with open("secret.key", "wb") as key_file:
            key_file.write(chave)
        print("[+] Chave de segurança gerada.")

def gerar_hash_senha(senha_texto_puro):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(senha_texto_puro.encode('utf-8'), salt)

def validar_login(email, senha_texto):
    """Verifica se o e-mail existe e se a senha bate com o Hash."""
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT senha_hash FROM usuarios WHERE email = ?", (email,))
    resultado = cursor.fetchone()
    conn.close()

    if resultado:
        senha_hash_banco = resultado[0]
        if bcrypt.checkpw(senha_texto.encode('utf-8'), senha_hash_banco):
            return True, "Login aprovado!"
        else:
            return False, "Senha incorreta."
    return False, "Usuário não encontrado."

# ==========================================
# 2. CONFIGURAÇÃO DO BANCO E USUÁRIOS
# ==========================================
MODELOS_PLACA_PADRAO = [
    "PLACA: AAA-1A11",
    "PLAC: AAA 1A11",
    "PLACA: AAA1A11",
    "PLAC: AAA1111",
]

MODELOS_KM_PADRAO = [
    "KM: 1",
    "KM 1",
    "ODOMETRO : 1",
    "ODOMETRO: 1",
    "HIDROMETRO: 1",
    "ODO: 1",
]


def _contar_registros(cursor, tabela):
    cursor.execute(f"SELECT COUNT(*) FROM {tabela}")
    row = cursor.fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(list(row.values())[0])
    return int(row[0])


def garantir_dados_padrao():
    """Popula modelos de placa/KM e configs vazias (mesmos padrões do app Windows)."""
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        if _contar_registros(cursor, "config_placas") == 0:
            for modelo in MODELOS_PLACA_PADRAO:
                cursor.execute("INSERT INTO config_placas (modelo) VALUES (?)", (modelo,))

        if _contar_registros(cursor, "config_km") == 0:
            for modelo in MODELOS_KM_PADRAO:
                cursor.execute("INSERT INTO config_km (modelo) VALUES (?)", (modelo,))

        if _contar_registros(cursor, "config_combustiveis") == 0:
            cursor.execute(
                "INSERT INTO config_combustiveis (id, cod_etanol, cod_gasolina, cod_s10, cod_s500, cod_arla) "
                "VALUES (1, ?, ?, ?, ?, ?)",
                ("", "", "", "", ""),
            )

        if _contar_registros(cursor, "config_relatorios") == 0:
            cursor.execute(
                "INSERT INTO config_relatorios (id, rel_veiculo, rel_item, cod_grupo_item) VALUES (1, ?, ?, ?)",
                ("117", "118", ""),
            )

        conn.commit()
    finally:
        conn.close()


def inicializar_banco():
    """Cria/atualiza schema no PostgreSQL."""
    executar_schema()
    garantir_dados_padrao()

def configurar_usuario_master():
    """Garante que o seu usuário master sempre exista e não seja contado no limite."""
    email_master = "op.almeida@hotmail.com"
    senha_master = "123"
    
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM usuarios WHERE email = ?", (email_master,))
    if not cursor.fetchone():
        senha_hash = gerar_hash_senha(senha_master)
        cursor.execute("INSERT INTO usuarios (nome_completo, email, senha_hash) VALUES (?, ?, ?)",
                       ("Master Admin", email_master, senha_hash))
        conn.commit()
    conn.close()

def contar_usuarios_comuns():
    """Conta quantos usuários existem, IGNORANDO o Master."""
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM usuarios WHERE email != 'op.almeida@hotmail.com'")
    total = cursor.fetchone()[0]
    conn.close()
    return total

def cadastrar_usuario(nome, email, senha):
    """Cadastra o operador respeitando o limite de 1 funcionário."""
    if contar_usuarios_comuns() >= 1:
        return False, "Limite de usuários atingido (Máximo: 1 Operador)."
    
    senha_hash = gerar_hash_senha(senha)
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO usuarios (nome_completo, email, senha_hash) VALUES (?, ?, ?)",
                       (nome, email, senha_hash))
        conn.commit()
        return True, "Operador cadastrado com sucesso!"
    except Exception as e:
        return False, f"Erro ao cadastrar: E-mail já existe."
    finally:
        conn.close()

# ==========================================
# 3. MÓDULO DE LICENCIAMENTO (SISTEMA NOVO OFF-LINE)
# ==========================================
import hashlib
from datetime import datetime, timedelta

PALAVRA_SECRETA = "AUTOMACAO_FROTA_SEFAZ_2026_MASTER"

def criar_tabela_licenca():
    """Garante que a tabela de licença exista no banco de dados"""
    conn = conectar_banco()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS licenca_sistema (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_expiracao TEXT NOT NULL,
            token_usado TEXT
        )
    ''')
    conn.commit()
    conn.close()

def checar_status_licenca():
    """Verifica se o sistema ainda está dentro do prazo"""
    criar_tabela_licenca()
    try:
        conn = conectar_banco()
        c = conn.cursor()
        c.execute('SELECT data_expiracao FROM licenca_sistema ORDER BY id DESC LIMIT 1')
        resultado = c.fetchone()
        conn.close()

        if not resultado:
            return -1, 0  # -1 = Nunca foi ativado (Tela de Bloqueio)

        data_expiracao = datetime.strptime(resultado[0], "%Y-%m-%d %H:%M:%S")
        hoje = datetime.now()
        
        dias_restantes = (data_expiracao - hoje).days
        
        if dias_restantes < 0:
            return -2, 0  # -2 = Vencido (Tela de Bloqueio)
        elif dias_restantes <= 5:
            return 0, dias_restantes  # 0 = Quase vencendo (Avisa na tela de login)
        else:
            return 1, dias_restantes  # 1 = Ativo e tudo certo
    except Exception as e:
        print(f"Erro ao checar licença: {e}")
        return -1, 0

def ativar_token_31_dias(token):
    """Valida matematicamente o token e adiciona 31 dias de uso"""
    criar_tabela_licenca()
    
    token_limpo = token.replace("-", "").strip().upper()
    if len(token_limpo) != 16:
        return False, "Token inválido! Certifique-se de digitar os 16 caracteres."
        
    caracteres_base = token_limpo[:12]
    assinatura_recebida = token_limpo[12:]
    
    # O aplicativo tenta recriar a assinatura usando o segredo
    assinatura_esperada = hashlib.sha256((caracteres_base + PALAVRA_SECRETA).encode()).hexdigest()[:4].upper()
    
    if assinatura_recebida == assinatura_esperada:
        conn = conectar_banco()
        c = conn.cursor()
        c.execute('SELECT id FROM licenca_sistema WHERE token_usado = ?', (token_limpo,))
        if c.fetchone():
            conn.close()
            return False, "Este token já foi utilizado anteriormente!"

        nova_validade = datetime.now() + timedelta(days=31)
        c.execute('INSERT INTO licenca_sistema (data_expiracao, token_usado) VALUES (?, ?)', 
                  (nova_validade.strftime("%Y-%m-%d %H:%M:%S"), token_limpo))
        conn.commit()
        conn.close()
        
        return True, "Licença validada com sucesso! O sistema foi liberado por 31 dias."
    else:
        return False, "Token inválido ou falsificado!"

# ==========================================
# 3.1 LICENCIAMENTO REMOTO (ID POR INSTALAÇÃO)
# ==========================================
import uuid

def criar_tabela_instalacao():
    conn = conectar_banco()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS instalacao_licenca (
            id INTEGER PRIMARY KEY,
            instalacao_id TEXT UNIQUE NOT NULL,
            razao_social TEXT,
            nome_arquivo_github TEXT,
            data_criacao TEXT NOT NULL,
            ultimo_upload TEXT,
            ultima_verificacao TEXT,
            status TEXT DEFAULT 'pendente'
        )
    ''')
    for col, typ in (
        ("razao_social", "TEXT"),
        ("nome_arquivo_github", "TEXT"),
        ("ultimo_ativado_github", "TEXT"),
    ):
        try:
            c.execute(f"ALTER TABLE instalacao_licenca ADD COLUMN IF NOT EXISTS {col} {typ}")
        except (OperationalError, DatabaseError):
            conn.rollback()
    conn.commit()
    conn.close()

def obter_ou_criar_instalacao_id():
    """Gera UUID único na primeira vez; reutiliza nas próximas."""
    criar_tabela_instalacao()
    conn = conectar_banco()
    c = conn.cursor()
    c.execute('SELECT instalacao_id FROM instalacao_licenca WHERE id = 1')
    row = c.fetchone()
    if row:
        conn.close()
        return row[0]
    novo_id = str(uuid.uuid4())
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        'INSERT INTO instalacao_licenca (id, instalacao_id, data_criacao, status) VALUES (1, ?, ?, ?)',
        (novo_id, agora, 'pendente'),
    )
    conn.commit()
    conn.close()
    return novo_id

def obter_instalacao_id():
    criar_tabela_instalacao()
    conn = conectar_banco()
    c = conn.cursor()
    c.execute('SELECT instalacao_id FROM instalacao_licenca WHERE id = 1')
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def carregar_instalacao_licenca():
    criar_tabela_instalacao()
    conn = conectar_banco()
    conn.row_factory = True
    c = conn.cursor()
    c.execute('SELECT * FROM instalacao_licenca WHERE id = 1')
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {}


def salvar_razao_social_transportadora(razao_social, nome_arquivo_github):
    criar_tabela_instalacao()
    obter_ou_criar_instalacao_id()
    conn = conectar_banco()
    c = conn.cursor()
    c.execute(
        'UPDATE instalacao_licenca SET razao_social = ?, nome_arquivo_github = ? WHERE id = 1',
        (razao_social.strip(), nome_arquivo_github),
    )
    conn.commit()
    conn.close()


def registrar_upload_licenca_ok():
    criar_tabela_instalacao()
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = conectar_banco()
    c = conn.cursor()
    c.execute(
        'UPDATE instalacao_licenca SET ultimo_upload = ?, status = ? WHERE id = 1',
        (agora, 'ativo'),
    )
    conn.commit()
    conn.close()

def registrar_verificacao_licenca(ok):
    """Atualiza status só após leitura confirmada do JSON no GitHub."""
    criar_tabela_instalacao()
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = 'ativo' if ok else 'bloqueado'
    ativado_github = 'sim' if ok else 'não'
    conn = conectar_banco()
    c = conn.cursor()
    c.execute(
        'UPDATE instalacao_licenca SET ultima_verificacao = ?, status = ?, ultimo_ativado_github = ? WHERE id = 1',
        (agora, status, ativado_github),
    )
    conn.commit()
    conn.close()


def limpar_bloqueio_indevido_rede():
    """Remove bloqueio gravado por falha de rede (não por ativado=não no GitHub)."""
    criar_tabela_instalacao()
    conn = conectar_banco()
    c = conn.cursor()
    c.execute(
        '''UPDATE instalacao_licenca
           SET status = 'ativo', ultimo_ativado_github = 'sim'
           WHERE id = 1 AND ultimo_ativado_github IN ('não', 'nao')
             AND nome_arquivo_github IS NOT NULL AND nome_arquivo_github != '' ''',
    )
    conn.commit()
    conn.close()

# ==========================================
# 4. MÓDULO DE CONFIGURAÇÕES E AUTOMAÇÃO
# ==========================================
def carregar_chave():
    return open("secret.key", "rb").read()

def salvar_configuracoes(
    link,
    user_sis,
    senha_sis,
    smtp,
    user_email,
    senha_email,
    ssl,
    porta,
    agendamento_tipo='',
    intervalo_horas=1,
    proxima_execucao='',
    ultima_execucao='',
    destinatarios='',
):
    chave = carregar_chave()
    f = Fernet(chave)
    
    senha_sis_crypt = f.encrypt(senha_sis.encode()) if senha_sis else b""
    senha_email_crypt = f.encrypt(senha_email.encode()) if senha_email else b""
    
    conn = conectar_banco()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM Configuracoes WHERE id = 1")
    if cursor.fetchone():
        cursor.execute("""UPDATE Configuracoes SET link_sistema=?, usuario_sistema=?, senha_sistema_criptografada=?, 
                          email_smtp=?, email_usuario=?, email_senha_criptografada=?, email_ssl=?, email_porta=?,
                          email_agendamento_tipo=?, email_intervalo_horas=?, email_proxima_execucao=?, email_ultima_execucao=?,
                          email_destinatarios=?
                          WHERE id=1""",
                       (
                           link, user_sis, senha_sis_crypt, smtp, user_email, senha_email_crypt, ssl, porta,
                           agendamento_tipo, intervalo_horas, proxima_execucao, ultima_execucao,
                           str(destinatarios or '').strip(),
                       ))
    else:
        cursor.execute("""INSERT INTO Configuracoes (id, link_sistema, usuario_sistema, senha_sistema_criptografada, 
                          email_smtp, email_usuario, email_senha_criptografada, email_ssl, email_porta,
                          email_agendamento_tipo, email_intervalo_horas, email_proxima_execucao, email_ultima_execucao,
                          email_destinatarios) 
                          VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (
                           link, user_sis, senha_sis_crypt, smtp, user_email, senha_email_crypt, ssl, porta,
                           agendamento_tipo, intervalo_horas, proxima_execucao, ultima_execucao,
                           str(destinatarios or '').strip(),
                       ))
    conn.commit()
    conn.close()
    return True, "Configurações salvas com segurança!"

def carregar_configuracoes():
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Configuracoes WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    
    if row:
        chave = carregar_chave()
        f = Fernet(chave)
        
        try: senha_sis = f.decrypt(row[3]).decode() if row[3] else ""
        except: senha_sis = ""
        
        try: senha_email = f.decrypt(row[6]).decode() if row[6] else ""
        except: senha_email = ""
        
        return {
            "link": row[1] or "", "user_sis": row[2] or "", "senha_sis": senha_sis,
            "smtp": row[4] or "", "user_email": row[5] or "", "senha_email": senha_email,
            "ssl": row[7] if row[7] is not None else 1, "porta": row[8] or "",
            "agendamento_tipo": row[9] or "", "intervalo_horas": row[10] or 1,
            "proxima_execucao": row[11] or "", "ultima_execucao": row[12] or "",
            "destinatarios": row[13] or "",
        }
    return None


def suporte_automatico_ja_enviado(chave):
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT 1 FROM suporte_envios_automaticos WHERE chave = ? LIMIT 1',
            (str(chave or '').strip(),),
        )
        enviado = cursor.fetchone() is not None
        conn.close()
        return enviado
    except Exception:
        return False


def registrar_envio_suporte_automatico(chave, horario=''):
    chave = str(chave or '').strip()
    if not chave:
        return False
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO suporte_envios_automaticos (chave, horario, enviado_em)
               VALUES (?, ?, to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
               ON CONFLICT (chave) DO UPDATE SET
                 horario = EXCLUDED.horario,
                 enviado_em = EXCLUDED.enviado_em''',
            (chave, str(horario or '').strip()),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erro ao registrar envio automático de suporte: {e}")
        return False


def atualizar_agendamento_email(tipo='', intervalo_horas=1, proxima_execucao='', ultima_execucao=None):
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        if ultima_execucao is None:
            cursor.execute(
                '''UPDATE Configuracoes
                   SET email_agendamento_tipo = ?, email_intervalo_horas = ?, email_proxima_execucao = ?
                   WHERE id = 1''',
                (tipo, intervalo_horas, proxima_execucao),
            )
        else:
            cursor.execute(
                '''UPDATE Configuracoes
                   SET email_agendamento_tipo = ?, email_intervalo_horas = ?,
                       email_proxima_execucao = ?, email_ultima_execucao = ?
                   WHERE id = 1''',
                (tipo, intervalo_horas, proxima_execucao, ultima_execucao),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erro ao atualizar agendamento de e-mail: {e}")
        return False

# --- Filtros ---
def salvar_filtros(
    mes,
    ano,
    cod_filial='',
    cod_unidade_embarque='',
    ultimos_30_dias=False,
    hoje_apenas=False,
    ultimos_15_dias=False,
    fornecedores_fatura_afaturar='',
    cod_tipo_fornecedor='',
):
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS filtros_salvos (
                id INTEGER PRIMARY KEY,
                mes TEXT,
                ano TEXT,
                cod_filial TEXT DEFAULT '',
                cod_unidade_embarque TEXT DEFAULT '',
                ultimos_30_dias INTEGER DEFAULT 0,
                hoje_apenas INTEGER DEFAULT 0
            )
        ''')
        for sql in (
            "ALTER TABLE filtros_salvos ADD COLUMN IF NOT EXISTS cod_filial TEXT DEFAULT ''",
            "ALTER TABLE filtros_salvos ADD COLUMN IF NOT EXISTS cod_unidade_embarque TEXT DEFAULT ''",
            "ALTER TABLE filtros_salvos ADD COLUMN IF NOT EXISTS ultimos_30_dias INTEGER DEFAULT 0",
            "ALTER TABLE filtros_salvos ADD COLUMN IF NOT EXISTS hoje_apenas INTEGER DEFAULT 0",
            "ALTER TABLE filtros_salvos ADD COLUMN IF NOT EXISTS fornecedores_fatura_afaturar TEXT DEFAULT ''",
            "ALTER TABLE filtros_salvos ADD COLUMN IF NOT EXISTS cod_tipo_fornecedor TEXT DEFAULT ''",
            "ALTER TABLE filtros_salvos ADD COLUMN IF NOT EXISTS ultimos_15_dias INTEGER DEFAULT 0",
        ):
            try:
                cursor.execute(sql)
            except (OperationalError, DatabaseError):
                conn.rollback()

        cursor.execute('SELECT id FROM filtros_salvos ORDER BY id DESC LIMIT 1')
        row = cursor.fetchone()
        valores = (
            mes,
            ano,
            str(cod_filial or '').strip(),
            str(cod_unidade_embarque or '').strip(),
            int(bool(ultimos_30_dias)),
            int(bool(hoje_apenas)),
            int(bool(ultimos_15_dias)),
            str(fornecedores_fatura_afaturar or '').strip(),
            str(cod_tipo_fornecedor or '').strip(),
        )
        if row:
            cursor.execute(
                '''UPDATE filtros_salvos
                   SET mes=?, ano=?, cod_filial=?, cod_unidade_embarque=?,
                       ultimos_30_dias=?, hoje_apenas=?, ultimos_15_dias=?,
                       fornecedores_fatura_afaturar=?, cod_tipo_fornecedor=?
                   WHERE id=?''',
                valores + (row[0],),
            )
        else:
            cursor.execute(
                '''INSERT INTO filtros_salvos
                   (mes, ano, cod_filial, cod_unidade_embarque, ultimos_30_dias,
                    hoje_apenas, ultimos_15_dias, fornecedores_fatura_afaturar,
                    cod_tipo_fornecedor)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                valores,
            )
        conn.commit()
        return True, "Filtros salvos com sucesso."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def carregar_filtros():
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT mes, ano, cod_filial, cod_unidade_embarque, ultimos_30_dias,
                      hoje_apenas, ultimos_15_dias, fornecedores_fatura_afaturar,
                      cod_tipo_fornecedor
               FROM filtros_salvos ORDER BY id DESC LIMIT 1''',
        )
        resultado = cursor.fetchone()
        conn.close()
        if resultado:
            return {
                'mes': resultado[0],
                'ano': resultado[1],
                'cod_filial': resultado[2] or '',
                'cod_unidade_embarque': resultado[3] or '',
                'ultimos_30_dias': bool(resultado[4]) if len(resultado) > 4 else False,
                'hoje_apenas': bool(resultado[5]) if len(resultado) > 5 else False,
                'ultimos_15_dias': bool(resultado[6]) if len(resultado) > 6 else False,
                'fornecedores_fatura_afaturar': (resultado[7] or '') if len(resultado) > 7 else '',
                'cod_tipo_fornecedor': (resultado[8] or '') if len(resultado) > 8 else '',
            }
        return None
    except Exception:
        try:
            conn = conectar_banco()
            cursor = conn.cursor()
            cursor.execute(
                '''SELECT mes, ano, cod_filial, cod_unidade_embarque, ultimos_30_dias
                   FROM filtros_salvos ORDER BY id DESC LIMIT 1''',
            )
            resultado = cursor.fetchone()
            conn.close()
            if resultado:
                return {
                    'mes': resultado[0],
                    'ano': resultado[1],
                    'cod_filial': (resultado[2] or '') if len(resultado) > 2 else '',
                    'cod_unidade_embarque': (resultado[3] or '') if len(resultado) > 3 else '',
                    'ultimos_30_dias': bool(resultado[4]) if len(resultado) > 4 else False,
                    'hoje_apenas': False,
                }
        except Exception:
            pass
        return None

# --- Notas Raspadas (Para a Dashboard) ---
_callback_painel_notas = None


def registrar_callback_painel_notas(callback):
    """Registra função chamada após cada gravação de nota (importada, erro, etc.)."""
    global _callback_painel_notas
    _callback_painel_notas = callback


def _notificar_painel_notas_alterado():
    cb = _callback_painel_notas
    if not cb:
        return
    try:
        cb()
    except Exception as e:
        print(f"Aviso: falha ao atualizar painel de notas: {e}")


def salvar_nota_raspada(dados_nota):
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        
        chave = str(dados_nota.get('chave_nfe') or '').strip()
        num = str(dados_nota.get('num_nota') or '').strip()
        if chave:
            cursor.execute(
                "SELECT id FROM notas_raspadas WHERE chave_nfe = ?",
                (chave,),
            )
        elif num:
            cursor.execute(
                "SELECT id FROM notas_raspadas WHERE num_nota = ? ORDER BY id DESC LIMIT 1",
                (num,),
            )
        else:
            conn.close()
            return False
        if cursor.fetchone():
            conn.close()
            return False

        cursor.execute('''
            INSERT INTO notas_raspadas (status, fornecedor, num_nota, data_em, valor, sit_nfe, chave_nfe, filial, user_ins, codigo_interno, erro_importacao, observacao_nfe, data_insercao)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            dados_nota.get('status', ''), dados_nota.get('fornecedor', ''), dados_nota.get('num_nota', ''), 
            dados_nota.get('data_em', ''), dados_nota.get('valor', ''), dados_nota.get('sit_nfe', ''), 
            dados_nota.get('chave_nfe', ''), dados_nota.get('filial', ''), dados_nota.get('user_ins', ''),
            dados_nota.get('codigo_interno', ''), dados_nota.get('erro_importacao', ''), dados_nota.get('observacao_nfe', ''),
            dados_nota.get('data_insercao') or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        ))
        conn.commit()
        conn.close()
        _notificar_painel_notas_alterado()
        return True
    except Exception as e:
        print(f"Erro ao salvar nota no banco: {e}")
        return False

MSG_ERRO_ARQUIVO_INDISPONIVEL = (
    'Arquivo indisponível para download (Cancelada/Rejeitada)'
)


def texto_indica_arquivo_indisponivel(texto):
    """True se o retorno SEFAZ/erro for cancelada/rejeitada/indisponível."""
    if not texto:
        return False
    tl = str(texto).lower()
    if 'indispon' in tl:
        return True
    if 'cancelad' in tl and ('arquivo' in tl or 'download' in tl or 'nfe' in tl):
        return True
    if 'rejeit' in tl and ('arquivo' in tl or 'download' in tl or '656' in tl):
        return True
    return False


def nota_erro_arquivo_indisponivel(erro_importacao):
    """True se a nota já foi gravada com o erro padrão de arquivo indisponível."""
    if not erro_importacao:
        return False
    if erro_importacao.strip() == MSG_ERRO_ARQUIVO_INDISPONIVEL:
        return True
    return texto_indica_arquivo_indisponivel(erro_importacao)


def registrar_erro_nota_painel(dados_nota, erro_msg):
    """Grava status Erro no banco e no dict da nota (dashboard / painel da aplicação)."""
    msg = (erro_msg or 'Erro no processamento')[:500]
    dados_nota['status'] = 'Erro'
    dados_nota['erro_importacao'] = msg
    dados_nota['codigo_interno'] = ''
    return salvar_ou_atualizar_nota_raspada(dados_nota)


def salvar_ou_atualizar_nota_raspada(dados_nota, arquivar_automatico=False):
    """Insere ou atualiza a nota no dashboard (upsert por chave_nfe ou num_nota)."""
    if atualizar_nota_raspada(dados_nota, arquivar_automatico=arquivar_automatico):
        return True
    return salvar_nota_raspada(dados_nota)


def atualizar_nota_raspada(dados_nota, arquivar_automatico=False):
    try:
        chave = str(dados_nota.get('chave_nfe') or '').strip()
        num = str(dados_nota.get('num_nota') or '').strip()
        if not chave and not num:
            return False

        conn = conectar_banco()
        cursor = conn.cursor()
        params_base = (
            dados_nota.get('status', ''),
            dados_nota.get('codigo_interno', ''),
            dados_nota.get('erro_importacao', ''),
            dados_nota.get('observacao_nfe', ''),
        )
        if chave:
            filtro = 'chave_nfe = ?'
            filtro_val = chave
        else:
            filtro = (
                'id = (SELECT id FROM notas_raspadas WHERE num_nota = ? '
                'ORDER BY id DESC LIMIT 1)'
            )
            filtro_val = num

        if arquivar_automatico:
            cursor.execute(
                f'''
                UPDATE notas_raspadas
                SET status = ?, codigo_interno = ?, erro_importacao = ?,
                    observacao_nfe = ?, nfe_arquiva = ?
                WHERE {filtro}
                ''',
                params_base + ('☑', filtro_val),
            )
        else:
            cursor.execute(
                f'''
                UPDATE notas_raspadas
                SET status = ?, codigo_interno = ?, erro_importacao = ?, observacao_nfe = ?
                WHERE {filtro}
                ''',
                params_base + (filtro_val,),
            )
        alterou = cursor.rowcount > 0
        conn.commit()
        conn.close()
        if alterou:
            _notificar_painel_notas_alterado()
        return alterou
    except Exception as e:
        print(f"Erro ao atualizar nota: {e}")
        return False


def marcar_nota_importada_painel(dados_nota):
    """Atualiza status Importado no dashboard (upsert) e refresca a tela."""
    dados = dict(dados_nota or {})
    dados['status'] = 'Importado'
    dados['erro_importacao'] = dados.get('erro_importacao') or ''
    return salvar_ou_atualizar_nota_raspada(dados)


def listar_todas_notas():
    try:
        conn = conectar_banco()
        conn.row_factory = True
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notas_raspadas ORDER BY id DESC")
        res = cursor.fetchall()
        conn.close()
        return [dict(row) for row in res]
    except:
        return []

if __name__ == "__main__":
    gerar_chave_seguranca()
    inicializar_banco()
    configurar_usuario_master()
    print("Banco e Master configurados com sucesso!")
    
# =======================================================
# FUNÇÕES PARA CONFIGURAÇÃO DOS MODELOS DE PLACA
# =======================================================
def criar_tabela_config_placas():
    """Garante modelos de placa (schema criado via pg_schema.sql)."""
    conn = conectar_banco()
    c = conn.cursor()
    if _contar_registros(c, 'config_placas') == 0:
        for p in MODELOS_PLACA_PADRAO:
            c.execute('INSERT INTO config_placas (modelo) VALUES (?)', (p,))
        conn.commit()
    conn.close()

def obter_modelos_placa():
    criar_tabela_config_placas()
    try:
        conn = conectar_banco()
        c = conn.cursor()
        c.execute('SELECT modelo FROM config_placas')
        resultados = c.fetchall()
        conn.close()
        return [r[0] for r in resultados]
    except:
        return []

def salvar_modelos_placa(modelos_str):
    criar_tabela_config_placas()
    conn = conectar_banco()
    c = conn.cursor()
    c.execute('DELETE FROM config_placas') 
    
    modelos = [m.strip() for m in modelos_str.split(',') if m.strip()]
    for m in modelos:
        c.execute('INSERT INTO config_placas (modelo) VALUES (?)', (m,))
        
    conn.commit()
    conn.close()

def obter_modelos_placa_string():
    modelos = obter_modelos_placa() 
    return ", ".join(modelos)


def validar_modelo_placa(modelo):
    """Valida máscara de placa: A = letra, 1 = número (texto inicial exato na NFe)."""
    texto = str(modelo or '').strip()
    if not texto:
        return False, 'Informe ao menos um modelo de placa.'
    match = re.search(r'([A1][A1\-\s]{5,}[A1])', texto)
    if not match:
        return False, (
            f"Formato inválido: '{texto}'\n\n"
            'Use A para letras e 1 para números na placa.\n'
            'O texto antes da placa deve ser igual ao da observação da NFe.\n'
            'Exemplo: Placa : AAA-1A11'
        )
    mascara = match.group(1)
    if re.search(r'[02-9]', mascara):
        return False, (
            f"Modelo inválido: '{texto}'\n\n"
            'Na máscara da placa use apenas A (letra) e 1 (número).\n'
            'Não use dígitos reais (0, 2, 3...).'
        )
    if re.search(r'[a-z]', mascara):
        return False, (
            f"Modelo inválido: '{texto}'\n\n"
            'Use A maiúsculo para representar letras na máscara.'
        )
    if re.search(r'[^A1\-\s]', mascara):
        return False, (
            f"Modelo inválido: '{texto}'\n\n"
            'Na máscara só são permitidos A, 1, hífen e espaço.'
        )
    return True, ''


def validar_modelo_km(modelo):
    """Valida máscara de KM: 1 = dígito; ponto/vírgula mantêm o formato."""
    texto = str(modelo or '').strip()
    if not texto:
        return False, 'Informe ao menos um modelo de KM.'
    if '1' not in texto:
        return False, (
            f"Modelo inválido: '{texto}'\n\n"
            'Substitua os dígitos do KM por 1.\n'
            'O texto inicial deve ser igual ao da observação da NFe.\n'
            'Exemplo: odometro : 111.111,11'
        )
    idx = texto.find('1')
    prefixo = texto[:idx]
    template = texto[idx:]
    if not prefixo.strip():
        return False, (
            f"Modelo inválido: '{texto}'\n\n"
            'Informe o texto que aparece antes do KM na NFe.\n'
            'Exemplo: odometro : 111.111,11'
        )
    if re.search(r'[^1\s.,]', template):
        return False, (
            f"Modelo inválido: '{texto}'\n\n"
            'Depois do texto inicial use apenas 1, ponto (.) e vírgula (,).\n'
            'Exemplo: odometro : 111.111,11'
        )
    if re.search(r'[02-9A-Za-z]', template):
        return False, (
            f"Modelo inválido: '{texto}'\n\n"
            'Use somente o caractere 1 no lugar dos dígitos do KM.'
        )
    return True, ''


def validar_lista_modelos_placa(modelos_str):
    modelos = [m.strip() for m in str(modelos_str or '').split(',') if m.strip()]
    if not modelos:
        return True, '', modelos
    for modelo in modelos:
        ok, msg = validar_modelo_placa(modelo)
        if not ok:
            return False, msg, modelos
    return True, '', modelos


def parse_lista_modelos_km(modelos_str):
    """
    Separa vários modelos de KM.
    Vírgula decimal (ex.: 111.111,11) não quebra o modelo.
    Nova vírgula só separa quando logo após vier letra (início do próximo texto).
    """
    texto = str(modelos_str or '').strip()
    if not texto:
        return []
    partes = re.split(r',(?=\s*[A-Za-zÀ-ÿ])', texto)
    return [p.strip() for p in partes if p.strip()]


def juntar_lista_modelos_km(modelos):
    lista = [str(m or '').strip() for m in (modelos or []) if str(m or '').strip()]
    return ', '.join(lista)


def validar_lista_modelos_km(modelos_str):
    modelos = parse_lista_modelos_km(modelos_str)
    if not modelos:
        return True, '', modelos
    for modelo in modelos:
        ok, msg = validar_modelo_km(modelo)
        if not ok:
            return False, msg, modelos
    return True, '', modelos

# =======================================================
# FUNÇÕES PARA CONFIGURAÇÃO DOS MODELOS DE KM
# =======================================================
def criar_tabela_config_km():
    """Garante modelos de KM (schema criado via pg_schema.sql)."""
    conn = conectar_banco()
    c = conn.cursor()
    if _contar_registros(c, 'config_km') == 0:
        for p in MODELOS_KM_PADRAO:
            c.execute('INSERT INTO config_km (modelo) VALUES (?)', (p,))
        conn.commit()
    conn.close()

def obter_modelos_km():
    criar_tabela_config_km()
    try:
        conn = conectar_banco()
        c = conn.cursor()
        c.execute('SELECT modelo FROM config_km')
        resultados = c.fetchall()
        conn.close()
        return [r[0] for r in resultados]
    except:
        return []

def salvar_modelos_km(modelos_str):
    criar_tabela_config_km()
    conn = conectar_banco()
    c = conn.cursor()
    c.execute('DELETE FROM config_km') 
    
    modelos = parse_lista_modelos_km(modelos_str)
    for m in modelos:
        c.execute('INSERT INTO config_km (modelo) VALUES (?)', (m,))
        
    conn.commit()
    conn.close()

def obter_modelos_km_string():
    modelos = obter_modelos_km() 
    return juntar_lista_modelos_km(modelos)

def normalizar_placa_frota(placa):
    return str(placa or '').replace('-', '').replace(' ', '').upper().strip()


MSG_ERRO_PLACA_VEICULO = 'erro placa veiculo'
MSG_ERRO_CARRETA_DUPLICADA = 'a mesma carreta esta em mais de um cavalo'
MSG_ERRO_FALTA_VEICULO_OBS = (
    'falta veiculo na observacao e nfe nao esta marcada para estoque'
)

_COLUNAS_CARRETA_FROTA = ('carreta1', 'carreta2', 'carreta3')
_CHAVE_ULTIMA_SYNC_FROTA = 'ultima_sincronizacao_frota'


def _garantir_tabela_config_sistema(cursor):
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS config_sistema (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )''',
    )


def registrar_ultima_sincronizacao_frota(data_hora=None, conn=None):
    """Grava data/hora da última sincronização do painel de veículos."""
    fechar = False
    if data_hora is None:
        data_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    try:
        if conn is None:
            conn = conectar_banco()
            fechar = True
        cursor = conn.cursor()
        _garantir_tabela_config_sistema(cursor)
        cursor.execute(
            '''INSERT INTO config_sistema (chave, valor)
               VALUES (?, ?)
               ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor''',
            (_CHAVE_ULTIMA_SYNC_FROTA, str(data_hora)),
        )
        if fechar:
            conn.commit()
            conn.close()
        return True
    except Exception as e:
        print(f'Erro ao registrar última sincronização da frota: {e}')
        return False


def obter_ultima_sincronizacao_frota():
    """Retorna texto da última atualização do painel de veículos ou string vazia."""
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        _garantir_tabela_config_sistema(cursor)
        cursor.execute(
            'SELECT valor FROM config_sistema WHERE chave = ?',
            (_CHAVE_ULTIMA_SYNC_FROTA,),
        )
        row = cursor.fetchone()
        conn.close()
        return str(row[0]).strip() if row and row[0] else ''
    except Exception:
        return ''


def _migrar_frota_erp_sem_unique_placa(cursor):
    """Legado SQLite — no PostgreSQL o schema já está correto."""
    return


def _garantir_colunas_frota_erp(cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS frota_erp (
        codVeiculo INTEGER PRIMARY KEY,
        cavalo TEXT DEFAULT '',
        placa TEXT,
        carreta1 TEXT DEFAULT '',
        carreta2 TEXT DEFAULT '',
        carreta3 TEXT DEFAULT '',
        veiculoProprio TEXT DEFAULT '',
        ultima_atualizacao DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    _migrar_frota_erp_sem_unique_placa(cursor)
    for sql in (
        "ALTER TABLE frota_erp ADD COLUMN IF NOT EXISTS cavalo TEXT DEFAULT ''",
        "ALTER TABLE frota_erp ADD COLUMN IF NOT EXISTS carreta1 TEXT DEFAULT ''",
        "ALTER TABLE frota_erp ADD COLUMN IF NOT EXISTS carreta2 TEXT DEFAULT ''",
        "ALTER TABLE frota_erp ADD COLUMN IF NOT EXISTS carreta3 TEXT DEFAULT ''",
        "ALTER TABLE frota_erp ADD COLUMN IF NOT EXISTS movimentacao_carreta TEXT DEFAULT ''",
        "ALTER TABLE frota_erp ADD COLUMN IF NOT EXISTS data_movimentacao TEXT DEFAULT ''",
    ):
        try:
            cursor.execute(sql)
        except (OperationalError, DatabaseError):
            pass
    _garantir_tabela_historico_movimentacao_carreta(cursor)


def _garantir_tabela_historico_movimentacao_carreta(cursor):
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS frota_historico_movimentacao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placa_carreta TEXT,
            cod_veiculo_origem INTEGER,
            placa_cavalo_origem TEXT,
            cod_veiculo_destino INTEGER,
            placa_cavalo_destino TEXT,
            data_movimentacao TEXT,
            texto_resumo TEXT
        )''',
    )


def _carregar_linhas_frota_erp(cursor=None):
    fechar = False
    if cursor is None:
        conn = conectar_banco()
        conn.row_factory = True
        cursor = conn.cursor()
        _garantir_colunas_frota_erp(cursor)
        fechar = True
    else:
        conn = None

    cursor.execute(
        '''SELECT codVeiculo, cavalo, placa, carreta1, carreta2, carreta3, veiculoProprio
           FROM frota_erp ORDER BY codVeiculo ASC''',
    )
    colunas = [desc[0] for desc in (cursor.description or [])]
    linhas = []
    for row in cursor.fetchall():
        if hasattr(row, 'keys'):
            linhas.append(dict(row))
        elif colunas:
            linhas.append(dict(zip(colunas, row)))
        else:
            linhas.append({})
    if fechar:
        conn.close()
    return linhas


def detectar_placas_carreta_duplicadas(linhas=None):
    """
    Placas repetidas em carreta1/2/3 em mais de um codVeiculo (cavalo).
    """
    if linhas is None:
        linhas = _carregar_linhas_frota_erp()

    indice = {}
    for row in linhas:
        cod = str(row.get('codVeiculo') or '').strip()
        if not cod:
            continue
        for coluna in _COLUNAS_CARRETA_FROTA:
            placa = normalizar_placa_frota(row.get(coluna))
            if not placa:
                continue
            indice.setdefault(placa, set()).add(cod)

    return {placa for placa, cods in indice.items() if len(cods) > 1}


def linha_frota_tem_carreta_duplicada(row, placas_duplicadas=None):
    if placas_duplicadas is None:
        placas_duplicadas = detectar_placas_carreta_duplicadas()
    for coluna in _COLUNAS_CARRETA_FROTA:
        if normalizar_placa_frota(row.get(coluna)) in placas_duplicadas:
            return True
    return False


def resolver_placa_na_frota(placa, linhas=None):
    """
    Resolve placa no painel da frota.
    Retorno: status ok | nao_encontrado | carreta_duplicada
    """
    placa_norm = normalizar_placa_frota(placa)
    if not placa_norm:
        return {'status': 'nao_encontrado', 'placa': ''}

    if linhas is None:
        linhas = _carregar_linhas_frota_erp()

    correspondencias = []
    for row in linhas:
        cod = str(row.get('codVeiculo') or '').strip()
        if not cod:
            continue
        for coluna in ('placa',) + _COLUNAS_CARRETA_FROTA:
            if normalizar_placa_frota(row.get(coluna)) == placa_norm:
                correspondencias.append((cod, coluna))

    if not correspondencias:
        return {'status': 'nao_encontrado', 'placa': placa_norm}

    codigos = sorted({cod for cod, _ in correspondencias})
    carreta_hits = [(cod, col) for cod, col in correspondencias if col in _COLUNAS_CARRETA_FROTA]
    codigos_carreta = sorted({cod for cod, _ in carreta_hits})

    if len(codigos_carreta) > 1:
        return {
            'status': 'carreta_duplicada',
            'placa': placa_norm,
            'codigos': codigos_carreta,
        }

    if len(codigos) > 1:
        return {
            'status': 'carreta_duplicada',
            'placa': placa_norm,
            'codigos': codigos,
        }

    cod, coluna = correspondencias[0]
    return {
        'status': 'ok',
        'placa': placa_norm,
        'cod_veiculo': cod,
        'coluna': coluna,
    }


def obter_cod_veiculo_por_placa(placa):
    """
    Busca codVeiculo na frota comparando placa, carreta1, carreta2 e carreta3.
    Retorna (cod_veiculo, coluna_encontrada) ou ('', '').
    """
    resultado = resolver_placa_na_frota(placa)
    if resultado.get('status') != 'ok':
        return '', ''
    return resultado.get('cod_veiculo', ''), resultado.get('coluna', '')


def _mapear_carretas_por_veiculo(linhas):
    """Mapeia placa de carreta -> cavalo onde estava."""
    mapa = {}
    for row in linhas or []:
        cod = row.get('codVeiculo')
        if cod is None or str(cod).strip() == '':
            continue
        try:
            cod_int = int(cod)
        except (TypeError, ValueError):
            continue
        placa_cav = normalizar_placa_frota(row.get('placa'))
        for coluna in _COLUNAS_CARRETA_FROTA:
            placa_carreta = normalizar_placa_frota(row.get(coluna))
            if placa_carreta:
                mapa[placa_carreta] = {
                    'codVeiculo': cod_int,
                    'placa_cavalo': placa_cav,
                }
    return mapa


def detectar_movimentacoes_carreta(linhas_antigas, linhas_novas, data_movimentacao):
    """
    Compara frota anterior com a nova importação do relatório 117.
    Registra carretas que mudaram de um cavalo para outro.
    """
    antigo = _mapear_carretas_por_veiculo(linhas_antigas)
    novo = _mapear_carretas_por_veiculo(linhas_novas)
    movimentos = []

    for placa_carreta, info_ant in antigo.items():
        info_nov = novo.get(placa_carreta)
        if not info_nov or info_nov['codVeiculo'] == info_ant['codVeiculo']:
            continue

        placa_orig = info_ant['placa_cavalo'] or str(info_ant['codVeiculo'])
        placa_dest = info_nov['placa_cavalo'] or str(info_nov['codVeiculo'])
        texto = (
            f'carreta {placa_carreta} saiu do veiculo {placa_orig} '
            f'p/ veiculo {placa_dest}'
        )
        movimentos.append({
            'placa_carreta': placa_carreta,
            'cod_veiculo_origem': info_ant['codVeiculo'],
            'placa_cavalo_origem': info_ant['placa_cavalo'],
            'cod_veiculo_destino': info_nov['codVeiculo'],
            'placa_cavalo_destino': info_nov['placa_cavalo'],
            'data_movimentacao': data_movimentacao,
            'texto_resumo': texto,
        })

    return movimentos


def _registrar_historico_movimentacoes_carreta(cursor, movimentos):
    _garantir_tabela_historico_movimentacao_carreta(cursor)
    for mov in movimentos or []:
        cursor.execute(
            '''INSERT INTO frota_historico_movimentacao (
                placa_carreta, cod_veiculo_origem, placa_cavalo_origem,
                cod_veiculo_destino, placa_cavalo_destino,
                data_movimentacao, texto_resumo
            ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (
                mov.get('placa_carreta', ''),
                mov.get('cod_veiculo_origem'),
                mov.get('placa_cavalo_origem', ''),
                mov.get('cod_veiculo_destino'),
                mov.get('placa_cavalo_destino', ''),
                mov.get('data_movimentacao', ''),
                mov.get('texto_resumo', ''),
            ),
        )


def _agrupar_movimentacoes_por_cavalo_origem(movimentos):
    por_cavalo = {}
    for mov in movimentos or []:
        cod = mov.get('cod_veiculo_origem')
        if cod is None:
            continue
        por_cavalo.setdefault(int(cod), []).append(mov.get('texto_resumo', ''))
    return {
        cod: ' | '.join(textos)
        for cod, textos in por_cavalo.items()
        if textos
    }


def sincronizar_frota_erp(lista_veiculos):
    conn = None
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        _garantir_colunas_frota_erp(cursor)
        conn.commit()

        linhas_antigas = _carregar_linhas_frota_erp(cursor)
        data_sync = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        movimentos = detectar_movimentacoes_carreta(
            linhas_antigas, lista_veiculos, data_sync,
        )
        mov_por_cavalo = _agrupar_movimentacoes_por_cavalo_origem(movimentos)
        if movimentos:
            _registrar_historico_movimentacoes_carreta(cursor, movimentos)

        cursor.execute('DELETE FROM frota_erp')

        for v in lista_veiculos:
            cod = v.get('codVeiculo')
            if cod is None or str(cod).strip() == '':
                continue
            placa = normalizar_placa_frota(v.get('placa'))
            if not placa:
                continue
            cod_int = int(cod)
            movimentacao = mov_por_cavalo.get(cod_int, '')
            data_mov = data_sync if movimentacao else ''
            cursor.execute(
                '''
                INSERT INTO frota_erp (
                    codVeiculo, cavalo, placa, carreta1, carreta2, carreta3,
                    veiculoProprio, ultima_atualizacao,
                    movimentacao_carreta, data_movimentacao
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                ''',
                (
                    cod_int,
                    str(v.get('cavalo') or '').strip().upper(),
                    placa,
                    normalizar_placa_frota(v.get('carreta1')),
                    normalizar_placa_frota(v.get('carreta2')),
                    normalizar_placa_frota(v.get('carreta3')),
                    str(v.get('veiculoProprio') or '').strip(),
                    movimentacao,
                    data_mov,
                ),
            )

        registrar_ultima_sincronizacao_frota(data_hora=data_sync, conn=conn)
        conn.commit()
        conn.close()
        if movimentos:
            print(f' 📦 {len(movimentos)} movimentação(ões) de carreta registrada(s).')
        return True
    except Exception as e:
        print(f"Erro ao salvar frota no banco: {e}")
        if conn is not None:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return False
    
def obter_vinculo_veiculo(codigo_veiculo):
    """Busca o tipo de vínculo do veículo na frota (PostgreSQL)."""
    try:
        conn = conectar_banco()
        c = conn.cursor()
        c.execute("SELECT veiculoProprio FROM frota_erp WHERE codVeiculo = ?", (codigo_veiculo,))
        resultado = c.fetchone()
        conn.close()
        
        if resultado:
            val = resultado.get('veiculoProprio') if isinstance(resultado, dict) else resultado[0]
            return str(val or '').strip().upper()
        return ""
    except Exception as e:
        print(f"Erro ao buscar vínculo do veículo {codigo_veiculo}: {e}")
        return ""
    
# =======================================================
# FUNÇÕES PARA CONFIGURAÇÃO DOS CÓDIGOS DE COMBUSTÍVEL
# =======================================================
def criar_tabela_config_combustiveis():
    """Garante registro de combustíveis (schema criado via pg_schema.sql)."""
    conn = conectar_banco()
    c = conn.cursor()
    if _contar_registros(c, 'config_combustiveis') == 0:
        c.execute(
            'INSERT INTO config_combustiveis (id, cod_etanol, cod_gasolina, cod_s10, cod_s500, cod_arla) '
            'VALUES (1, ?, ?, ?, ?, ?)',
            ('', '', '', '', ''),
        )
        conn.commit()
    conn.close()

def salvar_codigos_combustiveis(etanol, gasolina, s10, s500, arla):
    criar_tabela_config_combustiveis()
    conn = conectar_banco()
    c = conn.cursor()
    c.execute('''
        UPDATE config_combustiveis 
        SET cod_etanol = ?, cod_gasolina = ?, cod_s10 = ?, cod_s500 = ?, cod_arla = ?
        WHERE id = 1
    ''', (etanol.strip(), gasolina.strip(), s10.strip(), s500.strip(), arla.strip()))
    conn.commit()
    conn.close()

def carregar_codigos_combustiveis():
    criar_tabela_config_combustiveis()
    try:
        conn = conectar_banco()
        c = conn.cursor()
        c.execute('SELECT cod_etanol, cod_gasolina, cod_s10, cod_s500, cod_arla FROM config_combustiveis WHERE id = 1')
        res = c.fetchone()
        conn.close()
        if res:
            return {"etanol": res[0], "gasolina": res[1], "s10": res[2], "s500": res[3], "arla": res[4] if res[4] else ""}
    except:
        pass
    return {"etanol": "", "gasolina": "", "s10": "", "s500": "", "arla": ""}

def atualizar_estoque_nota(chave_nfe, valor_estoque):
    """Atualiza se a NFe vai para o estoque ou não"""
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute("UPDATE notas_raspadas SET nfe_estoque = ? WHERE chave_nfe = ?", (valor_estoque, chave_nfe))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erro ao atualizar estoque: {e}")
        return False
    
def atualizar_arquiva_nota(chave_nfe, valor_arquiva):
    """Marca se a NFe está arquivada (robô ignora download e importação)."""
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE notas_raspadas SET nfe_arquiva = ? WHERE chave_nfe = ?",
            (valor_arquiva, chave_nfe),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erro ao atualizar arquiva: {e}")
        return False


def nota_erro_download_permanente(chave_nfe='', num_nota=''):
    """Erro de download/XML já registrado — não tentar baixar de novo."""
    chave = (chave_nfe or '').strip()
    num = str(num_nota or '').strip()
    if not chave and not num:
        return False
    try:
        conn = conectar_banco()
        c = conn.cursor()
        if chave:
            c.execute(
                'SELECT status, erro_importacao FROM notas_raspadas WHERE chave_nfe = ?',
                (chave,),
            )
        else:
            c.execute(
                '''SELECT status, erro_importacao FROM notas_raspadas
                   WHERE num_nota = ? ORDER BY id DESC LIMIT 1''',
                (num,),
            )
        row = c.fetchone()
        conn.close()
        if not row or str(row[0] or '').strip().upper() != 'ERRO':
            return False
        erro = str(row[1] or '')
        if nota_erro_arquivo_indisponivel(erro):
            return True
        el = erro.lower()
        marcadores = (
            'sefaz', 'download', 'tentativas', 'consumo indevido', 'indispon',
            'rejeicao', 'rejeição', 'nao retornou', 'não retornou',
        )
        return any(m in el for m in marcadores)
    except Exception as e:
        print(f'Erro ao verificar download permanente: {e}')
        return False


def nota_encerrada_robo(chave_nfe='', num_nota=''):
    """
    Nota já tratada pelo robô: importada com código (finalizou) ou erro gravado.
    Evita ficar clicando Importar na mesma linha do painel.
    """
    chave = (chave_nfe or '').strip()
    num = str(num_nota or '').strip()
    if not chave and not num:
        return False, ''
    try:
        conn = conectar_banco()
        c = conn.cursor()
        if chave:
            c.execute(
                'SELECT status, codigo_interno, erro_importacao FROM notas_raspadas WHERE chave_nfe = ?',
                (chave,),
            )
        else:
            c.execute(
                '''SELECT status, codigo_interno, erro_importacao FROM notas_raspadas
                   WHERE num_nota = ? ORDER BY id DESC LIMIT 1''',
                (num,),
            )
        row = c.fetchone()
        conn.close()
        if not row:
            return False, ''
        status = str(row[0] or '').strip().upper()
        codigo = str(row[1] or '').strip()
        erro = str(row[2] or '').strip()
        if status == 'IMPORTADO' and codigo:
            return True, f'importada (cód. {codigo})'
        if status == 'ERRO' and erro:
            return True, 'erro registrado'
        return False, ''
    except Exception as e:
        print(f'Erro ao verificar nota encerrada: {e}')
        return False, ''


def verificar_nota_arquiva(chave_nfe):
    """True se no painel a nota está arquivada (exceto Importado/Processado)."""
    if not chave_nfe or not str(chave_nfe).strip():
        return False
    try:
        conn = conectar_banco()
        c = conn.cursor()
        c.execute(
            "SELECT nfe_arquiva, status FROM notas_raspadas WHERE chave_nfe = ?",
            (chave_nfe.strip(),),
        )
        row = c.fetchone()
        conn.close()
        if not row:
            return False
        status = str(row[1] or "").strip().upper()
        if status in ("IMPORTADO", "PROCESSADO"):
            return False
        return "☑" in str(row[0] or "")
    except Exception as e:
        print(f"Erro ao verificar arquiva: {e}")
        return False


def verificar_nota_estoque(chave_nfe):
    """Verifica se o usuário marcou a flag ☑ NFe p/ Estoque no painel"""
    try:
        conn = conectar_banco()
        c = conn.cursor()
        c.execute("SELECT nfe_estoque FROM notas_raspadas WHERE chave_nfe = ?", (chave_nfe,))
        res = c.fetchone()
        conn.close()
        
        # Se o resultado existir e contiver o símbolo de marcado (☑), retorna True
        if res and res[0] and "☑" in res[0]:
            return True
    except Exception as e:
        print(f"Erro ao verificar estoque: {e}")
        
    return False

# =======================================================
# FUNÇÕES PARA O RELATÓRIO DE ITENS (CÓD. 118)
# =======================================================
def criar_tabela_itens():
    conn = conectar_banco()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS itens_erp (
            codItemD TEXT PRIMARY KEY,
            descGrupoImp TEXT,
            descNegocioImp TEXT,
            descricao TEXT,
            ultima_atualizacao DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def sincronizar_itens_erp(lista_itens):
    """Salva os itens baixados do Excel no Banco de Dados"""
    criar_tabela_itens()
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        
        for item in lista_itens:
            cursor.execute('''
                INSERT INTO itens_erp (codItemD, descGrupoImp, descNegocioImp, descricao, ultima_atualizacao)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(codItemD) DO UPDATE SET
                descGrupoImp=excluded.descGrupoImp,
                descNegocioImp=excluded.descNegocioImp,
                descricao=excluded.descricao,
                ultima_atualizacao=CURRENT_TIMESTAMP
            ''', (
                str(item.get('codItemD', '')).strip(), 
                str(item.get('descGrupoImp', '')).strip(), 
                str(item.get('descNegocioImp', '')).strip(), 
                str(item.get('descricao', '')).strip()
            ))
            
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Erro ao salvar itens no banco: {e}")
        return False

def _carregar_historico_movimentacoes_agrupado(cursor=None):
    """Agrupa histórico de movimentações por cavalo de origem (codVeiculo)."""
    fechar = False
    if cursor is None:
        conn = conectar_banco()
        cursor = conn.cursor()
        _garantir_colunas_frota_erp(cursor)
        fechar = True
    else:
        conn = None

    _garantir_tabela_historico_movimentacao_carreta(cursor)
    cursor.execute(
        '''SELECT cod_veiculo_origem, data_movimentacao, texto_resumo
           FROM frota_historico_movimentacao
           ORDER BY id ASC''',
    )
    colunas = [desc[0] for desc in (cursor.description or [])]
    agrupado = {}
    for row in cursor.fetchall():
        if hasattr(row, 'keys'):
            item = dict(row)
        elif colunas:
            item = dict(zip(colunas, row))
        else:
            continue
        cod = item.get('cod_veiculo_origem')
        if cod is None:
            continue
        try:
            cod_int = int(cod)
        except (TypeError, ValueError):
            continue
        agrupado.setdefault(cod_int, []).append(item)

    if fechar:
        conn.close()
    return agrupado


def _formatar_historico_movimentacoes_cavalo(registros, max_itens=10):
    """Formata várias movimentações do mesmo cavalo para exibição no painel."""
    if not registros:
        return '', ''
    recentes = registros[-max_itens:]
    partes = []
    for reg in recentes:
        data = str(reg.get('data_movimentacao') or '').strip()
        texto = str(reg.get('texto_resumo') or '').strip()
        if data and texto:
            partes.append(f'{data} — {texto}')
        elif texto:
            partes.append(texto)
    return ' | '.join(partes), str(recentes[-1].get('data_movimentacao') or '').strip()


def _veiculo_corresponde_filtro_placa(veiculo, placa_filtro):
    if not placa_filtro:
        return True
    filtro = normalizar_placa_frota(placa_filtro)
    if not filtro:
        return True
    for coluna in ('placa',) + _COLUNAS_CARRETA_FROTA:
        placa = normalizar_placa_frota(veiculo.get(coluna))
        if placa and filtro in placa:
            return True
    return False


def obter_frota_erp(limite=100, placa_filtro=''):
    """Lê os veículos da frota para preencher a tela de Veículos Ativos."""
    try:
        conn = conectar_banco()
        conn.row_factory = True
        cursor = conn.cursor()
        _garantir_colunas_frota_erp(cursor)
        sql = (
            "SELECT codVeiculo, cavalo, placa, carreta1, carreta2, carreta3, "
            "veiculoProprio, ultima_atualizacao, movimentacao_carreta, data_movimentacao "
            "FROM frota_erp ORDER BY codVeiculo ASC"
        )
        cursor.execute(sql)
        veiculos = [dict(row) for row in cursor.fetchall()]
        historico_por_cavalo = _carregar_historico_movimentacoes_agrupado(cursor)
        conn.close()

        placa_filtro = str(placa_filtro or '').strip()
        if placa_filtro:
            veiculos = [
                v for v in veiculos
                if _veiculo_corresponde_filtro_placa(v, placa_filtro)
            ]

        if limite not in (None, "", "Todos"):
            try:
                veiculos = veiculos[:max(1, int(limite))]
            except (TypeError, ValueError):
                pass

        placas_dup = detectar_placas_carreta_duplicadas()
        for veiculo in veiculos:
            cod = veiculo.get('codVeiculo')
            try:
                cod_int = int(cod)
            except (TypeError, ValueError):
                cod_int = None
            if cod_int is not None:
                regs = historico_por_cavalo.get(cod_int, [])
                if regs:
                    texto, data = _formatar_historico_movimentacoes_cavalo(regs)
                    veiculo['movimentacao_carreta'] = texto
                    veiculo['data_movimentacao'] = data
            veiculo['carreta_duplicada'] = linha_frota_tem_carreta_duplicada(
                veiculo, placas_dup,
            )
        return veiculos
    except Exception as e:
        print(f"Erro ao ler frota do banco ({caminho_banco()}): {e}")
        return []


def obter_itens_erp():
    """Lê os itens do banco para preencher a tela"""
    criar_tabela_itens()
    try:
        conn = conectar_banco()
        conn.row_factory = True
        c = conn.cursor()
        c.execute("SELECT codItemD, descGrupoImp, descNegocioImp, descricao, ultima_atualizacao FROM itens_erp ORDER BY CAST(codItemD AS INTEGER) ASC")
        res = c.fetchall()
        conn.close()
        return [dict(row) for row in res]
    except Exception as e:
        print(f"Erro ao ler itens: {e}")
        return []

# =======================================================
# TABELA EXCLUSIVA PARA RELATÓRIOS (BLINDADA CONTRA DELETES)
# =======================================================
def criar_tabela_relatorios():
    conn = conectar_banco()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS config_relatorios (
            id INTEGER PRIMARY KEY,
            rel_veiculo TEXT,
            rel_item TEXT,
            cod_grupo_item TEXT
        )
    ''')
    try:
        c.execute("ALTER TABLE config_relatorios ADD COLUMN IF NOT EXISTS cod_grupo_item TEXT")
    except (OperationalError, DatabaseError):
        conn.rollback()
    conn.commit()
    conn.close()

def salvar_codigos_relatorios(rel_veic, rel_item, cod_grupo_item=""):
    criar_tabela_relatorios()
    conn = conectar_banco()
    c = conn.cursor()
    c.execute("DELETE FROM config_relatorios")
    c.execute(
        "INSERT INTO config_relatorios (id, rel_veiculo, rel_item, cod_grupo_item) VALUES (1, ?, ?, ?)",
        (rel_veic, rel_item, str(cod_grupo_item).strip()),
    )
    conn.commit()
    conn.close()

def carregar_codigos_relatorios():
    criar_tabela_relatorios()
    conn = conectar_banco()
    conn.row_factory = True
    c = conn.cursor()
    c.execute("SELECT * FROM config_relatorios WHERE id=1")
    row = c.fetchone()
    conn.close()
    if row:
        dados = dict(row)
        dados['rel_veiculo'] = str(dados.get('rel_veiculo') or '').strip()
        dados['rel_item'] = str(dados.get('rel_item') or '').strip()
        dados['cod_grupo_item'] = str(dados.get('cod_grupo_item') or '').strip()
        return dados
    return {'rel_veiculo': '', 'rel_item': '', 'cod_grupo_item': ''}


def carregar_codigo_grupo_item_padrao():
    """Código ERP do grupo INDEFINIDO no cadastro de item (configurado em Filtros)."""
    cfg = carregar_codigos_relatorios()
    return str(cfg.get('cod_grupo_item') or '').strip()


def normalizar_placa_painel(texto):
    """Placa no painel: somente letras e números (sem ponto, espaço ou traço)."""
    return re.sub(r'[^A-Za-z0-9]', '', str(texto or '')).upper()


def normalizar_km_painel(texto):
    """KM no painel: somente dígitos."""
    return re.sub(r'\D', '', str(texto or ''))


def validar_placa_painel(texto):
    placa = normalizar_placa_painel(texto)
    if not placa:
        return True, ''
    if len(placa) < 7 or len(placa) > 8:
        return False, (
            'Placa inválida: use 7 ou 8 caracteres, apenas letras e números '
            '(sem ponto, espaço ou traço).'
        )
    return True, placa


def validar_km_painel(texto):
    km = normalizar_km_painel(texto)
    if not km:
        return True, ''
    return True, km


def obter_painel_placa_km(chave_nfe='', num_nota=''):
    chave = str(chave_nfe or '').strip()
    num = str(num_nota or '').strip()
    if not chave and not num:
        return {'painel_placa': '', 'painel_km': ''}
    try:
        conn = conectar_banco()
        conn.row_factory = True
        c = conn.cursor()
        if chave:
            c.execute(
                'SELECT painel_placa, painel_km FROM notas_raspadas WHERE chave_nfe = ?',
                (chave,),
            )
        else:
            c.execute(
                '''SELECT painel_placa, painel_km FROM notas_raspadas
                   WHERE num_nota = ? ORDER BY id DESC LIMIT 1''',
                (num,),
            )
        row = c.fetchone()
        conn.close()
        if not row:
            return {'painel_placa': '', 'painel_km': ''}
        return {
            'painel_placa': normalizar_placa_painel(row['painel_placa']),
            'painel_km': normalizar_km_painel(row['painel_km']),
        }
    except Exception as e:
        print(f'Erro ao ler placa/KM do painel: {e}')
        return {'painel_placa': '', 'painel_km': ''}


def atualizar_painel_placa_km(chave_nfe='', num_nota='', placa=None, km=None):
    chave = str(chave_nfe or '').strip()
    num = str(num_nota or '').strip()
    if not chave and not num:
        return False, 'Chave ou número da nota não informado.'

    placa_norm = normalizar_placa_painel(placa) if placa is not None else None
    km_norm = normalizar_km_painel(km) if km is not None else None

    if placa_norm is not None and placa_norm:
        ok, msg = validar_placa_painel(placa_norm)
        if not ok:
            return False, msg
    if km_norm is not None and km_norm:
        ok, msg = validar_km_painel(km_norm)
        if not ok:
            return False, msg

    try:
        conn = conectar_banco()
        c = conn.cursor()
        if chave:
            filtro = 'chave_nfe = ?'
            filtro_val = chave
        else:
            filtro = (
                'id = (SELECT id FROM notas_raspadas WHERE num_nota = ? '
                'ORDER BY id DESC LIMIT 1)'
            )
            filtro_val = num

        if placa_norm is not None and km_norm is not None:
            c.execute(
                f'UPDATE notas_raspadas SET painel_placa = ?, painel_km = ? WHERE {filtro}',
                (placa_norm, km_norm, filtro_val),
            )
        elif placa_norm is not None:
            c.execute(
                f'UPDATE notas_raspadas SET painel_placa = ? WHERE {filtro}',
                (placa_norm, filtro_val),
            )
        elif km_norm is not None:
            c.execute(
                f'UPDATE notas_raspadas SET painel_km = ? WHERE {filtro}',
                (km_norm, filtro_val),
            )
        else:
            conn.close()
            return False, 'Nenhum valor para atualizar.'

        alterou = c.rowcount > 0
        conn.commit()
        conn.close()
        if alterou:
            _notificar_painel_notas_alterado()
        return alterou, ''
    except Exception as e:
        return False, str(e)


def nota_esta_arquivada(nota):
    return '☑' in str((nota or {}).get('nfe_arquiva') or '')


def status_exibicao_painel(nota):
    if nota_esta_arquivada(nota):
        return 'Arquivada'
    return str((nota or {}).get('status') or '').strip()


def _campo_data_eh_emissao(campo_data):
    texto = str(campo_data or '').strip().lower()
    return texto in ('emissao', 'data_em', 'data emissão nfe', 'data emissao nfe')


def _data_nota_para_filtro_painel(nota, campo_data):
    from datetime import datetime

    if _campo_data_eh_emissao(campo_data):
        data_str = str(nota.get('data_em') or '').strip()[:10]
        if not data_str:
            return None
        try:
            if '/' in data_str:
                return datetime.strptime(data_str, '%d/%m/%Y').date()
            if '-' in data_str:
                return datetime.strptime(data_str, '%Y-%m-%d').date()
        except ValueError:
            return None
        return None

    dt = _parse_data_insercao(nota.get('data_insercao'))
    return dt.date() if dt else None


def obter_fornecedores_unicos_notas():
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT DISTINCT TRIM(fornecedor) AS fornecedor
               FROM notas_raspadas
               WHERE TRIM(COALESCE(fornecedor, '')) != ''
               ORDER BY fornecedor ASC''',
        )
        valores = [str(row[0]).strip() for row in cursor.fetchall() if row and row[0]]
        conn.close()
        return ['Todos'] + valores
    except Exception as e:
        print(f'Erro ao listar fornecedores: {e}')
        return ['Todos']


def buscar_fornecedores_por_nome(texto):
    busca = str(texto or '').strip().lower()
    if not busca or busca == 'todos':
        return 'Todos'
    candidatos = obter_fornecedores_unicos_notas()
    exatos = [f for f in candidatos if f.lower() == busca]
    if exatos:
        return exatos[0]
    comeca = [f for f in candidatos if f.lower().startswith(busca)]
    if comeca:
        return comeca[0]
    contem = [f for f in candidatos if busca in f.lower()]
    return contem[0] if contem else texto.strip()


def listar_notas_filtradas(
    dt_ini='',
    dt_fim='',
    cod='',
    status='Todos',
    nota='',
    fornecedor='Todos',
    limite=None,
    campo_data='insercao',
):
    """Filtra notas do painel. Por padrão usa data de inserção (igual ao desktop)."""
    from datetime import datetime

    try:
        conn = conectar_banco()
        conn.row_factory = True
        c = conn.cursor()
        c.execute('SELECT * FROM notas_raspadas ORDER BY id DESC')
        todas_notas = [dict(row) for row in c.fetchall()]
        conn.close()

        tem_filtro_data = bool(dt_ini or dt_fim)
        fornecedor_filtro = str(fornecedor or 'Todos').strip()
        tem_outros_filtros = (
            bool(cod or nota)
            or status not in ('', 'Todos')
            or fornecedor_filtro not in ('', 'Todos')
        )

        if not tem_filtro_data and not tem_outros_filtros and status in ('', 'Todos'):
            resultado = [r for r in todas_notas if not nota_esta_arquivada(r)]
            if limite in (None, '', 'Todos'):
                return resultado
            try:
                return resultado[:max(1, int(limite))]
            except Exception:
                return resultado

        try:
            d_ini = (
                datetime.strptime(dt_ini, '%d/%m/%Y').date()
                if len(dt_ini) == 10 else datetime.min.date()
            )
        except ValueError:
            d_ini = datetime.min.date()

        try:
            d_fim = (
                datetime.strptime(dt_fim, '%d/%m/%Y').date()
                if len(dt_fim) == 10 else datetime.max.date()
            )
        except ValueError:
            d_fim = datetime.max.date()

        notas_filtradas = []
        status_filtro = str(status or 'Todos').strip()

        for r in todas_notas:
            arquivada = nota_esta_arquivada(r)

            if status_filtro == 'Arquivada':
                if not arquivada:
                    continue
            elif arquivada:
                continue

            val_cod = str(r.get('codigo_interno') or r.get('cod_interno') or '')
            if cod and cod.lower() not in val_cod.lower():
                continue

            val_nota = str(r.get('num_nota') or r.get('nota') or '')
            if nota and nota.lower() not in val_nota.lower():
                continue

            val_forn = str(r.get('fornecedor') or '')
            if (
                fornecedor_filtro
                and fornecedor_filtro not in ('', 'Todos')
                and fornecedor_filtro.lower() not in val_forn.lower()
            ):
                continue

            if status_filtro not in ('', 'Todos', 'Arquivada'):
                val_status = str(r.get('status') or '')
                if status_filtro.upper() not in val_status.upper():
                    continue

            if tem_filtro_data or d_ini != datetime.min.date() or d_fim != datetime.max.date():
                data_nota = _data_nota_para_filtro_painel(r, campo_data)
                if not data_nota:
                    continue
                if not (d_ini <= data_nota <= d_fim):
                    continue

            notas_filtradas.append(r)

        if limite in (None, '', 'Todos'):
            return notas_filtradas
        try:
            return notas_filtradas[:max(1, int(limite))]
        except Exception:
            return notas_filtradas

    except Exception as e:
        print(f'Erro ao buscar/filtrar notas: {e}')
        return []


def _parse_data_insercao(valor):
    from datetime import datetime

    texto = str(valor or '').strip()
    if not texto:
        return None
    candidatos = (
        ('%Y-%m-%d %H:%M:%S', texto[:19]),
        ('%d/%m/%Y %H:%M:%S', texto[:19]),
        ('%Y-%m-%d', texto[:10]),
        ('%d/%m/%Y', texto[:10]),
    )
    for formato, trecho in candidatos:
        try:
            return datetime.strptime(trecho, formato)
        except ValueError:
            continue
    return None


def listar_notas_por_data_insercao(dt_ini="", dt_fim=""):
    """Notas registradas no painel entre 00:00 da data inicial e 23:59 da final."""
    import log_service

    inicio, fim = log_service.periodo_suporte(dt_ini, dt_fim)
    conn = conectar_banco()
    conn.row_factory = True
    c = conn.cursor()
    c.execute("SELECT * FROM notas_raspadas ORDER BY data_insercao DESC, id DESC")
    todas = [dict(row) for row in c.fetchall()]
    conn.close()

    filtradas = []
    for nota in todas:
        dt_registro = _parse_data_insercao(nota.get('data_insercao'))
        if not dt_registro:
            continue
        if inicio <= dt_registro <= fim:
            filtradas.append(nota)
    return filtradas, inicio, fim


# ==========================================
# TARIFAS BANCÁRIAS (paridade desktop)
# ==========================================
_callback_painel_tarifas = None


def registrar_callback_painel_tarifas(callback):
    global _callback_painel_tarifas
    _callback_painel_tarifas = callback


def _notificar_painel_tarifas_alterado():
    cb = _callback_painel_tarifas
    if not cb:
        return
    try:
        cb()
    except Exception as e:
        print(f'Aviso: falha ao atualizar painel de tarifas: {e}')


def _normalizar_cnpj_tarifa(texto):
    return re.sub(r'[^0-9]', '', str(texto or ''))


def _parse_data_movimento_tarifa(valor):
    texto = str(valor or '').strip()
    if not texto:
        return None
    candidatos = (
        ('%d/%m/%Y', texto[:10]),
        ('%Y-%m-%d', texto[:10]),
        ('%d/%m/%Y %H:%M:%S', texto[:19]),
        ('%Y-%m-%d %H:%M:%S', texto[:19]),
    )
    for formato, trecho in candidatos:
        try:
            return datetime.strptime(trecho, formato)
        except ValueError:
            continue
    return None


def salvar_tarifa_bancaria(dados_tarifa):
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO tarifas_bancarias (
                cnpj, razao_social, agencia, conta, data_movimento, descricao, valor,
                status, codigo_interno, erro_processamento, data_insercao
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(dados_tarifa.get('cnpj') or '').strip(),
            str(dados_tarifa.get('razao_social') or '').strip(),
            str(dados_tarifa.get('agencia') or '').strip(),
            str(dados_tarifa.get('conta') or '').strip(),
            str(dados_tarifa.get('data_movimento') or '').strip(),
            str(dados_tarifa.get('descricao') or '').strip(),
            str(dados_tarifa.get('valor') or '').strip(),
            str(dados_tarifa.get('status') or 'Pendente').strip(),
            str(dados_tarifa.get('codigo_interno') or '').strip(),
            str(dados_tarifa.get('erro_processamento') or '').strip(),
            dados_tarifa.get('data_insercao') or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        ))
        conn.commit()
        conn.close()
        _notificar_painel_tarifas_alterado()
        return True
    except Exception as e:
        print(f'Erro ao salvar tarifa bancária: {e}')
        return False


def listar_tarifas_bancarias(cnpj_filtro='', data_ini='', data_fim='', status='Todos'):
    try:
        conn = conectar_banco()
        conn.row_factory = True
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, cnpj, razao_social, agencia, conta, data_movimento, descricao,
                   valor, status, codigo_interno, erro_processamento, data_insercao,
                   data_processamento
            FROM tarifas_bancarias
            ORDER BY cnpj ASC, agencia ASC, conta ASC, data_movimento DESC, id DESC
        ''')
        tarifas = [dict(row) for row in cursor.fetchall()]
        conn.close()

        cnpj_filtro_norm = _normalizar_cnpj_tarifa(cnpj_filtro)
        if cnpj_filtro_norm:
            tarifas = [
                t for t in tarifas
                if cnpj_filtro_norm in _normalizar_cnpj_tarifa(t.get('cnpj'))
            ]

        status_norm = str(status or 'Todos').strip().upper()
        if status_norm and status_norm != 'TODOS':
            tarifas = [
                t for t in tarifas
                if str(t.get('status') or '').strip().upper() == status_norm
            ]

        if data_ini or data_fim:
            inicio = _parse_data_movimento_tarifa(data_ini) if data_ini else None
            fim = _parse_data_movimento_tarifa(data_fim) if data_fim else None
            if fim:
                fim = fim.replace(hour=23, minute=59, second=59)
            filtradas = []
            for tarifa in tarifas:
                dt_mov = _parse_data_movimento_tarifa(tarifa.get('data_movimento'))
                if not dt_mov:
                    continue
                if inicio and dt_mov < inicio:
                    continue
                if fim and dt_mov > fim:
                    continue
                filtradas.append(tarifa)
            tarifas = filtradas

        return tarifas
    except Exception as e:
        print(f'Erro ao listar tarifas bancárias: {e}')
        return []


def _formatar_cnpj_exibicao(cnpj):
    digits = _normalizar_cnpj_tarifa(cnpj)
    if len(digits) != 14:
        return str(cnpj or '').strip()
    return f'{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:14]}'


def sincronizar_mapa_contas_sicredi(lista_contas):
    if not lista_contas:
        return 0
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        total = 0
        for item in lista_contas:
            agencia = str(item.get('agencia') or '').strip()
            conta = str(item.get('conta') or '').strip()
            if not agencia or not conta:
                continue
            cnpj = _formatar_cnpj_exibicao(item.get('cnpj') or '')
            razao = str(item.get('razao_social') or '').strip().upper()
            cod_filial = str(item.get('cod_filial') or '').strip()
            cod_conta_erp = str(item.get('cod_conta_erp') or '').strip()
            cursor.execute(
                '''INSERT INTO sicredi_mapa_contas (
                       cnpj, razao_social, agencia, conta, ultima_atualizacao,
                       cod_filial, cod_conta_erp
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(agencia, conta) DO UPDATE SET
                       cnpj = excluded.cnpj,
                       razao_social = CASE
                           WHEN excluded.razao_social != '' THEN excluded.razao_social
                           ELSE sicredi_mapa_contas.razao_social
                       END,
                       ultima_atualizacao = excluded.ultima_atualizacao,
                       cod_filial = CASE
                           WHEN excluded.cod_filial != '' THEN excluded.cod_filial
                           ELSE sicredi_mapa_contas.cod_filial
                       END,
                       cod_conta_erp = CASE
                           WHEN excluded.cod_conta_erp != '' THEN excluded.cod_conta_erp
                           ELSE sicredi_mapa_contas.cod_conta_erp
                       END''',
                (cnpj, razao, agencia, conta, agora, cod_filial, cod_conta_erp),
            )
            if cod_filial:
                _salvar_cnpj_filial(cnpj, cod_filial)
            total += 1
        conn.commit()
        conn.close()
        return total
    except Exception as e:
        print(f'Erro ao sincronizar mapa Sicredi: {e}')
        return 0


def obter_mapa_contas_sicredi():
    try:
        conn = conectar_banco()
        conn.row_factory = True
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT cnpj, razao_social, agencia, conta, ultima_atualizacao,
                      data_arquivo_xls, cod_filial, cod_conta_erp
               FROM sicredi_mapa_contas
               ORDER BY cnpj ASC, agencia ASC, conta ASC''',
        )
        dados = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return dados
    except Exception as e:
        print(f'Erro ao ler mapa Sicredi: {e}')
        return []


def obter_mapa_contas_sicredi_dict():
    mapa = {}
    for item in obter_mapa_contas_sicredi():
        chave = f"{item.get('agencia', '')}|{item.get('conta', '')}"
        mapa[chave] = item
    return mapa


def _formatar_mtime_arquivo(caminho):
    try:
        mtime = os.path.getmtime(caminho)
        return datetime.fromtimestamp(mtime).strftime('%d/%m/%Y %H:%M:%S')
    except OSError:
        return ''


def obter_data_arquivo_conta_pasta(agencia, conta):
    pasta = obter_pasta_tarifas_bancarias()
    if not pasta:
        return ''
    agencia = str(agencia or '').strip()
    conta = str(conta or '').strip().replace('_', '-')
    for ext in ('.xls', '.xlsx'):
        caminho = os.path.join(pasta, f'{agencia}_{conta}{ext}')
        if os.path.isfile(caminho):
            return _formatar_mtime_arquivo(caminho)
    return ''


def registrar_data_arquivo_conta(agencia, conta, caminho_arquivo=None, data_hora=None):
    agencia = str(agencia or '').strip()
    conta = str(conta or '').strip()
    if not agencia or not conta:
        return False
    if not data_hora and caminho_arquivo:
        data_hora = _formatar_mtime_arquivo(caminho_arquivo)
    data_hora = str(data_hora or '').strip()
    if not data_hora:
        return False
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute(
            '''UPDATE sicredi_mapa_contas SET data_arquivo_xls = ?
               WHERE agencia = ? AND conta = ?''',
            (data_hora, agencia, conta),
        )
        if cursor.rowcount == 0:
            cursor.execute(
                '''INSERT INTO sicredi_mapa_contas (
                       cnpj, razao_social, agencia, conta, data_arquivo_xls, ultima_atualizacao
                   ) VALUES ('', '', ?, ?, ?, ?)''',
                (agencia, conta, data_hora, datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f'Erro ao registrar data do arquivo da conta: {e}')
        return False


def obter_tarifas_agrupadas_por_cnpj_conta(cnpj_filtro='', data_ini='', data_fim='', status='Todos'):
    tarifas = listar_tarifas_bancarias(
        cnpj_filtro=cnpj_filtro, data_ini=data_ini, data_fim=data_fim, status=status,
    )
    mapa_contas = obter_mapa_contas_sicredi()
    grupos = {}
    cnpj_filtro_norm = _normalizar_cnpj_tarifa(cnpj_filtro)

    for item in mapa_contas:
        cnpj = str(item.get('cnpj') or '').strip() or '—'
        if cnpj_filtro_norm and cnpj_filtro_norm not in _normalizar_cnpj_tarifa(cnpj):
            continue
        razao = str(item.get('razao_social') or '').strip()
        agencia = str(item.get('agencia') or '').strip()
        conta = str(item.get('conta') or '').strip()
        chave_conta = f'{agencia}|{conta}'
        grupo = grupos.setdefault(cnpj, {'cnpj': cnpj, 'razao_social': razao, 'contas': {}})
        if razao and not grupo['razao_social']:
            grupo['razao_social'] = razao
        data_arquivo = str(item.get('data_arquivo_xls') or '').strip()
        if not data_arquivo:
            data_arquivo = obter_data_arquivo_conta_pasta(agencia, conta)
        grupo['contas'].setdefault(chave_conta, {
            'agencia': agencia, 'conta': conta,
            'data_arquivo_xls': data_arquivo, 'tarifas': [],
        })

    for tarifa in tarifas:
        cnpj = str(tarifa.get('cnpj') or '').strip() or '—'
        razao = str(tarifa.get('razao_social') or '').strip()
        agencia = str(tarifa.get('agencia') or '').strip()
        conta = str(tarifa.get('conta') or '').strip()
        chave_conta = f'{agencia}|{conta}'
        grupo = grupos.setdefault(cnpj, {'cnpj': cnpj, 'razao_social': razao, 'contas': {}})
        if razao and not grupo['razao_social']:
            grupo['razao_social'] = razao
        conta_item = grupo['contas'].setdefault(chave_conta, {
            'agencia': agencia, 'conta': conta,
            'data_arquivo_xls': '', 'tarifas': [],
        })
        if not conta_item.get('data_arquivo_xls'):
            conta_item['data_arquivo_xls'] = obter_data_arquivo_conta_pasta(agencia, conta)
        conta_item['tarifas'].append(tarifa)

    resultado = []
    for cnpj in sorted(grupos.keys()):
        grupo = grupos[cnpj]
        contas = [grupo['contas'][chave] for chave in sorted(grupo['contas'].keys())]
        resultado.append({
            'cnpj': grupo['cnpj'],
            'razao_social': grupo['razao_social'],
            'contas': contas,
        })
    return resultado


def obter_ultima_atualizacao_tarifas():
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(data_insercao) FROM tarifas_bancarias')
        row = cursor.fetchone()
        conn.close()
        return str(row[0]).strip() if row and row[0] else ''
    except Exception:
        return ''


def contar_tarifas_bancarias():
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM tarifas_bancarias')
        total = cursor.fetchone()[0]
        conn.close()
        return int(total or 0)
    except Exception:
        return 0


_CHAVE_PASTA_TARIFAS = 'pasta_tarifas_bancarias'
_CHAVE_ULTIMA_IMPORT_TARIFAS = 'ultima_importacao_tarifas'
_CHAVE_COD_FORNECEDOR_SICREDI = 'cod_fornecedor_sicredi'
_CHAVE_COD_GRUPO_ITEM_TARIFA = 'cod_grupo_item_tarifa'
_CHAVE_NOME_ITEM_TARIFA = 'nome_item_tarifa_padrao'


def _salvar_config_sistema(chave, valor):
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        _garantir_tabela_config_sistema(cursor)
        cursor.execute(
            '''INSERT INTO config_sistema (chave, valor)
               VALUES (?, ?)
               ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor''',
            (chave, str(valor or '').strip()),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f'Erro ao salvar config {chave}: {e}')
        return False


def _obter_config_sistema(chave, padrao=''):
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        _garantir_tabela_config_sistema(cursor)
        cursor.execute('SELECT valor FROM config_sistema WHERE chave = ?', (chave,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0] is not None:
            return str(row[0]).strip()
    except Exception:
        pass
    return str(padrao or '').strip()


def salvar_pasta_tarifas_bancarias(pasta):
    return _salvar_config_sistema(_CHAVE_PASTA_TARIFAS, os.path.abspath(str(pasta or '').strip()))


def obter_pasta_tarifas_bancarias():
    return _obter_config_sistema(_CHAVE_PASTA_TARIFAS, '')


def registrar_ultima_importacao_tarifas(data_hora=None):
    if data_hora is None:
        data_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    return _salvar_config_sistema(_CHAVE_ULTIMA_IMPORT_TARIFAS, str(data_hora))


def obter_ultima_importacao_tarifas():
    return _obter_config_sistema(_CHAVE_ULTIMA_IMPORT_TARIFAS, '')


def _tarifa_bancaria_ja_existe(cursor, dados):
    cursor.execute(
        '''SELECT id FROM tarifas_bancarias
           WHERE cnpj = ? AND agencia = ? AND conta = ?
             AND data_movimento = ? AND descricao = ? AND valor = ?
           LIMIT 1''',
        (
            str(dados.get('cnpj') or '').strip(),
            str(dados.get('agencia') or '').strip(),
            str(dados.get('conta') or '').strip(),
            str(dados.get('data_movimento') or '').strip(),
            str(dados.get('descricao') or '').strip(),
            str(dados.get('valor') or '').strip(),
        ),
    )
    return cursor.fetchone() is not None


def importar_tarifas_bancarias_lote(lista_tarifas, notificar=True):
    novas = 0
    duplicadas = 0
    if not lista_tarifas:
        return novas, duplicadas
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for dados in lista_tarifas:
            if _tarifa_bancaria_ja_existe(cursor, dados):
                duplicadas += 1
                continue
            cursor.execute('''
                INSERT INTO tarifas_bancarias (
                    cnpj, razao_social, agencia, conta, data_movimento, descricao, valor,
                    status, codigo_interno, erro_processamento, data_insercao
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                str(dados.get('cnpj') or '').strip(),
                str(dados.get('razao_social') or '').strip(),
                str(dados.get('agencia') or '').strip(),
                str(dados.get('conta') or '').strip(),
                str(dados.get('data_movimento') or '').strip(),
                str(dados.get('descricao') or '').strip(),
                str(dados.get('valor') or '').strip(),
                str(dados.get('status') or 'Pendente').strip(),
                str(dados.get('codigo_interno') or '').strip(),
                str(dados.get('erro_processamento') or '').strip(),
                dados.get('data_insercao') or agora,
            ))
            novas += 1
        conn.commit()
        conn.close()
        if novas and notificar:
            _notificar_painel_tarifas_alterado()
        return novas, duplicadas
    except Exception as e:
        print(f'Erro ao importar tarifas em lote: {e}')
        return novas, duplicadas


def salvar_config_tarifa_erp(cod_fornecedor_sicredi=None, cod_grupo_item_tarifa=None, nome_item_tarifa=None):
    ok = True
    for chave, valor in {
        _CHAVE_COD_FORNECEDOR_SICREDI: cod_fornecedor_sicredi,
        _CHAVE_COD_GRUPO_ITEM_TARIFA: cod_grupo_item_tarifa,
        _CHAVE_NOME_ITEM_TARIFA: nome_item_tarifa,
    }.items():
        if valor is not None:
            ok = _salvar_config_sistema(chave, valor) and ok
    return ok


def obter_config_tarifa_erp():
    return {
        'cod_fornecedor_sicredi': _obter_config_sistema(_CHAVE_COD_FORNECEDOR_SICREDI, '640'),
        'cod_grupo_item_tarifa': _obter_config_sistema(_CHAVE_COD_GRUPO_ITEM_TARIFA, '44'),
        'nome_item_tarifa_padrao': _obter_config_sistema(_CHAVE_NOME_ITEM_TARIFA, ''),
    }


def atualizar_status_tarifa_bancaria(tarifa_id, status, codigo_interno='', erro_processamento=''):
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            '''UPDATE tarifas_bancarias
               SET status = ?, codigo_interno = ?, erro_processamento = ?,
                   data_processamento = ?
               WHERE id = ?''',
            (
                str(status or '').strip(),
                str(codigo_interno or '').strip(),
                str(erro_processamento or '').strip(),
                agora,
                int(tarifa_id),
            ),
        )
        conn.commit()
        conn.close()
        _notificar_painel_tarifas_alterado()
        return cursor.rowcount > 0
    except Exception as e:
        print(f'Erro ao atualizar tarifa {tarifa_id}: {e}')
        return False


def _salvar_cnpj_filial(cnpj, cod_filial):
    digits = _normalizar_cnpj_tarifa(cnpj)
    cod = str(cod_filial or '').strip()
    if len(digits) != 14 or not cod:
        return
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            '''INSERT INTO sicredi_cnpj_filial (cnpj_digits, cod_filial, atualizado_em)
               VALUES (?, ?, ?)
               ON CONFLICT(cnpj_digits) DO UPDATE SET
                   cod_filial = excluded.cod_filial,
                   atualizado_em = excluded.atualizado_em''',
            (digits, cod, agora),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'Erro ao salvar filial do CNPJ {cnpj}: {e}')


def obter_cod_filial_por_cnpj(cnpj):
    digits = _normalizar_cnpj_tarifa(cnpj)
    if len(digits) != 14:
        return ''
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT cod_filial FROM sicredi_cnpj_filial WHERE cnpj_digits = ?',
            (digits,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            conn.close()
            return str(row[0]).strip()
        cursor.execute(
            '''SELECT cod_filial FROM sicredi_mapa_contas
               WHERE REPLACE(REPLACE(REPLACE(cnpj, '.', ''), '/', ''), '-', '') = ?
                 AND TRIM(cod_filial) != ''
               LIMIT 1''',
            (digits,),
        )
        row = cursor.fetchone()
        conn.close()
        return str(row[0]).strip() if row and row[0] else ''
    except Exception:
        return ''


def obter_cod_conta_erp_por_conta(agencia, conta):
    agencia = str(agencia or '').strip()
    conta = str(conta or '').strip().replace('_', '-')
    if not agencia or not conta:
        return ''
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT cod_conta_erp FROM sicredi_mapa_contas
               WHERE agencia = ? AND conta = ? AND TRIM(cod_conta_erp) != ''
               LIMIT 1''',
            (agencia, conta),
        )
        row = cursor.fetchone()
        conn.close()
        return str(row[0]).strip() if row and row[0] else ''
    except Exception:
        return ''