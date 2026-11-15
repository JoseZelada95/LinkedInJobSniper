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

from .config import Settings, deepseek_is_peak
from .logging_utils import get_logger

logger = get_logger("llm")

# Tipo genérico del resultado devuelto por la acción (dict, str, modelo…).
ResultType = TypeVar("ResultType")

# Evita repetir el aviso de tarifa DeepSeek en cada oferta.
_deepseek_tariff_logged = False


def create_chat_model(
    model_name: str,
    settings: Settings,
    temperature: float = 0.0,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra_body: Optional[dict] = None,
) -> ChatOpenAI:
    """Crea un cliente compatible con OpenAI (Groq o DeepSeek).

    `temperature=0` → respuestas deterministas (útil para puntuar);
    `temperature>0` → algo de creatividad (útil para reescribir viñetas).
    Los `*` opcionales permiten apuntar a otro proveedor (DeepSeek) sin
    duplicar clientes ni tocar la configuración global.
    """
    return ChatOpenAI(
        model=model_name,
        api_key=api_key if api_key is not None else settings.api_key,
        base_url=base_url if base_url is not None else settings.base_url,
        temperature=temperature,
        extra_body=extra_body,
    )


def _close_chat_model(cliente: Optional[ChatOpenAI]) -> None:
    """Cierra el pool HTTP del cliente para no dejarlo abierto entre intentos."""
    raiz = getattr(cliente, "root_client", None)
    if raiz is not None:
        try:
            raiz.close()
        except Exception:  # noqa: BLE001 — cerrar nunca debe romper el flujo
            pass


def _llm_candidates(settings: Settings):
    """Cadena de intentos: primero Groq (gratis) y luego DeepSeek (de pago)."""
    for model in settings.fallback_models:
        yield model, settings.api_key, settings.base_url, None, "groq"
    if settings.deepseek_api_key:
        # thinking=disabled: más barato y rápido para puntuar/redactar.
        yield (
            settings.deepseek_model,
            settings.deepseek_api_key,
            settings.deepseek_base_url,
            {"thinking": {"type": "disabled"}},
            "deepseek",
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
    global _deepseek_tariff_logged
    for model_name, api_key, base_url, extra_body, proveedor in _llm_candidates(settings):
        cliente: Optional[ChatOpenAI] = None
        try:
            if proveedor == "deepseek" and not _deepseek_tariff_logged:
                logger.info(
                    "DeepSeek (%s) tarifa %s",
                    model_name,
                    "PEAK" if deepseek_is_peak() else "off-peak",
                )
                _deepseek_tariff_logged = True
            cliente = create_chat_model(
                model_name,
                settings,
                temperature,
                api_key=api_key,
                base_url=base_url,
                extra_body=extra_body,
            )
            return action(cliente)
        except Exception as exc:  # noqa: BLE001 — cualquier fallo debe pasar al siguiente modelo
            logger.warning(
                "Modelo %s (%s) falló en '%s' (%s: %s)",
                model_name,
                proveedor,
                action_name,
                type(exc).__name__,
                exc,
            )
        finally:
            _close_chat_model(cliente)

    logger.error("Agotada la cadena de modelos en '%s'; se degrada el resultado", action_name)
    return None
