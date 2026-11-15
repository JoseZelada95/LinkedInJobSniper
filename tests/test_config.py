"""Tests de la capa de configuración (`job_sniper.config`)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from job_sniper.config import (
    ConfigurationError,
    build_fallback_models,
    load_settings,
    resolve_model_name,
    validate_settings,
)


# ── Modelos: alias y cadena de fallback ─────────────────────────────────────
def test_resolve_model_name_traduce_alias_antiguo():
    """El alias histórico de Groq debe mapear al ID real del modelo."""
    assert resolve_model_name("llama3-70b-8192") == "llama-3.3-70b-versatile"


def test_resolve_model_name_deja_passthrough_desconocidos():
    """Los nombres ya reales (p. ej. 'mistral') no se tocan."""
    assert resolve_model_name("mistral") == "mistral"


def test_build_fallback_models_pon_el_modelo_principal_primero():
    modelos = build_fallback_models("mistral")
    assert modelos[0] == "mistral"
    assert "llama-3.3-70b-versatile" in modelos


def test_build_fallback_models_sin_duplicados():
    """Si el modelo principal ya es un respaldo, no debe repetirse en la cadena."""
    modelos = build_fallback_models("llama-3.3-70b-versatile")
    assert len(modelos) == len(set(modelos))


# ── Lectura del entorno ─────────────────────────────────────────────────────
def test_load_settings_lee_umbrales_del_entorno(monkeypatch):
    monkeypatch.setenv("SCORE_THRESHOLD", "70")
    monkeypatch.setenv("TAILOR_THRESHOLD", "85")
    monkeypatch.setenv("RESULT_LIMIT", "3")

    settings = load_settings()

    assert settings.score_threshold == 70
    assert settings.tailor_threshold == 85
    assert settings.result_limit == 3


def test_load_settings_tolera_valores_no_numericos(monkeypatch):
    """Un Secret corrupto (p. ej. SCORE_THRESHOLD='cincuenta') no debe tumbar el arranque."""
    monkeypatch.setenv("SCORE_THRESHOLD", "cincuenta")

    settings = load_settings()

    assert settings.score_threshold == 50  # valor por defecto


def test_load_settings_sin_entorno_usa_defectos(monkeypatch):
    for variable in ("SCORE_THRESHOLD", "TAILOR_THRESHOLD", "TOP_JOBS_LIMIT"):
        monkeypatch.delenv(variable, raising=False)

    settings = load_settings()

    assert settings.score_threshold == 50
    assert settings.tailor_threshold == 75
    assert settings.top_jobs_limit == 20


# ── Validación ──────────────────────────────────────────────────────────────
def test_validate_settings_ok(settings):
    """Con el fixture completo la validación no debe lanzar nada."""
    validate_settings(settings)  # no lanza excepción


def test_validate_settings_lista_todos_los_problemas(settings):
    """Debe informar de API_KEY, API_BASE y CV a la vez (no solo del primero)."""
    incompleto = replace(settings, api_key=None, base_url=None, resume_text=None)

    with pytest.raises(ConfigurationError) as exc_info:
        validate_settings(incompleto)

    mensaje = str(exc_info.value)
    assert "API_KEY" in mensaje
    assert "API_BASE" in mensaje
    assert "RESUME_TEXT" in mensaje


def test_settings_es_inmutable(settings):
    """`Settings` es frozen: impedir mutaciones evita efectos secundarios entre módulos."""
    with pytest.raises(AttributeError):
        settings.score_threshold = 99


# ── DeepSeek: configuración y tarifa ────────────────────────────────────────
def test_load_settings_lee_deepseek_del_entorno(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")

    settings = load_settings()

    assert settings.deepseek_api_key == "sk-test"
    assert settings.deepseek_model == "deepseek-flash"
    assert settings.deepseek_base_url == "https://api.deepseek.com"


def test_deepseek_is_peak():
    from datetime import datetime, timezone

    from job_sniper.config import deepseek_is_peak

    # 2026-11-16 es lunes; 2026-11-14 es sábado.
    assert deepseek_is_peak(datetime(2026, 11, 16, 3, tzinfo=timezone.utc)) is True
    assert deepseek_is_peak(datetime(2026, 11, 16, 11, tzinfo=timezone.utc)) is False
    assert deepseek_is_peak(datetime(2026, 11, 14, 3, tzinfo=timezone.utc)) is False
