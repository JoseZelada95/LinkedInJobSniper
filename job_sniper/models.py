"""Modelos de datos del dominio.

Dos tipos distintos, cada uno para su caso de uso:

  * `JobEvaluation` (Pydantic) → salida *estructurada* que el LLM debe devolver.
    Pydantic valida la respuesta y la convierte en un objeto tipado.
  * `JobOffer` (dataclass)    → oferta de empleo ya evaluada, que circula por
    el resto del pipeline (sheets, email, PDF). Simple y sin dependencias.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

# Motivos de fallido reutilizados para no repetir literales por el código.
SIN_DESCRIPCION = "No description"
ERROR_IA = "AI Error"


class JobEvaluation(BaseModel):
    """Estructura que el LLM debe devolver al evaluar una oferta.

    El prompt pide JSON con estas tres claves; `with_structured_output()`
    se asegura de que la respuesta cumpla este esquema.
    """

    score: int = Field(description="Puntuación de afinidad con el CV, de 0 a 100.")
    reason: str = Field(description="Frase breve (una sola) que justifica la puntuación.")
    yoe: str = Field(
        description="Años de experiencia exigidos por la oferta, o 'Not Specified'."
    )

    @field_validator("yoe", mode="before")
    @classmethod
    def _coerce_yoe(cls, valor):
        """Algunos modelos devuelven `4` en vez de `"4"`: se acepta igual."""
        return "Not Specified" if valor is None else str(valor)

    @classmethod
    def failed(cls, reason: str) -> "JobEvaluation":
        """Evaluación neutra para cuando no se pudo puntuar (sin descripción o fallo de IA)."""
        return cls(score=0, reason=reason, yoe="N/A")


@dataclass
class JobOffer:
    """Oferta de empleo ya puntuada: la unidad de trabajo del pipeline.

    `tailored_bullets` se rellena más adelante (solo para las mejores ofertas),
    por eso tiene valor por defecto.
    """

    title: str
    company: str
    job_url: str
    score: int
    reason: str
    yoe: str
    description: str = field(default="", repr=False)  # repr=False: el texto es enorme para logs
    tailored_bullets: str = ""

    @property
    def has_tailored_bullets(self) -> bool:
        """True si la oferta ya tiene viñetas de CV personalizadas."""
        return bool(self.tailored_bullets.strip())
