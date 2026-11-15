"""Tests del cliente LLM con fallback (`job_sniper.llm`).

Ningún test toca la red: la acción simulada lanza (o devuelve) directamente,
así se verifica el patrón de reintentos entre modelos.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from job_sniper.llm import create_chat_model, run_with_fallback


def test_run_with_fallback_devuelve_el_primer_resultado_correcto(settings):
    """Si el primer modelo funciona, no se prueba ningún otro."""
    llamadas = []

    def _accion(modelo):
        llamadas.append(modelo.model)
        return "ok"

    resultado = run_with_fallback("prueba", _accion, settings)

    assert resultado == "ok"
    assert len(llamadas) == 1


def test_run_with_fallback_pasa_al_siguiente_modelo(settings):
    """Un modelo fallido no aborta: se intenta con el siguiente de la cadena."""
    llamadas = []

    def _accion(modelo):
        llamadas.append(modelo.model)
        if len(llamadas) == 1:
            raise RuntimeError("rate limit")
        return "ok-en-los-que-sigue"

    multi = replace(settings, fallback_models=("modelo-uno", "modelo-dos"))

    resultado = run_with_fallback("prueba", _accion, multi)

    assert resultado == "ok-en-los-que-sigue"
    assert llamadas == ["modelo-uno", "modelo-dos"]


def test_run_with_fallback_devuelve_none_si_fallan_todos(settings):
    """Con la cadena entera rota el contrato es `None`, nunca una excepción."""

    def _accion(modelo):
        raise ConnectionError("sin red")

    resultado = run_with_fallback("prueba", _accion, settings)

    assert resultado is None


def test_run_with_fallback_respeta_la_temperatura(settings):
    """La temperatura pedida debe llegar al cliente (0 para puntuar, 0.4 para redactar)."""
    temperaturas = []

    def _accion(modelo):
        temperaturas.append(modelo.temperature)
        return "ok"

    run_with_fallback("prueba", _accion, settings, temperature=0.4)

    assert temperaturas == [0.4]


def test_create_chat_model_usa_configuracion(settings):
    cliente = create_chat_model("modelo-x", settings, temperature=0.1)

    assert cliente.model == "modelo-x"
    assert cliente.openai_api_key is not None  # llega la clave (LangChain la envuelve en SecretStr)
    assert cliente.temperature == 0.1


def test_salida_estructurada_se_puede_construir(settings):
    """Blindaje ante cambios de API de LangChain.

    Si una versión futura elimina `method="json_mode"`, este test falla aquí
    (en local, gratis) en lugar de en el GitHub Action en producción.
    """
    from job_sniper.models import JobEvaluation

    cliente = create_chat_model("modelo-x", settings)

    estructurado = cliente.with_structured_output(JobEvaluation, method="json_mode")

    assert estructurado is not None


def test_run_with_fallback_con_cadena_vacia(settings):
    """Cadena vacía → `None` inmediato (no debe intentar nada)."""
    vacio = replace(settings, fallback_models=())

    assert run_with_fallback("prueba", lambda m: "nunca", vacio) is None


def test_run_with_fallback_usa_deepseek_cuando_groq_falla(settings, monkeypatch):
    """Groq cae → se usa DeepSeek con su propio base_url y modelo."""
    import job_sniper.llm as llm_module

    llamadas = []

    class _ClienteFalso:
        def __init__(self, model, base_url):
            self.model = model
            self.openai_api_base = base_url

    def _crear(model_name, settings, temperature=0.0, **kwargs):
        cliente = _ClienteFalso(model_name, kwargs.get("base_url"))
        llamadas.append(cliente)
        return cliente

    monkeypatch.setattr(llm_module, "create_chat_model", _crear)

    def _accion(modelo):
        if modelo.model in settings.fallback_models:
            raise RuntimeError("429 rate limit")
        return "ok-deepseek"

    con_deepseek = replace(
        settings,
        deepseek_api_key="sk-test",
        deepseek_model="deepseek-flash",
        deepseek_base_url="https://api.deepseek.com",
    )

    resultado = run_with_fallback("prueba", _accion, con_deepseek)

    assert resultado == "ok-deepseek"
    assert llamadas[-1].model == "deepseek-flash"
    assert "deepseek" in (llamadas[-1].openai_api_base or "")


def test_sin_deepseek_api_key_no_se_prueba_deepseek(settings):
    """Sin clave de DeepSeek la cadena sigue siendo solo Groq."""
    modelos = []

    def _accion(modelo):
        modelos.append(modelo.model)
        raise RuntimeError("sin cuota")

    assert run_with_fallback("prueba", _accion, settings) is None
    assert "deepseek-flash" not in modelos
