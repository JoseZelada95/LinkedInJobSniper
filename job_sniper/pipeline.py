"""Orquestación: flujos completos que usan los puntos de entrada.

Aquí se combinan los módulos anteriores sin contener lógica de negocio propia:

    bootstrap_settings()   → cargar config + CV + validar (arranque)
    collect_scored_offers()→ scrape → descripción fallback → evaluar → ordenar
    run_daily_report()     → informe diario (Sheets + email)   [main.py]
    run_tailoring_cycle()  → personalizar PDFs + registrar     [autonomous_agent.py]
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional, Sequence, Set

from .applications import applications_today_count, load_logged_job_urls, prepare_application
from .config import AGENT_SITES, DAILY_SITES, ConfigurationError, Settings, load_settings, validate_settings
from .emailer import send_email
from .evaluation import evaluate_job, has_enough_description
from .history import filter_new_offers, job_key, load_sent_keys, record_sent_offers
from .logging_utils import configure_logging, get_logger
from .models import JobOffer
from .resume import resolve_resume
from .scraping import fetch_missing_description, scrape_all_jobs
from .sheets import export_to_sheets, fetch_sent_job_urls
from .tailoring import tailor_resume_for_job

logger = get_logger("pipeline")


def bootstrap_settings() -> Settings:
    """Arranque del proceso: config → CV → logging → validación.

    Único punto donde se mira el entorno y se comprueba que todo está listo;
    si falta algo, lanza `ConfigurationError` con **todos** los problemas a la vez.
    """
    settings = load_settings()
    configure_logging(settings.log_dir)
    settings = resolve_resume(settings)
    validate_settings(settings)
    return settings


def collect_scored_offers(
    settings: Settings,
    sites: Sequence[str] = DAILY_SITES,
    skip_keys: Optional[Set[str]] = None,
) -> List[JobOffer]:
    """Scrapea, completa descripciones, evalúa con IA y devuelve ofertas ordenadas.

    `skip_keys` descarta ofertas ya enviadas antes de gastar tokens en evaluarlas.
    Devuelve las ofertas que superan `score_threshold`, de mayor a menor puntuación.
    """
    ofertas_df = scrape_all_jobs(settings, sites)
    if ofertas_df.empty:
        return []

    ofertas_puntuadas: List[JobOffer] = []
    saltadas = 0
    for _, row in ofertas_df.iterrows():
        title = str(row.get("title") or "Unknown")
        company = str(row.get("company") or "Unknown")
        job_url = str(row.get("job_url") or "")
        description = str(row.get("description") or "")

        if skip_keys and job_key(job_url, title, company) in skip_keys:
            saltadas += 1
            continue

        # jobspy a veces devuelve la descripción vacía → se intenta descargarla.
        if not has_enough_description(description):
            description = fetch_missing_description(job_url, settings)
        if not has_enough_description(description):
            logger.info("Sin descripción suficiente: se omite '%s'", title)
            continue

        evaluacion = evaluate_job(title, description, settings)
        logger.info(
            "  [%3d] %s @ %s | %s",
            evaluacion.score,
            title,
            company,
            evaluacion.reason[:60],
        )

        if evaluacion.score >= settings.score_threshold:
            ofertas_puntuadas.append(
                JobOffer(
                    title=title,
                    company=company,
                    job_url=job_url,
                    score=evaluacion.score,
                    reason=evaluacion.reason,
                    yoe=evaluacion.yoe,
                    description=description,
                )
            )

    ofertas_puntuadas.sort(key=lambda oferta: oferta.score, reverse=True)
    if saltadas:
        logger.info("Descartadas %d ofertas ya enviadas antes de evaluar", saltadas)
    logger.info(
        "Ofertas superando el umbral (%d): %d de %d evaluadas",
        settings.score_threshold,
        len(ofertas_puntuadas),
        len(ofertas_df) - saltadas,
    )
    return ofertas_puntuadas


def tailor_bullets_for_top(offers: Sequence[JobOffer], settings: Settings) -> None:
    """Genera viñetas personalizadas (email) para las ofertas por encima de `tailor_threshold`.

    Se rellena `offer.tailored_bullets` in place: el resto del flujo
    (Sheets/email) solo tiene que comprobar si el campo está vacío.
    """
    for offer in offers:
        if offer.score < settings.tailor_threshold:
            continue
        logger.info("  ✨ Personalizando viñetas: '%s' @ '%s'", offer.title, offer.company)
        offer.tailored_bullets = tailor_resume_for_job(offer.title, offer.description, settings)


SCORED_CACHE_FILENAME = "scored_offers.json"


def cache_scored_offers(offers: Sequence[JobOffer], settings: Settings) -> Path:
    """Persiste las ofertas ya puntuadas para reutilizarlas en el mismo run.

    `main.py` (informe diario) y `autonomous_agent.py` (agente) son procesos
    separados; esta caché evita re-scrapear y re-evaluar LinkedIn (que consume
    cuota de LLM) cuando el workflow pide PDFs con `--from-cache`.
    """
    path = settings.log_dir / SCORED_CACHE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(oferta) for oferta in offers]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    logger.info("Ofertas puntuadas cacheadas: %d en %s", len(payload), path)
    return path


def load_cached_offers(settings: Settings) -> List[JobOffer]:
    """Carga las ofertas puntuadas cacheadas, o lista vacía si no hay o está corrupta."""
    path = settings.log_dir / SCORED_CACHE_FILENAME
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [JobOffer(**item) for item in payload]
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Caché de ofertas ilegible en %s (%s): se ignora", path, exc)
        return []


def run_daily_report(settings: Settings) -> None:
    """Informe diario completo: scrape → evaluar → descartar repetidas → email.

    Es el flujo que ejecuta `python main.py` en el GitHub Actions.

    Para no repetir ofertas ya enviadas, se combinan dos historiales:
    el fichero local `sent_jobs.json` y las URLs ya exportadas a Google Sheets
    (persistente entre ejecuciones de Actions). Solo si el email se envía con
    éxito se exportan a Sheets y se añaden al historial local; así un fallo de
    SMTP no «quema» las ofertas como enviadas para la siguiente ejecución.
    """
    # Historial = fichero local + URLs de la hoja + solicitudes ya preparadas.
    # Se calcula antes de evaluar para no gastar tokens en ofertas ya enviadas.
    urls_previas = [
        *fetch_sent_job_urls(settings),
        *load_logged_job_urls(settings.applications_log),
    ]
    claves_enviadas = load_sent_keys(settings, urls_previas)

    ofertas = collect_scored_offers(settings, DAILY_SITES, skip_keys=claves_enviadas)
    if not ofertas:
        logger.warning("Sin ofertas por encima del umbral. Revisa red/proxy/umbrales.")
        return

    # Se cachean las ofertas ya puntuadas para que el agente autónomo (si corre
    # con --from-cache) no tenga que re-scrapear ni re-evaluar LinkedIn.
    cache_scored_offers(ofertas, settings)

    # Red de seguridad: ofertas sin URL reconocible o carreras entre ejecuciones.
    nuevas = filter_new_offers(ofertas, claves_enviadas)
    descartadas = len(ofertas) - len(nuevas)
    if descartadas:
        logger.info("Descartadas %d ofertas ya enviadas anteriormente", descartadas)

    if not nuevas:
        logger.info("No hay ofertas nuevas: hoy no se envía email")
        return

    # Solo las N mejores entran en el informe (ya vienen ordenadas por score).
    top_offers = nuevas[: settings.top_jobs_limit]
    tailor_bullets_for_top(top_offers, settings)

    logger.info("%d ofertas nuevas por encima del umbral. Enviando email…", len(top_offers))
    if not send_email(top_offers, settings):
        # Si el email falla, no se registra nada: las ofertas se reintentan mañana.
        logger.warning("Email no enviado: las ofertas quedan pendientes para la próxima ejecución")
        return

    # El email ya salió: la hoja (historial en la nube) y el fichero local se
    # actualizan solo ahora, para no marcar como enviadas ofertas que no lo están.
    export_to_sheets(top_offers, settings)
    record_sent_offers(top_offers, settings)


def drop_prepared_offers(offers: Sequence[JobOffer], applications_log: Path) -> List[JobOffer]:
    """Descarta ofertas para las que el agente ya preparó un CV.

    El historial de preparadas vive en `applications.log`, que se persiste en
    la caché de GitHub Actions; así una ejecución diaria no regenera el mismo
    PDF una y otra vez.
    """
    claves = {job_key(url) for url in load_logged_job_urls(applications_log)}
    return filter_new_offers(offers, claves)


def run_tailoring_cycle(
    settings: Settings,
    max_tailored: Optional[int] = None,
    scored_offers: Optional[Sequence[JobOffer]] = None,
) -> None:
    """Ciclo del agente autónomo: PDFs personalizados + registro de solicitudes.

    Diferencias con el informe diario:
      * Solo busca en LinkedIn (`AGENT_SITES`).
      * Genera el PDF completo (offline, sin LLM) en lugar de viñetas para email.
      * Respeta el límite diario de solicitudes (`max_applications_per_day`).

    `scored_offers` (opcional) reutiliza ofertas ya puntuadas por el informe
    diario (`--from-cache`), evitando re-scrapear y re-evaluar LinkedIn.
    """
    if scored_offers is None:
        ofertas = drop_prepared_offers(
            collect_scored_offers(settings, AGENT_SITES), settings.applications_log
        )
    else:
        ofertas = drop_prepared_offers(scored_offers, settings.applications_log)
    if not ofertas:
        logger.info("Ciclo sin ofertas que superen el umbral")
        return

    personalizados = 0
    aplicaciones_hoy = applications_today_count(settings.applications_log)

    # Import perezoso: PyMuPDF solo se necesita para generar PDFs (agente
    # autónomo); el informe diario no debe fallar si no está instalado.
    from .pdf_generator import tailor_cv_for_job

    for offer in ofertas:
        if offer.score < settings.tailor_threshold:
            continue
        if aplicaciones_hoy + personalizados >= settings.max_applications_per_day:
            logger.info("Límite diario (%d) alcanzado: se deja de personalizar", settings.max_applications_per_day)
            break
        if max_tailored is not None and personalizados >= max_tailored:
            break

        logger.info("Personalizando PDF: '%s' @ '%s' (score=%d)", offer.title, offer.company, offer.score)
        try:
            ruta_pdf = tailor_cv_for_job(offer, settings)
        except Exception as exc:  # noqa: BLE001 — una oferta rota no detiene el ciclo
            logger.error("Error generando PDF de '%s': %s", offer.title, type(exc).__name__)
            continue

        if ruta_pdf:
            personalizados += 1
            prepare_application(offer, ruta_pdf, settings)  # registra (respeta límite diario)
        else:
            logger.info("Sin PDF para '%s' @ '%s' (score=%d)", offer.title, offer.company, offer.score)


__all__ = [
    "ConfigurationError",
    "bootstrap_settings",
    "collect_scored_offers",
    "tailor_bullets_for_top",
    "run_daily_report",
    "run_tailoring_cycle",
    "cache_scored_offers",
    "load_cached_offers",
]
