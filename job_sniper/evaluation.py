"""Evaluación de ofertas con IA: puntuación de afinidad oferta ↔ CV (0-100).

Flujo:
  1. Comprobar que la descripción es suficiente (si no, puntuación 0 sin gastar tokens).
  2. Pedir al LLM una salida **estructurada** (`JobEvaluation`) con fallback de modelos.
  3. Si todo falla, devolver una evaluación neutra para no tumbar el pipeline.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.prompts import ChatPromptTemplate

from .config import Settings
from .llm import run_with_fallback
from .logging_utils import get_logger
from .models import ERROR_IA, SIN_DESCRIPCION, JobEvaluation
from .prompts import EVALUATION_SYSTEM_TEMPLATE, EVALUATION_USER_TEMPLATE

logger = get_logger("evaluation")

# ── Límites de contexto ─────────────────────────────────────────────────────
# Recortar el texto reduce coste/latencia sin perder señal relevante.
MIN_DESCRIPTION_LENGTH = 50   # caracteres mínimos para confiar en la descripción
RESUME_CHAR_LIMIT = 2000      # máx. caracteres del CV enviados al LLM
DESCRIPTION_CHAR_LIMIT = 1500 # máx. caracteres de la descripción enviados al LLM


def build_evaluation_prompt(criteria: str) -> ChatPromptTemplate:
    """Construye el prompt de evaluación.

    `criteria` son instrucciones extra opcionales (variable `CRITERIA` en .env)
    que se añaden al final del sistema para personalizar la puntuación sin
    tocar el código.
    """
    return ChatPromptTemplate.from_messages(
        [
            ("system", EVALUATION_SYSTEM_TEMPLATE + criteria),
            ("user", EVALUATION_USER_TEMPLATE),
        ]
    )


def has_enough_description(description: Optional[str]) -> bool:
    """¿Tiene la oferta texto suficiente como para evaluarla fiablemente?"""
    return bool(description) and len(str(description)) >= MIN_DESCRIPTION_LENGTH


def evaluate_job(title: str, description: str, settings: Settings) -> JobEvaluation:
    """Puntúa una oferta de 0 a 100 frente al CV configurado.

    Nunca lanza excepción: si falla la IA devuelve `score=0` con el motivo,
    de modo que el ciclo completo sigue adelante con el resto de ofertas.
    """
    if not has_enough_description(description):
        return JobEvaluation.failed(SIN_DESCRIPCION)

    prompt = build_evaluation_prompt(settings.criteria)
    payload = {
        "resume": (settings.resume_text or "")[:RESUME_CHAR_LIMIT],
        "title": title,
        "description": str(description)[:DESCRIPTION_CHAR_LIMIT],
    }

    def _invocar(modelo) -> JobEvaluation:
        """Cadena prompt → LLM con salida estructurada (JSON validado por Pydantic)."""
        cadena = prompt | modelo.with_structured_output(JobEvaluation, method="json_mode")
        return cadena.invoke(payload)

    resultado = run_with_fallback("evaluación de oferta", _invocar, settings, temperature=0.0)

    if resultado is None:
        return JobEvaluation.failed(ERROR_IA)

    logger.debug("Evaluación OK para '%s': %d", title, resultado.score)
    return resultado
