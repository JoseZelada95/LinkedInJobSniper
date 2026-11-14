"""Tests del generador de PDFs personalizados (`job_sniper.pdf_generator`).

Se centran en la lógica pura (tokenizar, puntuar, elegir CV, reescribir
viñetas y sanitizar nombres de fichero). La escritura real de PDFs se cubre
en `test_pdf_generator_io.py`.
"""

from __future__ import annotations

from pathlib import Path

from job_sniper.pdf_generator import (
    extract_bullet_lines,
    generate_tailored_bullets,
    score_cv_for_job,
    select_best_cv,
    sanitise_for_filename,
    tokenise,
)

CV_EJEMPLO = """
Jose Alexander Zelada Aviles
Experiencia:
- Desarrollé APIs REST con .NET Core y Entity Framework
- Rediseñé el motor de pagos con C# y SQL Server
- Mantuve sistemas de e-commerce en ASP.NET
"""


# ── Tokenización ────────────────────────────────────────────────────────────
def test_tokenise_a_minusculas_y_sin_puntuacion():
    assert tokenise("ASP.NET, C# y SQL!") == ["asp", "net", "c", "y", "sql"]


# ── Scoring local ───────────────────────────────────────────────────────────
def test_score_cv_es_cero_si_la_oferta_no_tiene_palabras():
    assert score_cv_for_job(CV_EJEMPLO, "", "") == 0.0


def test_score_cv_entre_cero_y_uno():
    puntuacion = score_cv_for_job(CV_EJEMPLO, "Desarrollador .NET", "APIs con C# y SQL Server")

    assert 0.0 <= puntuacion <= 1.0
    assert puntuacion > 0  # el CV comparte .NET / C# / SQL Server


def test_score_cv_maximo_si_todo_coincide():
    assert score_cv_for_job("net c sql", "Net C Sql", "net c sql") == 1.0


# ── Selección del mejor CV ──────────────────────────────────────────────────
def test_select_best_cv_elige_el_mas_afin():
    cvs = [
        (Path("java.pdf"), "spring boot java kotlin"),
        (Path("dotnet.pdf"), "net c sql server apis rest"),
    ]

    mejor = select_best_cv(cvs, "Desarrollador .NET", "APIs con C# y SQL Server")

    assert mejor is not None
    assert mejor[0] == Path("dotnet.pdf")
    assert mejor[2] > 0


def test_select_best_cv_sin_cvs_devuelve_none():
    assert select_best_cv([], "puesto", "desc") is None


# ── Extracción y reescritura de viñetas ─────────────────────────────────────
def test_extract_bullet_lines_detecta_prefijos():
    cv = "- Primera viñeta suficientemente larga\nSin formato\n• Segunda viñeta también larga"
    viñetas = extract_bullet_lines(cv)

    assert len(viñetas) == 2
    assert viñetas[0] == "Primera viñeta suficientemente larga"


def test_extract_bullet_lines_ignora_viñetas_demasiado_cortas():
    assert extract_bullet_lines("- ok\n- va") == []


def test_generate_tailored_bullets_reescribe_al_estilo_xyz():
    """Con la preposición «by» se parte en X by Y (fórmula Google XYZ)."""
    cv = "- Developed the payment engine by migrating .NET Framework to .NET Core"

    salida = generate_tailored_bullets("Backend .NET", "Acme", "migraciones .NET", cv)

    assert "Accomplished" in salida
    assert "by" in salida
    assert salida.startswith("- ")


def test_generate_tailored_bullets_sin_by_usa_texto_de_respaldo():
    cv = "- Developed the payment engine in C Sharp"

    salida = generate_tailored_bullets("Backend .NET", "Acme", "pagos", cv)

    assert "delivering responsibilities closely aligned with the role" in salida


def test_generate_tailored_bullets_sin_viñetas_devuelve_vacio():
    assert generate_tailored_bullets("Puesto", "Acme", "desc", "CV sin viñetas") == ""


def test_generate_tailored_bullets_limita_a_tres():
    cv = "\n".join(f"- Logré la tarea número {i} con C Sharp y .NET" for i in range(10))

    salida = generate_tailored_bullets("Backend .NET", "Acme", ".NET", cv)

    assert salida.count("- Accomplished") == 3


# ── Nombres de fichero ──────────────────────────────────────────────────────
def test_sanitise_quita_caracteres_invalidos():
    assert sanitise_for_filename("Acme: S.L. / Madrid") == "Acme_S_L_Madrid"


def test_sanitise_con_texto_vacio_devuelve_unknown():
    assert sanitise_for_filename("   ") == "Unknown"
    assert sanitise_for_filename("!!!") == "Unknown"


def test_sanitise_conserva_letras_acentuadas_y_numeros():
    assert sanitise_for_filename("Desarrollador 2026") == "Desarrollador_2026"
