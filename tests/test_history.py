"""Tests del historial de ofertas enviadas (`job_sniper.history`).

Se comprueba la clave canónica (base del deduplicado), la lectura/escritura
del fichero local y el filtrado de ofertas repetidas. Todo offline.
"""

from __future__ import annotations

from pathlib import Path

from job_sniper.history import (
    filter_new_offers,
    history_path,
    job_key,
    load_local_history,
    load_sent_keys,
    record_sent_offers,
)
from job_sniper.models import JobOffer


def _oferta(**kwargs) -> JobOffer:
    base = dict(
        title="Backend .NET Developer",
        company="Acme",
        job_url="https://example.test/job/1",
        score=82,
        reason="Coincidencia alta",
        yoe="3-5",
    )
    base.update(kwargs)
    return JobOffer(**base)


# ── Clave canónica ──────────────────────────────────────────────────────────
def test_job_key_elimina_query_y_barra_final():
    """El tracking de LinkedIn (`?eBP=...`) no debe crear duplicados."""
    con_tracking = job_key("https://www.linkedin.com/jobs/view/123/?eBP=abc&refId=xyz")
    limpia = job_key("https://linkedin.com/jobs/view/123")

    assert con_tracking == limpia


def test_job_key_normaliza_esquema_y_host():
    assert job_key("HTTPS://WWW.Example.TEST/Job/1/") == job_key(
        "https://example.test/Job/1"
    )


def test_job_key_sin_url_usa_titulo_y_empresa():
    clave = job_key("", "Backend .NET Developer", "Acme")

    assert clave == "backend .net developer|acme"


def test_job_key_distingue_ofertas_distintas():
    assert job_key("https://example.test/job/1") != job_key("https://example.test/job/2")


def test_job_key_conserva_el_id_de_indeed():
    """En Indeed el ID viaja en `?jk=`; no debe perderse al limpiar tracking."""
    con_tracking = job_key("https://es.indeed.com/viewjob?jk=abc123&from=serp")
    limpia = job_key("https://www.indeed.com/viewjob?jk=abc123")

    assert con_tracking == limpia


def test_job_key_distingue_dos_ofertas_de_indeed():
    """Sin el `jk` todas las ofertas de Indeed tendrían la misma clave."""
    assert job_key("https://indeed.com/viewjob?jk=a") != job_key(
        "https://indeed.com/viewjob?jk=b"
    )


def test_job_key_normaliza_el_subdominio_de_idioma():
    assert job_key("https://es.linkedin.com/jobs/view/1") == job_key(
        "https://www.linkedin.com/jobs/view/1"
    )


def test_job_key_une_variantes_de_url_de_indeed():
    """El mismo `jk` con distinta ruta (`/viewjob` vs `/rc/clk`) es la misma oferta."""
    assert job_key("https://es.indeed.com/viewjob?jk=abc123") == job_key(
        "https://es.indeed.com/rc/clk?jk=abc123&fccid=xyz"
    )


def test_job_key_distingue_ofertas_de_glassdoor():
    """Antes todas las ofertas de Glassdoor colapsaban en la misma clave."""
    assert job_key("https://www.glassdoor.es/job-listing/j?jl=111") != job_key(
        "https://www.glassdoor.es/job-listing/j?jl=222"
    )


def test_job_key_distingue_ofertas_de_infojobs():
    assert job_key("https://www.infojobs.net/madrid/net/of-i111") != job_key(
        "https://www.infojobs.net/madrid/net/of-i222"
    )


# ── Lectura del fichero local ───────────────────────────────────────────────
def test_load_local_history_fichero_inexistente(tmp_path: Path):
    assert load_local_history(tmp_path / "sent_jobs.json") == {}


def test_load_local_history_json_corrupto_no_lanza(tmp_path: Path):
    ruta = tmp_path / "sent_jobs.json"
    ruta.write_text("{esto no es json", encoding="utf-8")

    assert load_local_history(ruta) == {}


def test_load_local_history_acepta_lista_de_urls(tmp_path: Path):
    ruta = tmp_path / "sent_jobs.json"
    ruta.write_text('["https://example.test/job/1"]', encoding="utf-8")

    historial = load_local_history(ruta)

    assert job_key("https://example.test/job/1") in historial


# ── Registro de enviadas ────────────────────────────────────────────────────
def test_record_sent_offers_crea_el_historial(settings):
    assert record_sent_offers([_oferta()], settings) == 1

    historial = load_local_history(history_path(settings))
    assert job_key("https://example.test/job/1") in historial


def test_record_sent_offers_no_duplica_entradas(settings):
    oferta = _oferta()
    record_sent_offers([oferta], settings)
    record_sent_offers([oferta], settings)

    assert len(load_local_history(history_path(settings))) == 1


def test_record_sent_offers_sin_ofertas_no_crea_fichero(settings):
    assert record_sent_offers([], settings) == 0
    assert not history_path(settings).exists()


# ── Filtrado ────────────────────────────────────────────────────────────────
def test_filter_new_offers_descarta_las_ya_enviadas(settings):
    enviada = _oferta(job_url="https://example.test/job/1")
    nueva = _oferta(job_url="https://example.test/job/2", title="Otro puesto")
    record_sent_offers([enviada], settings)

    claves = load_sent_keys(settings)
    resultado = filter_new_offers([enviada, nueva], claves)

    assert [o.job_url for o in resultado] == ["https://example.test/job/2"]


def test_filter_new_offers_sin_historial_devuelve_todas(settings):
    ofertas = [_oferta(job_url="https://example.test/job/1")]

    assert filter_new_offers(ofertas, set()) == ofertas


def test_load_sent_keys_incluye_urls_externas(settings):
    claves = load_sent_keys(settings, ["https://example.test/job/9"])

    assert job_key("https://example.test/job/9") in claves
    assert job_key("https://example.test/job/10") not in claves


def test_load_sent_keys_recalcula_entradas_antiguas(settings):
    """Un historial con claves del formato viejo no debe producir repetidos."""
    ruta = history_path(settings)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        '{"https://es.indeed.com/viewjob": {"job_url": '
        '"https://es.indeed.com/viewjob?jk=abc123"}}',
        encoding="utf-8",
    )

    claves = load_sent_keys(settings)

    assert job_key("https://es.indeed.com/rc/clk?jk=abc123") in claves
