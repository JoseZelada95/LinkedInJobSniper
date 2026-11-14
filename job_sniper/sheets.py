"""Exportación de ofertas a la hoja de cálculo de seguimiento (Google Sheets).

La hoja funciona como CRM manual: el candidato marca la columna «Estado»
cuando envía la solicitud. Se conserva el mismo orden de columnas (A:J) que
la plantilla original para no romper hojas ya en uso.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import List, Optional, Sequence

from .config import Settings
from .logging_utils import get_logger
from .models import JobOffer

logger = get_logger("sheets")

# Rango que abarcan las 10 columnas de la plantilla.
SPREADSHEET_RANGE = "A:J"

# Extrae cifras tipo «35.000 €», «35 000 eur» o «35k» → se guarda como «35k».
_SALARY_PATTERN = re.compile(r"(\d{2,3}[.\s]?\d{3})\s*(?:€|eur|k)")

# Cabecera de la columna «URL Oferta» (columna G). Se descarta al leer el
# historial porque no es una oferta.
_URL_COLUMN_HEADER = "url oferta"

# Valores fijos de la plantilla.
_FIXED_LOCATION = "Madrid / Remote"
_NEW_STATUS = "Nuevo"
_USED_CV = "Drive CV"
_REASON_MAX_LENGTH = 200  # la columna «Notas» no debe desbordar


def extract_salary(description: str) -> str:
    """Extrae la mención de salario de una descripción, o `""` si no aparece.

    Se normaliza a miles con sufijo «k»: «35.000 €» o «40 000 eur» → «35k»/«40k»,
    que es como se lee de un vistazo en la hoja de seguimiento.
    """
    match = _SALARY_PATTERN.search((description or "").lower())
    if not match:
        return ""

    cifra = re.sub(r"[.\s]", "", match.group(1))  # "35.000" → "35000"
    return f"{int(cifra) // 1000}k"


def build_rows(offers: Sequence[JobOffer], today: Optional[str] = None) -> List[List[str]]:
    """Convierte ofertas en las filas de la hoja (10 columnas, mismo orden).

    Función pura (sin API) → se puede testear sin credenciales de Google.
    """
    fecha = today or datetime.now().strftime("%d/%m/%Y")
    return [
        [
            fecha,                                   # Fecha
            offer.company,                           # Empresa
            offer.title,                             # Puesto
            _FIXED_LOCATION,                         # Ubicación
            f"{offer.score}%",                       # Match %
            extract_salary(offer.description),       # Salario
            offer.job_url,                           # URL Oferta
            _NEW_STATUS,                             # Estado
            offer.reason[:_REASON_MAX_LENGTH],       # Notas
            _USED_CV,                                # CV Usado
        ]
        for offer in offers
    ]


def get_sheets_service(settings: Settings):
    """Crea el cliente de la API de Google Sheets desde `gcp_credentials_json`.

    Los imports de Google son perezosos a propósito: solo se cargan cuando se
    usa la exportación (ahorra arranque y evita fallos si no están instalados).
    Devuelve `None` si no hay credenciales.
    """
    if not settings.gcp_credentials_json:
        logger.info("Sin GCP_CREDENTIALS_JSON: se omite la exportación a Sheets")
        return None

    try:
        credentials_dict = json.loads(settings.gcp_credentials_json)
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return build("sheets", "v4", credentials=credentials)
    except ValueError as exc:  # el JSON está mal formado → aviso claro
        logger.error("GCP_CREDENTIALS_JSON no es un JSON válido: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 — un fallo de Google no debe tumbar el informe
        logger.error("No se pudo inicializar el cliente de Sheets: %s", exc)
        return None


def fetch_sent_job_urls(settings: Settings) -> List[str]:
    """Lee las URLs ya registradas en la hoja (columna «URL Oferta», G).

    Se usa como historial persistente entre ejecuciones de GitHub Actions, donde
    el fichero local `sent_jobs.json` no sobrevive. Devuelve `[]` (y no lanza)
    si Sheets no está configurado o la API falla.
    """
    if not settings.google_sheet_id:
        return []

    service = get_sheets_service(settings)
    if not service:
        return []

    try:
        respuesta = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=settings.google_sheet_id, range="G:G")
            .execute()
        )
        urls = []
        for indice, fila in enumerate(respuesta.get("values", [])):
            valor = str(fila[0]).strip() if fila else ""
            if not valor:
                continue
            if indice == 0 and valor.casefold() == _URL_COLUMN_HEADER:
                continue
            urls.append(valor)
        logger.info("Sheets: %d URLs recuperadas del historial", len(urls))
        return urls
    except Exception as exc:  # noqa: BLE001 — degradar sin abortar el informe
        logger.warning("No se pudo leer el historial de Sheets: %s", exc)
        return []


def export_to_sheets(offers: Sequence[JobOffer], settings: Settings) -> None:
    """Añade las ofertas al final de la hoja de seguimiento.

    Nunca lanza excepción: si Google falla, el email se envía igualmente.
    """
    if not offers:
        logger.info("Sin ofertas que exportar a Sheets")
        return
    if not settings.google_sheet_id:
        logger.info("Sin GOOGLE_SHEET_ID: se omite la exportación a Sheets")
        return

    service = get_sheets_service(settings)
    if not service:
        return

    try:
        rows = build_rows(offers)
        service.spreadsheets().values().append(
            spreadsheetId=settings.google_sheet_id,
            range=SPREADSHEET_RANGE,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
        logger.info("✅ Exportadas %d ofertas a Google Sheets", len(rows))
    except Exception as exc:  # noqa: BLE001 — degradar sin abortar el ciclo
        logger.error("❌ Falló la exportación a Sheets: %s", exc)
