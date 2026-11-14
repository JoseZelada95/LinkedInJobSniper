"""Tests del registro diario de solicitudes (`job_sniper.applications`)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from job_sniper.applications import (
    STATUS_PREPARED,
    applications_today_count,
    load_logged_job_urls,
    log_application,
    prepare_application,
)
from job_sniper.models import JobOffer


def _oferta() -> JobOffer:
    return JobOffer(
        title="Backend .NET Developer",
        company="Acme",
        job_url="https://example.test/job/1",
        score=85,
        reason="Encaje alto",
        yoe="3-5",
    )


# ── Conteo diario ───────────────────────────────────────────────────────────
def test_contador_en_fichero_inexistente(tmp_path: Path):
    assert applications_today_count(tmp_path / "applications.log") == 0


def test_contador_solo_cuenta_las_lineas_de_hoy(tmp_path: Path):
    from datetime import datetime

    log = tmp_path / "applications.log"
    hoy = datetime.now().strftime("%Y-%m-%d")
    contenido = (
        f"puesto a | empresa | url | 80 | cv.pdf | {hoy} 10:00:00 | Prepared\n"
        f"puesto b | empresa | url | 81 | cv.pdf | {hoy} 11:00:00 | Prepared\n"
        "puesto c | empresa | url | 70 | cv.pdf | 2020-01-01 09:00:00 | Prepared\n"
        "\n"  # línea vacía: no debe contar
    )
    log.write_text(contenido, encoding="utf-8")

    assert applications_today_count(log) == 2


def test_load_logged_job_urls_lee_la_columna_url(tmp_path: Path):
    log = tmp_path / "applications.log"
    contenido = (
        "puesto a | empresa | https://example.test/job/1 | 80 | cv.pdf | 2026-01-01 10:00:00 | Prepared\n"
        "puesto b | empresa |  | 81 | cv.pdf | 2026-01-01 11:00:00 | Prepared\n"
        "\n"
    )
    log.write_text(contenido, encoding="utf-8")

    assert load_logged_job_urls(log) == ["https://example.test/job/1"]


def test_load_logged_job_urls_sin_fichero(tmp_path: Path):
    assert load_logged_job_urls(tmp_path / "applications.log") == []


# ── Escritura del log ───────────────────────────────────────────────────────
def test_log_application_escribe_todos_los_campos(tmp_path: Path):
    log = tmp_path / "applications.log"

    log_application(_oferta(), cv_path=tmp_path / "cv.pdf", status=STATUS_PREPARED, applications_log=log)

    lineas = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lineas) == 1

    campos = lineas[0].split(" | ")
    assert campos[0] == "Backend .NET Developer"
    assert campos[1] == "Acme"
    assert campos[2] == "https://example.test/job/1"
    assert campos[3] == "85"
    assert campos[4] == str(tmp_path / "cv.pdf")
    assert campos[6] == STATUS_PREPARED


def test_log_application_sin_cv(tmp_path: Path):
    log = tmp_path / "applications.log"

    log_application(_oferta(), cv_path=None, status=STATUS_PREPARED, applications_log=log)

    assert log.read_text(encoding="utf-8").count(" |  | ") == 1  # hueco del campo CV


# ── Límite diario ───────────────────────────────────────────────────────────
def test_prepare_application_respeta_el_limite(settings):
    """Con límite 2, la tercera solicitud del día no debe registrarse."""
    limite = settings.max_applications_per_day  # 2 en el fixture

    registradas = [prepare_application(_oferta(), None, settings) for _ in range(limite + 1)]

    assert registradas == [True, True, False]
    assert applications_today_count(settings.applications_log) == limite


def test_prepare_application_registra_la_intencion(settings):
    assert prepare_application(_oferta(), Path("cv.pdf"), settings) is True

    contenido = settings.applications_log.read_text(encoding="utf-8")
    assert STATUS_PREPARED in contenido
    assert "manual submit" in contenido


def test_limite_configurable(settings):
    """El límite diario es configuración, no una constante incrustada."""
    from pathlib import Path as P  # noqa: F401  (import local para claridad)

    estricto = replace(settings, max_applications_per_day=1)

    assert prepare_application(_oferta(), None, estricto) is True
    assert prepare_application(_oferta(), None, estricto) is False
