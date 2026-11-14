"""Tests de los modelos de dominio (`job_sniper.models`)."""

from __future__ import annotations

from job_sniper.models import ERROR_IA, SIN_DESCRIPCION, JobEvaluation, JobOffer


def test_job_evaluation_failed_devuelve_puntuacion_cero():
    """Una evaluación fallida debe ser usable igual que una normal (score=0)."""
    evaluacion = JobEvaluation.failed(SIN_DESCRIPCION)

    assert evaluacion.score == 0
    assert evaluacion.reason == SIN_DESCRIPCION
    assert evaluacion.yoe == "N/A"


def test_job_evaluation_failed_con_error_de_ia():
    evaluacion = JobEvaluation.failed(ERROR_IA)

    assert evaluacion.score == 0
    assert evaluacion.reason == "AI Error"


def test_job_offer_sin_bullets_por_defecto():
    oferta = JobOffer(
        title="Backend .NET",
        company="Acme",
        job_url="https://example.test/job/1",
        score=80,
        reason="Buena coincidencia",
        yoe="3-5",
    )

    assert oferta.tailored_bullets == ""
    assert not oferta.has_tailored_bullets
    assert oferta.description == ""  # descripción opcional al construir


def test_job_offer_con_bullets():
    oferta = JobOffer(
        title="Backend .NET",
        company="Acme",
        job_url="https://example.test/job/1",
        score=80,
        reason="Buena coincidencia",
        yoe="3-5",
        tailored_bullets="<ul><li>Logré X</li></ul>",
    )

    assert oferta.has_tailored_bullets
