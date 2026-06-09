import re
import time
import database_setup as db

# ====================================================================
# FUNÇÕES AUXILIARES DO DETETIVE FISCAL (XML)
# ====================================================================
def _voltar_aba_principal(page):
    """Função auxiliar para garantir que o robô saia do XML e volte para a tela normal"""
    try:
        # Ele tenta achar a aba 'A. Dados Gerais' ou 'Itens'. Se não achar, clica na 1ª aba da tela
        aba_dados_gerais = page.locator('span.rf-tab-lbl', has_text=re.compile(r"Dados Gerais|Itens"))
        if aba_dados_gerais.count() > 0:
            aba_dados_gerais.first.click()
        else:
            page.locator('span.rf-tab-lbl').first.click()
        time.sleep(1.5)
    except:
        pass

def buscar_dados_xml_item(page, log, idx_item):
    """Lê o XML da NFe para descobrir o produto via NCM/ANP e extrai a Unidade de Medida"""
    log(f"   🔎 Abrindo aba XML para investigar NCM, ANP e Unidade do Item {idx_item + 1}...")
    
    try:
        aba_xml = page.locator('span.rf-tab-lbl', has_text="Arquivo XML da NFe")
        if aba_xml.is_visible():
            aba_xml.click()
            time.sleep(2)
        else:
            log("   ⚠️ Aba XML não encontrada.")
            return None, "UN" # Retorna None pro combustível e UN como padrão
            
        xml_texto = page.locator('div, pre, td, span').filter(has_text="nfeProc").last.text_content()
        numero_item_xml = idx_item + 1
        bloco_match = re.search(rf'<det nItem="{numero_item_xml}".*?</det>', xml_texto, re.DOTALL)
        
        if not bloco_match:
            _voltar_aba_principal(page)
            return None, "UN"
            
        bloco_item = bloco_match.group(0)
        
        # Extrações
        ncm_match = re.search(r'<NCM>(\d+)</NCM>', bloco_item)
        anp_match = re.search(r'<cProdANP>(\d+)</cProdANP>', bloco_item)
        xprod_match = re.search(r'<xProd>(.*?)</xProd>', bloco_item)
        ucom_match = re.search(r'<uCom>(.*?)</uCom>', bloco_item) # <--- NOVA CAPTURA AQUI
        
        ncm = ncm_match.group(1) if ncm_match else ""
        anp = anp_match.group(1) if anp_match else ""
        xprod = xprod_match.group(1).upper() if xprod_match else ""
        ucom_xml = ucom_match.group(1).upper().strip() if ucom_match else "UN"
        
        log(f"   🏷️ XML Lido -> NCM: {ncm} | Unidade: {ucom_xml} | Produto: {xprod}")
        
        # ====================================================================
        # TRADUTOR DE UNIDADES (SEFAZ -> SEU ERP)
        # ====================================================================
        mapa_unidades = {
            "LT": "LT", "L": "L", "LITRO": "L", "LTS": "L",
            "KG": "KG", "KILO": "KG", "KGS": "KG",
            "PC": "PC", "PÇ": "PC", "PECA": "PC", "PECAS": "PC", "PÇS": "PC",
            "UN": "UN", "UNID": "UN", "UD": "UN", "UNIDADE": "UN",
            "CDA": "CDA", "JG": "JG", "JOGO": "JG", "JGS": "JG",
            "TON": "TON", "T": "T", "PR": "PR", "PAR": "PR", "PARES": "PR",
            "MT": "MT", "M": "MT", "METRO": "MT", "MTS": "MT", "ML": "ML",
            "M2": "M2", "M3": "M3", "CJ": "CJ", "CONJ": "CJ", "CONJUNTO": "CJ",
            "CX": "CX", "CAIXA": "CX", "CXS": "CX",
            "LA": "LA", "LATA": "LA", "LTAS": "LA",
            "BD": "BD", "BALDE": "BD", "BDS": "BD",
            "PO": "PO", "PCT": "PO", "PACOTE": "PO", "PCTS": "PO",
            "KT": "KT", "KIT": "KT", "KITS": "KT",
            "SC": "SC", "SACO": "SC", "SACA": "SC", "SCS": "SC",
            "SV": "SV", "SERVICO": "SV", "GL": "GL", "GALAO": "GL", "GAL": "GL",
            "CB": "CB", "CABECA": "CB", "B": "B", "BAG": "B", "BB": "BB", "BIGBAG": "BB",
            "CT": "CT", "CENTO": "CT", "TA": "TA", "TAMBOR": "TA"
        }
        
        # Descobre a unidade correta pro ERP (se vier algo bizarro, usa 'UN' por segurança)
        unidade_erp = mapa_unidades.get(ucom_xml, "UN")
        
        # ====================================================================
        # LÓGICA DE COMBUSTÍVEIS
        # ====================================================================
        codigos_erp = db.carregar_codigos_combustiveis()
        codigo_final = None
        
        if ncm == "31021010": codigo_final = codigos_erp.get("arla")
        elif ncm == "27101259": codigo_final = codigos_erp.get("gasolina")
        elif ncm in ["22072019", "22071090", "22071010"]: codigo_final = codigos_erp.get("etanol")
        elif ncm == "27101921":
            if anp in ["820101033", "820101034"] or "S10" in xprod or "S-10" in xprod:
                codigo_final = codigos_erp.get("s10")
            elif anp in ["820101012", "820101013"] or "S500" in xprod or "S-500" in xprod:
                codigo_final = codigos_erp.get("s500")

        _voltar_aba_principal(page)
        
        # Retorna a dupla: (Código do Combustível se tiver, Unidade do ERP)
        if codigo_final and str(codigo_final).strip() != "":
            return codigo_final, unidade_erp
        return None, unidade_erp
            
    except Exception as e:
        log(f"   ⚠️ Erro ao ler XML: {e}")
        _voltar_aba_principal(page)
        return None, "UN"

# ====================================================================
# FUNÇÃO PRINCIPAL DE PROCESSAMENTO DO ITEM
# ====================================================================
def processar_cadastro_item(page, log, idx, item_block, codigo_negocio, is_estoque=False):
    campo_item = page.locator(f'input[id="formCad:tableItemNota:{idx}:itemDInput"]')
    
    # ====================================================================
    # 1. LÓGICA DE PREENCHIMENTO E CADASTRO DO ITEM
    # ====================================================================
    if not campo_item.input_value().strip():
        time.sleep(1.5) 
        
        try:
            texto_item_bloco = item_block.inner_text()
            busca_nome = re.search(r"Produto na NFe:\s*([^\n]+)", texto_item_bloco, re.IGNORECASE)
            if busca_nome:
                nome_bruto = busca_nome.group(1).upper()
                nome_limpo = re.split(r'\s{2,}|\bNegócio\b', nome_bruto, flags=re.IGNORECASE)[0].strip()
                nome_limpo = re.sub(r'^[^a-zA-Z0-9]*\d+(?:\s+|-|\)|\])[^a-zA-Z0-9]*', '', nome_limpo).strip()
                nome_item_temporario = nome_limpo[:80]
            else:
                nome_item_temporario = "ITEM NFE"
        except Exception:
            nome_item_temporario = "ITEM NFE"

        campo_item.click()
        campo_item.clear()
        campo_item.press_sequentially(nome_item_temporario, delay=30) 
        time.sleep(1)
        campo_item.press("Enter")
        time.sleep(1.5) 
        campo_item.press("Enter")
        time.sleep(0.5)
        campo_item.press("Tab")
        time.sleep(2) 

        valor_item_atual = campo_item.input_value().strip().upper()
        
        if "CADASTRO NAO ENCONTRADO" in valor_item_atual or "REFAZER CONSULTA" in valor_item_atual or not valor_item_atual:
            log("-> Item não achou pelo nome. Iniciando investigação no XML...")
            
            # Aqui ele chama a função que já criamos antes para ler o XML
            codigo_combustivel, unidade_erp = buscar_dados_xml_item(page, log, idx)
            
            if codigo_combustivel:
                log(f"   ✅ É Combustível/ARLA! Injetando código: {codigo_combustivel}")
                campo_item.click()
                campo_item.clear()
                campo_item.press_sequentially(codigo_combustivel, delay=50)
                time.sleep(1)
                campo_item.press("Enter")
                time.sleep(1)
                campo_item.press("Enter")
                time.sleep(0.5)
                campo_item.press("Tab")
                time.sleep(2)
                log("   -> Código do combustível vinculado com sucesso!")
                
            else:
                log(f"-> Iniciando cadastro na aba secundária. Unidade identificada: [{unidade_erp}]")
                campo_item.clear()
                linha_img = item_block.locator('tr').filter(has=page.locator(f'input[id="formCad:tableItemNota:{idx}:itemDInput"]'))

                with page.context.expect_page() as nova_aba_item:
                    linha_img.locator('img[title="Inserir/Alterar"]').click()
                
                aba_item = nova_aba_item.value
                aba_item.wait_for_load_state("networkidle")
                
                aba_item.locator('input[id="formitemD:ItemD_descricao"]').fill(nome_item_temporario)
                cod_grupo_padrao = db.carregar_codigo_grupo_item_padrao()
                if not cod_grupo_padrao:
                    print("[AVISO] Código do grupo INDEFINIDO não configurado em Parâmetros ERP.")
                else:
                    aba_item.locator('select[id="formitemD:ItemD_grupoD"]').select_option(value=cod_grupo_padrao)
                
                # Injeta a unidade que veio do XML
                aba_item.locator('select[id="formitemD:ItemD_unidade"]').select_option(value=unidade_erp)
                
                aba_item.locator('select[id="formitemD:ItemD_gerenciaEstoque"]').select_option(value="N")
                aba_item.locator('select[id="formitemD:ItemD_viagem"]').select_option(value="N")
                
                if codigo_negocio != "-": 
                    aba_item.locator('select[id="formitemD:ItemD_negocio"]').select_option(value=codigo_negocio)
                
                aba_item.locator('input[id="formitemD:gravaritemD"]').click()
                aba_item.wait_for_function('() => { var el = document.getElementById("formitemD:ItemD_codItemD"); return el !== null && el.value.trim() !== ""; }', timeout=15000)
                
                novo_cod = aba_item.locator('input[id="formitemD:ItemD_codItemD"]').input_value()
                log(f"-> Código gerado com sucesso: {novo_cod}")
                
                aba_item.close()
                page.bring_to_front()
                time.sleep(1)
                
                campo_item.click()
                campo_item.clear()
                campo_item.press_sequentially(novo_cod, delay=50)
                time.sleep(1)
                campo_item.press("Enter")
                time.sleep(1)
                campo_item.press("Enter")
                time.sleep(0.5)
                campo_item.press("Tab")

        else:
            log(f"-> SUCESSO! Item selecionado pelo nome: {valor_item_atual}")
    else:
        log(f"-> O item já estava preenchido: {campo_item.input_value()}")

    # ====================================================================
    # 2. DEFINIÇÃO DA FLAG "NÃO PREVISTA (EM VIAGEM)" COM BASE NO CÓDIGO
    # ====================================================================
    # Espera vital de 3 segundos: Garante que o ERP já carregou a linha toda (AJAX) e não vai resetar nosso campo!
    time.sleep(3) 

    if not is_estoque:
        # Busca o select diretamente da página para evitar erro de elemento obsoleto
        sel_prevista = page.locator(f'select[id="formCad:tableItemNota:{idx}:naoPrevista"]')
        
        if sel_prevista.count() > 0 and sel_prevista.first.is_visible():
            
            # Puxa os códigos dos combustíveis cadastrados no seu Painel de Filtros
            cods_db = db.carregar_codigos_combustiveis()
            codigos_combustivel = [str(v).strip() for v in cods_db.values() if str(v).strip()]
            
            # Lê o que ficou preenchido definitivamente na tela
            campo_atualizado = page.locator(f'input[id="formCad:tableItemNota:{idx}:itemDInput"]')
            valor_final_tela = campo_atualizado.input_value().strip()
            
            # -------------------------------------------------------------
            # CORREÇÃO CIRÚRGICA AQUI: Mudamos de [0] para [-1] para pegar o FINAL
            # Ex: "OLEO DIESEL S10-8" -> Pega o "8"
            # -------------------------------------------------------------
            codigo_extraido = valor_final_tela.split("-")[-1].strip()
            
            # A MÁGICA INFALÍVEL: Só marca "S" se o código exato da tela estiver na sua lista de combustíveis!
            if codigo_extraido in codigos_combustivel and codigo_extraido != "":
                sel_prevista.first.select_option(value="S")
                log(f"   -> Código [{codigo_extraido}] detectado como Combustível. 'Não Prevista (em viagem)': [S] Sim")
            else:
                sel_prevista.first.select_option(value="N")
                log(f"   -> Código [{codigo_extraido}] detectado como Peça/Outro. 'Não Prevista (em viagem)': [N] Não")
            
            time.sleep(1) # Aguarda a tela fixar a escolha