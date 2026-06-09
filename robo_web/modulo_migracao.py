import time
import re
from playwright.sync_api import sync_playwright
from robo_web import modulo_frota
from robo_web.erp_lock import ERP_LOCK
from robo_web.runtime_config import usar_headless
from robo_web.utils import fazer_login_erp


def iniciar_migracao_lote(config, itens_codigos, novo_grupo_nome, log_callback, grupo_atual="Filtrado"):
    def log(msg):
        print(f"[MIGRAÇÃO EM LOTE]: {msg}")
        if log_callback:
            log_callback(msg)

    with ERP_LOCK:
        log("🔒 Sessão ERP exclusiva iniciada")
        log("✅ Processo iniciado: Troca de grupo em lote")
        log(f"📤 Grupo de origem: {grupo_atual}")
        log(f"📥 Novo grupo destino: {novo_grupo_nome}")
        log(f"📦 Total de itens a processar: {len(itens_codigos)}")

        nome_destino = novo_grupo_nome.strip().upper()
        cont_ok, cont_falha, cont_pulado = 0, 0, 0

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=usar_headless(),
                    channel="chrome",
                )                                                   
                context = browser.new_context(viewport={"width": 1300, "height": 850})
                page = context.new_page()

                fazer_login_erp(page, config, log=log)

                log("📂 Abrindo Cadastros > Itens Despesa/Estoque...")
                page.locator('div[id="formMenu:j_idt128_label"]').click(force=True)
                page.wait_for_timeout(400)
                page.locator('div[id="formMenu:j_idt151"]').click(force=True)
                page.locator('input[id="formitemDFil:ItemDFil_codItemD"]').wait_for(
                    state="visible", timeout=15000,
                )
                page.wait_for_timeout(300)

                for posicao, cod_item in enumerate(itens_codigos, 1):
                    cod_item = str(cod_item).strip()
                    log(f"\n🔄 Item {posicao}/{len(itens_codigos)} | Código: {cod_item}")

                    campo_busca = page.locator('input[id="formitemDFil:ItemDFil_codItemD"]')
                    if not campo_busca.is_visible(timeout=800):
                        try:
                            log("→ Carregando tela de pesquisa...")
                            botao_listar = page.locator('input[id="formitemD:listitemD"]')
                            botao_listar.wait_for(state="visible", timeout=8000)
                            botao_listar.click(force=True)
                            campo_busca.wait_for(state="visible", timeout=8000)
                            page.wait_for_timeout(300)
                        except Exception:
                            if not campo_busca.is_visible(timeout=1500):
                                log(f"❌ Tela não carregou, pulando {cod_item}")
                                cont_pulado += 1
                                continue

                    log(f"→ Buscando código {cod_item}...")
                    campo_codigo = campo_busca
                    campo_codigo.wait_for(state="visible", timeout=3000)
                    campo_codigo.click(click_count=3)
                    campo_codigo.fill(cod_item)

                    page.locator('input[id="formitemDFil:filtraritemDFil"]').click(force=True)
                    page.wait_for_load_state("load", timeout=8000)
                    page.wait_for_timeout(600)

                    campo_grupo = page.locator('select[id="formitemD:ItemD_grupoD"]')
                    if campo_grupo.is_visible(timeout=4000):
                        log("✅ Cadastro já aberto (1 registro)")
                    else:
                        try:
                            if page.is_closed():
                                raise RuntimeError("navegador fechado")
                            linhas = page.locator('table.rf-dt tbody tr, tbody tr')
                            page.wait_for_timeout(800)
                            n = linhas.count()
                            if n == 0:
                                log(f"❌ Item {cod_item} não encontrado (lista vazia)")
                                cont_pulado += 1
                                continue
                            linha = linhas.first if n == 1 else None
                            if linha is None:
                                for i in range(n):
                                    candidata = linhas.nth(i)
                                    if candidata.locator('td').first.inner_text().strip() == cod_item:
                                        linha = candidata
                                        break
                            if linha is None:
                                candidata = linhas.filter(
                                    has_text=re.compile(rf'^\s*{re.escape(cod_item)}\s')
                                )
                                if candidata.count() > 0:
                                    linha = candidata.first
                            if linha is None:
                                log(f"❌ Item {cod_item} não encontrado na lista")
                                cont_pulado += 1
                                continue
                            linha.dblclick(force=True)
                            page.wait_for_load_state("networkidle", timeout=10000)
                            page.wait_for_timeout(2000)
                            log("✅ Cadastro aberto")
                        except Exception as e_abrir:
                            log(f"❌ Item {cod_item} não encontrado ({str(e_abrir)[:50]})")
                            cont_pulado += 1
                            continue

                    try:
                        log(f"→ Trocando grupo para: {novo_grupo_nome}")
                        campo_grupo = page.locator('select[id="formitemD:ItemD_grupoD"]')
                        campo_grupo.scroll_into_view_if_needed()
                        campo_grupo.wait_for(state="attached", timeout=8000)
                        option_value = campo_grupo.evaluate(
                            """(select, nomeAlvo) => {
                                const target = Array.from(select.options)
                                    .find(opt => opt.text.trim().toUpperCase() === nomeAlvo);
                                return target ? target.value : null;
                            }""",
                            nome_destino,
                        )
                        if not option_value:
                            log(f"⚠️ Grupo '{novo_grupo_nome}' não encontrado no combobox do ERP!")
                            cont_falha += 1
                            continue
                        campo_grupo.select_option(value=option_value)
                        campo_grupo.evaluate("""el => {
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('blur', { bubbles: true }));
                        }""")
                        page.wait_for_timeout(1000)
                        grupo_selecionado = campo_grupo.evaluate(
                            "el => el.options[el.selectedIndex].text"
                        ).strip().upper()
                        if grupo_selecionado != nome_destino:
                            log("⚠️ Grupo selecionado difere do destino")
                            cont_falha += 1
                            continue
                        log(f"✅ Grupo confirmado: {grupo_selecionado}")
                    except Exception as erro_grupo:
                        log(f"❌ Falha ao alterar grupo: {str(erro_grupo)[:50]}")
                        cont_falha += 1
                        continue

                    try:
                        log("→ Gravando alteração...")
                        botao_gravar = page.locator('input[id="formitemD:gravaritemD"]')
                        botao_gravar.scroll_into_view_if_needed()
                        botao_gravar.click(force=True)
                        page.wait_for_load_state("networkidle", timeout=12000)
                        page.wait_for_timeout(2000)

                        mensagem = page.locator('div[id="formitemD:messages"] li.fontInfoMessages')
                        if mensagem.is_visible(timeout=4000):
                            log(f"✅ {cod_item} | Salvo com sucesso!")
                        else:
                            log(f"✅ {cod_item} | Finalizado")
                        cont_ok += 1
                    except Exception as e_gravar:
                        if "strict mode violation" in str(e_gravar):
                            log(f"✅ {cod_item} | Gravado normalmente")
                            cont_ok += 1
                        else:
                            log(f"⚠️ {cod_item} | Atenção: {str(e_gravar)[:50]}")
                            cont_falha += 1

                if cont_falha == 0 and cont_pulado == 0:
                    log(f"\n🏁 Migração concluída: {cont_ok} item(ns) alterado(s).")
                else:
                    log(
                        f"\n🏁 Concluída com ressalvas: {cont_ok} ok, "
                        f"{cont_falha} falha(s), {cont_pulado} pulado(s)."
                    )

                context.close()
                browser.close()
                log("🔒 Navegador fechado com sucesso.")

        except Exception as erro_geral:
            log(f"\n❌ Erro geral: {str(erro_geral)}")

        try:
            log("\n🔄 Atualizando banco de dados...")
            modulo_frota.baixar_e_importar_itens()
            log("✅ Sincronização concluída!")
        except Exception as e_sync:
            log(f"⚠️ Erro na sincronização: {str(e_sync)}")
