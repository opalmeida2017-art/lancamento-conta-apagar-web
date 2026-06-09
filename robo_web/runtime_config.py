import os
import sys


def em_execucao_empacotada():
    """Retorna True quando o app está rodando como executável (PyInstaller)."""
    return getattr(sys, "frozen", False)


def usar_headless():
    """
    Define modo do navegador:
    - Terminal/python: visível (headless=False)
    - Executável (.exe): oculto (headless=True)
    """
    valor_forcado = os.getenv("ROBO_HEADLESS")
    if valor_forcado is not None:
        return str(valor_forcado).strip().lower() in {"1", "true", "sim", "yes", "on"}
    return em_execucao_empacotada()
