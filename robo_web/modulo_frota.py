import time
import os
import re
from datetime import datetime
import pandas as pd
from playwright.sync_api import sync_playwright
import database_setup as db
from robo_web.erp_lock import ERP_LOCK
from robo_web.runtime_config import usar_headless
from robo_web.utils import fazer_login_erp


def _normalizar_nome_coluna_frota(nome):
    return re.sub(r'[^a-z0-9]', '', str(nome or '').strip().lower())


def _mapear_colunas_frota(df):
    """Padroniza colunas do relatório 117 para o banco local."""
    mapa_destino = {
        'codveiculo': 'codVeiculo',
        'cavalo': 'cavalo',
        'placa': 'placa',
        'carreta1': 'carreta1',
        'carreta2': 'carreta2',
        'carreta3': 'carreta3',
        'veiculoproprio': 'veiculoProprio',
        'veicproprio': 'veiculoProprio',
        'tipovinculo': 'veiculoProprio',
        'vinculo': 'veiculoProprio',
    }
    renomear = {}
    for col in df.columns:
        chave = _normalizar_nome_coluna_frota(col)
        if chave in mapa_destino:
            renomear[col] = mapa_destino[chave]
    if renomear:
        df = df.rename(columns=renomear)
    return df


def _preparar_lista_veiculos_frota(df):
    df = _mapear_colunas_frota(df)
    if 'codVeiculo' not in df.columns or 'placa' not in df.columns:
        raise RuntimeError(
            'Relatório 117 sem colunas codVeiculo/placa. '
            'Verifique o filtro Cavalo=Cavalo e o layout do relatório.'
        )

    for col in ('carreta1', 'carreta2', 'carreta3', 'cavalo', 'veiculoProprio'):
        if col not in df.columns:
            df[col] = ''

    df = df.fillna('')
    df = df.dropna(subset=['placa'])
    df = df[df['placa'].astype(str).str.strip() != '']

    registros = []
    for _, row in df.iterrows():
        cod = str(row.get('codVeiculo', '')).strip()
        if not cod or not str(cod).replace('.', '', 1).isdigit():
            continue
        registros.append({
            'codVeiculo': int(float(cod)),
            'cavalo': str(row.get('cavalo', '')).strip(),
            'placa': str(row.get('placa', '')).strip(),
            'carreta1': str(row.get('carreta1', '')).strip(),
            'carreta2': str(row.get('carreta2', '')).strip(),
            'carreta3': str(row.get('carreta3', '')).strip(),
            'veiculoProprio': str(row.get('veiculoProprio', '')).strip(),
        })
    return registros


def baixar_e_importar_frota(config_override=None):
    with ERP_LOCK:
        return _baixar_e_importar_frota_impl(config_override=config_override)


def _baixar_e_importar_frota_impl(config_override=None):
    # Puxa o código da tabela blindada
    try: config_rel = db.carregar_codigos_relatorios()
    except: config_rel = {}
    codigo_relatorio = str(config_rel.get('rel_veiculo') or '').strip()

    # Agora o print fala a verdade e mostra o número que você digitou!
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Iniciando robô de sincronização de Frota (Relatório {codigo_relatorio})...")
    
    config = config_override or db.carregar_configuracoes()
    if not config or not config['link']:
        print("❌ Sistema não configurado. Impossível baixar frota.")
        return False
    if not codigo_relatorio:
        print("❌ Código do relatório de Veículos não configurado. Ajuste em Parâmetros ERP.")
        return False

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
            
            print(f" -> Pesquisando relatório {codigo_relatorio} (Veículos)...")
            campo_codigo = page.locator('text=Código:').locator('xpath=./following::input[1]')
            campo_codigo.fill(codigo_relatorio)
            page.locator('img[src*="search"], img[src*="lupa"], input[type="image"]').first.click()
            
            page.wait_for_selector(f"td:has-text('{codigo_relatorio}')", timeout=15000)
            link_resultado = page.locator(f"td:has-text('{codigo_relatorio}')").locator("xpath=./following-sibling::td[1]").locator("a")
            link_resultado.evaluate("node => node.click()")
            
            with context.expect_page() as new_page_info:
                page.locator('text="Exportar Dados"').evaluate("node => node.click()")
            nova_aba = new_page_info.value
            nova_aba.locator('text="### Link para exportação ###"').first.evaluate("node => node.click()")
            nova_aba.wait_for_load_state("networkidle")
            
            print(" -> Preenchendo filtros de Data, Liberado (Sim), Veículo e Cavalo (Cavalo)...")
            nova_aba.locator('input[id="formrelFilVeicDados:RelFilVeicDados_dataIniInputDate"]').fill("01/01/2000")
            nova_aba.locator('input[id="formrelFilVeicDados:RelFilVeicDados_dataIniInputDate"]').press("Tab")
            nova_aba.locator('select[id="formrelFilVeicDados:RelFilVeicDados_filtroLiberado"]').select_option(value="1")
            nova_aba.locator('select[id="formrelFilVeicDados:RelFilVeicDados_veiculoProprio"]').select_option(value="5")
            nova_aba.locator('select[id="formrelFilVeicDados:RelFilVeicDados_cavalo"]').select_option(value="S")
            
            print(" -> Gerando relatório...")
            nova_aba.locator('input[id*="filtrar"], input[value="Gerar"], input[value="Filtrar"]').first.click(force=True, no_wait_after=True)
            
            selector_link = nova_aba.get_by_text("Clique aqui para visualizar arquivo", exact=False)
            selector_link.wait_for(state="visible", timeout=90000)
            
            caminho_arquivo = os.path.join(pasta_downloads, "relFilVeicDados.xls")
            if os.path.exists(caminho_arquivo): os.remove(caminho_arquivo)
            
            with nova_aba.expect_download(timeout=60000) as download_info:
                selector_link.click()
            download_info.value.save_as(caminho_arquivo)
            print(" ✅ Download concluído!")
            
            print(" -> Lendo a planilha e atualizando o banco de dados...")
            try:
                df = pd.read_excel(caminho_arquivo)
            except Exception:
                with open(caminho_arquivo, 'r', encoding='latin-1') as f:
                    df = pd.read_html(f.read(), decimal=',', thousands='.')[0]
            
            lista_veiculos = _preparar_lista_veiculos_frota(df)
            if not db.sincronizar_frota_erp(lista_veiculos):
                print(' ❌ Falha ao gravar frota no banco de dados.')
                return False
            duplicadas = db.detectar_placas_carreta_duplicadas()
            print(f' 🌟 SUCESSO! {len(lista_veiculos)} veículos sincronizados.')
            if duplicadas:
                print(
                    f" ⚠️ {len(duplicadas)} carreta(s) duplicada(s) em mais de um cavalo: "
                    f"{', '.join(sorted(duplicadas)[:8])}"
                    f"{'...' if len(duplicadas) > 8 else ''}"
                )
            return True
                
        except Exception as e:
            print(f"❌ ERRO NO MÓDULO FROTA: {e}")
            return False
        finally:
            browser.close()


def baixar_e_importar_itens(config_override=None):
    with ERP_LOCK:
        return _baixar_e_importar_itens_impl(config_override=config_override)


def _baixar_e_importar_itens_impl(config_override=None):
    # Puxa o código da tabela blindada
    try: config_rel = db.carregar_codigos_relatorios()
    except: config_rel = {}
    codigo_relatorio = str(config_rel.get('rel_item') or '').strip()

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Iniciando robô de sincronização de Itens (Relatório {codigo_relatorio})...")
    
    config = config_override or db.carregar_configuracoes()
    if not config or not config['link']:
        print("❌ Sistema não configurado. Impossível baixar itens.")
        return False
    if not codigo_relatorio:
        print("❌ Código do relatório de Itens não configurado. Ajuste em Parâmetros ERP.")
        return False

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
            
            print(f" -> Pesquisando relatório {codigo_relatorio} (Itens)...")
            campo_codigo = page.locator('text=Código:').locator('xpath=./following::input[1]')
            campo_codigo.fill(codigo_relatorio)
            page.locator('img[src*="search"], img[src*="lupa"], input[type="image"]').first.click()
            
            page.wait_for_selector(f"td:has-text('{codigo_relatorio}')", timeout=15000)
            link_resultado = page.locator(f"td:has-text('{codigo_relatorio}')").locator("xpath=./following-sibling::td[1]").locator("a")
            link_resultado.evaluate("node => node.click()")
            
            with context.expect_page() as new_page_info:
                page.locator('text="Exportar Dados"').evaluate("node => node.click()")
            nova_aba = new_page_info.value
            nova_aba.locator('text="### Link para exportação ###"').first.evaluate("node => node.click()")
            nova_aba.wait_for_load_state("networkidle")
            
            print(" -> Gerando relatório de itens...")
            nova_aba.locator('input[id*="filtrar"], input[value="Gerar"], input[value="Filtrar"]').first.click(force=True, no_wait_after=True)
            
            selector_link = nova_aba.get_by_text("Clique aqui para visualizar arquivo", exact=False)
            selector_link.wait_for(state="visible", timeout=90000)
            
            caminho_arquivo = os.path.join(pasta_downloads, "itemDFil.xls")
            if os.path.exists(caminho_arquivo): os.remove(caminho_arquivo)
            
            with nova_aba.expect_download(timeout=60000) as download_info:
                selector_link.click()
            download_info.value.save_as(caminho_arquivo)
            print(" ✅ Download concluído!")
            
            print(" -> Lendo a planilha de Itens e atualizando o banco de dados...")
            try:
                df = pd.read_excel(caminho_arquivo)
            except Exception:
                with open(caminho_arquivo, 'r', encoding='latin-1') as f:
                    df = pd.read_html(f.read(), decimal=',', thousands='.')[0]
            
            df = df.dropna(subset=['codItemD']) 
            df = df.fillna("")
            lista_itens = df.to_dict('records')
            
            db.sincronizar_itens_erp(lista_itens)
            print(f" 🌟 SUCESSO! {len(lista_itens)} itens sincronizados.")
            return True
                
        except Exception as e:
            print(f"❌ ERRO NO MÓDULO ITENS: {e}")
            return False
        finally:
            browser.close()