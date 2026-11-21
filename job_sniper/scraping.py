"""Obtención de ofertas de empleo.

Tres fuentes:

  1. `jobspy` → API multi-sitio (LinkedIn, Indeed, Glassdoor) con reintentos.
  2. API oficial de InfoJobs (`infojobs`) → solo si el sitio 'infojobs'
     está en la lista y hay credenciales (jobspy no lo soporta).
  3. Descarga HTTP directa → solo cuando jobspy devuelve la descripción vacía
     (es el caso habitual en LinkedIn, donde el texto está plegado tras un botón).
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Sequence

import pandas as pd
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from jobspy import scrape_jobs

from .config import Settings
from .logging_utils import get_logger
from . import infojobs

logger = get_logger("scraping")

# ── Parámetros de robustez ──────────────────────────────────────────────────
MAX_ATTEMPTS = 3                    # intentos por cada término × ubicación
BACKOFF_SECONDS = (3.0, 6.0)        # espera aleatoria entre intentos (evita bloqueos)
REQUEST_PAUSE = (2.0, 4.0)          # pausa antes de una descarga directa (parece humano)
REQUEST_TIMEOUT = 10                # segundos máximos de espera HTTP
FALLBACK_TEXT_LENGTH = 5000         # longitud máxima si no se localiza el bloque de descripción

# Paralelismo moderado del scraping: suficiente para acortar 36 términos × 3
# ubicaciones sin disparar bloqueos por rate-limit de los portales.
MAX_SCRAPE_WORKERS = 4

# Selectores donde LinkedIn guarda el cuerpo de la descripción.
# Se comparan por *subcadena* de clase (`class*=`), porque LinkedIn usa varias
# clases CSS en el mismo div y las renombra con el tiempo.
_DESCRIPTION_SELECTORS = (
    "show-more-less-html__markup",   # versión actual
    "description__text",             # versión clásica
    "jobs-description__content",     # contenedor reciente de LinkedIn
)

_FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# `fake_useragent` es costoso de construir → se crea una sola vez por proceso.
_user_agent: Optional[UserAgent] = None
_user_agent_fallo = False


def _random_user_agent() -> str:
    """Cabecera aleatoria para parecer tráfico normal (con red de seguridad)."""
    global _user_agent, _user_agent_fallo

    if _user_agent_fallo:  # ya falló antes: no reintentamos en cada llamada
        return _FALLBACK_USER_AGENT
    if _user_agent is None:
        try:
            _user_agent = UserAgent()
        except Exception as exc:  # noqa: BLE001 — sin red la librería puede fallar
            _user_agent_fallo = True
            logger.warning("No se pudo inicializar fake_useragent (%s); cabecera fija.", exc)
            return _FALLBACK_USER_AGENT
    return _user_agent.random


def scrape_search(
    location: str,
    search_term: str,
    settings: Settings,
    sites: Sequence[str],
) -> pd.DataFrame:
    """Busca una combinación término × ubicación, con reintentos y backoff aleatorio.

    Devuelve un DataFrame vacío si agota los intentos: el llamado decide
    si continuar con el resto de combinaciones.
    """
    proxies = [settings.proxy_url] if settings.proxy_url else None
    logger.info("Buscando '%s' en '%s'", search_term, location)

    for intento in range(1, MAX_ATTEMPTS + 1):
        try:
            ofertas = scrape_jobs(
                site_name=list(sites),
                search_term=search_term,
                location=location,
                country_indeed="spain",  # si no, Indeed busca en EE.UU. (default "usa")
                results_wanted=settings.result_limit,
                hours_old=settings.hours_old,
                proxies=proxies,
            )
            logger.info("  → %d ofertas encontradas", len(ofertas))
            return ofertas
        except Exception as exc:  # noqa: BLE001 — jobspy lanza excepciones heterogéneas
            logger.warning("  Intento %d/%d falló: %s", intento, MAX_ATTEMPTS, exc)
            if intento < MAX_ATTEMPTS:
                time.sleep(random.uniform(*BACKOFF_SECONDS))

    logger.error("Búsqueda '%s' en '%s' fallida tras %d intentos", search_term, location, MAX_ATTEMPTS)
    return pd.DataFrame()


def scrape_all_jobs(
    settings: Settings,
    sites: Sequence[str] = (),
) -> pd.DataFrame:
    """Recorre todas las ubicaciones × términos y devuelve ofertas **únicas**.

    La misma oferta aparece en varias búsquedas: se deduplica por URL para no
    evaluar (ni puntuar) dos veces el mismo puesto.

    `infojobs` no es un sitio de jobspy: se consulta por la API oficial de
    InfoJobs (módulo `infojobs`) y solo si hay credenciales configuradas.
    """
    # jobspy no conoce 'infojobs': se le pasan solo los suyos.
    jobspy_sites = [s for s in sites if s != "infojobs"]
    infojobs_df = pd.DataFrame()
    if "infojobs" in sites:
        infojobs_df = infojobs.scrape_infojobs(settings)

    dataframes: list[pd.DataFrame] = []
    combinaciones = [
        (location, term)
        for location in settings.locations
        for term in settings.search_terms
    ]

    # Combinaciones término × ubicación consultadas en paralelo (con un tope de
    # workers para no disparar bloqueos). Cada búsqueda conserva sus reintentos.
    if jobspy_sites and combinaciones:
        with ThreadPoolExecutor(max_workers=MAX_SCRAPE_WORKERS) as ejecutor:
            futuros = {
                ejecutor.submit(scrape_search, location, term, settings, jobspy_sites): (location, term)
                for location, term in combinaciones
            }
            for futuro in as_completed(futuros):
                dataframes.append(futuro.result())

    if not infojobs_df.empty:
        dataframes.append(infojobs_df)

    if not dataframes:
        logger.warning("No se obtuvo ninguna oferta en este ciclo")
        return pd.DataFrame()

    combinadas = pd.concat(dataframes, ignore_index=True, sort=False)
    if combinadas.empty:
        logger.warning("No se obtuvo ninguna oferta en este ciclo")
        return combinadas

    # `job_url` puede no existir si jobspy cambia su esquema → comprobamos antes.
    if "job_url" in combinadas.columns:
        combinadas = combinadas.drop_duplicates(subset=["job_url"]).reset_index(drop=True)

    logger.info("Ofertas únicas tras deduplicar: %d", len(combinadas))
    return combinadas


def fetch_missing_description(url: str, settings: Settings) -> str:
    """Descarga la descripción directamente cuando jobspy la dejó vacía.

    Devuelve `""` ante cualquier fallo (HTTP, timeout, selector no encontrado):
    una oferta sin descripción simplemente no se podrá evaluar.
    """
    if not url:
        return ""

    time.sleep(random.uniform(*REQUEST_PAUSE))  # pausa anti-bloqueo

    headers = {
        "User-Agent": _random_user_agent(),
        "Accept-Language": "en-US,en;q=0.9",
    }
    proxies = {"http": settings.proxy_url, "https": settings.proxy_url} if settings.proxy_url else None

    try:
        response = requests.get(url, headers=headers, proxies=proxies, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Descarga manual fallida para %s: %s", url, exc)
        return ""

    if response.status_code != 200:
        logger.warning("HTTP %d al descargar la descripción de %s", response.status_code, url)
        return ""

    soup = BeautifulSoup(response.text, "html.parser")
    for selector in _DESCRIPTION_SELECTORS:
        # `class*=` busca subcadena de clase: tolera divs con varias clases CSS.
        bloque = soup.select_one(f"div[class*='{selector}']")
        if bloque:
            return bloque.get_text(separator="\n").strip()

    # Último recurso: texto completo de la página, recortado.
    return soup.get_text()[:FALLBACK_TEXT_LENGTH]
