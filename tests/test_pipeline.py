"""Tests de la orquestación del informe diario (`job_sniper.pipeline`).

Se sustituyen scrapers, LLM, Sheets y SMTP por dobles para comprobar la regla
clave: **una oferta ya enviada no vuelve a aparecer en el email**.
"""

from __future__ import annotations

from job_sniper import pipeline
from job_sniper.history import job_key, load_local_history, history_path, record_sent_offers
from job_sniper.models import JobOffer


def _oferta(url: str, title: str = "Backend .NET Developer") -> JobOffer:
    return JobOffer(
        title=title,
        company="Acme",
        job_url=url,
        score=82,
        reason="Encaje alto",
        yoe="3-5",
    )


def _preparar(monkeypatch, ofertas, enviadas):
    """Sustituye todos los efectos externos del informe diario."""
    monkeypatch.setattr(pipeline, "collect_scored_offers", lambda s, sites, **kw: ofertas)
    monkeypatch.setattr(pipeline, "fetch_sent_job_urls", lambda s: [])
    monkeypatch.setattr(pipeline, "tailor_bullets_for_top", lambda offers, s: None)
    monkeypatch.setattr(pipeline, "export_to_sheets", lambda offers, s: None)
    monkeypatch.setattr(
        pipeline,
        "send_email",
        lambda offers, s, pdf_paths=None: enviadas.append(list(offers)) or True,
    )


def test_daily_report_descarta_ofertas_ya_enviadas(settings, monkeypatch):
    vieja = _oferta("https://example.test/job/1")
    nueva = _oferta("https://example.test/job/2", title="Otro puesto")
    record_sent_offers([vieja], settings)

    enviadas = []
    _preparar(monkeypatch, [vieja, nueva], enviadas)

    pipeline.run_daily_report(settings)

    assert len(enviadas) == 1
    assert [oferta.job_url for oferta in enviadas[0]] == ["https://example.test/job/2"]


def test_daily_report_sin_ofertas_nuevas_no_envia(settings, monkeypatch):
    vieja = _oferta("https://example.test/job/1")
    record_sent_offers([vieja], settings)

    enviadas = []
    _preparar(monkeypatch, [vieja], enviadas)

    pipeline.run_daily_report(settings)

    assert enviadas == []


def test_daily_report_registra_las_enviadas(settings, monkeypatch):
    oferta = _oferta("https://example.test/job/3")

    enviadas = []
    _preparar(monkeypatch, [oferta], enviadas)

    pipeline.run_daily_report(settings)

    historial = load_local_history(history_path(settings))
    assert job_key(oferta.job_url) in historial


def test_daily_report_descarta_ofertas_ya_preparadas_por_el_agente(settings, monkeypatch):
    """Lo que el agente autónomo ya preparó no debe volver por email."""
    preparada = _oferta("https://example.test/job/5")
    nueva = _oferta("https://example.test/job/6", title="Otro puesto")

    settings.applications_log.parent.mkdir(parents=True, exist_ok=True)
    settings.applications_log.write_text(
        "Backend .NET Developer | Acme | https://example.test/job/5 | 82 | cv.pdf | "
        "2026-01-01 10:00:00 | Prepared\n",
        encoding="utf-8",
    )

    enviadas = []
    _preparar(monkeypatch, [preparada, nueva], enviadas)

    pipeline.run_daily_report(settings)

    assert [oferta.job_url for oferta in enviadas[0]] == ["https://example.test/job/6"]


def test_daily_report_si_falla_el_email_no_registra_ni_exporta(settings, monkeypatch):
    """Un fallo de SMTP no debe «quemar» las ofertas como ya enviadas."""
    oferta = _oferta("https://example.test/job/4")

    exportadas = []
    monkeypatch.setattr(pipeline, "collect_scored_offers", lambda s, sites, **kw: [oferta])
    monkeypatch.setattr(pipeline, "fetch_sent_job_urls", lambda s: [])
    monkeypatch.setattr(pipeline, "tailor_bullets_for_top", lambda offers, s: None)
    monkeypatch.setattr(
        pipeline,
        "export_to_sheets",
        lambda offers, s: exportadas.append(list(offers)),
    )
    monkeypatch.setattr(pipeline, "send_email", lambda offers, s, pdf_paths=None: False)

    pipeline.run_daily_report(settings)

    assert exportadas == []
    assert not history_path(settings).exists()


def test_ciclo_autonomo_descarta_ofertas_ya_preparadas(settings):
    """El agente no debe regenerar el PDF de una oferta ya preparada."""
    preparada = _oferta("https://example.test/job/5")
    nueva = _oferta("https://example.test/job/6", title="Otro puesto")

    settings.applications_log.parent.mkdir(parents=True, exist_ok=True)
    settings.applications_log.write_text(
        "Backend .NET Developer | Acme | https://example.test/job/5 | 82 | cv.pdf | "
        "2026-01-01 10:00:00 | Prepared\n",
        encoding="utf-8",
    )

    pendientes = pipeline.drop_prepared_offers(
        [preparada, nueva], settings.applications_log
    )

    assert [oferta.job_url for oferta in pendientes] == ["https://example.test/job/6"]


def test_collect_scored_offers_omite_enviadas_antes_de_evaluar(settings, monkeypatch):
    """Las ofertas ya enviadas se descartan antes de gastar tokens en el LLM."""
    import pandas as pd

    from job_sniper.models import JobEvaluation

    filas = pd.DataFrame(
        [
            {
                "title": "A",
                "company": "Acme",
                "job_url": "https://example.test/job/1",
                "description": "x" * 60,
            },
            {
                "title": "B",
                "company": "Beta",
                "job_url": "https://example.test/job/2",
                "description": "x" * 60,
            },
        ]
    )
    monkeypatch.setattr(pipeline, "scrape_all_jobs", lambda s, sites: filas)
    evaluadas = []
    monkeypatch.setattr(
        pipeline,
        "evaluate_job",
        lambda title, description, s: evaluadas.append(title)
        or JobEvaluation(score=80, reason="ok", yoe="1"),
    )

    ofertas = pipeline.collect_scored_offers(
        settings,
        ("linkedin",),
        skip_keys={job_key("https://example.test/job/1")},
    )

    assert evaluadas == ["B"]
    assert [oferta.job_url for oferta in ofertas] == ["https://example.test/job/2"]
