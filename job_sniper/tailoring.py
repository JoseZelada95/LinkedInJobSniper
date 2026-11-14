"""Personalización de viñetas del CV para el email diario (usa el LLM).

Para las ofertas con puntuación alta reescribimos 3-4 viñetas existentes del CV
con el vocabulario de la oferta. Es una pista de *cómo adaptar el CV*, no un
documento: el CV real se genera aparte con `pdf_generator` (sin IA).
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from .config import Settings
from .llm import run_with_fallback
from .logging_utils import get_logger
from .prompts import TAILORING_SYSTEM_TEMPLATE, TAILORING_USER_TEMPLATE

logger = get_logger("tailoring")

# Recortes de contexto (mismos límites que la evaluación: ahorran tokens).
RESUME_CHAR_LIMIT = 2000
DESCRIPTION_CHAR_LIMIT = 1500

# El modelo debe devolver exclusivamente la lista HTML; se validan colas defensivas.
_TAILORING_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", TAILORING_SYSTEM_TEMPLATE),
        ("user", TAILORING_USER_TEMPLATE),
    ]
)


def tailor_resume_for_job(title: str, description: str, settings: Settings) -> str:
    """Devuelve las viñetas personalizadas en HTML (`<ul><li>…`), o `""` si falla.

    Se usa temperatura 0.4: algo de creatividad para reescribir, pero sin
    alejarse de los hechos del CV.
    """
    payload = {
        "resume": (settings.resume_text or "")[:RESUME_CHAR_LIMIT],
        "title": title,
        "description": str(description)[:DESCRIPTION_CHAR_LIMIT],
    }

    def _invocar(modelo) -> str:
        cadena = _TAILORING_PROMPT | modelo
        return cadena.invoke(payload).content.strip()

    resultado = run_with_fallback("personalización de CV", _invocar, settings, temperature=0.4)

    if not resultado:
        logger.warning("No se pudieron generar viñetas personalizadas para '%s'", title)
        return ""
    return resultado
