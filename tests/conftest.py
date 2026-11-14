"""Fixtures compartidas para toda la suite de tests.

El objetivo es que los tests corran **sin credenciales reales, sin red y sin
tocar ficheros fuera de un directorio temporal** (`tmp_path` de pytest).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_sniper.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """`Settings` de prueba: credenciales ficticias y rutas dentro de `tmp_path`.

    Es el fixture base: los tests que necesiten algo distinto usan
    `dataclasses.replace(settings, campo=valor)` para derivar de él.
    """
    cv_dir = tmp_path / "cvs"
    return Settings(
        # LLM (nunca se usa la red en los tests)
        api_key="gsk_test_ficticia",
        base_url="https://api.test.local/v1",
        llm_model="modelo-de-prueba",
        fallback_models=("modelo-de-prueba",),
        criteria="",
        # CV
        resume_text="CV de prueba: desarrollo backend .NET, C#, ASP.NET y SQL Server.",
        # Scraping
        search_terms=("Desarrollador .NET",),
        locations=("Madrid, Spain",),
        result_limit=2,
        hours_old=24,
        proxy_url=None,
        # Umbrales
        score_threshold=50,
        tailor_threshold=75,
        top_jobs_limit=5,
        max_applications_per_day=2,
        # Integraciones
        google_sheet_id=None,
        email_sender="remitente@example.com",
        email_password="contraseña-ficticia",
        email_receiver="destinatario@example.com",
        gcp_credentials_json=None,
        resume_file_id=None,
        # Ficheros
        log_dir=tmp_path / "logs",
        cv_base_dir=cv_dir,
        tailored_cv_dir=cv_dir / "tailored",
    )
