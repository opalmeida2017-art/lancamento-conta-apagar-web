import threading
import time
from datetime import datetime

import agendamento_email
import relatorio_suporte

import backend.app.bootstrap as boot

db = boot.db
log_service = boot.log_service

from robo_web import modulo_frota, modulo_item_sync  # noqa: E402

HORARIOS_SUPORTE = ("08:00", "12:00", "15:00", "18:00")
INTERVALO_SYNC_SEG = 3600
INTERVALO_EMAIL_SEG = 60
INTERVALO_SUPORTE_SEG = 30


class BackgroundService:
    def __init__(self):
        self._lock = threading.Lock()
        self._iniciado = False
        self._chaves_suporte = set()

    def iniciar(self):
        with self._lock:
            if self._iniciado:
                return
            self._iniciado = True
        threading.Thread(target=self._loop_sync, daemon=True).start()
        threading.Thread(target=self._loop_email, daemon=True).start()
        threading.Thread(target=self._loop_suporte, daemon=True).start()

    def _loop_sync(self):
        time.sleep(5)
        while True:
            try:
                cfg = db.carregar_configuracoes() or {}
                if cfg.get("link"):
                    modulo_frota.baixar_e_importar_frota()
                    modulo_item_sync.baixar_e_importar_itens()
            except Exception as exc:
                log_service.registrar_log(
                    f"Falha na sincronização automática: {exc}",
                    origem="SYNC",
                    nivel="ERRO",
                )
            time.sleep(INTERVALO_SYNC_SEG)

    def _loop_email(self):
        time.sleep(10)
        while True:
            try:
                cfg = db.carregar_configuracoes() or {}
                if agendamento_email.agendamento_esta_vencido(cfg):
                    resultado = agendamento_email.enviar_relatorios_agendados(cfg)
                    proxima = resultado.get("proxima_execucao")
                    db.atualizar_agendamento_email(
                        tipo=cfg.get("agendamento_tipo"),
                        intervalo_horas=cfg.get("intervalo_horas") or 1,
                        proxima_execucao=agendamento_email.formatar_data_hora(proxima),
                        ultima_execucao=agendamento_email.formatar_data_hora(datetime.now()),
                    )
                    log_service.registrar_log(
                        f"E-mail agendado enviado: {resultado.get('total_notas')} notas, "
                        f"{resultado.get('total_itens')} itens.",
                        origem="EMAIL",
                    )
            except Exception as exc:
                log_service.registrar_log(
                    f"Falha no envio agendado: {exc}",
                    origem="EMAIL",
                    nivel="ERRO",
                )
            time.sleep(INTERVALO_EMAIL_SEG)

    def _chave_horario(self, agora, horario):
        return f"{agora.strftime('%Y-%m-%d')} {horario}"

    def _loop_suporte(self):
        time.sleep(15)
        while True:
            try:
                agora = datetime.now()
                hoje = agora.strftime("%Y-%m-%d")
                self._chaves_suporte = {c for c in self._chaves_suporte if c.startswith(hoje)}
                for horario in HORARIOS_SUPORTE:
                    hora, minuto = map(int, horario.split(":"))
                    slot = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
                    if agora < slot:
                        continue
                    chave = self._chave_horario(agora, horario)
                    if chave in self._chaves_suporte:
                        continue
                    if db.suporte_automatico_ja_enviado(chave):
                        self._chaves_suporte.add(chave)
                        continue
                    dt_ref = agora.strftime("%d/%m/%Y")
                    relatorio_suporte.enviar_log_suporte_por_email(
                        dt_ref, dt_ref, horario_envio=horario,
                    )
                    self._chaves_suporte.add(chave)
                    db.registrar_envio_suporte_automatico(chave, horario)
            except Exception as exc:
                log_service.registrar_log(
                    f"Falha no envio automático de suporte: {exc}",
                    origem="EMAIL_SUPORTE",
                    nivel="ERRO",
                )
            time.sleep(INTERVALO_SUPORTE_SEG)


background_service = BackgroundService()
