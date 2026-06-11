import threading

import backend.app.bootstrap as boot
from backend.app.ws_manager import ws_manager

db = boot.db
log_service = boot.log_service

from robo_web import automacao, modulo_frota, modulo_importa_xml, modulo_item_sync, modulo_migracao, modulo_tarifa_bancaria  # noqa: E402
from robo_web.controle_robo import (  # noqa: E402
    RoboParadoPeloUsuario,
    esta_rodando,
    solicitar_parada,
    solicitar_parada_apos_nota,
)


class RoboService:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessao_log = None
        self._status = "Parado"
        self._log_listener = None
        self._importacao_xml_em_andamento = False
        self._importacao_xml_pendente = None
        self._migracao_em_andamento = False
        self._tarifa_monitor_timer = None
        self._tarifa_snapshot = {}
        self._importando_tarifa_auto = False
        self._lancando_tarifa = False

    def status(self) -> dict:
        return {
            "rodando": esta_rodando(),
            "status": self._status,
            "sessao_id": self._sessao_log,
            "importacao_xml": self._importacao_xml_em_andamento,
            "migracao": self._migracao_em_andamento,
            "tarifa_monitor": self.tarifa_monitor_ativo(),
        }

    def tarifa_monitor_ativo(self) -> bool:
        return self._tarifa_monitor_timer is not None

    def _emit(self, evento: dict):
        ws_manager.emit_from_thread(evento)

    def _on_log(self, evento):
        self._emit({"tipo": "log", **evento})

    def _registrar_listener(self):
        if self._log_listener is None:
            self._log_listener = self._on_log
            log_service.adicionar_listener(self._log_listener)

    def _remover_listener(self):
        if self._log_listener:
            log_service.remover_listener(self._log_listener)
            self._log_listener = None

    def _cancelar_monitor_tarifa(self):
        self._tarifa_monitor_timer = None
        self._tarifa_snapshot = {}

    def _agendar_monitor_tarifa(self):
        self._cancelar_monitor_tarifa()
        pasta = db.obter_pasta_tarifas_bancarias()
        if pasta:
            try:
                self._tarifa_snapshot = modulo_tarifa_bancaria.obter_snapshot_planilhas(pasta)
            except Exception:
                self._tarifa_snapshot = {}

        def _tick():
            if not esta_rodando():
                self._cancelar_monitor_tarifa()
                return
            self._verificar_pasta_tarifas_auto()
            if esta_rodando():
                self._tarifa_monitor_timer = threading.Timer(5.0, _tick)
                self._tarifa_monitor_timer.daemon = True
                self._tarifa_monitor_timer.start()

        if esta_rodando():
            _tick()

    def _verificar_pasta_tarifas_auto(self):
        if self._importando_tarifa_auto or not esta_rodando():
            return
        pasta = db.obter_pasta_tarifas_bancarias()
        if not pasta:
            return
        pendentes, _ = modulo_tarifa_bancaria.detectar_planilhas_pendentes(
            self._tarifa_snapshot, pasta,
        )
        if not pendentes:
            return
        self._importando_tarifa_auto = True
        try:
            novo_snapshot, importados = modulo_tarifa_bancaria.importar_planilhas_alteradas(
                pasta, self._tarifa_snapshot,
            )
            self._tarifa_snapshot = novo_snapshot
            if importados:
                self._emit({"tipo": "tarifas_atualizadas", "importados": importados})
                log_service.registrar_log(
                    f"Tarifas: {importados} planilha(s) importada(s) automaticamente.",
                    origem="TARIFA",
                )
        finally:
            self._importando_tarifa_auto = False

    def _resolver_parametros_robo(self, log_ui, sessao):
        config = db.carregar_configuracoes()
        if not config or not config.get("link"):
            log_ui("Configure link e usuário do ERP em Configurações.")
            return None

        filtros = db.carregar_filtros() or {}
        mes_escolhido = filtros.get("mes", "01 - Janeiro")
        anos_selecionados = [filtros.get("ano", "2024")]
        ultimos_30_dias = bool(filtros.get("ultimos_30_dias", 0))
        hoje_apenas = bool(filtros.get("hoje_apenas", 0))
        ultimos_15_dias = bool(filtros.get("ultimos_15_dias", 0))

        partes_mes = str(mes_escolhido).split(" ")
        mes_formatado = partes_mes[2] if len(partes_mes) > 2 else partes_mes[0]
        mes_curto = mes_formatado[:3].capitalize()
        meses_selecionados = [mes_curto]

        return {
            "config": config,
            "meses_selecionados": meses_selecionados,
            "anos_selecionados": anos_selecionados,
            "ultimos_30_dias": ultimos_30_dias,
            "hoje_apenas": hoje_apenas,
            "ultimos_15_dias": ultimos_15_dias,
            "mes_formatado": mes_formatado,
        }

    def iniciar(self, nota_alvo=None, compra_estoque=False, notas_lote=None) -> dict:
        with self._lock:
            if esta_rodando():
                solicitar_parada()
                self._status = "Parando..."
                self._emit({"tipo": "status", "mensagem": self._status})
                self._cancelar_monitor_tarifa()
                return {"ok": True, "acao": "parada_solicitada"}

            if notas_lote:
                return self._iniciar_lote(notas_lote)

            self._registrar_listener()
            descricao = f"Nota alvo {nota_alvo}" if nota_alvo else "Processamento completo do robô"
            self._sessao_log = log_service.iniciar_sessao(origem="ROBO", descricao=descricao)
            self._status = "Iniciando..."
            self._emit({"tipo": "status", "mensagem": self._status, "rodando": True})

            thread = threading.Thread(
                target=self._executar,
                args=(nota_alvo, compra_estoque),
                daemon=True,
            )
            thread.start()
            self._agendar_monitor_tarifa()
            return {"ok": True, "acao": "iniciado", "sessao_id": self._sessao_log}

    def _iniciar_lote(self, notas_lote) -> dict:
        notas = [str(n or "").strip() for n in (notas_lote or []) if str(n or "").strip()]
        if not notas:
            return {"ok": False, "mensagem": "Informe ao menos um número de nota."}

        self._registrar_listener()
        resumo = ", ".join(notas[:8])
        if len(notas) > 8:
            resumo += f" (+{len(notas) - 8})"
        self._sessao_log = log_service.iniciar_sessao(
            origem="ROBO",
            descricao=f"Lote de notas ({len(notas)}): {resumo}",
        )
        self._status = "Iniciando lote..."
        self._emit({"tipo": "status", "mensagem": self._status, "rodando": True})

        thread = threading.Thread(target=self._executar_lote, args=(notas,), daemon=True)
        thread.start()
        self._agendar_monitor_tarifa()
        return {"ok": True, "acao": "lote_iniciado", "total": len(notas), "sessao_id": self._sessao_log}

    def _executar(self, nota_alvo, compra_estoque):
        sessao = self._sessao_log
        status_final = "SUCESSO"

        def log_ui(mensagem):
            texto = str(mensagem or "").strip()
            if not texto:
                return
            self._status = texto
            log_service.registrar_log(texto, origem="ROBO", sessao_id=sessao)
            self._emit({"tipo": "status", "mensagem": texto, "rodando": True})

        try:
            params = self._resolver_parametros_robo(log_ui, sessao)
            if not params:
                status_final = "ERRO"
                return

            if nota_alvo:
                log_ui(f"Iniciando robô para a nota {nota_alvo}...")
            elif params["hoje_apenas"]:
                log_ui("Iniciando robô para as notas de ontem e hoje...")
            elif params["ultimos_30_dias"]:
                log_ui("Iniciando robô para os últimos 30 dias...")
            elif params["ultimos_15_dias"]:
                log_ui("Iniciando robô para os últimos 15 dias...")
            else:
                log_ui(
                    f"Iniciando robô para {params['mes_formatado']}/{params['anos_selecionados'][0]}...",
                )

            automacao.iniciar_automacao(
                params["config"],
                params["meses_selecionados"],
                params["anos_selecionados"],
                progresso_callback=log_ui,
                nota_alvo=nota_alvo,
                compra_estoque=compra_estoque,
                ultimos_30_dias=params["ultimos_30_dias"],
                hoje_apenas=params["hoje_apenas"],
                ultimos_15_dias=params["ultimos_15_dias"],
            )
            log_ui("Automação concluída.")
        except Exception as exc:
            status_final = "ERRO"
            log_ui(f"ERRO: {exc}")
        finally:
            log_service.finalizar_sessao(sessao, origem="ROBO", status=status_final)
            self._status = "Parado"
            self._sessao_log = None
            self._remover_listener()
            self._cancelar_monitor_tarifa()
            self._emit({"tipo": "status", "mensagem": "Parado", "rodando": False})
            self._emit({"tipo": "painel_atualizar"})

    def _executar_lote(self, notas_lote):
        sessao = self._sessao_log
        status_final = "SUCESSO"
        erros = 0
        total = len(notas_lote)

        def log_ui(mensagem):
            texto = str(mensagem or "").strip()
            if not texto:
                return
            self._status = texto
            log_service.registrar_log(texto, origem="ROBO", sessao_id=sessao)
            self._emit({"tipo": "status", "mensagem": texto, "rodando": True})

        params = self._resolver_parametros_robo(log_ui, sessao)
        if not params:
            log_service.finalizar_sessao(sessao, origem="ROBO", status="ERRO")
            self._finalizar_robo()
            return

        try:
            log_ui(f"Iniciando lote com {total} nota(s)...")
            for indice, nota_alvo in enumerate(notas_lote, start=1):
                self._emit({"tipo": "lote_nota", "nota": nota_alvo, "indice": indice, "total": total})
                log_ui(f"Lote {indice}/{total}: iniciando nota {nota_alvo}...")
                try:
                    automacao.iniciar_automacao(
                        params["config"],
                        params["meses_selecionados"],
                        params["anos_selecionados"],
                        progresso_callback=log_ui,
                        nota_alvo=nota_alvo,
                        compra_estoque=False,
                        ultimos_30_dias=params["ultimos_30_dias"],
                        hoje_apenas=params["hoje_apenas"],
                        ultimos_15_dias=params["ultimos_15_dias"],
                    )
                    log_ui(f"Lote {indice}/{total}: nota {nota_alvo} concluída.")
                except RoboParadoPeloUsuario:
                    status_final = "PARADA"
                    log_ui("Lote interrompido pelo usuário.")
                    break
                except Exception as exc:
                    erros += 1
                    log_ui(f"Lote {indice}/{total}: erro na nota {nota_alvo} — {exc}")
                    log_service.registrar_log(
                        f"Erro no lote (nota {nota_alvo}): {exc}",
                        origem="ROBO",
                        sessao_id=sessao,
                        nivel="ERROR",
                    )

            if status_final != "PARADA":
                if erros:
                    status_final = "ERRO"
                    log_ui(f"Lote finalizado com {erros} erro(s) em {total} nota(s).")
                else:
                    log_ui(f"Lote finalizado: {total} nota(s) processada(s).")
        except Exception as exc:
            status_final = "ERRO"
            log_ui(f"Falha crítica no lote: {exc}")
        finally:
            log_service.finalizar_sessao(sessao, origem="ROBO", status=status_final)
            self._finalizar_robo()

    def _finalizar_robo(self):
        self._status = "Parado"
        self._sessao_log = None
        self._remover_listener()
        self._cancelar_monitor_tarifa()
        self._emit({"tipo": "status", "mensagem": "Parado", "rodando": False})
        self._emit({"tipo": "painel_atualizar"})

    def importar_tarifas_pasta(self, pasta) -> dict:
        def rodar():
            self._emit({"tipo": "status", "mensagem": "Importando planilhas de tarifa..."})

            def log_ui(msg):
                log_service.registrar_log(msg, origem="TARIFA")
                self._emit({"tipo": "log", "mensagem": msg})

            try:
                ok = modulo_tarifa_bancaria.importar_tarifas_pasta(pasta, log_callback=log_ui)
                msg = "Planilhas importadas." if ok else "Nenhuma tarifa nova encontrada."
            except Exception as exc:
                ok = False
                msg = str(exc)
            self._emit({"tipo": "status", "mensagem": msg})
            self._emit({"tipo": "tarifas_atualizadas", "ok": ok})

        threading.Thread(target=rodar, daemon=True).start()
        return {"ok": True, "mensagem": "Importação de planilhas iniciada."}

    def lancar_tarifas_pendentes(self) -> dict:
        if self._lancando_tarifa:
            return {"ok": False, "mensagem": "Lançamento de tarifas já em andamento."}
        if esta_rodando():
            return {"ok": False, "mensagem": "Pare o robô NFe antes de lançar tarifas."}

        def rodar():
            self._lancando_tarifa = True
            sessao = log_service.iniciar_sessao(origem="TARIFA", descricao="Lançamento de tarifas pendentes")

            def log_ui(msg):
                log_service.registrar_log(msg, origem="TARIFA", sessao_id=sessao)
                self._emit({"tipo": "log", "mensagem": msg})

            try:
                config = db.carregar_configuracoes()
                modulo_tarifa_bancaria.processar_tarifas_pendentes(config=config, log_callback=log_ui)
            except Exception as exc:
                log_ui(f"ERRO: {exc}")
            finally:
                log_service.finalizar_sessao(sessao, origem="TARIFA", status="SUCESSO")
                self._lancando_tarifa = False
                self._emit({"tipo": "tarifas_atualizadas"})

        threading.Thread(target=rodar, daemon=True).start()
        return {"ok": True, "mensagem": "Lançamento de tarifas pendentes iniciado."}

    def sincronizar_frota(self) -> dict:
        def rodar():
            self._emit({"tipo": "status", "mensagem": "Sincronizando frota (117)..."})
            try:
                ok = modulo_frota.baixar_e_importar_frota()
                msg = "Frota sincronizada." if ok else "Falha ao sincronizar frota."
            except Exception as exc:
                ok = False
                msg = str(exc)
            self._emit({"tipo": "status", "mensagem": msg})
            self._emit({"tipo": "frota_atualizada", "ok": ok})

        threading.Thread(target=rodar, daemon=True).start()
        return {"ok": True, "mensagem": "Sincronização de frota iniciada."}

    def sincronizar_itens(self) -> dict:
        def rodar():
            self._emit({"tipo": "status", "mensagem": "Sincronizando itens (118)..."})
            try:
                ok = modulo_item_sync.baixar_e_importar_itens()
                msg = "Itens sincronizados." if ok else "Falha ao sincronizar itens."
            except Exception as exc:
                ok = False
                msg = str(exc)
            self._emit({"tipo": "status", "mensagem": msg})
            self._emit({"tipo": "itens_atualizados", "ok": ok})

        threading.Thread(target=rodar, daemon=True).start()
        return {"ok": True, "mensagem": "Sincronização de itens iniciada."}

    def iniciar_importacao_xml(self, itens_xml) -> dict:
        itens = [dict(i) for i in (itens_xml or []) if i.get("caminho")]
        if not itens:
            return {"ok": False, "mensagem": "Nenhum XML informado."}
        if self._importacao_xml_em_andamento:
            return {"ok": False, "mensagem": "Importação XML já em andamento."}
        if esta_rodando():
            self._importacao_xml_pendente = itens
            solicitar_parada_apos_nota()
            self._emit({"tipo": "status", "mensagem": "Aguardando robô parar para importar XML..."})
            return {"ok": True, "acao": "agendada", "mensagem": "Importação agendada após nota atual."}
        threading.Thread(target=self._executar_importacao_xml, args=(itens,), daemon=True).start()
        return {"ok": True, "acao": "iniciada", "total": len(itens)}

    def _executar_importacao_xml(self, itens_xml):
        self._importacao_xml_em_andamento = True
        sessao = log_service.iniciar_sessao(origem="XML", descricao=f"Importação de {len(itens_xml)} XML(s)")
        status_final = "SUCESSO"

        def log_ui(msg):
            log_service.registrar_log(msg, origem="XML", sessao_id=sessao)
            self._emit({"tipo": "log", "mensagem": msg, "nivel": "INFO"})
            self._emit({"tipo": "xml_status", "mensagem": msg})

        def status_cb(item, status, mensagem=""):
            self._emit({
                "tipo": "xml_item",
                "caminho": item.get("caminho"),
                "status": status,
                "mensagem": mensagem,
            })

        try:
            config = db.carregar_configuracoes()
            if not config or not config.get("link"):
                log_ui("Configure link e usuário do ERP.")
                status_final = "ERRO"
                return
            modulo_importa_xml.iniciar_importacao_xml(
                config, itens_xml, log_callback=log_ui, status_callback=status_cb,
            )
        except Exception as exc:
            status_final = "ERRO"
            log_ui(f"ERRO: {exc}")
        finally:
            log_service.finalizar_sessao(sessao, origem="XML", status=status_final)
            self._importacao_xml_em_andamento = False
            pendente = self._importacao_xml_pendente
            self._importacao_xml_pendente = None
            self._emit({"tipo": "xml_concluida"})
            if pendente:
                self.iniciar_importacao_xml(pendente)

    def iniciar_migracao(self, codigos, novo_grupo, grupo_atual="Filtrado") -> dict:
        if self._migracao_em_andamento:
            return {"ok": False, "mensagem": "Migração já em andamento."}
        if esta_rodando():
            return {"ok": False, "mensagem": "Pare o robô antes de migrar itens."}
        config = db.carregar_configuracoes()
        if not config or not config.get("link"):
            return {"ok": False, "mensagem": "Configure o ERP primeiro."}
        threading.Thread(
            target=self._executar_migracao,
            args=(config, codigos, novo_grupo, grupo_atual),
            daemon=True,
        ).start()
        return {"ok": True, "mensagem": f"Migração de {len(codigos)} item(ns) iniciada."}

    def _executar_migracao(self, config, codigos, novo_grupo, grupo_atual):
        self._migracao_em_andamento = True
        sessao = log_service.iniciar_sessao(
            origem="MIGRACAO",
            descricao=f"Migração de {len(codigos)} itens para {novo_grupo}",
        )

        def log_ui(msg):
            log_service.registrar_log(msg, origem="MIGRACAO", sessao_id=sessao)
            self._emit({"tipo": "log", "mensagem": msg})

        try:
            modulo_migracao.iniciar_migracao_lote(
                config, codigos, novo_grupo, log_ui, grupo_atual,
            )
        except Exception as exc:
            log_ui(f"ERRO: {exc}")
        finally:
            log_service.finalizar_sessao(sessao, origem="MIGRACAO", status="SUCESSO")
            self._migracao_em_andamento = False
            self._emit({"tipo": "itens_atualizados"})


robo_service = RoboService()
