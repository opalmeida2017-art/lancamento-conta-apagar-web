import time
import os
from datetime import datetime
import pandas as pd
from playwright.sync_api import sync_playwright
import database_setup as db
from robo_web.runtime_config import usar_headless
from robo_web.utils import fazer_login_erp

def baixar_e_importar_itens():
    try:
        cfg_rel = db.carregar_codigos_relatorios()
    except Exception:
        cfg_rel = {}
    codigo_relatorio = str(cfg_rel.get('rel_item') or '').strip()
    print(
        f"\n[{datetime.now().strftime('%H:%M:%S')}] "
        f"Iniciando robô de sincronização de Itens (Relatório {codigo_relatorio or 'não configurado'})..."
    )
    
    config = db.carregar_configuracoes()
    if not config or not config['link']:
        print("❌ Sistema não configurado. Impossível baixar itens.")
        return
    if not codigo_relatorio:
        print("❌ Código do relatório de Itens não configurado. Ajuste em Parâmetros ERP.")
        return

    pasta_downloads = os.path.abspath("downloads_erp")
    os.makedirs(pasta_downloads, exist_ok=True)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=usar_headless(), channel="chrome")
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        
        try:
            print(" -> Fazendo login no ERP...")
            fazer_login_erp(page, config, log=print)

            print(" -> Acessando módulo de Exportações...")
            page.locator('text="Exp./Imp."').first.click()
            time.sleep(1)
            page.locator('text="Cadastro de Exportações"').first.click()
            page.wait_for_load_state("networkidle")
            
            # 3. BUSCANDO RELATÓRIO DE ITENS CONFIGURADO
            print(f" -> Pesquisando relatório {codigo_relatorio}...")
            campo_codigo = page.locator('text=Código:').locator('xpath=./following::input[1]')
            campo_codigo.fill(codigo_relatorio)
            page.locator('img[src*="search"], img[src*="lupa"], input[type="image"]').first.click()
            
            page.wait_for_selector(f"td:has-text('{codigo_relatorio}')", timeout=15000)
            # Clica no link de detalhe/edição do relatório configurado
            link_resultado = page.locator(f"td:has-text('{codigo_relatorio}')").locator("xpath=./following-sibling::td[1]").locator("a")
            link_resultado.evaluate("node => node.click()")
            page.wait_for_load_state("networkidle")
            
            # 4. ABRINDO A TELA DE EXPORTAÇÃO
            with context.expect_page() as new_page_info:
                page.locator('text="Exportar Dados"').evaluate("node => node.click()")
            nova_aba = new_page_info.value
            nova_aba.wait_for_load_state("networkidle")
            
            # Clica no link gerado pelo sistema SAT
            nova_aba.locator('text="### Link para exportação ###"').first.evaluate("node => node.click()")
            nova_aba.wait_for_load_state("networkidle")
            time.sleep(2)
            
            # 5. GERANDO E BAIXANDO (Itens geralmente não precisam de filtros de data)
            print(" -> Solicitando geração do arquivo de itens...")
            # Clica no botão Filtrar/Gerar da tela configurada
            btn_gerar = nova_aba.locator('input[id*="filtrar"], input[value="Gerar"], input[value="Filtrar"]').first
            btn_gerar.click(force=True, no_wait_after=True)
            
            # Aguarda o link de download aparecer
            selector_link = nova_aba.get_by_text("Clique aqui para visualizar arquivo", exact=False)
            selector_link.wait_for(state="visible", timeout=90000)
            
            caminho_arquivo = os.path.join(pasta_downloads, "itemDFil.xls")
            if os.path.exists(caminho_arquivo):
                os.remove(caminho_arquivo)
            
            with nova_aba.expect_download(timeout=60000) as download_info:
                selector_link.click()
            download_info.value.save_as(caminho_arquivo)
            print(f" ✅ Download do Relatório {codigo_relatorio} concluído!")
            
            # 6. PROCESSAMENTO COM PANDAS
            print(" -> Lendo a planilha de Itens e atualizando o banco de dados...")
            try:
                # Tenta ler como HTML primeiro (comum no JSF)
                df = pd.read_html(caminho_arquivo)[0]
            except Exception:
                # Se falhar, tenta Excel nativo
                df = pd.read_excel(caminho_arquivo)
                
            # Limpa valores e foca na coluna do código do item
            df = df.dropna(subset=['codItemD']) 
            df = df.fillna("") # Remove NaNs para não dar erro no banco
            
            lista_itens = df.to_dict('records')
            
            # 7. SINCRONIZAÇÃO COM O BANCO LOCAL
            # Chama a função que criamos anteriormente no database_setup.py
            sucesso = db.sincronizar_itens_erp(lista_itens)
            
            if sucesso:
                print(f" 🌟 SUCESSO! {len(lista_itens)} itens sincronizados com o banco local.")
            else:
                print(" ❌ Falha ao salvar os itens no banco SQLite.")
                
        except Exception as e:
            print(f"❌ ERRO GERAL NO MÓDULO DE ITENS: {e}")
        finally:
            browser.close()