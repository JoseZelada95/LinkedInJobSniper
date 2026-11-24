"""Generación de un PDF de CV personalizado para una oferta concreta.

Flujo (100 % local, **sin llamadas a la API** para no consumir cuota):

  1. Listar los CVs originales de `cv_base_dir` (excluye la carpeta de salida).
  2. Elegir el CV con más solapamiento de palabras con la oferta (scoring simple).
  3. Reescribir las 3 mejores viñetas al estilo Google XYZ (heurística local).
  4. Escribir un PDF nuevo: portada diseñada con las viñetas + CV original intacto.

Se mantienen funciones pequeñas y puras (`tokenise`, `score_cv_for_job`,
`sanitise_for_filename`…) porque son las fáciles de testear.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:  # PyMuPDF >= 1.24 expone el módulo como `pymupdf`
    import pymupdf as fitz
except ImportError:  # versiones antiguas usan el nombre histórico `fitz`
    import fitz

from .config import Settings
from .logging_utils import get_logger
from .models import JobOffer

logger = get_logger("pdf_generator")

# Formato de la primera página del PDF generado (A4 en puntos).
_PAGE_RECT = fitz.Rect(40, 40, 550, 800)
_FONT_SIZE = 11
_FALLBACK_XYZ = "delivering responsibilities closely aligned with the role"

# Paleta de la portada diseñada (RGB 0-1). Azul profesional + grises legibles.
_ACCENT = (0.10, 0.35, 0.61)
_ACCENT_LIGHT = (0.91, 0.94, 0.97)
_INK = (0.11, 0.13, 0.16)
_MUTED = (0.42, 0.45, 0.50)
_MARGIN = 42
_CARD_HEIGHT = 58

# Marcadores de viñeta que se aceptan al leer el texto del CV original.
_BULLET_PREFIXES = ("-", "•", "*")
_MIN_BULLET_LENGTH = 10
_TOP_BULLETS = 3


# ────────────────────────────────────────────────────────────────────────────
# Utilidades de texto (puras → testables)
# ────────────────────────────────────────────────────────────────────────────
def tokenise(text: str) -> List[str]:
    """Divide texto en palabras en minúsculas (sin signos ni números)."""
    return re.findall(r"[A-Za-z]+", text.lower())


def sanitise_for_filename(value: str) -> str:
    """Convierte un texto libre en un nombre de fichero seguro para cualquier SO."""
    limpio = re.sub(r"[^\w]+", "_", value.strip())
    return limpio.strip("_") or "Unknown"


def score_cv_for_job(cv_text: str, job_title: str, job_description: str) -> float:
    """Relevancia local (0-1) = palabras de la oferta que aparecen en el CV.

    No envía datos a ningún sitio: scoring barato para elegir el mejor CV base.
    """
    job_tokens = set(tokenise(f"{job_title} {job_description}"))
    if not job_tokens:
        return 0.0
    cv_tokens = set(tokenise(cv_text))
    return len(job_tokens & cv_tokens) / len(job_tokens)


def select_best_cv(
    cvs: Sequence[Tuple[Path, str]],
    job_title: str,
    job_description: str,
) -> Optional[Tuple[Path, str, float]]:
    """Devuelve (ruta, texto, relevancia) del CV más afín a la oferta."""
    mejor: Optional[Tuple[Path, str, float]] = None
    for ruta, texto in cvs:
        relevancia = score_cv_for_job(texto, job_title, job_description)
        if mejor is None or relevancia > mejor[2]:
            mejor = (ruta, texto, relevancia)
    return mejor


def extract_bullet_lines(cv_text: str) -> List[str]:
    """Extrae las líneas con formato de viñeta (`-`, `•`, `*`) del CV."""
    viñetas: List[str] = []
    for linea in cv_text.splitlines():
        recortada = linea.strip()
        if recortada.startswith(_BULLET_PREFIXES) and len(recortada) > _MIN_BULLET_LENGTH:
            viñetas.append(recortada.lstrip("-•* ").strip())
    return viñetas


def generate_tailored_bullets(job_title: str, company: str, job_description: str, cv_text: str) -> str:
    """Reescribe hasta 3 viñetas al estilo Google XYZ *sin* llamar a ninguna API.

    Heurística: se rankean las viñetas por solapamiento con la oferta y cada una
    se parte en «X by Y» (si aparece la preposición «by») para encajar en la
    fórmula *Accomplished X by Y, resulting in Z*.
    """
    bullet_lines = extract_bullet_lines(cv_text)
    if not bullet_lines:
        return ""

    job_tokens = set(tokenise(f"{job_title} {job_description}"))

    def puntuar(linea: str) -> int:
        """Afinidad de una viñeta concreta con la oferta."""
        return len(job_tokens & set(tokenise(linea)))

    # Primero las que tienen solapamiento real; si no hay ninguna, las 3 primeras.
    ordenadas = sorted(bullet_lines, key=puntuar, reverse=True)
    seleccionadas = [ln for ln in ordenadas if puntuar(ln) > 0][:_TOP_BULLETS] or ordenadas[:_TOP_BULLETS]

    reescritas: List[str] = []
    for original in seleccionadas:
        partes = re.split(r"\bby\b", original, maxsplit=1, flags=re.IGNORECASE)
        if len(partes) == 2:
            parte_x = partes[0].strip(" -–—:;,.")
            parte_y = partes[1].strip(" -–—:;,.")
        else:
            parte_x = original.strip(" -–—:;,.")
            parte_y = _FALLBACK_XYZ

        reescritas.append(f"- Accomplished {parte_x} by {parte_y}, resulting in stronger outcomes for the organisation.")

    return "\n".join(reescritas)


# ────────────────────────────────────────────────────────────────────────────
# E/S de ficheros
# ────────────────────────────────────────────────────────────────────────────
def ensure_dirs(settings: Settings) -> None:
    """Crea (si no existen) las carpetas de salida y logs."""
    settings.tailored_cv_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)


def list_original_cvs(settings: Settings) -> List[Path]:
    """Lista los PDFs del directorio base, excluyendo los ya personalizados."""
    if not settings.cv_base_dir.exists():
        return []

    originales: List[Path] = []
    for ruta in settings.cv_base_dir.rglob("*.pdf"):
        if settings.tailored_cv_dir in ruta.parents:  # ya es un resultado: no se reutiliza
            continue
        originales.append(ruta)
    return sorted(originales)


def ensure_base_cv(settings: Settings) -> Optional[Path]:
    """Garantiza que exista al menos un CV base en `cv_base_dir`.

    Si ya hay PDFs originales no hace nada. Si no, reconstruye `cv_base.pdf`
    desde `RESUME_TEXT` (texto plano), para que `tailor_cv_for_job` pueda
    generar PDFs personalizados incluso sin CVs originales en disco (caso
    habitual en el informe diario de GitHub Actions).

    Devuelve la ruta del CV base creado, o `None` si no hay cómo obtenerlo.
    """
    ensure_dirs(settings)
    if list_original_cvs(settings):
        return None  # ya hay CVs base: no se toca nada

    texto = (settings.resume_text or "").strip()
    if not texto:
        logger.warning(
            "Sin CVs base en %s ni RESUME_TEXT: no se pueden generar PDFs",
            settings.cv_base_dir,
        )
        return None

    ruta = settings.cv_base_dir / "cv_base.pdf"
    documento = fitz.open()
    for i in range(0, len(texto.splitlines()), 55):
        pagina = documento.new_page()
        pagina.insert_textbox(
            fitz.Rect(40, 40, 555, 800),
            "\n".join(texto.splitlines()[i : i + 55]),
            fontsize=10,
        )
    documento.save(ruta)
    documento.close()
    logger.info("CV base reconstruido desde RESUME_TEXT: %s", ruta)
    return ruta


def extract_pdf_text(path: Path) -> str:
    """Extrae todo el texto de un PDF (una línea por cambio de página)."""
    with fitz.open(path) as documento:
        return "\n".join(pagina.get_text("text") for pagina in documento)


def _bullet_texts(bullets: str) -> List[str]:
    """Convierte el bloque de viñetas en textos sueltos, sin prefijo ni vacíos."""
    textos: List[str] = []
    for linea in bullets.splitlines():
        recortada = linea.lstrip("-•* ").strip()
        if recortada:
            textos.append(recortada)
    return textos


def _draw_designed_cover(
    pagina: "fitz.Page",
    job_title: str,
    company: str,
    bullets: str,
) -> None:
    """Portada A4 con cabecera de color y tarjetas para los logros adaptados."""
    ancho, alto = pagina.rect.width, pagina.rect.height

    # Cabecera sólida con el puesto y la empresa.
    pagina.draw_rect(fitz.Rect(0, 0, ancho, 132), color=_ACCENT, fill=_ACCENT)
    pagina.insert_textbox(
        fitz.Rect(_MARGIN, 30, ancho - _MARGIN, 50),
        "CV ADAPTADO A LA OFERTA",
        fontsize=9,
        fontname="hebo",
        color=(0.85, 0.90, 0.97),
    )
    pagina.insert_textbox(
        fitz.Rect(_MARGIN, 52, ancho - _MARGIN, 104),
        job_title,
        fontsize=21,
        fontname="hebo",
        color=(1, 1, 1),
    )
    pagina.insert_textbox(
        fitz.Rect(_MARGIN, 106, ancho - _MARGIN, 126),
        f"{company}  ·  {date.today():%d/%m/%Y}",
        fontsize=10,
        fontname="helv",
        color=(0.85, 0.90, 0.97),
    )

    # Título de sección con filete fino.
    y = 168
    pagina.insert_text(
        (_MARGIN, y), "LOGROS ALINEADOS CON LA OFERTA", fontsize=10, fontname="hebo", color=_ACCENT
    )
    pagina.draw_line(
        fitz.Point(_MARGIN, y + 8), fitz.Point(ancho - _MARGIN, y + 8), color=_ACCENT, width=1
    )
    y += 24

    # Una tarjeta por logro: fondo suave + barra de acento a la izquierda.
    for texto in _bullet_texts(bullets):
        caja = fitz.Rect(_MARGIN, y, ancho - _MARGIN, y + _CARD_HEIGHT)
        pagina.draw_rect(caja, color=_ACCENT_LIGHT, fill=_ACCENT_LIGHT)
        pagina.draw_rect(
            fitz.Rect(caja.x0, caja.y0, caja.x0 + 4, caja.y1), color=_ACCENT, fill=_ACCENT
        )
        pagina.insert_textbox(
            fitz.Rect(caja.x0 + 14, caja.y0 + 9, caja.x1 - 12, caja.y1 - 8),
            texto,
            fontsize=10.5,
            fontname="helv",
            color=_INK,
        )
        y += _CARD_HEIGHT + 10

    # Pie: el CV completo va después, intacto.
    pagina.draw_line(
        fitz.Point(_MARGIN, alto - 62), fitz.Point(ancho - _MARGIN, alto - 62), color=_ACCENT_LIGHT, width=1
    )
    pagina.insert_textbox(
        fitz.Rect(_MARGIN, alto - 52, ancho - _MARGIN, alto - 22),
        "El CV original completo continúa en las páginas siguientes.",
        fontsize=9,
        fontname="heit",
        color=_MUTED,
    )


def _draw_plain_cover(pagina: "fitz.Page", job_title: str, company: str, bullets: str, original_text: str) -> None:
    """Portada de respaldo si el dibujo avanzado no está disponible."""
    portada = f"Tailored CV bullets for {job_title} at {company}\n\n{bullets}\n\nOriginal CV:\n{original_text}"
    pagina.insert_textbox(_PAGE_RECT, portada, fontsize=_FONT_SIZE, fontname="helv")


def write_tailored_pdf(
    original: Path,
    output: Path,
    job_title: str,
    company: str,
    bullets: str,
) -> None:
    """Crea el PDF final: portada diseñada + páginas originales intactas.

    El CV original se copia tal cual (fechas, empresas y puestos se preservan
    byte a byte): solo se le antepone una portada con las viñetas adaptadas.
    Si el dibujo avanzado fallara en alguna versión de PyMuPDF, se cae a una
    portada de texto simple para no romper el informe diario.
    """
    documento_nuevo = fitz.open()
    try:
        _draw_designed_cover(
            documento_nuevo.new_page(width=595, height=842), job_title, company, bullets
        )
    except Exception:  # noqa: BLE001 - cualquier fallo de diseño usa el respaldo
        logger.warning("No se pudo dibujar la portada diseñada; se usa texto simple", exc_info=True)
        documento_nuevo.close()
        documento_nuevo = fitz.open()
        _draw_plain_cover(
            documento_nuevo.new_page(), job_title, company, bullets, extract_pdf_text(original)
        )

    with fitz.open(original) as documento_original:
        documento_nuevo.insert_pdf(documento_original)

    documento_nuevo.save(output)
    documento_nuevo.close()


# ────────────────────────────────────────────────────────────────────────────
# Función principal
# ────────────────────────────────────────────────────────────────────────────
def tailor_cv_for_job(job: JobOffer, settings: Settings) -> Optional[Path]:
    """Genera el PDF personalizado para una oferta. Devuelve su ruta o `None`.

    `None` significa «no procede» (datos incompletos, sin CVs base…): el
    llamado lo registra y sigue con la siguiente oferta.
    """
    ensure_dirs(settings)

    titulo = job.title.strip()
    empresa = job.company.strip()
    descripcion = job.description.strip()
    if not titulo or not empresa or not descripcion:
        logger.info("Oferta incompleta (título/empresa/descripción): se omite")
        return None

    cvs = list_original_cvs(settings)
    if not cvs:
        logger.warning("No hay CVs originales en %s", settings.cv_base_dir)
        return None

    # Se extrae el texto una sola vez por CV y se reutiliza para todas las ofertas
    # del ciclo (extraer texto de PDF es la parte lenta).
    cvs_con_texto = [(ruta, extract_pdf_text(ruta)) for ruta in cvs]

    mejor = select_best_cv(cvs_con_texto, titulo, descripcion)
    if not mejor:
        return None
    mejor_ruta, mejor_texto, relevancia = mejor

    bullets = generate_tailored_bullets(
        job_title=titulo,
        company=empresa,
        job_description=descripcion,
        cv_text=mejor_texto,
    )
    if not bullets:
        logger.warning("El CV %s no tiene viñetas reescribibles", mejor_ruta.name)
        return None

    nombre_salida = (
        f"{sanitise_for_filename(empresa)}_{sanitise_for_filename(titulo)}_{date.today():%Y-%m-%d}.pdf"
    )
    ruta_salida = settings.tailored_cv_dir / nombre_salida

    write_tailored_pdf(
        original=mejor_ruta,
        output=ruta_salida,
        job_title=titulo,
        company=empresa,
        bullets=bullets,
    )

    # Trazabilidad: qué CV se usó y cómo de afín era a la oferta.
    logger.info(
        "PDF generado | oferta='%s' @ '%s' | score=%s | cv='%s' | relevancia=%.3f | salida='%s'",
        titulo,
        empresa,
        job.score,
        mejor_ruta.name,
        relevancia,
        ruta_salida,
    )
    return ruta_salida
