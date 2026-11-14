"""Tests del email diario (`job_sniper.emailer`).

Cobertura de las partes puras: sanitizado de HTML (seguridad) y construcción
del cuerpo del mensaje. El envío SMTP real no se testea (requiere red).
"""

from __future__ import annotations

from job_sniper.emailer import build_email_html, sanitise_bullets_html, send_email
from job_sniper.models import JobOffer


def _oferta(**kwargs) -> JobOffer:
    base = dict(
        title="Backend .NET Developer",
        company="Acme",
        job_url="https://example.test/job/1",
        score=82,
        reason="Coincidencia alta con .NET",
        yoe="3-5",
        description="",
    )
    base.update(kwargs)
    return JobOffer(**base)


# ── Sanitizado de la salida del LLM (seguridad) ─────────────────────────────
def test_sanitise_conserva_las_etiquetas_permitidas():
    entrada = "<ul><li>Logré X mejorando Y</li></ul>"

    assert sanitise_bullets_html(entrada) == entrada


def test_sanitise_elimina_scripts():
    """Un `<script>` inyectado por el modelo debe desaparecer (queda su texto)."""
    salida = sanitise_bullets_html("<ul><script>alert(1)</script><li>ok</li></ul>")

    assert "<script" not in salida
    assert "ok" in salida


def test_sanitise_escapa_ampersands():
    """Un `&` literal debe escaparse para no romper el HTML del correo."""
    salida = sanitise_bullets_html("<ul><li>Rediseñé R&D del negocio</li></ul>")

    assert "&amp;" in salida
    assert "Rediseñé R&amp;D" in salida


def test_sanitise_elimina_etiquetas_desconocidas_conservando_el_texto():
    salida = sanitise_bullets_html("<ul><li><em>énfasis</em> y <strong>fuerza</strong></li></ul>")

    assert "<em>" not in salida
    assert "<strong>" not in salida
    assert "énfasis" in salida
    assert "fuerza" in salida


def test_sanitise_normaliza_mayusculas_y_espacios():
    """Las etiquetas permitidas se reescriben en su forma canónica (`<ul>`, `<li>`)."""
    salida = sanitise_bullets_html("<UL ><LI >hecho</LI ></UL >")

    assert salida == "<ul><li>hecho</li></ul>"


def test_sanitise_acepta_entrada_vacia():
    assert sanitise_bullets_html("") == ""
    assert sanitise_bullets_html(None) == ""  # type: ignore[arg-type]


# ── Construcción del HTML ───────────────────────────────────────────────────
def test_build_email_html_contiene_las_ofertas(settings):
    html = build_email_html([_oferta()], settings)

    assert "Backend .NET Developer" in html
    assert "Acme" in html
    assert "https://example.test/job/1" in html
    assert "82" in html


def test_build_email_html_escapa_el_titulo(settings):
    """Un título con HTML no debe inyectarse sin escapar en el correo."""
    html = build_email_html([_oferta(title="Senior <b>Dev</b>")], settings)

    assert "<b>Dev</b>" not in html
    assert "&lt;b&gt;Dev&lt;/b&gt;" in html


def test_build_email_html_sin_bullets_no_muestra_la_fila(settings):
    html = build_email_html([_oferta()], settings)

    assert "Tailored CV Bullets" not in html


def test_build_email_html_con_bullets_muestra_la_fila(settings):
    oferta = _oferta(tailored_bullets="<ul><li>viñeta</li></ul>")

    html = build_email_html([oferta], settings)

    assert "Tailored CV Bullets" in html
    assert "<li>viñeta</li>" in html


def test_build_email_html_sin_hoja_de_seguimiento_no_menciona_sheets(settings):
    html = build_email_html([_oferta()], settings)  # fixture sin google_sheet_id

    assert "tracking sheet" not in html


def test_build_email_html_menciona_sheets_si_hay_id(settings):
    from dataclasses import replace

    con_hoja = replace(settings, google_sheet_id="ID_DE_HOJA")

    html = build_email_html([_oferta()], con_hoja)

    assert "ID_DE_HOJA" in html


# ── Envío degradado ─────────────────────────────────────────────────────────
def test_send_email_sin_ofertas_no_envia(settings, monkeypatch):
    def _no_enviar(*args, **kwargs):  # pragma: no cover
        raise AssertionError("No debería enviarse ningún email")

    monkeypatch.setattr("job_sniper.emailer.build_email_html", _no_enviar)

    send_email([], settings)  # no lanza


def test_send_email_sin_configuracion_no_envia(settings, monkeypatch):
    from dataclasses import replace

    from job_sniper import emailer as modulo_email

    incompleto = replace(settings, email_password=None)

    llamado = []

    def _no_enviar(*args, **kwargs):  # pragma: no cover
        llamado.append(True)

    monkeypatch.setattr(modulo_email, "build_email_html", _no_enviar)

    send_email([_oferta()], incompleto)  # email_configured → False

    assert llamado == []
