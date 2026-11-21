"""Configuración centralizada del agente.

Toda la configuración proviene de dos fuentes:

  1. Constantes del proyecto (términos de búsqueda, ubicaciones, umbrales).
  2. Variables de entorno (`.env` en local o GitHub Secrets en CI).

Regla de arquitectura: **ningún otro módulo debe llamar a `os.getenv`**.
Aquí se lee el entorno, se valida y se expone como un único objeto
inmutable (`Settings`). Así evitamos el acoplamiento por variables
globales y los efectos secundarios al importar módulos.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from dotenv import load_dotenv

# ────────────────────────────────────────────────────────────────────────────
# Términos de búsqueda (estrategia dual del candidato)
# ────────────────────────────────────────────────────────────────────────────
# Perfil: Backend .NET Developer en Madrid.
#   · Línea A: puestos Mid/Senior .NET (experiencia 2015-2024).
#   · Línea B: puestos Junior/Prácticas .NET o Java (reciclaje / reincorporación).
SEARCH_TERMS: Tuple[str, ...] = (
    # ── Línea A: Mid/Senior .NET (experiencia 2015-2024) ──
    "Desarrollador .NET",
    "Backend .NET Developer",
    "Desarrollador Backend C#",
    "ASP.NET Developer",
    ".NET Developer",
    "Analista .NET",
    "Desarrollador C# .NET Core",
    "Desarrollador Full Stack .NET",
    "C# ASP.NET Developer",
    "Desarrollador Web API .NET",
    "Entity Framework Developer",
    "Desarrollador Blazor",
    "VB.NET Developer",
    "Desarrollador SQL Server",
    "SQL Developer T-SQL",
    "Desarrollador Azure .NET",
    "Azure Developer",
    "Desarrollador Microservicios",
    "Backend Developer C#",
    "Software Engineer .NET",
    "C# Developer Spain",
    # ── Nichos (experiencia previa) ──
    "Analista de Aplicaciones",
    "E-commerce Developer .NET",
    # ── Línea B: Junior/Prácticas (brecha + FP en curso) ──
    "Junior .NET Developer",
    "Junior C# Developer",
    "Desarrollador Junior .NET",
    "Becario .NET",
    "Prácticas .NET Desarrollador",
    "Junior Backend Developer",
    "Junior Full Stack",
    # ── Línea Java (curso Spring Boot en curso) ──
    "Junior Java Developer",
    "Java Spring Boot Developer",
    "Desarrollador Java Junior",
    "Java Backend Developer",
    # ── Roles puente ──
    "Soporte Técnico .NET",
    "Analista Junior",
)

# Ubicaciones a las que se lanzan las búsquedas.
LOCATIONS: Tuple[str, ...] = ("Madrid, Spain", "Madrid, España", "Remote")

# Sitios que usa el informe diario (email + Sheets) y el agente autónomo.
# `infojobs` no viene de jobspy (no lo soporta): se consulta por la API oficial
# de InfoJobs (ver `job_sniper/infojobs.py`) y solo si hay credenciales.
DAILY_SITES: Tuple[str, ...] = ("linkedin", "indeed", "glassdoor", "infojobs")
AGENT_SITES: Tuple[str, ...] = ("linkedin",)

# ────────────────────────────────────────────────────────────────────────────
# Valores por defecto (solo se sobrescriben vía variable de entorno)
# ────────────────────────────────────────────────────────────────────────────
DEFAULT_LLM_MODEL = "llama-3.3-70b-versatile"
DEFAULT_RESULT_LIMIT = 8          # ofertas por término de búsqueda
DEFAULT_HOURS_OLD = 48            # antigüedad máxima de la oferta
DEFAULT_SCORE_THRESHOLD = 50      # puntuación mínima para aparecer en el email
DEFAULT_TAILOR_THRESHOLD = 75     # puntuación mínima para personalizar el CV
DEFAULT_TOP_JOBS_LIMIT = 20       # máximo de ofertas en el informe diario
DEFAULT_MAX_APPLICATIONS_PER_DAY = 20  # límite diario de solicitudes (agente)

# Alias antiguos → IDs reales que expone el proveedor (Groq renombra modelos).
MODEL_ALIASES = {
    "llama3-70b-8192": "llama-3.3-70b-versatile",
}

# Modelos de respaldo, en orden, que se intentan si el principal falla.
FALLBACK_MODELS: Tuple[str, ...] = (
    "llama-3.3-70b-versatile",  # primer respaldo potente
    "llama-3.1-8b-instant",     # segundo respaldo rápido
)

# Carpeta por defecto donde viven los CVs originales (solo modo autónomo).
DEFAULT_CV_DIR = Path.home() / "Downloads" / "CVs"
DEFAULT_LOG_DIR = Path.home() / ".linkedin_job_sniper" / "logs"

# DeepSeek: fallback de pago cuando Groq agota su cuota gratuita. Off-peak es
# la mitad de precio; el peak es 01:00-04:00 y 06:00-10:00 UTC, L-V.
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_PEAK_HOURS_UTC = ((1, 4), (6, 10))


class ConfigurationError(Exception):
    """Configuración incompleta o inválida: impide arrancar con un mensaje claro."""


# ────────────────────────────────────────────────────────────────────────────
# Funciones puras (fáciles de testear)
# ────────────────────────────────────────────────────────────────────────────
def resolve_model_name(model_name: str) -> str:
    """Traduce un alias antiguo de modelo a su ID real (p. ej. 'llama3-70b-8192')."""
    return MODEL_ALIASES.get(model_name, model_name)


def build_fallback_models(primary_model: str) -> List[str]:
    """Devuelve la cadena de modelos a intentar, en orden y sin duplicados.

    El modelo configurado en el Secret siempre va primero; los de respaldo
    se añaden detrás solo si aportan algo nuevo.
    """
    candidatos = [resolve_model_name(primary_model)] + [resolve_model_name(m) for m in FALLBACK_MODELS]

    modelos_unicos: List[str] = []
    for modelo in candidatos:
        if modelo not in modelos_unicos:  # se conserva el primer orden de aparición
            modelos_unicos.append(modelo)
    return modelos_unicos


def deepseek_is_peak(now: Optional[datetime] = None) -> bool:
    """¿Estamos en tarifa peak de DeepSeek? (01-04 y 06-10 UTC, L-V).

    Los festivos chinos no se contemplan; para decidir coste/log es suficiente.
    """
    momento = now or datetime.now(timezone.utc)
    if momento.weekday() >= 5:  # fin de semana = off-peak todo el día
        return False
    return any(inicio <= momento.hour < fin for inicio, fin in DEEPSEEK_PEAK_HOURS_UTC)


def _read_int_env(name: str, default: int) -> int:
    """Lee un entero del entorno; si el valor no es numérico, avisa y usa el default."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "Valor no numérico para %s (%r); se usa el valor por defecto %d",
            name,
            raw,
            default,
        )
        return default


def _read_path_env(name: str, default: Path) -> Path:
    """Lee una ruta del entorno (se expande '~' si aparece)."""
    raw = os.getenv(name)
    return Path(raw).expanduser() if raw and raw.strip() else default


# ────────────────────────────────────────────────────────────────────────────
# Modelo de configuración
# ────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Settings:
    """Configuración inmutable de una ejecución. Se construye con `load_settings()`.

    `frozen=True` + tuplas en las listas evitan que un módulo mutje la
    configuración por sorpresa mientras otro la está leyendo.
    """

    # ── LLM ──
    api_key: Optional[str]
    base_url: Optional[str]
    llm_model: str
    fallback_models: Tuple[str, ...]
    criteria: str                       # instrucciones extra de puntuación (opcional)

    # ── CV ──
    resume_text: Optional[str]

    # ── Scraping ──
    search_terms: Tuple[str, ...]
    locations: Tuple[str, ...]
    result_limit: int
    hours_old: int
    proxy_url: Optional[str]

    # ── Umbrales ──
    score_threshold: int
    tailor_threshold: int
    top_jobs_limit: int
    max_applications_per_day: int

    # ── Integraciones ──
    google_sheet_id: Optional[str]
    email_sender: Optional[str]
    email_password: Optional[str]
    email_receiver: Optional[str]
    gcp_credentials_json: Optional[str]  # cuenta de servicio de Google (Sheets/Drive)
    resume_file_id: Optional[str]        # ID del CV en Google Drive (fallback)

    # ── Ficheros locales ──
    log_dir: Path
    cv_base_dir: Path
    tailored_cv_dir: Path

    # ── InfoJobs (API oficial) ──
    # Opcional: sin credenciales la fuente se salta silenciosamente.
    infojobs_client_id: Optional[str] = None
    infojobs_client_secret: Optional[str] = None

    # ── DeepSeek (fallback de pago, opcional) ──
    # Sin clave no se usa: Groq sigue siendo el proveedor principal.
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL

    @property
    def applications_log(self) -> Path:
        """Fichero donde se registran las solicitudes del agente autónomo."""
        return self.log_dir / "applications.log"

    @property
    def email_configured(self) -> bool:
        """True solo si hay las tres credenciales de email (evita envíos a medias)."""
        return bool(self.email_sender and self.email_password and self.email_receiver)


# ────────────────────────────────────────────────────────────────────────────
# Carga y validación
# ────────────────────────────────────────────────────────────────────────────
def _load_project_env() -> None:
    """Carga el `.env` de la raíz del proyecto si existe (en CI viene del entorno)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=str(env_path), override=False)


def load_settings() -> Settings:
    """Lee el entorno y construye el objeto `Settings`.

    No valida nada: la validación es explícita vía `validate_settings()` para
    que los módulos puedan importarse (y testearse) sin credenciales.
    """
    _load_project_env()

    modelo_principal = os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)
    cv_base_dir = _read_path_env("CV_BASE_DIR", DEFAULT_CV_DIR)

    return Settings(
        # LLM
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("API_BASE"),
        llm_model=resolve_model_name(modelo_principal),
        fallback_models=tuple(build_fallback_models(modelo_principal)),
        criteria=os.getenv("CRITERIA", ""),
        # CV
        resume_text=os.getenv("RESUME_TEXT") or None,
        # Scraping
        search_terms=SEARCH_TERMS,
        locations=LOCATIONS,
        result_limit=_read_int_env("RESULT_LIMIT", DEFAULT_RESULT_LIMIT),
        hours_old=_read_int_env("HOURS_OLD", DEFAULT_HOURS_OLD),
        proxy_url=os.getenv("PROXY_URL") or None,
        # Umbrales
        score_threshold=_read_int_env("SCORE_THRESHOLD", DEFAULT_SCORE_THRESHOLD),
        tailor_threshold=_read_int_env("TAILOR_THRESHOLD", DEFAULT_TAILOR_THRESHOLD),
        top_jobs_limit=_read_int_env("TOP_JOBS_LIMIT", DEFAULT_TOP_JOBS_LIMIT),
        max_applications_per_day=_read_int_env(
            "MAX_APPLICATIONS_PER_DAY", DEFAULT_MAX_APPLICATIONS_PER_DAY
        ),
        # Integraciones
        google_sheet_id=os.getenv("GOOGLE_SHEET_ID") or None,
        email_sender=os.getenv("EMAIL_SENDER") or None,
        email_password=os.getenv("EMAIL_PASSWORD") or None,
        email_receiver=os.getenv("EMAIL_RECEIVER") or None,
        gcp_credentials_json=os.getenv("GCP_CREDENTIALS_JSON") or None,
        resume_file_id=os.getenv("RESUME_FILE_ID") or None,
        # Ficheros locales
        log_dir=_read_path_env("LOG_DIR", DEFAULT_LOG_DIR),
        cv_base_dir=cv_base_dir,
        tailored_cv_dir=cv_base_dir / "tailored",
        # InfoJobs (API oficial: registro de app en developer.infojobs.net)
        infojobs_client_id=os.getenv("INFOJOBS_CLIENT_ID") or None,
        infojobs_client_secret=os.getenv("INFOJOBS_CLIENT_SECRET") or None,
        # DeepSeek (fallback de pago, opcional)
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
        deepseek_base_url=os.getenv("DEEPSEEK_API_BASE") or DEFAULT_DEEPSEEK_BASE_URL,
        deepseek_model=os.getenv("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL,
    )


def validate_settings(settings: Settings) -> None:
    """Comprueba los requisitos mínimos para ejecutar; lanza `ConfigurationError`.

    Separar cargar/validar permite informar de *todos* los problemas de una vez
    en lugar de fallar repetidamente en tiempo de ejecución.
    """
    problemas: List[str] = []

    if not settings.api_key:
        problemas.append("Falta API_KEY (clave de la API del LLM) en el entorno.")
    if not settings.base_url:
        problemas.append("Falta API_BASE (URL base de la API, p. ej. https://api.groq.com/openai/v1).")
    if not settings.resume_text:
        problemas.append(
            "Falta RESUME_TEXT (texto del CV). Configúralo en .env o activa el "
            "fallback de Google Drive (GCP_CREDENTIALS_JSON + RESUME_FILE_ID)."
        )

    if problemas:
        raise ConfigurationError("\n  - ".join(["Configuración incompleta:"] + problemas))
