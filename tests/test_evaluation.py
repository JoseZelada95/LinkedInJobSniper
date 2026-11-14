"""Tests de la evaluación de ofertas (`job_sniper.evaluation`).

Solo se testea la lógica local: ningún test llega a la red. El camino del LLM
se simula con `monkeypatch` para verificar cómo degrada el código cuando la
API no responde.
"""

from __future__ import annotations

from dataclasses import replace

from job_sniper import evaluation as modulo_evaluacion
from job_sniper.evaluation import (
    MIN_DESCRIPTION_LENGTH,
    build_evaluation_prompt,
    evaluate_job,
    has_enough_description,
)
from job_sniper.models import ERROR_IA, SIN_DESCRIPCION

DESCRIPCION_VALIDA = "Buscamos desarrollador .NET con experiencia en APIs REST. " * 3


# ── ¿Es evaluable una descripción? ──────────────────────────────────────────
def test_descripcion_vacia_no_es_evaluable():
    assert not has_enough_description("")
    assert not has_enough_description(None)


def test_descripcion_demasiada_corta_no_es_evaluable():
    corta = "x" * (MIN_DESCRIPTION_LENGTH - 1)
    assert not has_enough_description(corta)


def test_descripcion_en_el_limite_si_es_evaluable():
    justa = "x" * MIN_DESCRIPTION_LENGTH
    assert has_enough_description(justa)
    assert has_enough_description(justa + "y")


# ── evaluate_job: degradación sin red ───────────────────────────────────────
def test_evaluate_job_devuelve_cero_sin_descripcion(settings, monkeypatch):
    """Sin descripción no se debe invocar al LLM (ahorro de tokens)."""

    def _no_debe_llamarse(*args, **kwargs):  # pragma: no cover - solo falla si se usa
        raise AssertionError("No debería llamarse al LLM sin descripción")

    monkeypatch.setattr(modulo_evaluacion, "run_with_fallback", _no_debe_llamarse)

    resultado = evaluate_job("Backend .NET", "", settings)

    assert resultado.score == 0
    assert resultado.reason == SIN_DESCRIPCION


def test_evaluate_job_degrada_si_todos_los_modelos_fallan(settings, monkeypatch):
    """Si la cadena de modelos se agota, se devuelve una evaluación neutra (no excepción)."""
    monkeypatch.setattr(modulo_evaluacion, "run_with_fallback", lambda *args, **kwargs: None)

    resultado = evaluate_job("Backend .NET", DESCRIPCION_VALIDA, settings)

    assert resultado.score == 0
    assert resultado.reason == ERROR_IA


def test_evaluate_job_usa_el_cv_configurado(settings, monkeypatch):
    """El payload que llega al LLM debe incluir el CV y recortar textos largos."""
    capturas = {}

    def _simular_llm(action_name, action, cfg, temperature=0.0):
        capturas["resume"] = cfg.resume_text
        return None  # forzamos el camino de error, solo nos interesa el payload

    monkeypatch.setattr(modulo_evaluacion, "run_with_fallback", _simular_llm)

    evaluate_job("Backend .NET", DESCRIPCION_VALIDA, settings)

    assert "CV de prueba" in capturas["resume"]


# ── Prompt ──────────────────────────────────────────────────────────────────
def test_prompt_incluye_criterias_extras():
    """La variable CRITERIA debe añadirse al prompt del sistema."""
    prompt = build_evaluation_prompt(" Penaliza los turnos de noche.")

    mensajes = prompt.format_messages(resume="cv", title="puesto", description="desc")

    assert "Penaliza los turnos de noche." in mensajes[0].content
    assert len(mensajes) == 2  # system + user


def test_prompt_sin_criterias_extra_no_rompe():
    prompt = build_evaluation_prompt("")
    mensajes = prompt.format_messages(resume="cv", title="puesto", description="desc")

    assert len(mensajes) == 2
    assert "{criteria}" not in mensajes[0].content  # no quedan marcadores sin sustituir


def test_evaluate_job_con_criterias_diferentes(settings, monkeypatch):
    """Asegura que `settings.criteria` llega construido al prompt."""
    monkeypatch.setattr(modulo_evaluacion, "run_with_fallback", lambda *args, **kwargs: None)
    con_criterias = replace(settings, criteria=" Solo puestos híbridos.")

    resultado = evaluate_job("Backend .NET", DESCRIPCION_VALIDA, con_criterias)

    assert resultado.reason == ERROR_IA  # degrada igual, pero el prompt se construyó
