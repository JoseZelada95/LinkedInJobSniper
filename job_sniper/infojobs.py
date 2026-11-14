"""Fuente de ofertas: **API oficial de InfoJobs** (`api.infojobs.net`).

Motivo: `jobspy` no soporta InfoJobs, así que sus ofertas nunca llegaban al
informe diario. Aquí se consulta la API pública documentada en
https://developer.infojobs.net y se devuelve un DataFrame con las mismas
columnas que produce jobspy (`title`, `company`, `job_url`, `description`…),
de modo que el pipeline no necesita tratamientos especiales.

Autenticación: HTTP basic con `client_id:client_secret` (se obtienen al
registrar la aplicación en developer.infojobs.net con la cuenta InfoJobs).
Los endpoints usados son públicos (role `public`, scope `none`): las
credenciales no dan acceso a datos del candidato, solo identifican la app.

Contrato de robustez idéntico al de `scraping.py`: reintentos con backoff
aleatorio y pausas entre llamadas para no saturar la API.
"""

from __future__ import annotations

import datetime as _dt
import random
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

from .config import Settings
from .logging_utils import get_logger

logger = get_logger("infojobs")

BASE_URL = "https://api.infojobs.net"
LIST_ENDPOINT = "/api/9/offer"          # búsqueda: q, province, telecommute, maxItems
DETAIL_ENDPOINT = "/api/7/offer/{id}"   # detalle: description, minRequirements…

MAX_ATTEMPTS = 3                        # intentos por llamada
BACKOFF_SECONDS = (3.0, 6.0)            # espera aleatoria entre reintentos
REQUEST_PAUSE = (1.0, 2.0)              # pausa entre llamadas (respeto a la API)
REQUEST_TIMEOUT = 15                    # segundos máximos de espera HTTP

# Límite defensivo: la API corta `description` en 3.500 caracteres y los
# `requirements` en 2.500; 8.000 deja margen a la concatenación de los tres bloques.
MAX_DESCRIPTION_CHARS = 8000

# Columnas de salida, compatibles con el DataFrame de jobspy (lo que espera
# `pipeline.collect_scored_offers`). `offer_id`/`published` son propios de aquí.
_OUTPUT_COLUMNS = (
    "title",
    "company",
    "job_url",
    "description",
    "location",
    "offer_id",
    "published",
)


def has_credentials(settings: Settings) -> bool:
    """True si hay par completo de credenciales de InfoJobs en la configuración."""
    return bool(settings.infojobs_client_id and settings.infojobs_client_secret)


def _http_get(path: str, params: Dict[str, str], settings: Settings) -> Optional[requests.Response]:
    """GET autenticado contra la API con reintentos; None si agota los intentos."""
    auth = (settings.infojobs_client_id, settings.infojobs_client_secret)

    for intento in range(1, MAX_ATTEMPTS + 1):
        try:
            respuesta = requests.get(
                BASE_URL + path,
                params=params,
                auth=auth,
                timeout=REQUEST_TIMEOUT,
            )
            if respuesta.status_code == 200:
                return respuesta
            logger.warning(
                "  Intento %d/%d: HTTP %d en %s",
                intento,
                MAX_ATTEMPTS,
                respuesta.status_code,
                path,
            )
        except requests.RequestException as exc:
            logger.warning("  Intento %d/%d falló: %s", intento, MAX_ATTEMPTS, exc)

        if intento < MAX_ATTEMPTS:
            time.sleep(random.uniform(*BACKOFF_SECONDS))

    logger.error("Llamada fallida tras %d intentos: %s", MAX_ATTEMPTS, path)
    return None


def _offer_list(response_json: dict) -> List[dict]:
    """Extrae la lista de ofertas bruta del cuerpo de la API (versión v9 y v7)."""
    for clave in ("offers", "offer", "items"):
        valor = response_json.get(clave)
        if isinstance(valor, list):
            return valor
    return []


def _profile_name(offer: dict) -> str:
    """Nombre de la empresa, tolerante a los dos formatos que devuelve la API."""
    perfil = offer.get("profile")
    if isinstance(perfil, dict) and perfil.get("name"):
        return str(perfil["name"])
    autor = offer.get("author")
    if isinstance(autor, dict) and autor.get("name"):
        return str(autor["name"])
    return "Unknown"


def _published_datetime(offer: dict) -> Optional[_dt.datetime]:
    """Fecha de publicación de la oferta (ISO 8601) o None si no viene.

    La API descarta fracciones tipo `.000+0000` sin problema `fromisoformat`
    en Python 3.11+, pero por robustez se recorta a la forma `…:ss+0000`.
    """
    raw = offer.get("published") or offer.get("creationDate")
    if not raw or not isinstance(raw, str):
        return None
    texto = raw.rstrip("Z")
    try:
        datetime_obj = _dt.datetime.fromisoformat(texto)
    except ValueError:
        return None
    # La API puede firmar con offset UTC explícito: se normaliza a UTC ingenuo
    # para comparar contra `hours_old` (que siempre está en horas de reloj).
    if datetime_obj.tzinfo is not None:
        datetime_obj = datetime_obj.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return datetime_obj


def _is_fresh(offer: dict, hours_old: int, ahora: Optional[_dt.datetime] = None) -> bool:
    """True si la oferta es más reciente que `hours_old` (o no tiene fecha)."""
    fecha = _published_datetime(offer)
    if fecha is None:
        return True  # sin fecha no hay forma de descartarla: se conserva
    referencia = ahora or _dt.datetime.utcnow()
    return (referencia - fecha) <= _dt.timedelta(hours=hours_old)


def _location_to_params(ubicacion: str) -> Dict[str, str]:
    """Traduce una ubicación del estilo del proyecto a parámetros de la API.

    'Madrid, Spain' → province=madrid ; 'Remote' → telecommute (bélico booleano
    que la API interpreta como cualquier valor truthy).
    """
    ciudad = ubicacion.split(",")[0].strip().lower()
    if "remote" in ciudad:
        return {"telecommute": "1"}
    if ciudad:
        return {"province": ciudad}
    return {}


def _compose_description(detalle: dict) -> str:
    """Une los bloques textuales del detalle en un solo cuerpo para el evaluador."""
    bloques = [
        detalle.get("description") or "",
        detalle.get("minRequirements") or "",
        detalle.get("desiredRequirements") or "",
    ]
    return "\n".join(bloque for bloque in bloques if bloque).strip()[:MAX_DESCRIPTION_CHARS]


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUTPUT_COLUMNS)


def search_offers(
    search_term: str,
    location: str,
    settings: Settings,
) -> Optional[List[dict]]:
    """Búsqueda por término ± ubicación: devuelve lista de ofertas crudas.

    None si la llamada agota sus reintentos (el llamado decide si inyecta
    el resto de combinaciones o degrada el resultado).
    """
    params: Dict[str, str] = {"q": search_term, "maxItems": str(settings.result_limit)}
    params.update(_location_to_params(location))

    respuesta = _http_get(LIST_ENDPOINT, params, settings)
    if respuesta is None:
        return None

    try:
        cuerpo = respuesta.json()
    except ValueError as exc:
        logger.warning("Respuesta no JSON ante llamada correcta: %s", exc)
        return None

    return _offer_list(cuerpo)


def offer_detail(offer_id: str, settings: Settings) -> Optional[dict]:
    """Detalle completo de una oferta (título, descripción, requisitos…)."""
    if not offer_id:
        return None

    respuesta = _http_get(DETAIL_ENDPOINT.format(id=offer_id), {}, settings)
    if respuesta is None:
        return None

    try:
        return respuesta.json()
    except ValueError:
        return None


def scrape_infojobs(settings: Settings, ahora: Optional[_dt.datetime] = None) -> pd.DataFrame:
    """Recorre los términos × ubicaciones y devuelve las ofertas **únicas**.

    Plano de salida igual al de jobspy → `scrape_all_jobs` lo concatena y
    deduplica por `job_url` junto con el resto de fuentes.

    El parámetro `ahora` (opcional) inyecta la fecha de referencia para el
    filtro de antigüedad: usada solo en tests; en ejecución real se omite.
    """
    if not has_credentials(settings):
        logger.info("InfoJobs sin credenciales (INFOJOBS_CLIENT_ID/SECRET): fuente omitida")
        return _empty_frame()

    filas: List[dict] = []

    for location in settings.locations:
        for term in settings.search_terms:
            logger.info("Buscando '%s' en InfoJobs (%s)", term, location)

            ofertas = search_offers(term, location, settings)
            if not ofertas:
                continue

            for offer in ofertas:
                # La lista ya puede traer descripción en versiones futuras de la API.
                if not str(offer.get("description") or "").strip():
                    time.sleep(random.uniform(*REQUEST_PAUSE))
                    detalle = offer_detail(str(offer.get("id") or ""), settings)
                    if detalle:
                        offer["description"] = _compose_description(detalle)

                filas.append(
                    {
                        "title": str(offer.get("title") or "Unknown"),
                        "company": _profile_name(offer),
                        "job_url": str(offer.get("link") or ""),
                        "description": str(offer.get("description") or ""),
                        "location": location,
                        "offer_id": str(offer.get("id") or ""),
                        "published": str(offer.get("published") or offer.get("creationDate") or ""),
                    }
                )

    if not filas:
        logger.info("InfoJobs: 0 ofertas en este ciclo")
        return _empty_frame()

    frame = pd.DataFrame(filas, columns=_OUTPUT_COLUMNS)
    frame = frame[_is_fresh_from_frame(frame, settings.hours_old, ahora)]
    frame = frame.drop_duplicates(subset=["job_url"]).reset_index(drop=True)

    logger.info("InfoJobs: %d ofertas únicas", len(frame))
    return frame


def _is_fresh_from_frame(
    frame: pd.DataFrame, hours_old: int, ahora: Optional[_dt.datetime] = None
) -> pd.Series:
    """Máscara booleana de la antigüedad a partir de la columna `published`."""
    referencia = ahora or _dt.datetime.utcnow()

    def _vigente(texto: str) -> bool:
        if not texto:
            return True
        return _is_fresh({"published": texto}, hours_old, ahora=referencia)

    return frame["published"].map(_vigente)


__all__ = [
    "BASE_URL",
    "has_credentials",
    "offer_detail",
    "scrape_infojobs",
    "search_offers",
]
