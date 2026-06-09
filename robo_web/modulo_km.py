import re
import time

import database_setup as db
from robo_web.utils import converter_modelo_km_para_regex, limpar_km_extraido


def _carregar_modelos_km():
    try:
        modelos = db.obter_modelos_km()
        if modelos:
            return modelos
    except Exception:
        pass
    return []


def extrair_km_da_observacao(memoria_obs, log=None):
    obs = str(memoria_obs or '')
    if not obs.strip():
        if log:
            log('   ⚠️ Observação da nota vazia no ERP — impossível extrair KM.')
        return None, None

    modelos = _carregar_modelos_km()
    if not modelos:
        if log:
            log('   ⚠️ Nenhum modelo de KM configurado em Parâmetros ERP.')
        return None, None

    for modelo in modelos:
        padrao = converter_modelo_km_para_regex(modelo)
        if not padrao:
            continue
        match = re.search(padrao, obs)
        if match:
            km = limpar_km_extraido(match.group(1))
            if km:
                return km, f"máscara '{modelo}'"

    if log:
        log(
            '   ⚠️ KM não encontrado: o texto da observação não corresponde '
            f'ao modelo configurado. Modelos: {modelos}'
        )
    return None, None


def processar_km(page, log, idx, memoria_obs, km_painel=None):
    log(f'   -> Verificando KM para o Item {idx + 1}...')

    campo_km = page.locator(
        f'input[name^="formCad:tableItemNota:{idx}:j_idt"][size="10"]'
    )
    if campo_km.count() == 0:
        campo_km = page.locator(
            f'input[name="formCad:tableItemNota:{idx}:j_idt143"]'
        )

    if campo_km.count() == 0 or not campo_km.first.is_visible():
        log('   ⚠️ Campo de KM não encontrado na tela.')
        return False

    km_painel = re.sub(r'\D', '', str(km_painel or ''))
    if km_painel:
        log(f'   -> KM informado no painel do robô: {km_painel}')
        campo_km.first.click()
        campo_km.first.clear()
        campo_km.first.fill(km_painel)
        campo_km.first.press('Tab')
        time.sleep(1)
        return True

    valor_atual = campo_km.first.input_value().strip()
    if valor_atual and valor_atual != '0':
        log(
            f'   -> KM já preenchido pelo ERP ao validar o veículo: {valor_atual} '
            '(não usa a observação)'
        )
        return True

    km_encontrado, origem = extrair_km_da_observacao(memoria_obs, log=log)
    if km_encontrado:
        log(f'   -> 🛣️ KM extraído da observação via {origem}: {km_encontrado}')
        campo_km.first.click()
        campo_km.first.clear()
        campo_km.first.fill(km_encontrado)
        campo_km.first.press('Tab')
        time.sleep(1)
        return True

    return False
