"""Registro y control diario de solicitudes (modo agente autónomo).

El agente **no** envía solicitudes automáticamente (LinkedIn Easy Apply
requiere automatización de navegador, fuera de alcance). Lo que hace es:

  1. Generar el PDF personalizado para la oferta.
  2. Registrar la intención en `applications.log` con estado `Prepared`.
  3. Respetar un límite diario para no convertirse en un spammer.

El fichero `applications.log` es estructurado (una línea por intento,
separado por `|`) para poder leerlo con scripts o contarlo a mano.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config import Settings
from .logging_utils import get_logger
from .models import JobOffer

logger = get_logger("applications")

# Estados posibles de una solicitud en el log.
STATUS_PREPARED = "Prepared"   # PDF listo → el candidato debe enviarlo a mano
STATUS_SUBMITTED = "Submitted" # marcado manualmente cuando se envía
STATUS_FAILED = "Failed"


def _today() -> str:
    """Fecha actual en formato ISO (clave de contaje diario)."""
    return datetime.now().strftime("%Y-%m-%d")


def applications_today_count(applications_log: Path) -> int:
    """Cuenta las solicitudes registradas hoy.

    Se busca la fecha ISO dentro de cada línea del log: es un formato simple y
    robusto frente a campos extra que se añadan en el futuro.
    """
    if not applications_log.exists():
        return 0

    today = _today()
    return sum(
        1
        for line in applications_log.read_text(encoding="utf-8").splitlines()
        if line.strip() and today in line
    )


def load_logged_job_urls(applications_log: Path) -> List[str]:
    """URLs de las solicitudes ya registradas por el agente autónomo.

    El informe diario las usa como historial: una oferta para la que ya se
    preparó un CV no debe volver a aparecer en el email.
    """
    if not applications_log.exists():
        return []

    urls: List[str] = []
    for linea in applications_log.read_text(encoding="utf-8").splitlines():
        campos = linea.split(" | ")
        if len(campos) >= 3 and campos[2].strip():
            urls.append(campos[2].strip())
    return urls


def log_application(
    job: JobOffer,
    cv_path: Optional[Path],
    status: str,
    applications_log: Path,
    message: str = "",
) -> None:
    """Escribe una línea de auditoría en `applications.log` (formato pipe-separated)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    campos = [
        job.title,
        job.company,
        job.job_url,
        str(job.score),
        str(cv_path) if cv_path else "",
        timestamp,
        status,
    ]
    if message:
        campos.append(message)

    applications_log.parent.mkdir(parents=True, exist_ok=True)
    with applications_log.open("a", encoding="utf-8") as fichero:
        fichero.write(" | ".join(campos) + "\n")


def prepare_application(job: JobOffer, cv_path: Optional[Path], settings: Settings) -> bool:
    """Registra la intención de solicitar una oferta, respetando el límite diario.

    Returns:
        `True` si se registró; `False` si se alcanzó el límite del día.
    """
    limite = settings.max_applications_per_day
    if applications_today_count(settings.applications_log) >= limite:
        logger.info("Límite diario (%d) alcanzado: no se registra '%s'", limite, job.title)
        return False

    log_application(
        job,
        cv_path,
        status=STATUS_PREPARED,
        applications_log=settings.applications_log,
        message="Tailored CV ready; manual submit on LinkedIn required.",
    )
    logger.info("Solicitud preparada: '%s' @ '%s'", job.title, job.company)
    return True
