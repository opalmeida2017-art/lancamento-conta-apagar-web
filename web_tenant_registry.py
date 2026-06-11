"""Registro de instâncias web (uma transportadora = um banco PostgreSQL)."""
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = Path(os.getenv("NFE_TENANTS_FILE", WEB_ROOT / "tenants.json"))


def slugify(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "-", texto.lower()).strip("-")
    return (texto[:40] or "empresa").strip("-")


def db_name_for_slug(slug: str) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", slug.lower())
    nome = f"nfe_web_{base}"[:63]
    return nome


def load_registry() -> dict:
    if not REGISTRY_PATH.is_file():
        return {"tenants": [], "base_url": os.getenv("NFE_PUBLIC_BASE_URL", "")}
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"tenants": [], "base_url": ""}


def save_registry(data: dict):
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_tenants() -> list:
    return load_registry().get("tenants") or []


def get_tenant(slug: str):
    slug = str(slug or "").strip().lower()
    for item in list_tenants():
        if str(item.get("slug") or "").lower() == slug:
            return item
    return None


def tenant_exists(slug: str) -> bool:
    return get_tenant(slug) is not None


def public_url(slug: str, host: str = "", port: str = "") -> str:
    reg = load_registry()
    base = (reg.get("base_url") or os.getenv("NFE_PUBLIC_BASE_URL") or "").rstrip("/")
    if base:
        return f"{base}/t/{slug}/"
    host = host or os.getenv("NFE_PUBLIC_HOST", "localhost")
    port = port or os.getenv("NFE_WEB_PORT", "8090")
    if port in ("80", "443", ""):
        return f"http://{host}/t/{slug}/"
    return f"http://{host}:{port}/t/{slug}/"


def register_tenant(razao_social: str, slug=None, host: str = "", port: str = "") -> dict:
    razao = str(razao_social or "").strip()
    if not razao:
        raise ValueError("Informe o nome da transportadora.")
    slug = slugify(slug or razao)
    if tenant_exists(slug):
        raise ValueError(f"Já existe instância web com slug '{slug}'.")

    pg_db = db_name_for_slug(slug)
    data = load_registry()
    item = {
        "slug": slug,
        "razao_social": razao,
        "pg_database": pg_db,
        "url": public_url(slug, host, port),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ativo": True,
    }
    data.setdefault("tenants", []).append(item)
    if host and not data.get("base_url"):
        data["base_url"] = public_url("", host, port).rsplit("/t/", 1)[0]
    save_registry(data)
    return item
