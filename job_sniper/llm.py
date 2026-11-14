"""Acceso al LLM con estrategia de *fallback* entre modelos.

Patrón aplicado: un único punto (`run_with_fallback`) recorre la cadena de
modelos configurada en `Settings.fallback_models` y ejecuta la acción pedida.
Si un modelo falla (rate limit, modelo retirado, red…), se prueba el siguiente;
si fallan todos, devuelve `None` y el llamado decide cómo degradar.

De este modo la lógica de reintentos vive en un solo sitio y no se repite
en evaluación y personalización.
"""

from __future__ import annotations

from typing import Callable, Optional, TypeVar

from langchain_openai import ChatOpenAI

from .config import Settings
from .logging_utils import get_logger

logger = get_logger("llm")

# Tipo genérico del resultado devuelto por la acción (dict, str, modelo…).
ResultType = TypeVar("ResultType")


def create_chat_model(
    model_name: str,
    settings: Settings,
    temperature: float = 0.0,
) -> ChatOpenAI:
    """Crea un cliente apuntando a la API compatible con OpenAI (Groq por defecto).

    `temperature=0` → respuestas deterministas (útil para puntuar);
    `temperature>0` → algo de creatividad (útil para reescribir viñetas).
    """
    return ChatOpenAI(
        model=model_name,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=temperature,
    )


def run_with_fallback(
    action_name: str,
    action: Callable[[ChatOpenAI], ResultType],
    settings: Settings,
    temperature: float = 0.0,
) -> Optional[ResultType]:
    """Ejecuta `action` probando cada modelo de la cadena, en orden.

    Args:
        action_name: etiqueta legible para los logs (p. ej. "evaluación de oferta").
        action:      función que recibe el cliente LLM y devuelve el resultado.
        settings:    configuración con `api_key`, `base_url` y `fallback_models`.
        temperature: creatividad del modelo para esta tarea concreta.

    Returns:
        El resultado de la primera acción que tenga éxito, o `None` si agotan
        todos los modelos.
    """
    for model_name in settings.fallback_models:
        try:
            cliente = create_chat_model(model_name, settings, temperature)
            return action(cliente)
        except Exception as exc:  # noqa: BLE001 — cualquier fallo debe pasar al siguiente modelo
            logger.warning(
                "Modelo %s falló en '%s' (%s: %s)",
                model_name,
                action_name,
                type(exc).__name__,
                exc,
            )

    logger.error("Agotada la cadena de modelos en '%s'; se degrada el resultado", action_name)
    return None
