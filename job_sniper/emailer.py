"""Digesto diario por email: HTML con las mejores ofertas del día.

Separación de responsabilidades:
  * `build_email_html`  → construye el HTML (función pura, testeable sin SMTP).
  * `send_email`        → solo se ocupa de transportar ese HTML por Gmail SMTP.
"""

from __future__ import annotations

import html
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Sequence

from .config import Settings
from .logging_utils import get_logger
from .models import JobOffer

logger = get_logger("emailer")

SMTP_HOST = "smtp.gmail.com"  # Gmail con contraseña de aplicación
SMTP_PORT = 465

# Puntuación a partir de la cual el «score» se pinta de verde.
_HIGH_SCORE = 75
_COLOR_HIGH = "#27ae60"
_COLOR_MEDIUM = "#e67e22"

# Únicas etiquetas HTML que se conservan de la salida del LLM.
_ALL_TAGS_PATTERN = re.compile(r"<[^>]*>")
_ALLOWED_TAG_PATTERN = re.compile(r"</?\s*(ul|li)\s*>", re.IGNORECASE)


def sanitise_bullets_html(raw: str) -> str:
    """Convierte la salida del LLM en HTML seguro para el email.

    Proceso en tres pasos:
      1. Se eliminan todas las etiquetas salvo `<ul>`/`<li>` (se guardan como token).
      2. Se escapa el texto restante (`<`, `>`, `&` → entidades).
      3. Se restauran las etiquetas permitidas ya normalizadas.

    Así un `<script>` o cualquier otra etiqueta inyectada desaparece (queda su
    texto) y no se renderiza en el cliente de correo.
    """
    if not raw:
        return ""

    tokens = []

    def _procesar(match: "re.Match[str]") -> str:
        etiqueta = match.group(0)
        if not _ALLOWED_TAG_PATTERN.fullmatch(etiqueta):
            return ""  # etiqueta no permitida: se elimina, el texto se conserva
        tokens.append(re.sub(r"\s+", "", etiqueta).lower())
        return f"\x00{len(tokens) - 1}\x00"

    texto = _ALL_TAGS_PATTERN.sub(_procesar, raw)
    escapado = html.escape(texto, quote=False)

    for indice, etiqueta in enumerate(tokens):
        escapado = escapado.replace(f"\x00{indice}\x00", etiqueta)
    return escapado


def _score_color(score: int) -> str:
    """Verde para ≥75, naranja para el resto (referencia visual rápida)."""
    return _COLOR_HIGH if score >= _HIGH_SCORE else _COLOR_MEDIUM


def _build_tailored_row(job: JobOffer) -> str:
    """Fila adicional bajo la oferta con las viñetas personalizadas."""
    return f"""
        <tr>
            <td colspan="6" style="padding:8px 10px 14px 10px;background:#f0fff4;border-bottom:2px solid {_COLOR_HIGH};">
                <b style="color:{_COLOR_HIGH};">✨ Tailored CV Bullets for this role:</b><br>
                <div style="font-size:13px;color:#2c3e50;margin-top:4px;">{sanitise_bullets_html(job.tailored_bullets)}</div>
            </td>
        </tr>"""


def build_email_html(offers: Sequence[JobOffer], settings: Settings) -> str:
    """Construye el cuerpo HTML del digesto (sin enviar nada).

    Todo texto proveniente de la oferta se escapa con `html.escape` para que
    un título o razón con `<`/`&` no rompa la tabla.
    """
    filas = ""
    for job in offers:
        color = _score_color(job.score)
        filas += f"""
        <tr>
            <td style="padding:10px;border-bottom:1px solid #eee;font-weight:bold;color:{color};">{job.score}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{html.escape(job.title)}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{html.escape(job.company)}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{html.escape(job.yoe)}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;font-size:13px;color:#555;">{html.escape(job.reason)}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">
                <a href="{html.escape(job.job_url)}" style="background:#0a66c2;color:white;padding:6px 12px;text-decoration:none;border-radius:4px;font-size:12px;">Apply</a>
            </td>
        </tr>{_build_tailored_row(job) if job.has_tailored_bullets else ''}"""

    nota_sheets = ""
    if settings.google_sheet_id:
        enlace = f"https://docs.google.com/spreadsheets/d/{html.escape(settings.google_sheet_id)}"
        nota_sheets = (
            f'<p style="font-size:12px;color:#555;">📊 All jobs exported to '
            f'<a href="{enlace}">your tracking sheet</a> — mark Applied when done!</p>'
        )

    return f"""<html><body style="font-family:Arial,sans-serif;max-width:900px;margin:auto;">
        <h2 style="color:#2c3e50;">🎯 Búsqueda de Empleo Diaria — Resultados</h2>
        <p><b>{len(offers)}</b> ofertas puntuadas ≥{settings.score_threshold} hoy · Línea A (Mid/Senior .NET) + Línea B (Junior/Prácticas) · ✨ = bullets CV personalizados</p>
        {nota_sheets}
        <table style="border-collapse:collapse;width:100%;">
            <tr style="background:#f8f9fa;">
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Score</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Title</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Company</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">YoE</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Why a Match</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Link</th>
            </tr>{filas}
        </table>
        <p style="margin-top:20px;font-size:11px;color:#aaa;">Powered by LinkedIn Job Sniper · LangChain · Groq</p>
    </body></html>"""


def send_email(offers: Sequence[JobOffer], settings: Settings) -> bool:
    """Envía el digesto por SMTP SSL. Nunca lanza excepción (degrada y sigue).

    Returns:
        `True` si el email se envió; `False` si no había ofertas, faltaba
        configuración o el envío falló.
    """
    if not offers:
        logger.info("Sin ofertas por encima del umbral: no se envía email")
        return False
    if not settings.email_configured:
        logger.warning("Faltan EMAIL_SENDER/EMAIL_PASSWORD/EMAIL_RECEIVER: no se envía email")
        return False

    asunto = (
        f"[Búsqueda Empleo] {len(offers)} ofertas .NET Madrid - "
        f"{datetime.now():%d/%m/%Y}"
    )

    mensaje = MIMEMultipart()
    mensaje["Subject"] = asunto
    mensaje["From"] = settings.email_sender
    mensaje["To"] = settings.email_receiver
    mensaje.attach(MIMEText(build_email_html(offers, settings), "html"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as servidor:
            servidor.login(settings.email_sender, settings.email_password)
            servidor.send_message(mensaje)
        logger.info("Email enviado a %s (%d ofertas)", settings.email_receiver, len(offers))
        return True
    except Exception as exc:  # noqa: BLE001 — SMTP puede fallar de muchas formas
        logger.error("Falló el envío del email: %s", exc)
        return False
