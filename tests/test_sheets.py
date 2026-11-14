"""Tests de la exportación a Google Sheets (`job_sniper.sheets`).

Solo se testea la construcción de filas y la extracción de salario: la llamada
a la API de Google es código muerto en tests (credenciales ficticias).
"""

from __future__ import annotations

from job_sniper.models import JobOffer
from job_sniper.sheets import (
    build_rows,
    export_to_sheets,
    extract_salary,
    fetch_sent_job_urls,
)


def _oferta(**kwargs) -> JobOffer:
    """Oferta de ejemplo con valores por defecto (los tests solo cambian lo relevante)."""
    base = dict(
        title="Backend .NET Developer",
        company="Acme",
        job_url="https://example.test/job/1",
        score=82,
        reason="Coincidencia alta con .NET y SQL Server",
        yoe="3-5",
        description="Salario de 35.000 € brutos al año.",
    )
    base.update(kwargs)
    return JobOffer(**base)


# ── Extracción de salario ───────────────────────────────────────────────────
def test_extract_salary_con_cifras_euro():
    assert extract_salary("Salario de 35.000 € brutos") == "35k"


def test_extract_salary_con_formato_con_espacio():
    assert extract_salary("sueldo 40 000 eur anuales") == "40k"


def test_extract_salary_sin_mencion_devuelve_vacio():
    assert extract_salary("No indicamos salario") == ""


def test_extract_salary_con_texto_vacio_o_none():
    assert extract_salary("") == ""
    assert extract_salary(None) == ""  # type: ignore[arg-type]


def test_extract_salary_es_case_insensitive():
    assert extract_salary("SALARIO 45.000 EUR") == "45k"


# ── Construcción de filas ───────────────────────────────────────────────────
def test_build_rows_produce_diez_columnas(settings):
    filas = build_rows([_oferta()], today="01/02/2026")

    assert len(filas) == 1
    assert len(filas[0]) == 10  # A:J → 10 columnas de la plantilla


def test_build_rows_orden_de_columnas(settings):
    """El orden fijo (Fecha, Empresa, Puesto, …) no debe cambiar: la hoja ya existe."""
    fila = build_rows([_oferta()], today="01/02/2026")[0]

    assert fila[0] == "01/02/2026"   # Fecha
    assert fila[1] == "Acme"         # Empresa
    assert fila[2] == "Backend .NET Developer"  # Puesto
    assert fila[4] == "82%"          # Match %
    assert fila[5] == "35k"          # Salario
    assert fila[7] == "Nuevo"        # Estado


def test_build_rows_recorta_las_notas(settings):
    """La columna «Notas» no debe desbordar con razones larguísimas del LLM."""
    oferta = _oferta(reason="x" * 500)

    fila = build_rows([oferta], today="01/02/2026")[0]

    assert len(fila[8]) == 200


def test_build_rows_sin_ofertas(settings):
    assert build_rows([]) == []


# ── Exportación degradada ───────────────────────────────────────────────────
def test_export_to_sheets_sin_ofertas_no_hace_nada(settings, monkeypatch):
    """No debe intentar contactar con Google si no hay nada que exportar."""

    def _no_llamar(*args, **kwargs):  # pragma: no cover
        raise AssertionError("No debería inicializarse el cliente de Google")

    monkeypatch.setattr("job_sniper.sheets.get_sheets_service", _no_llamar)

    export_to_sheets([], settings)  # no lanza excepción


def test_export_to_sheets_sin_id_de_hoja_no_hace_nada(settings, monkeypatch):
    monkeypatch.setattr("job_sniper.sheets.get_sheets_service", lambda s: None)

    export_to_sheets([_oferta()], settings)  # google_sheet_id es None en el fixture


# ── Lectura del historial (columna G) ───────────────────────────────────────
def test_fetch_sent_job_urls_sin_hoja_no_llama_a_google(settings, monkeypatch):
    def _no_llamar(*args, **kwargs):  # pragma: no cover
        raise AssertionError("No debería inicializarse el cliente de Google")

    monkeypatch.setattr("job_sniper.sheets.get_sheets_service", _no_llamar)

    assert fetch_sent_job_urls(settings) == []


def test_fetch_sent_job_urls_lee_la_columna_g(settings, monkeypatch):
    from dataclasses import replace

    capturado = {}

    class _Valores:
        def get(self, **kwargs):
            capturado.update(kwargs)
            return self

        def execute(self):
            return {
                "values": [
                    ["URL Oferta"],
                    ["https://example.test/job/1"],
                    [""],
                    ["https://example.test/job/2"],
                ]
            }

    class _Spreadsheets:
        def values(self):
            return _Valores()

    class _Servicio:
        def spreadsheets(self):
            return _Spreadsheets()

    monkeypatch.setattr("job_sniper.sheets.get_sheets_service", lambda s: _Servicio())
    con_hoja = replace(settings, google_sheet_id="ID_DE_HOJA")

    urls = fetch_sent_job_urls(con_hoja)

    assert urls == [
        "https://example.test/job/1",
        "https://example.test/job/2",
    ]
    assert capturado["range"] == "G:G"


def test_fetch_sent_job_urls_degrada_si_la_api_falla(settings, monkeypatch):
    from dataclasses import replace

    class _ServicioRoto:
        def spreadsheets(self):
            raise RuntimeError("boom")

    monkeypatch.setattr("job_sniper.sheets.get_sheets_service", lambda s: _ServicioRoto())
    con_hoja = replace(settings, google_sheet_id="ID_DE_HOJA")

    assert fetch_sent_job_urls(con_hoja) == []
