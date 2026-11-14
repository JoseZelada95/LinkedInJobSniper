"""Carga del CV del candidato.

Dos fuentes, en este orden:
  1. `RESUME_TEXT` → texto plano en el entorno (la más simple, la recomendada).
  2. Google Drive  → fallback: se descarga el PDF y se extrae su texto.

La segunda opción solo se intenta si la primera no está configurada, para
no gastar llamadas a la API de Drive sin motivo.
"""

from __future__ import annotations

import io
from dataclasses import replace
from typing import Optional

from .config import Settings
from .logging_utils import get_logger

logger = get_logger("resume")

# Alcance mínimo necesario: leer el fichero, nada más.
_DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


def load_resume_from_google_drive(settings: Settings) -> Optional[str]:
    """Descarga el CV de Google Drive y devuelve su texto, o `None` si falla.

    Los imports de Google/PyPDF son perezosos: solo se ejecutan si realmente
    hay credenciales y un ID de fichero configurados.
    """
    if not settings.gcp_credentials_json or not settings.resume_file_id:
        return None

    logger.info("Cargando CV desde Google Drive…")
    try:
        import json

        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
        from pypdf import PdfReader

        credentials = service_account.Credentials.from_service_account_info(
            json.loads(settings.gcp_credentials_json),
            scopes=[_DRIVE_READONLY_SCOPE],
        )
        servicio = build("drive", "v3", credentials=credentials)
        solicitud = servicio.files().get_media(fileId=settings.resume_file_id)

        buffer = io.BytesIO()
        descargador = MediaIoBaseDownload(buffer, solicitud)
        completado = False
        while not completado:
            _, completado = descargador.next_chunk()
        buffer.seek(0)

        # Une el texto de todas las páginas del PDF.
        return "\n".join(pagina.extract_text() or "" for pagina in PdfReader(buffer).pages)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de Drive degrada a "sin CV"
        logger.error("No se pudo cargar el CV de Google Drive: %s", exc)
        return None


def resolve_resume(settings: Settings) -> Settings:
    """Devuelve `Settings` con el CV resuelto (intentando Drive si hace falta).

    `Settings` es inmutable, así que se usa `dataclasses.replace` para crear
    una copia con el campo `resume_text` completado.
    """
    if settings.resume_text:
        return settings  # ya está en el entorno: no se toca Google Drive

    resume_from_drive = load_resume_from_google_drive(settings)
    if not resume_from_drive:
        return settings

    return replace(settings, resume_text=resume_from_drive)
