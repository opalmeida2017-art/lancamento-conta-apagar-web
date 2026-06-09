"""Inicializa o sistema web autônomo (sem dependência do projeto desktop)."""
import os
import sys
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[2]
_root = str(WEB_ROOT)

if _root not in sys.path:
    sys.path.insert(0, _root)

os.chdir(_root)

import database_setup as db  # noqa: E402
import log_service  # noqa: E402
