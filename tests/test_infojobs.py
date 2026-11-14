"""Tests del cliente de la API oficial de InfoJobs (`job_sniper.infojobs`).

Ningún test toca la red: `infojobs._http_get` y `time.sleep` se sustituyen por
dobles de prueba con `monkeypatch`. Los cuerpos JSON imitan la respuesta
documentada en developer.infojobs.net (v9 para búsqueda, v7 para detalle).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import replace
from typing import List, Optional

import pandas as pd
import pytest

from job_sniper import infojobs, scraping


# ── Dobles de prueba ─────────────────────────────────────────────────────────

class _Respuesta:
    """Sustituto de `requests.Response` con lo que el módulo realmente usa."""

    def __init__(self, datos: Optional[dict], status: int = 200):
        self.status_code = status
        self._datos = datos

    def json(self) -> dict:
        if self._datos is None:
            raise ValueError("no hay JSON")
        return self._datos


def settings_con_infojobs(settings):
    """Deriva el settings base añadiendo credenciales ficticias de InfoJobs."""
    return replace(
        settings,
        infojobs_client_id="client-id-de-prueba",
        infojobs_client_secret="client-secret-de-prueba",
    )


LISTA_ES = {  # respuesta del endpoint /api/9/offer
    "currentPage": 1,
    "pageSize": 2,
    "totalResults": 2,
    "offers": [
        {
            "id": "oferta-abc123",
            "title": "Desarrollador .NET",
            "link": "https://www.infojobs.net/oferta/net/of-abc123",
            "published": "2026-10-20T09:30:00.000+0000",
            "profile": {"name": "Tecnologias S.L."},
        },
        {
            "id": "oferta-xyz789",
            "title": "Backend C#",
            "link": "https://www.infojobs.net/oferta/c-of-x1",
            "published": "2026-10-20T14:00:00.000+0000",
            "author": {"name": "Otra S.A."},
        },
    ],
}

DETALLE_ES = {  # respuesta del endpoint /api/7/offer/{id}
    "id": "oferta-abc123",
    "title": "Desarrollador .NET",
    "description": "Para participación en el desarrollo de aplicaciones corporativas.",
    "minRequirements": "C#, ASP.NET Core, SQL Server",
    "desiredRequirements": "Azure, Docker",
}


@pytest.fixture
def sin_espera(monkeypatch):
    """Elimina las pausas para que los tests no duerman."""
    monkeypatch.setattr(infojobs.time, "sleep", lambda _segundos: None)
    monkeypatch.setattr(scraping.time, "sleep", lambda _segundos: None)


# Referencia temporal fija para el filtro de antigüedad (2026-10-20T15:00Z):
# en LISTA_ES la 1.ª oferta es de ~5,5 h antes y la 2.ª de hace 1 h; con
# HOURS_OLD=24 ambas son frescas y con HOURS_OLD=3 solo la segunda.
_AHORA = _dt.datetime(2026, 10, 20, 15, 0, 0)


# ── Tests ────────────────────────────────────────────────────────────────────

def test_sin_credenciales_no_llama_a_la_red(settings, monkeypatch, sin_espera):
    """Sin INFOJOBS_CLIENT_ID/SECRET la fuente se omite sin red de por medio."""

    def _prohibido(*_args, **_kwargs):
        raise AssertionError("no debe haber red sin credenciales")

    monkeypatch.setattr(infojobs.requests, "get", _prohibido)
    assert not infojobs.has_credentials(settings)

    resultado = infojobs.scrape_infojobs(settings)
    assert resultado.empty


def test_scrape_infojobs_mapea_columnas_como_jobspy(settings, monkeypatch, sin_espera):
    """El DataFrame sale con las columnas que espera `collect_scored_offers`."""
    llamadas: List[str] = []

    def _get(path, params=None, **_kwargs):
        llamadas.append(path)
        if path.endswith(infojobs.LIST_ENDPOINT):  # búsqueda (llega la URL completa)
            return _Respuesta(LISTA_ES)
        return _Respuesta(DETALLE_ES)  # detalle

    monkeypatch.setattr(infojobs.requests, "get", _get)

    df = infojobs.scrape_infojobs(
        settings_con_infojobs(settings), ahora=_AHORA
    )

    assert not df.empty
    assert {"title", "company", "job_url", "description", "location"}.issubset(df.columns)
    assert df.loc[0, "offer_id"] == "oferta-abc123"
    # El detalle se pidió y los tres bloques textuales quedaron concatenados.
    assert "C#, ASP.NET Core" in df.loc[0, "description"]
    assert "Azure" in df.loc[0, "description"]
    # 1 búsqueda (1 término × 1 ubicación) + 1 detalle por oferta de la lista.
    assert len(llamadas) == 3


def test_filtro_de_antiguedad_descarta_ofertas_viejas(settings, monkeypatch, sin_espera):
    """Con HOURS_OLD=3 solo sobrevive la oferta publicada hace 1 hora."""
    lista = {
        "offers": [
            dict(LISTA_ES["offers"][0]),  # 09:30, vieja
            dict(LISTA_ES["offers"][1]),  # 14:00, fresca
        ]
    }

    def _get(_path, _params=None, **_kwargs):
        return _Respuesta(lista)

    monkeypatch.setattr(infojobs.requests, "get", _get)

    df = infojobs.scrape_infojobs(
        replace(settings_con_infojobs(settings), hours_old=3), ahora=_AHORA
    )

    assert len(df) == 1
    assert df.loc[0, "offer_id"] == "oferta-xyz789"


def test_reintento_ante_http_500_y_agotamiento(settings, monkeypatch, sin_espera):
    """3 intentos ante errores HTTP y, agotados, None."""
    estados: List[str] = []

    def _get(_path, _params=None, **_kwargs):
        estados.append("intento")
        return _Respuesta(None, status=500)

    monkeypatch.setattr(infojobs.requests, "get", _get)

    resultado = infojobs.search_offers(
        "Desarrollador .NET", "Madrid", settings_con_infojobs(settings)
    )

    assert resultado is None
    assert len(estados) == infojobs.MAX_ATTEMPTS


def test_scrape_all_jobs_dedupe_entre_fuentes(settings, monkeypatch, sin_espera):
    """'infojobs' se concatena con jobspy y se deduplica por job_url."""
    compartida = "https://www.infojobs.net/oferta/net/of-abc123"
    df_jobspy = pd.DataFrame(
        [
            {"title": "Net .NET", "company": "A", "job_url": compartida, "description": "d"},
            {"title": "Otro", "company": "B", "job_url": "https://x/otro", "description": "d"},
        ]
    )
    df_infojobs = pd.DataFrame(
        [{"title": "Public .NET", "company": "C", "job_url": compartida, "description": "d-api"}]
    )

    monkeypatch.setattr(scraping, "scrape_search", lambda *args, **kwargs: df_jobspy.copy())
    monkeypatch.setattr(infojobs, "scrape_infojobs", lambda _settings: df_infojobs.copy())

    resultado = scraping.scrape_all_jobs(settings, sites=("linkedin", "infojobs"))

    urls = set(resultado["job_url"])
    assert len(resultado) == 2  # la compartida solo una vez
    assert urls == {compartida, "https://x/otro"}


def test_scrape_all_jobs_sin_infojobs_no_consulta_api(settings, monkeypatch):
    """Si 'infojobs' no está en `sites`, el módulo infojobs no se toca."""
    df = pd.DataFrame([{"title": "T", "company": "C", "job_url": "u", "description": "d"}])
    monkeypatch.setattr(scraping, "scrape_search", lambda *args, **kwargs: df.copy())

    def _prohibido(*_args, **_kwargs):
        raise AssertionError("InfoJobs no debe consultarse si no está en sites")

    monkeypatch.setattr(infojobs, "scrape_infojobs", _prohibido)

    resultado = scraping.scrape_all_jobs(settings, sites=("linkedin",))
    assert len(resultado) == 1


def test_location_to_params_convierte_ubicaciones():
    """'Madrid, Spain' → province; 'Remote' → telecommute."""
    assert infojobs._location_to_params("Madrid, Spain") == {"province": "madrid"}
    assert infojobs._location_to_params("Remote") == {"telecommute": "1"}
    assert infojobs._location_to_params("") == {}
