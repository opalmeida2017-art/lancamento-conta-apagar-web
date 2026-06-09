import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from playwright.sync_api import sync_playwright

from .erp_lock import ERP_LOCK
from .runtime_config import usar_headless
from .utils import verificar_pagina_erp_ok


TAMANHO_LOTE_XML = 250
MARCADORES_ERRO = (
    "erro",
    "falha",
    "nao informado",
    "não informado",
    "inválido",
    "invalido",
    "recusad",
    "não encontrado",
    "nao encontrado",
)
MARCADORES_SUCESSO = (
    "sucesso",
    "importad",
    "processad",
    "inclu",
    "carregad",
    "enviad",
    "conclu",
)


def _texto_tag(root, nome_tag):
    for elem in root.iter():
        if str(elem.tag).split("}")[-1] != nome_tag:
            continue
        texto = str(elem.text or "").strip()
        if texto:
            return texto
    return ""


def _chave_nfe(root):
    for elem in root.iter():
        if str(elem.tag).split("}")[-1] != "infNFe":
            continue
        chave = str(elem.attrib.get("Id") or "").strip()
        if chave.upper().startswith("NFE"):
            return chave[3:]
        if chave:
            return chave
    return ""


def ler_metadados_xml(caminho_arquivo):
    caminho = Path(caminho_arquivo)
    retorno = {
        "caminho": str(caminho.resolve()),
        "arquivo": caminho.name,
        "numero_nota": "",
        "chave_nfe": "",
        "mensagem": "",
    }
    try:
        root = ET.parse(str(caminho)).getroot()
        retorno["numero_nota"] = _texto_tag(root, "nNF")
        retorno["chave_nfe"] = _chave_nfe(root)
    except Exception as e:
        retorno["mensagem"] = f"Falha ao ler XML: {e}"
    return retorno


def listar_xmls_da_pasta(caminho_pasta):
    pasta = Path(caminho_pasta)
    if not pasta.exists() or not pasta.is_dir():
        return []
    arquivos = []
    for caminho in sorted(pasta.glob("*.xml"), key=lambda item: item.name.lower()):
        if caminho.is_file():
            arquivos.append(ler_metadados_xml(caminho))
    return arquivos


def _primeiro_locator_disponivel(candidatos):
    for locator in candidatos:
        try:
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue
    return None


def _fazer_login(page, config, log):
    from robo_web.utils import fazer_login_erp

    log("🔐 Fazendo login no ERP para importação manual de XML...")
    fazer_login_erp(page, config, log=log)


def _abrir_painel_nfe(page, log):
    log("📂 Abrindo Painel de NFe para importação manual...")
    page.locator("text='Painéis' >> visible=true").first.hover()
    time.sleep(0.8)
    page.locator("text='NFe' >> visible=true").first.hover()
    time.sleep(0.8)
    page.locator(
        "text='Painel de NFe (Notas de Compras/Destinadas)' >> visible=true"
    ).first.click(force=True)
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    verificar_pagina_erp_ok(page, log)


def _localizar_input_xml(page):
    return _primeiro_locator_disponivel(
        (
            page.locator('input.rf-fu-inp[type="file"]'),
            page.locator('input[accept="xml"][type="file"]'),
            page.locator('input[type="file"][multiple]'),
            page.locator('input[type="file"]'),
        )
    )


def _localizar_botao_importar_xml(page):
    return _primeiro_locator_disponivel(
        (
            page.locator('input[id="formCad:importarArqXML"]'),
            page.locator('input[name="formCad:importarArqXML"]'),
            page.locator('input[value="Importar XML Manual"]'),
            page.locator('button:has-text("Importar XML Manual")'),
            page.locator('text="Importar XML Manual"'),
        )
    )


def _abrir_aba_xml_manual(page, log):
    if _localizar_input_xml(page):
        return

    candidatos = (
        page.locator('span.rf-tab-lbl', has_text="XML"),
        page.locator('a', has_text="XML"),
        page.locator('text=/XML/i'),
    )
    for locator in candidatos:
        try:
            total = locator.count()
        except Exception:
            total = 0
        for indice in range(total):
            try:
                alvo = locator.nth(indice)
                if alvo.is_visible(timeout=500):
                    alvo.click(force=True)
                    time.sleep(1.2)
                    if _localizar_input_xml(page):
                        log("📑 Aba de importação XML localizada.")
                        return
            except Exception:
                continue

    if not _localizar_input_xml(page):
        raise RuntimeError("Aba/campo de importação XML manual não encontrado no ERP.")


def _coletar_mensagens(page):
    mensagens = []
    itens = page.locator("li.fontErrorMessages, li.fontInfoMessages, li.fontWarnMessages, li")
    try:
        total = itens.count()
    except Exception:
        total = 0
    for indice in range(total):
        item = itens.nth(indice)
        try:
            if not item.is_visible(timeout=200):
                continue
        except Exception:
            continue
        texto = str(item.text_content() or "").strip()
        if texto and texto not in mensagens:
            mensagens.append(texto)
    return mensagens


def _aguardar_arquivos_carregados(page, arquivos_lote, log):
    input_xml = _localizar_input_xml(page)
    if not input_xml:
        raise RuntimeError("Campo de seleção de XML não encontrado.")

    input_xml.set_input_files(arquivos_lote)
    nomes = [os.path.basename(caminho) for caminho in arquivos_lote]
    primeiro = nomes[0] if nomes else ""
    ultimo = nomes[-1] if nomes else ""
    inicio = time.time()

    while time.time() - inicio < 90:
        verificar_pagina_erp_ok(page, log)
        try:
            texto_lista = ""
            lista = page.locator(".rf-fu-lst").first
            if lista.count() > 0:
                texto_lista = str(lista.text_content(timeout=1000) or "")
            if (not primeiro or primeiro in texto_lista) and (not ultimo or ultimo in texto_lista):
                time.sleep(1.5)
                return
        except Exception:
            pass
        time.sleep(0.8)

    log("⚠️ Lista visual de upload não confirmou todos os XMLs; seguindo com a importação.")


def _aguardar_retorno_importacao(page, baseline, log):
    inicio = time.time()
    texto_estavel = ""
    mudou_em = time.time()

    while time.time() - inicio < 120:
        verificar_pagina_erp_ok(page, log)
        mensagens = _coletar_mensagens(page)
        texto = " | ".join(mensagens)
        if texto and texto != baseline:
            if texto != texto_estavel:
                texto_estavel = texto
                mudou_em = time.time()
            elif time.time() - mudou_em >= 2:
                return mensagens
        time.sleep(1)

    return _coletar_mensagens(page)


def _classificar_retorno(mensagens):
    if not mensagens:
        return "inconclusivo", "Nenhuma mensagem retornada pelo ERP após a importação."

    texto = " | ".join(mensagens)
    texto_lower = texto.lower()
    if any(marcador in texto_lower for marcador in MARCADORES_ERRO):
        return "erro", texto
    if any(marcador in texto_lower for marcador in MARCADORES_SUCESSO):
        return "sucesso", texto
    return "inconclusivo", texto


def _importar_lote(page, lote, log):
    _abrir_aba_xml_manual(page, log)
    _aguardar_arquivos_carregados(page, [item["caminho"] for item in lote], log)
    baseline = " | ".join(_coletar_mensagens(page))

    botao = _localizar_botao_importar_xml(page)
    if not botao:
        raise RuntimeError("Botão 'Importar XML Manual' não encontrado no ERP.")

    botao.click(force=True)
    try:
        page.wait_for_load_state("networkidle", timeout=60000)
    except Exception:
        pass
    mensagens = _aguardar_retorno_importacao(page, baseline, log)
    return _classificar_retorno(mensagens)


def _notificar_status(item, status_callback, status, mensagem):
    if status_callback:
        status_callback(item, status, mensagem)


def iniciar_importacao_xml(config, itens_xml, log_callback=None, status_callback=None):
    def log(msg):
        print(f"[IMPORTA XML]: {msg}")
        if log_callback:
            log_callback(msg)

    itens = [dict(item) for item in itens_xml if str(item.get("caminho") or "").strip()]
    if not itens:
        return {"ok": 0, "erro": 0, "total": 0}

    total_ok = 0
    total_erro = 0

    with ERP_LOCK:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=usar_headless(),
                channel="chrome",
            )
            context = browser.new_context(viewport={"width": 1380, "height": 900})
            page = context.new_page()

            try:
                _fazer_login(page, config, log)
                _abrir_painel_nfe(page, log)

                total_lotes = (len(itens) + TAMANHO_LOTE_XML - 1) // TAMANHO_LOTE_XML
                for indice_lote, inicio in enumerate(range(0, len(itens), TAMANHO_LOTE_XML), 1):
                    lote = itens[inicio:inicio + TAMANHO_LOTE_XML]
                    log(
                        f"📦 Lote {indice_lote}/{total_lotes}: "
                        f"enviando {len(lote)} XML(s) para importação manual..."
                    )
                    for item in lote:
                        _notificar_status(item, status_callback, "ENVIANDO", "XML enviado ao lote atual.")

                    status_lote, mensagem_lote = _importar_lote(page, lote, log)
                    if status_lote == "sucesso":
                        for item in lote:
                            _notificar_status(item, status_callback, "IMPORTADO", mensagem_lote)
                            total_ok += 1
                        continue

                    if len(lote) > 1:
                        log(
                            "⚠️ Lote retornou erro/inconsistência. "
                            "Reprocessando individualmente para identificar cada XML."
                        )
                        for item in lote:
                            _notificar_status(
                                item,
                                status_callback,
                                "REPROCESSANDO",
                                "Lote sem retorno confiável. Reprocessando XML individualmente.",
                            )
                            status_item, mensagem_item = _importar_lote(page, [item], log)
                            if status_item == "sucesso":
                                _notificar_status(item, status_callback, "IMPORTADO", mensagem_item)
                                total_ok += 1
                            else:
                                _notificar_status(item, status_callback, "ERRO", mensagem_item)
                                total_erro += 1
                    else:
                        item = lote[0]
                        _notificar_status(item, status_callback, "ERRO", mensagem_lote)
                        total_erro += 1
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass

    log(
        f"🏁 Importação XML concluída: "
        f"{total_ok} sucesso(s), {total_erro} erro(s), total {len(itens)}."
    )
    return {"ok": total_ok, "erro": total_erro, "total": len(itens)}
