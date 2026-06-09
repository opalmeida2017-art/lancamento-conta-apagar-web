from datetime import datetime
from pathlib import Path
import html
import tempfile

import database_setup as db
import log_service
from agendamento_email import EMAIL_SUPORTE_LOG, enviar_mensagem_smtp


def _agora_formatado():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def _sufixo_arquivo():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _pasta_temp():
    pasta = Path(tempfile.gettempdir()) / "suporte_logs_robo"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _periodo_texto(inicio, fim):
    return (
        f"{inicio.strftime('%d/%m/%Y %H:%M:%S')} até "
        f"{fim.strftime('%d/%m/%Y %H:%M:%S')}"
    )


def _obter_razao_social():
    inst = db.carregar_instalacao_licenca() or {}
    razao = str(inst.get('razao_social') or '').strip()
    return razao or 'Transportadora nao informada'


def _html_base(titulo, corpo):
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>{html.escape(titulo)}</title>
<style>
@page {{
    size: A4;
    margin: 12mm;
}}
body {{
    font-family: Arial, sans-serif;
    margin: 18px;
    color: #1f2937;
}}
h1 {{
    color: #1f538d;
    margin-bottom: 4px;
}}
.meta {{
    margin-bottom: 16px;
    color: #4b5563;
    line-height: 1.5;
}}
.filtros {{
    background: #f3f4f6;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 18px;
}}
pre.logs {{
    font-family: Consolas, "Courier New", monospace;
    font-size: 11px;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
    background: #0f172a;
    color: #e2e8f0;
    border-radius: 8px;
    padding: 14px;
    border: 1px solid #334155;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
}}
th, td {{
    border: 1px solid #d1d5db;
    padding: 6px;
    font-size: 10px;
    vertical-align: top;
    word-break: break-word;
}}
th {{
    background: #1f538d;
    color: white;
}}
tr:nth-child(even) {{
    background: #f9fafb;
}}
@media print {{
    body {{ margin: 0; }}
    pre.logs {{
        background: white;
        color: black;
        border: 1px solid #999;
    }}
}}
</style>
</head>
<body>
{corpo}
</body>
</html>"""


def _salvar_html_temp(conteudo, nome_arquivo):
    caminho = _pasta_temp() / nome_arquivo
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


def gerar_arquivo_logs_suporte(dt_ini, dt_fim):
    logs, inicio, fim = log_service.listar_logs_por_periodo(dt_ini, dt_fim)
    linhas = [log_service.formatar_evento(item) for item in logs]
    texto_logs = "\n".join(linhas) if linhas else "Nenhum log encontrado no período informado."

    corpo = f"""
<h1>Relatório de Logs — Suporte</h1>
<div class="meta">
    Gerado em {html.escape(_agora_formatado())}<br>
    Período: {html.escape(_periodo_texto(inicio, fim))}<br>
    Total de registros: {len(logs)}
</div>
<div class="filtros">
    <strong>Período solicitado</strong><br>
    De {html.escape(dt_ini)} 00:00:00 até {html.escape(dt_fim)} 23:59:59
</div>
<pre class="logs">{html.escape(texto_logs)}</pre>
"""
    nome = f"logs_{inicio.strftime('%Y%m%d')}_{fim.strftime('%Y%m%d')}_{_sufixo_arquivo()}.html"
    caminho = _salvar_html_temp(_html_base("Logs Suporte", corpo), nome)
    return caminho, len(logs), inicio, fim


def _linha_nota(nota):
    def limpa(valor):
        return "" if valor is None else str(valor)

    return [
        limpa(nota.get('data_insercao')),
        limpa(nota.get('codigo_interno')),
        limpa(nota.get('status')),
        limpa(nota.get('fornecedor')),
        limpa(nota.get('num_nota')),
        db.normalizar_placa_painel(nota.get('painel_placa')),
        db.normalizar_km_painel(nota.get('painel_km')),
        limpa(nota.get('data_em')),
        limpa(nota.get('valor')),
        limpa(nota.get('sit_nfe')),
        limpa(nota.get('chave_nfe')),
        limpa(nota.get('filial')),
        limpa(nota.get('user_ins')),
        limpa(nota.get('erro_importacao')),
        limpa(nota.get('observacao_nfe')),
    ]


def gerar_arquivo_notas_suporte(dt_ini, dt_fim):
    notas, inicio, fim = db.listar_notas_por_data_insercao(dt_ini, dt_fim)
    cabecalhos = [
        "Inserção", "Cód. Interno", "Status", "Fornecedor", "No.Nota",
        "Placa", "KM", "Data Em.", "Valor", "Sit. NFe", "Chave NFe",
        "Filial", "Usuário Inserção", "Erro Importação", "Observação NFe",
    ]
    linhas = [_linha_nota(nota) for nota in notas]
    cabecalho_html = "".join(
        f"<th>{html.escape(col)}</th>" for col in cabecalhos
    )
    linhas_html = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape(str(val))}</td>" for val in linha)
        + "</tr>"
        for linha in linhas
    ) or (
        f'<tr><td colspan="{len(cabecalhos)}">'
        "Nenhuma nota registrada no painel no período informado.</td></tr>"
    )

    corpo = f"""
<h1>Relatório de NFes — Suporte</h1>
<div class="meta">
    Gerado em {html.escape(_agora_formatado())}<br>
    Período (data de registro no painel): {html.escape(_periodo_texto(inicio, fim))}<br>
    Total de notas: {len(notas)}
</div>
<div class="filtros">
    <strong>Filtro aplicado</strong><br>
    Data inicial: {html.escape(dt_ini)} 00:00:00<br>
    Data final: {html.escape(dt_fim)} 23:59:59<br>
    Critério: campo <em>Inserção</em> (registro no painel).
</div>
<table>
    <thead><tr>{cabecalho_html}</tr></thead>
    <tbody>{linhas_html}</tbody>
</table>
"""
    nome = f"notas_{inicio.strftime('%Y%m%d')}_{fim.strftime('%Y%m%d')}_{_sufixo_arquivo()}.html"
    caminho = _salvar_html_temp(_html_base("NFes Suporte", corpo), nome)
    return caminho, len(notas), inicio, fim


def montar_assunto_log_suporte(dt_ini, dt_fim, horario_envio=None):
    razao = _obter_razao_social()
    horario = str(horario_envio or "").strip()
    if horario:
        return f"LOG {razao} {dt_ini} a {dt_fim} {horario}"
    return f"LOG {razao} {dt_ini} a {dt_fim}"


def enviar_log_suporte_por_email(dt_ini, dt_fim, horario_envio=None):
    caminho_logs, qtd_logs, inicio, fim = gerar_arquivo_logs_suporte(dt_ini, dt_fim)
    caminho_notas, qtd_notas, _, _ = gerar_arquivo_notas_suporte(dt_ini, dt_fim)
    razao = _obter_razao_social()
    assunto = montar_assunto_log_suporte(dt_ini, dt_fim, horario_envio=horario_envio)

    corpo = "\n".join([
        "Relatório de logs e NFes gerado pelo sistema de automação.",
        "",
        f"Transportadora: {razao}",
        f"Período: {dt_ini} 00:00:00 até {dt_fim} 23:59:59",
        f"Total de logs: {qtd_logs}",
        f"Total de NFes (registro no painel): {qtd_notas}",
        "",
        "Anexos:",
        f"- {caminho_logs.name}",
        f"- {caminho_notas.name}",
        "",
        "Abra os anexos HTML no navegador e use Imprimir → Salvar como PDF, se necessário.",
    ])

    anexos = [
        {
            "path": caminho_logs,
            "filename": caminho_logs.name,
            "maintype": "text",
            "subtype": "html",
        },
        {
            "path": caminho_notas,
            "filename": caminho_notas.name,
            "maintype": "text",
            "subtype": "html",
        },
    ]

    envio = enviar_mensagem_smtp(
        assunto=assunto,
        corpo_texto=corpo,
        anexos=anexos,
        destinatarios=[EMAIL_SUPORTE_LOG],
    )

    return {
        "assunto": assunto,
        "destinatario": EMAIL_SUPORTE_LOG,
        "remetente": envio.get("remetente", ""),
        "qtd_logs": qtd_logs,
        "qtd_notas": qtd_notas,
        "inicio": inicio,
        "fim": fim,
        "razao_social": razao,
    }


def enviar_relatorios_suporte_anonimo(dt_ini, dt_fim):
    """
    Envia os dois relatórios de suporte (logs + NFes) sem identificação
    da empresa no assunto/corpo.
    """
    caminho_logs, qtd_logs, inicio, fim = gerar_arquivo_logs_suporte(dt_ini, dt_fim)
    caminho_notas, qtd_notas, _, _ = gerar_arquivo_notas_suporte(dt_ini, dt_fim)
    assunto = f"Relatorios suporte {dt_ini} a {dt_fim}"

    corpo = "\n".join([
        "Relatorios automaticos de suporte.",
        "",
        f"Periodo: {dt_ini} 00:00:00 ate {dt_fim} 23:59:59",
        f"Total de logs: {qtd_logs}",
        f"Total de NFes: {qtd_notas}",
    ])

    anexos = [
        {
            "path": caminho_logs,
            "filename": caminho_logs.name,
            "maintype": "text",
            "subtype": "html",
        },
        {
            "path": caminho_notas,
            "filename": caminho_notas.name,
            "maintype": "text",
            "subtype": "html",
        },
    ]

    envio = enviar_mensagem_smtp(
        assunto=assunto,
        corpo_texto=corpo,
        anexos=anexos,
        destinatarios=[EMAIL_SUPORTE_LOG],
    )

    return {
        "assunto": assunto,
        "destinatario": EMAIL_SUPORTE_LOG,
        "remetente": envio.get("remetente", ""),
        "qtd_logs": qtd_logs,
        "qtd_notas": qtd_notas,
        "inicio": inicio,
        "fim": fim,
    }
