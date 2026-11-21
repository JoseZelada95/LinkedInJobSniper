"""Punto de entrada del agente autónomo (modo continuo o de un solo ciclo).

Uso:
    python autonomous_agent.py --once                     # un ciclo y salir
    python autonomous_agent.py --once --max-tailored 3    # como máximo 3 PDFs
    python autonomous_agent.py                            # bucle infinito (24 h entre ciclos)

Responsabilidades de este fichero: interpretar la línea de comandos y mantener
el bucle de ejecución. La lógica de negocio está en `job_sniper.pipeline`.
"""

import argparse
import time
from typing import Optional

from job_sniper.config import ConfigurationError, Settings
from job_sniper.logging_utils import get_logger
from job_sniper.pipeline import bootstrap_settings, load_cached_offers, run_tailoring_cycle

logger = get_logger("agent")

# Espera tras un ciclo con éxito y tras un error (permite reintentar pronto).
DEFAULT_SLEEP_SECONDS = 24 * 3600
ERROR_SLEEP_SECONDS = 5 * 60


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """Define y parsea los argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(description="Autonomous LinkedIn Job Sniper agent")
    parser.add_argument("--once", action="store_true", help="Ejecuta un solo ciclo y termina")
    parser.add_argument(
        "--max-tailored",
        type=int,
        default=None,
        help="Máximo de PDFs personalizados a generar en esta ejecución",
    )
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Reutiliza las ofertas puntuadas cacheadas por el informe diario (evita re-scrapear)",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=int,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Segundos de espera entre ciclos en modo continuo (defecto: {DEFAULT_SLEEP_SECONDS})",
    )
    return parser.parse_args(argv)


def run_forever(settings: Settings, sleep_seconds: int, max_tailored: Optional[int]) -> None:
    """Bucle infinito: ejecuta ciclos y espera; si algo falla, reintenta en 5 minutos.

    El `try/except` del bucle es la red de seguridad que impide que un error
    puntual (red caída, API caída) detenga el agente para siempre.
    """
    while True:
        try:
            run_tailoring_cycle(settings, max_tailored=max_tailored)
            time.sleep(sleep_seconds)
        except Exception as exc:  # noqa: BLE001 — registrar y reintentar es el contrato
            logger.error("Error en el ciclo (%s); se reintenta en %d minutos", type(exc).__name__, ERROR_SLEEP_SECONDS // 60)
            time.sleep(ERROR_SLEEP_SECONDS)


def main(argv: Optional[list] = None) -> None:
    args = parse_args(argv)

    try:
        settings = bootstrap_settings()  # config + CV + logging + validación
    except ConfigurationError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)

    logger.info(
        "Agente arrancado | once=%s | max_tailored=%s | sleep_seconds=%s",
        args.once,
        args.max_tailored,
        args.sleep_seconds,
    )

    # --from-cache: reutiliza ofertas puntuadas por el informe diario del mismo
    # run (evita re-scrapear y re-evaluar LinkedIn). Si no hay caché, se degrada.
    scored_offers = load_cached_offers(settings) if args.from_cache else None
    if args.from_cache and not scored_offers:
        logger.warning("--from-cache sin caché disponible: se re-scrapea LinkedIn")
        scored_offers = None

    if args.once:
        try:
            run_tailoring_cycle(settings, max_tailored=args.max_tailored, scored_offers=scored_offers)
        except Exception as exc:  # noqa: BLE001 — en modo --once informamos y salimos
            logger.error("El ciclo falló: %s", type(exc).__name__)
        logger.info("Ciclo único completado")
        return

    run_forever(settings, args.sleep_seconds, args.max_tailored)


if __name__ == "__main__":
    main()
