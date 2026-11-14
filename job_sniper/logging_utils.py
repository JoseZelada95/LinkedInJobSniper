"""Logging uniforme para todo el agente (consola + fichero).

Por qué `logging` y no `print`:
  * Niveles (INFO/WARNING/ERROR) → se puede silenciar ruido sin borrar código.
  * Se configura **una sola vez** por proceso (patrón idempotente): si ya hay
    handlers montados no se duplican líneas.
  * En GitHub Actions la consola (`stdout`) es lo que aparece en el log del job.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# Formato: fecha | nivel | módulo | mensaje
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-24s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Nombre raíz de la jerarquía: todos los loggers del paquete cuelgan de él.
_ROOT_NAME = "job_sniper"

_CONFIGURADO = False  # evita montar los handlers dos veces en el mismo proceso


def configure_logging(log_dir: Optional[Path] = None, level: int = logging.INFO) -> logging.Logger:
    """Configura la salida del agente: consola siempre, fichero si se indica `log_dir`.

    Idempotente: llamarlo varias veces no duplica mensajes.
    """
    global _CONFIGURADO

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level)

    if _CONFIGURADO:
        return root

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # 1) Consola → stdout para que GitHub Actions lo muestre en el job.
    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formatter)
    root.addHandler(consola)

    # 2) Fichero → historial persistente entre ejecuciones locales.
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fichero = logging.FileHandler(log_dir / "agent.log", encoding="utf-8")
            fichero.setFormatter(formatter)
            root.addHandler(fichero)
        except OSError as exc:
            # Un fallo de fichero no debe tumbar el agente: seguimos con consola.
            root.warning("No se pudo abrir el fichero de log en %s: %s", log_dir, exc)

    _CONFIGURADO = True
    return root


def get_logger(component: str) -> logging.Logger:
    """Devuelve el logger de un componente (p. ej. `get_logger('evaluation')`).

    Los mensajes aparecen con el nombre del módulo, lo que facilita rastrear
    de dónde viene cada línea en un pipeline con varias fases.
    """
    return logging.getLogger(f"{_ROOT_NAME}.{component}")
