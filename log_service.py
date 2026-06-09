from datetime import datetime
import re
import threading
import uuid

import database_setup as db
from database_connection import conectar_banco, executar_schema


def _db_path():
    return db.caminho_banco()


_LISTENERS = []
_LOCK = threading.Lock()


def _agora_texto():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def garantir_tabelas():
    executar_schema()


def adicionar_listener(callback):
    with _LOCK:
        if callback not in _LISTENERS:
            _LISTENERS.append(callback)


def remover_listener(callback):
    with _LOCK:
        if callback in _LISTENERS:
            _LISTENERS.remove(callback)


def _notificar(evento):
    with _LOCK:
        listeners = list(_LISTENERS)
    for callback in listeners:
        try:
            callback(evento)
        except Exception:
            pass


def iniciar_sessao(origem="ROBO", descricao=""):
    garantir_tabelas()
    sessao_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO logs_sessoes (sessao_id, origem, descricao, iniciada_em)
        VALUES (?, ?, ?, ?)
        """,
        (sessao_id, str(origem or "").strip(), str(descricao or "").strip(), _agora_texto()),
    )
    conn.commit()
    conn.close()
    registrar_log(
        f"===== INICIO DA EXECUCAO: {descricao or origem} =====",
        origem=origem,
        sessao_id=sessao_id,
    )
    return sessao_id


def finalizar_sessao(sessao_id, origem="ROBO", status="CONCLUIDA"):
    if not sessao_id:
        return
    garantir_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE logs_sessoes
        SET finalizada_em = ?, status = ?
        WHERE sessao_id = ?
        """,
        (_agora_texto(), str(status or "").strip().upper(), sessao_id),
    )
    conn.commit()
    conn.close()
    registrar_log(
        f"===== FIM DA EXECUCAO: {str(status or '').strip().upper()} =====",
        origem=origem,
        sessao_id=sessao_id,
    )


def registrar_log(mensagem, origem="SISTEMA", sessao_id=None, nivel="INFO"):
    garantir_tabelas()
    evento = {
        "sessao_id": sessao_id or "",
        "origem": str(origem or "SISTEMA").strip().upper(),
        "nivel": str(nivel or "INFO").strip().upper(),
        "mensagem": str(mensagem or "").strip(),
        "criado_em": _agora_texto(),
    }
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO logs_execucao (sessao_id, origem, nivel, mensagem, criado_em)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            evento["sessao_id"],
            evento["origem"],
            evento["nivel"],
            evento["mensagem"],
            evento["criado_em"],
        ),
    )
    conn.commit()
    conn.close()
    _notificar(evento)
    return evento


def listar_logs(limite=1000):
    garantir_tabelas()
    conn = conectar_banco()
    conn.row_factory = True
    cursor = conn.cursor()
    if limite in (None, "", "Todos"):
        cursor.execute(
            """
            SELECT sessao_id, origem, nivel, mensagem, criado_em
            FROM logs_execucao
            ORDER BY id ASC
            """
        )
    else:
        cursor.execute(
            """
            SELECT sessao_id, origem, nivel, mensagem, criado_em
            FROM (
                SELECT id, sessao_id, origem, nivel, mensagem, criado_em
                FROM logs_execucao
                ORDER BY id DESC
                LIMIT ?
            ) sub
            ORDER BY id ASC
            """,
            (max(1, int(limite)),),
        )
    linhas = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return linhas


def parse_filtro_data_hora(texto, fim_do_dia=False):
    valor = str(texto or "").strip()
    if not valor:
        return None

    formatos = (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    )
    for formato in formatos:
        try:
            dt = datetime.strptime(valor, formato)
            if formato in ("%d/%m/%Y", "%Y-%m-%d") and fim_do_dia:
                return dt.replace(hour=23, minute=59, second=59)
            return dt
        except ValueError:
            continue
    raise ValueError(
        "Use data/hora no formato DD/MM/AAAA HH:MM, DD/MM/AAAA HH:MM:SS ou só DD/MM/AAAA."
    )


def _evento_corresponde_nota(evento, numero_nota):
    nota = str(numero_nota or "").strip()
    if not nota:
        return True

    mensagem = str((evento or {}).get("mensagem") or "")
    if not mensagem:
        return False

    padroes = (
        rf"\bnota(?:\s+alvo)?\s*[:=]?\s*{re.escape(nota)}\b",
        rf"\bn[º°o]?\s*nota\s*[:=]?\s*{re.escape(nota)}\b",
    )
    for padrao in padroes:
        if re.search(padrao, mensagem, flags=re.IGNORECASE):
            return True

    return bool(re.search(rf"\b{re.escape(nota)}\b", mensagem))


def filtrar_logs(logs, dt_ini="", dt_fim="", numero_nota=""):
    inicio = parse_filtro_data_hora(dt_ini, fim_do_dia=False) if str(dt_ini or "").strip() else None
    fim = parse_filtro_data_hora(dt_fim, fim_do_dia=True) if str(dt_fim or "").strip() else None

    filtrados = []
    for log in logs or []:
        criado_em = str((log or {}).get("criado_em") or "").strip()
        try:
            dt_log = datetime.strptime(criado_em, "%Y-%m-%d %H:%M:%S") if criado_em else None
        except ValueError:
            dt_log = None

        if inicio and dt_log and dt_log < inicio:
            continue
        if fim and dt_log and dt_log > fim:
            continue
        if inicio and not dt_log:
            continue
        if fim and not dt_log:
            continue
        if not _evento_corresponde_nota(log, numero_nota):
            continue
        filtrados.append(log)
    return filtrados


def limpar_logs():
    garantir_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM logs_execucao")
    cursor.execute("DELETE FROM logs_sessoes")
    conn.commit()
    conn.close()


def formatar_evento(evento):
    sessao = str((evento or {}).get("sessao_id") or "").strip()
    prefixo_sessao = f" [{sessao}]" if sessao else ""
    return (
        f"[{(evento or {}).get('criado_em', '')}]"
        f" [{(evento or {}).get('origem', 'SISTEMA')}]"
        f"{prefixo_sessao} {(evento or {}).get('mensagem', '')}"
    )


def periodo_suporte(dt_ini, dt_fim):
    """Converte DD/MM/AAAA em intervalo 00:00:00 até 23:59:59."""
    inicio = parse_filtro_data_hora(dt_ini, fim_do_dia=False)
    fim = parse_filtro_data_hora(dt_fim, fim_do_dia=True)
    if not inicio or not fim:
        raise ValueError("Informe data inicial e final no formato DD/MM/AAAA.")
    if inicio > fim:
        raise ValueError("A data inicial não pode ser maior que a data final.")
    return inicio, fim


def listar_logs_por_periodo(dt_ini="", dt_fim=""):
    """Lista logs do período (00:00 da inicial até 23:59 da final)."""
    inicio, fim = periodo_suporte(dt_ini, dt_fim)
    garantir_tabelas()
    conn = conectar_banco()
    conn.row_factory = True
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT sessao_id, origem, nivel, mensagem, criado_em
        FROM logs_execucao
        WHERE criado_em >= ? AND criado_em <= ?
        ORDER BY id ASC
        """,
        (
            inicio.strftime("%Y-%m-%d %H:%M:%S"),
            fim.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    linhas = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return linhas, inicio, fim
