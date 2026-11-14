"""Tests de E/S del generador de PDFs: creación real de ficheros en `tmp_path`.

Únicos tests de la suite que escriben ficheros (siempre dentro del directorio
temporal que gestiona pytest).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import fitz  # PyMuPDF

from job_sniper.models import JobOffer
from job_sniper.pdf_generator import (
    ensure_dirs,
    extract_pdf_text,
    list_original_cvs,
    tailor_cv_for_job,
    write_tailored_pdf,
)


def _crear_pdf(ruta: Path, texto: str) -> Path:
    """Crea un PDF mínimo de una página con el texto indicado."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    documento = fitz.open()
    pagina = documento.new_page()
    pagina.insert_textbox(fitz.Rect(40, 40, 550, 800), texto, fontsize=11)
    documento.save(str(ruta))
    documento.close()
    return ruta


def _oferta(**kwargs) -> JobOffer:
    base = dict(
        title="Backend .NET Developer",
        company="Acme",
        job_url="https://example.test/job/1",
        score=88,
        reason="Encaje alto",
        yoe="3-5",
        description="Buscamos .NET y SQL Server para APIs REST con Entity Framework.",
    )
    base.update(kwargs)
    return JobOffer(**base)


# ── Lectura ─────────────────────────────────────────────────────────────────
def test_extract_pdf_text_devuelve_el_texto(tmp_path: Path):
    ruta = _crear_pdf(tmp_path / "cv.pdf", "Jose Alexander Zelada Aviles\nBackend .NET")

    texto = extract_pdf_text(ruta)

    assert "Jose Alexander Zelada Aviles" in texto
    assert "Backend .NET" in texto


def test_list_original_cvs_sin_carpeta(settings):
    assert list_original_cvs(settings) == []  # cv_base_dir aún no existe


def test_list_original_cvs_encuentra_el_cv(settings):
    ensure_dirs(settings)
    _crear_pdf(settings.cv_base_dir / "cv_base.pdf", "CV base")

    assert [p.name for p in list_original_cvs(settings)] == ["cv_base.pdf"]


def test_list_original_cvs_excluye_los_generados(settings):
    """Los PDFs de `tailored/` no deben reutilizarse como CV base en bucle."""
    ensure_dirs(settings)
    _crear_pdf(settings.cv_base_dir / "cv_base.pdf", "CV base")
    _crear_pdf(settings.tailored_cv_dir / "resultado_previo.pdf", "Salida previa")

    nombres = [p.name for p in list_original_cvs(settings)]

    assert nombres == ["cv_base.pdf"]


# ── Escritura ───────────────────────────────────────────────────────────────
def test_write_tailored_pdf_preserva_el_original(tmp_path: Path):
    original = _crear_pdf(tmp_path / "original.pdf", "Contenido original del CV")
    salida = tmp_path / "salida.pdf"

    write_tailored_pdf(original, salida, "Backend .NET", "Acme", "- Logré X by Y")

    texto = extract_pdf_text(salida)
    assert "Contenido original del CV" in texto  # el CV sigue intacto tras la portada
    assert "CV ADAPTADO A LA OFERTA" in texto  # portada diseñada, no solo texto plano
    assert "Backend .NET" in texto and "Acme" in texto


# ── Flujo completo ──────────────────────────────────────────────────────────
def test_tailor_cv_for_job_genera_el_pdf(settings):
    ensure_dirs(settings)
    _crear_pdf(
        settings.cv_base_dir / "cv_base.pdf",
        "CV\n- Desarrollé APIs REST con .NET y Entity Framework\n- Rediseñé pagos con C Sharp",
    )

    ruta = tailor_cv_for_job(_oferta(), settings)

    assert ruta is not None
    assert ruta.exists()
    assert ruta.parent == settings.tailored_cv_dir
    assert "Acme" in ruta.name and "Backend_NET_Developer" in ruta.name


def test_tailor_cv_for_job_datos_incompletos(settings):
    """Sin empresa no se genera nada (devuelve None, no lanza)."""
    assert tailor_cv_for_job(_oferta(company=""), settings) is None
    assert tailor_cv_for_job(_oferta(description=""), settings) is None


def test_tailor_cv_for_job_sin_cvs(settings):
    """Directorio de CVs vacío → None con aviso (el ciclo sigue con la siguiente oferta)."""
    ensure_dirs(settings)

    assert tailor_cv_for_job(_oferta(), settings) is None
