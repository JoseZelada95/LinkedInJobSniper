"""Historial de ofertas ya enviadas por email.

El informe diario se ejecuta a diario y, sin memoria, volvería a enviar las
mismas ofertas mientras sigan apareciendo en los portales. Este módulo guarda
una clave canónica por oferta para poder descartar las que ya se enviaron.

Dos fuentes de persistencia:

  1. **Fichero local** (`sent_jobs.json` dentro de `log_dir`): suficiente para
     ejecuciones locales o del agente autónomo.
  2. **Google Sheets**: la columna «URL Oferta» de la hoja de seguimiento se usa
     como historial en la nube, porque los runners de GitHub Actions son
     efímeros y pierden el fichero local entre ejecuciones.

La clave se calcula a partir de la URL (sin parámetros de tracking) y, si no
hay URL, a partir de `título|empresa`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Settings
from .logging_utils import get_logger
from .models import JobOffer

logger = get_logger("history")

HISTORY_FILENAME = "sent_jobs.json"

# Tope de entradas para que el fichero no crezca sin límite con los años.
MAX_HISTORY_ENTRIES = 5000

# Parámetros de query que SÍ identifican el puesto (Indeed usa `jk`, LinkedIn
# `currentJobId`…). El resto son de tracking y se descartan para deduplicar.
_JOB_ID_PARAMS = frozenset(
    {"jk", "vjk", "jobid", "job_id", "currentjobid", "vacancyid", "offerid", "id"}
)

# Dominios de portales: se normaliza el subdominio de idioma
# (`es.indeed.com` / `www.indeed.com` → `indeed.com`).
_BOARD_DOMAINS = (
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "glassdoor.es",
    "infojobs.net",
)

# Identificador estable por portal. Se usa para construir la clave canónica:
# así la misma oferta cuenta igual aunque jobspy devuelva la URL con otra forma
# (`/viewjob` vs `/rc/clk` en Indeed) o con tracking distinto.
_BOARD_ID_PARAMS: Dict[str, Tuple[str, ...]] = {
    "linkedin.com": ("currentjobid", "jobid"),
    "indeed.com": ("jk", "vjk", "jobid"),
    "glassdoor.com": ("jl", "joblistingid"),
    "glassdoor.es": ("jl", "joblistingid"),
    "infojobs.net": ("offerid",),
}

_BOARD_ID_PATH: Dict[str, "re.Pattern[str]"] = {
    "linkedin.com": re.compile(r"/jobs/view/(\d+)"),
    "infojobs.net": re.compile(r"(of-[a-z0-9]+)", re.IGNORECASE),
}


def _normalise_text(value: str) -> str:
    """Minúsculas y espacios simples (para comparar títulos/empresas)."""
    return " ".join((value or "").lower().split())


def _canonical_netloc(netloc: str) -> str:
    """Normaliza el host: sin `www.` ni subdominios de idioma en portales conocidos."""
    netloc = netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    for dominio in _BOARD_DOMAINS:
        if netloc == dominio or netloc.endswith("." + dominio):
            return dominio
    return netloc


def _board_job_id(netloc: str, path: str, query: str) -> Optional[Tuple[str, str]]:
    """Extrae `(portal, id)` del portal, o `None` si no se reconoce el formato.

    Se busca primero en los parámetros con nombre conocido y luego en la ruta.
    """
    portal = _canonical_netloc(netloc)
    parametros = {clave.lower(): valor for clave, valor in parse_qsl(query, keep_blank_values=False)}

    for nombre in _BOARD_ID_PARAMS.get(portal, ()):
        if parametros.get(nombre):
            return portal, parametros[nombre]

    patron = _BOARD_ID_PATH.get(portal)
    if patron:
        coincidencia = patron.search(path)
        if coincidencia:
            return portal, coincidencia.group(1)

    return None


def job_key(url: str, title: str = "", company: str = "") -> str:
    """Devuelve la clave canónica de una oferta.

    - Si la URL es de un portal conocido y se puede extraer su identificador
      estable, la clave es `portal#id` (p. ej. `indeed.com#abc123`). Así la
      misma oferta no se duplica aunque cambie el resto de la URL.
    - Si no, se normaliza el dominio, se quita la barra final y se eliminan los
      parámetros de tracking, conservando los identificadores conocidos.
    - Si no hay URL: se usa `título|empresa` normalizado como respaldo.
    """
    url = (url or "").strip()
    if url:
        partes = urlsplit(url)
        if partes.scheme and partes.netloc:
            portal_id = _board_job_id(partes.netloc, partes.path, partes.query)
            if portal_id:
                portal, identificador = portal_id
                return f"{portal}#{identificador}"

            identificadores = sorted(
                (clave.lower(), valor)
                for clave, valor in parse_qsl(partes.query, keep_blank_values=False)
                if clave.lower() in _JOB_ID_PARAMS
            )
            return urlunsplit(
                (
                    partes.scheme.lower(),
                    _canonical_netloc(partes.netloc),
                    partes.path.rstrip("/"),
                    urlencode(identificadores),
                    "",
                )
            )
        return url.lower()

    return f"{_normalise_text(title)}|{_normalise_text(company)}"


def history_path(settings: Settings) -> Path:
    """Ruta del historial local de ofertas enviadas."""
    return settings.log_dir / HISTORY_FILENAME


def load_local_history(path: Path) -> Dict[str, dict]:
    """Carga el historial local. Si no existe o está corrupto, devuelve `{}`.

    Se aceptan dos formatos por compatibilidad:
      * diccionario `{clave: metadatos}` (formato actual),
      * lista de URLs (formato antiguo/simple).
    """
    if not path.exists():
        return {}

    try:
        contenido = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Historial ilegible en %s (%s): se empieza de cero", path, exc)
        return {}

    if isinstance(contenido, dict):
        return {str(clave): valor for clave, valor in contenido.items()}
    if isinstance(contenido, list):
        return {job_key(str(url)): {} for url in contenido if str(url).strip()}

    logger.warning("Formato de historial desconocido en %s: se empieza de cero", path)
    return {}


def _write_history(path: Path, history: Dict[str, dict]) -> None:
    """Escritura atómica (fichero temporal + `replace`) para no corromper el JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(path.suffix + ".tmp")
    temporal.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporal.replace(path)


def load_sent_keys(settings: Settings, extra_urls: Iterable[str] = ()) -> Set[str]:
    """Conjunto de claves ya enviadas: historial local + URLs externas (Sheets).

    Las entradas antiguas guardan su `job_url`, así que se recalculan con la
    clave actual para no volver a enviar ofertas ya registradas.
    """
    claves: Set[str] = set()
    for clave, metadatos in load_local_history(history_path(settings)).items():
        if isinstance(metadatos, dict) and metadatos.get("job_url"):
            claves.add(job_key(str(metadatos["job_url"])))
        else:
            claves.add(clave)

    for url in extra_urls:
        if url and url.strip():
            claves.add(job_key(url))
    return claves


def filter_new_offers(offers: Sequence[JobOffer], sent_keys: Set[str]) -> List[JobOffer]:
    """Devuelve solo las ofertas cuya clave no está en el historial."""
    return [
        offer
        for offer in offers
        if job_key(offer.job_url, offer.title, offer.company) not in sent_keys
    ]


def record_sent_offers(offers: Sequence[JobOffer], settings: Settings) -> int:
    """Marca las ofertas como enviadas en el historial local.

    Returns:
        Número de ofertas registradas en esta llamada.
    """
    if not offers:
        return 0

    path = history_path(settings)
    history = load_local_history(path)
    enviada_en = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for offer in offers:
        history[job_key(offer.job_url, offer.title, offer.company)] = {
            "title": offer.title,
            "company": offer.company,
            "job_url": offer.job_url,
            "sent_at": enviada_en,
        }

    # Se conservan las entradas más recientes (dict mantiene orden de inserción).
    if len(history) > MAX_HISTORY_ENTRIES:
        history = dict(list(history.items())[-MAX_HISTORY_ENTRIES:])

    _write_history(path, history)
    logger.info(
        "Historial actualizado: %d ofertas nuevas (%d en total)",
        len(offers),
        len(history),
    )
    return len(offers)


__all__ = [
    "HISTORY_FILENAME",
    "MAX_HISTORY_ENTRIES",
    "filter_new_offers",
    "history_path",
    "job_key",
    "load_local_history",
    "load_sent_keys",
    "record_sent_offers",
]
