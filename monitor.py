#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitor de Entradas e Inventario - Castelo de Soutomaior
=======================================================
Vigila diariamente la aparición de nuevas entradas o apartados a la venta
en tickets.castelodesoutomaior.com y notifica novedades por Telegram.
"""

import os
import sys
import html
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Set, Any
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

# ==============================================================================
# CONFIGURACIÓN Y ENTORNO
# ==============================================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TARGET_URL = os.getenv("TARGET_URL", "https://tickets.castelodesoutomaior.com/")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DATA_FILE = BASE_DIR / "estado_anterior.json"
LOG_FILE = BASE_DIR / "monitor.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("CasteloMonitor")

CLORIAN_TOKEN_URL = "https://services.clorian.com/user/api/oauth/token?secretKey=thecastelodesoutomaiorfrontendoftomorrow"
CLORIAN_PRODUCTS_GENERAL = "https://services.clorian.com/catalog/salesGroups/1329/products?hiddenOnline=false&includeEventsWithoutAvailability=true&isGeneral=true&relations=productTagIds&subProduct=false"
CLORIAN_PRODUCTS_EVENTS = "https://services.clorian.com/catalog/salesGroups/1329/products?hiddenOnline=false&includeEventsWithoutAvailability=true&isGeneral=false&relations=events%2CproductTagIds&subProduct=false"


# ==============================================================================
# SESIÓN HTTP RESILIENTE
# ==============================================================================

def create_session() -> requests.Session:
    """Crea una sesión HTTP con política de reintentos exponenciales."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin": "https://tickets.castelodesoutomaior.com",
        "Referer": "https://tickets.castelodesoutomaior.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9",
    })
    return session


# ==============================================================================
# EXTRACCIÓN DE ENTRADAS (API OFICIAL)
# ==============================================================================

def fetch_product_list(session: requests.Session, url: str, headers: Dict[str, str], label: str) -> List[Dict[str, Any]]:
    """
    Descarga un catálogo y exige que sea una lista. Una respuesta inesperada se
    trata como error: tomarla como vacía haría creer que se retiraron entradas.
    """
    resp = session.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Respuesta inesperada del catálogo {label}: {str(data)[:200]}")
    return data


def fetch_products_via_api() -> List[Dict[str, Any]]:
    """
    Extrae los productos consultando el backend oficial de venta (Clorian).
    Es instantáneo, fiable y no requiere renderizar JavaScript pesado.
    """
    session = create_session()
    logger.info("Obteniendo token de autenticación del catálogo...")

    resp = session.post(CLORIAN_TOKEN_URL, timeout=20)
    resp.raise_for_status()
    token_data = resp.json()

    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("No se pudo obtener el token de acceso de Clorian.")

    pos_id = str((token_data.get("posAllowed") or ["3255"])[0])
    headers = {
        "Authorization": f"Bearer {access_token}",
        "pos": pos_id,
        "Accept": "application/json",
    }

    logger.info(f"Consultando catálogo general y de eventos (POS {pos_id})...")
    general_items = fetch_product_list(session, CLORIAN_PRODUCTS_GENERAL, headers, "general")
    event_items = fetch_product_list(session, CLORIAN_PRODUCTS_EVENTS, headers, "eventos")

    raw_items = general_items + event_items
    cleaned = []
    seen_ids = set()

    for item in raw_items:
        prod_id = item.get("productId") or item.get("id")
        nombre = (item.get("name") or "").strip()
        if not nombre or prod_id in seen_ids:
            continue
        seen_ids.add(prod_id)

        cleaned.append({
            "id": prod_id,
            "name": nombre,
            "short_name": item.get("shortName"),
            "price": item.get("basePrice"),
            "slug": item.get("slug"),
            "status": item.get("status", "enabled"),
        })

    logger.info(f"Catálogo obtenido vía API: {len(cleaned)} entradas/apartados encontrados.")
    return cleaned


# ==============================================================================
# PERSISTENCIA DE ESTADO
# ==============================================================================

def load_previous_state(filepath: Path) -> Dict[str, Any]:
    """Carga el estado del archivo JSON previo."""
    if not filepath.exists():
        return {}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error al leer {filepath}: {e}. Se reiniciará el estado.")
        return {}


def save_current_state(filepath: Path, items: List[Dict[str, Any]]) -> None:
    """Guarda atómicamente el estado actual."""
    temp_file = filepath.with_suffix(".tmp")
    payload = {
        "last_checked": datetime.now().isoformat(),
        "total_items": len(items),
        "items": items,
    }
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    temp_file.replace(filepath)
    logger.info(f"Estado actualizado en {filepath} con {len(items)} entradas.")


# ==============================================================================
# SISTEMA DE NOTIFICACIONES TELEGRAM
# ==============================================================================

def send_telegram_message(message: str) -> bool:
    """
    Envía un mensaje formateado a Telegram dividiéndolo si supera 4000 caracteres.
    Devuelve True solo si todos los fragmentos se enviaron correctamente.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Credenciales de Telegram no configuradas en el archivo .env.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    MAX_LENGTH = 4000

    chunks = []
    if len(message) <= MAX_LENGTH:
        chunks = [message]
    else:
        current_chunk = ""
        for line in message.split("\n"):
            if len(current_chunk) + len(line) + 1 > MAX_LENGTH:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk = f"{current_chunk}\n{line}" if current_chunk else line
        if current_chunk:
            chunks.append(current_chunk)

    ok = True
    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if not resp.ok:
                # Telegram explica el motivo (p. ej. HTML mal formado) en el cuerpo
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            logger.info("Notificación enviada a Telegram con éxito.")
        except Exception as e:
            logger.error(f"Error al enviar el mensaje a Telegram: {e}")
            ok = False
    return ok


def format_price(price: Any) -> str:
    """Formatea el precio como '7.50€'; tolera valores numéricos en texto."""
    try:
        return f"{float(price):.2f}€"
    except (TypeError, ValueError):
        return html.escape(f"{price}€")


def notify_new_items(new_items: List[Dict[str, Any]]) -> bool:
    """Construye y envía la alerta de nuevos apartados o entradas."""
    lineas = []
    for item in new_items:
        nombre = html.escape(item["name"])
        precio = f" | <b>{format_price(item['price'])}</b>" if item.get("price") is not None else ""
        lineas.append(f"🎟 <b>{nombre}</b>{precio}")

    lista_texto = "\n".join(lineas)

    mensaje = (
        "🚨 <b>¡NUEVAS ENTRADAS DETECTADAS!</b> 🚨\n"
        "🏰 <i>Castelo de Soutomaior</i>\n\n"
        f"Se han identificado <b>{len(new_items)}</b> nuevas entradas o apartados a la venta:\n\n"
        f"{lista_texto}\n\n"
        f"🔗 <a href='{TARGET_URL}'>Comprar / Ver Entradas Oficiales</a>\n"
        f"📅 <i>Fecha de detección: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</i>"
    )
    return send_telegram_message(mensaje)


def notify_first_run(items: List[Dict[str, Any]]) -> bool:
    """Envía un mensaje de confirmación al iniciar por primera vez."""
    lineas = [
        f"• {html.escape(item['name'])}"
        + (f" ({format_price(item['price'])})" if item.get("price") is not None else "")
        for item in items
    ]
    lista_texto = "\n".join(lineas)

    mensaje = (
        "🟢 <b>Monitor de Entradas Activado</b>\n"
        "🏰 <i>Castelo de Soutomaior</i>\n\n"
        f"Se ha inicializado la vigilancia. Actualmente hay <b>{len(items)}</b> entradas registradas:\n\n"
        f"{lista_texto}\n\n"
        "Te avisaré automáticamente si aparece cualquier nueva entrada o apartado a la venta."
    )
    return send_telegram_message(mensaje)


def normalize_name(text: str) -> str:
    """Normaliza espacios, elimina caracteres invisibles (\xa0) y pasa a minúsculas."""
    if not text:
        return ""
    return " ".join(text.replace("\xa0", " ").split()).lower()


# ==============================================================================
# EJECUCIÓN PRINCIPAL
# ==============================================================================

def run() -> int:
    """Ejecuta una comprobación. Devuelve el código de salida (0 = OK, 1 = error)."""
    logger.info("=================================================================")
    logger.info("Iniciando monitorización: Castelo de Soutomaior")
    logger.info("=================================================================")

    try:
        # 1. Extraer inventario actual
        current_items = fetch_products_via_api()
        if not current_items:
            logger.warning("No se obtuvieron elementos en el inventario actual.")
            return 0

        # Mapeo normalizado
        current_keys_map = {}
        for item in current_items:
            key = str(item.get("id")) if item.get("id") else normalize_name(item.get("name", ""))
            current_keys_map[key] = item

        # 2. Cargar estado previo
        previous_data = load_previous_state(DATA_FILE)
        is_first_run = "items" not in previous_data

        if is_first_run:
            logger.info("Primera ejecución detectada. Guardando estado base inicial...")
            # Se guarda aunque falle el aviso: perder la confirmación no oculta entradas nuevas
            sent = notify_first_run(current_items)
            save_current_state(DATA_FILE, current_items)
            return 0 if sent else 1

        previous_items = previous_data.get("items", [])
        previous_keys = set()
        for item in previous_items:
            key = str(item.get("id")) if item.get("id") else normalize_name(item.get("name", ""))
            previous_keys.add(key)

        # 3. Comparación (Diferencia de conjuntos)
        new_keys = set(current_keys_map.keys()) - previous_keys
        has_catalog_changed = False

        if new_keys:
            logger.info(f"¡Atención! Se han encontrado {len(new_keys)} elementos nuevos.")
            new_items_list = [current_keys_map[k] for k in new_keys]
            if not notify_new_items(new_items_list):
                # Sin guardar estado, la próxima ejecución reintentará el aviso
                logger.error("No se pudo avisar por Telegram; el estado no se actualiza.")
                return 1
            has_catalog_changed = True
        elif set(current_keys_map.keys()) != previous_keys or len(current_items) != len(previous_items):
            logger.info("El catálogo ha cambiado (entradas retiradas o modificadas).")
            has_catalog_changed = True
        else:
            logger.info("Comprobación finalizada: No hay entradas nuevas hoy.")

        # 4. Actualizar estado únicamente si hubo cambios reales en el catálogo
        if has_catalog_changed:
            save_current_state(DATA_FILE, current_items)
        return 0

    except Exception as exc:
        logger.critical(f"Error crítico en el proceso de monitorización: {exc}", exc_info=True)
        return 1
    finally:
        logger.info("Fin de la ejecución del monitor.\n")


if __name__ == "__main__":
    sys.exit(run())
