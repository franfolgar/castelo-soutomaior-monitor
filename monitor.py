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
import json
import logging
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
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
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
# EXTRACCIÓN DE ENTRADAS (API OFICIAL & FALLBACK PLAYWRIGHT)
# ==============================================================================

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

    pos_id = str(token_data.get("posAllowed", ["3255"])[0])
    headers = {
        "Authorization": f"Bearer {access_token}",
        "pos": pos_id,
        "Accept": "application/json",
    }

    logger.info(f"Consultando catálogo general y de eventos (POS {pos_id})...")
    res_gen = session.get(CLORIAN_PRODUCTS_GENERAL, headers=headers, timeout=20)
    res_gen.raise_for_status()
    general_items = res_gen.json() if isinstance(res_gen.json(), list) else []

    res_events = session.get(CLORIAN_PRODUCTS_EVENTS, headers=headers, timeout=20)
    res_events.raise_for_status()
    event_items = res_events.json() if isinstance(res_events.json(), list) else []

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


def fetch_products_via_playwright() -> List[Dict[str, Any]]:
    """
    Fallback usando Playwright para renderizar la página SPA si la API cambia.
    """
    from playwright.sync_api import sync_playwright

    logger.warning("Ejecutando fallback mediante Playwright (navegador headless)...")
    items = []

    with sync_playwright() as p:
        browser = None
        for channel in ["msedge", "chrome", None]:
            try:
                if channel:
                    browser = p.chromium.launch(headless=True, channel=channel)
                else:
                    browser = p.chromium.launch(headless=True)
                break
            except Exception:
                continue

        if not browser:
            raise RuntimeError("No se pudo iniciar ningún navegador Chromium para Playwright.")

        page = browser.new_page()
        page.goto(TARGET_URL, wait_until="networkidle", timeout=45000)

        # Extraer títulos de entradas visibles
        cards = page.locator("h2, h3, .title, [class*='title']").all_text_contents()
        browser.close()

        for c in cards:
            c_clean = c.strip()
            # Filtrar textos no relevantes
            if len(c_clean) > 5 and not any(skip in c_clean.lower() for skip in ["cookie", "politica", "aviso", "deputacion"]):
                items.append({
                    "id": None,
                    "name": c_clean,
                    "price": None,
                    "slug": None,
                    "status": "enabled",
                })

    logger.info(f"Catálogo obtenido vía Playwright: {len(items)} entradas detectadas.")
    return items


def get_current_inventory() -> List[Dict[str, Any]]:
    """Intenta primero por API directa; si falla, recurre a Playwright."""
    try:
        return fetch_products_via_api()
    except Exception as e:
        logger.error(f"Error consultando API directa: {e}. Probando fallback Playwright...")
        return fetch_products_via_playwright()


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

def send_telegram_message(message: str) -> None:
    """Envía un mensaje formateado a Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Credenciales de Telegram no configuradas en el archivo .env.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Notificación enviada a Telegram con éxito.")
    except Exception as e:
        logger.error(f"Error al conectar con la API de Telegram: {e}")


def notify_new_items(new_items: List[Dict[str, Any]]) -> None:
    """Construye y envía la alerta de nuevos apartados o entradas."""
    lineas = []
    for item in new_items:
        nombre = item["name"]
        precio = f" | <b>{item['price']:.2f}€</b>" if item.get("price") is not None else ""
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
    send_telegram_message(mensaje)


def notify_first_run(items: List[Dict[str, Any]]) -> None:
    """Envía un mensaje de confirmación al iniciar por primera vez."""
    lineas = [f"• {item['name']}" + (f" ({item['price']:.2f}€)" if item.get('price') else "") for item in items]
    lista_texto = "\n".join(lineas)

    mensaje = (
        "🟢 <b>Monitor de Entradas Activado</b>\n"
        "🏰 <i>Castelo de Soutomaior</i>\n\n"
        f"Se ha inicializado la vigilancia diaria. Actualmente hay <b>{len(items)}</b> entradas registradas:\n\n"
        f"{lista_texto}\n\n"
        "Te avisaré automáticamente cada día si aparece cualquier nueva entrada o apartado a la venta."
    )
    send_telegram_message(mensaje)


# ==============================================================================
# EJECUCIÓN PRINCIPAL
# ==============================================================================

def run():
    logger.info("=================================================================")
    logger.info("Iniciando monitorización: Castelo de Soutomaior")
    logger.info("=================================================================")

    try:
        # 1. Extraer inventario actual
        current_items = get_current_inventory()
        if not current_items:
            logger.warning("No se obtuvieron elementos en el inventario actual.")
            return

        # Conjunto normalizado de nombres actuales
        current_names_map = {item["name"].strip().lower(): item for item in current_items}

        # 2. Cargar estado previo
        previous_data = load_previous_state(DATA_FILE)
        is_first_run = "items" not in previous_data

        if is_first_run:
            logger.info("Primera ejecución detectada. Guardando estado base inicial...")
            notify_first_run(current_items)
            save_current_state(DATA_FILE, current_items)
            return

        previous_items = previous_data.get("items", [])
        previous_names = {item["name"].strip().lower() for item in previous_items}

        # 3. Comparación (Diferencia de conjuntos)
        new_names = set(current_names_map.keys()) - previous_names

        if new_names:
            logger.info(f"¡Atención! Se han encontrado {len(new_names)} elementos nuevos.")
            new_items_list = [current_names_map[name] for name in new_names]
            notify_new_items(new_items_list)
        else:
            logger.info("Comprobación finalizada: No hay entradas nuevas hoy.")

        # 4. Actualizar estado
        save_current_state(DATA_FILE, current_items)

    except Exception as exc:
        logger.critical(f"Error crítico en el proceso de monitorización: {exc}", exc_info=True)
    finally:
        logger.info("Fin de la ejecución del monitor.\n")


if __name__ == "__main__":
    run()
