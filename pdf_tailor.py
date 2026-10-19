"""
PDF Tailor - Genera CVs adaptados en PDF para ofertas de alto score.
- PDF base limpio desde RESUME_TEXT (env var)
- Página inicial con bullets personalizados por oferta
- Groq LLM para bullets de alta calidad
- Retorna bytes para adjuntar al email
"""
import io
import os
import re
from datetime import date

import fitz
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("API_BASE")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
RESUME = os.getenv("RESUME_TEXT") or ""

_tailor_llm = None


def _get_llm():
    global _tailor_llm
    if _tailor_llm is None:
        _tailor_llm = ChatOpenAI(
            model=LLM_MODEL, api_key=API_KEY, base_url=BASE_URL, temperature=0.4
        )
    return _tailor_llm


def generate_base_cv_pdf(resume_text: str) -> bytes:
    """Genera PDF base limpio del CV con pagination."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (40, 55), "JOSE ALEXANDER ZELADA AVILES",
        fontsize=15, fontname="helv", color=(0.08, 0.2, 0.4),
    )
    page.insert_text(
        (40, 73), "Backend .NET Developer  |  Madrid, España  |  alex2521@hotmail.com",
        fontsize=9, fontname="helv", color=(0.35, 0.35, 0.35),
    )
    shape = page.new_shape()
    shape.draw_line((40, 82), (555, 82))
    shape.finish(color=(0.7, 0.7, 0.7), width=0.5)
    shape.commit()

    y = 100
    line_height = 11.5
    font_size = 8.5
    max_chars = 105

    def new_page():
        nonlocal page, y
        page = doc.new_page()
        y = 50

    def write_line(text: str, fs: float, color):
        nonlocal y
        if y > 780:
            new_page()
        page.insert_text((40, y), text, fontsize=fs, fontname="helv", color=color)
        y += line_height

    for line in resume_text.split("\n"):
        is_header = (line.isupper() and len(line) < 60) or (
            line.endswith(":") and len(line) < 60 and line == line.upper()
        )
        fs = font_size + 1.5 if is_header else font_size
        color = (0.1, 0.2, 0.4) if is_header else (0, 0, 0)

        if len(line) <= max_chars:
            write_line(line, fs, color)
        else:
            current = ""
            for word in line.split():
                test = (current + " " + word).strip()
                if len(test) > max_chars and current:
                    write_line(current, fs, color)
                    current = word
                else:
                    current = test
            if current:
                write_line(current, fs, color)

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def get_tailored_bullets(
    job_title: str, company: str, description: str, resume_text: str
) -> str:
    """Genera 4 bullets personalizados usando Groq LLM."""
    llm = _get_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """Eres un experto escritor de CVs para el mercado español tech.
Dado un resume y una descripción de puesto, reescribe 4 bullets del candidato 
para maximizar coincidencia con el puesto.

REGLAS ESTRICTAS:
- Mantén los HECHOS reales del resume (NO inventes logros ni números)
- Usa palabras clave del puesto cuando coincidan con experiencia real
- 4 bullets máximo, cada uno máximo 2 líneas
- Empieza con verbo de acción en pasado (Optimicé, Desarrollé, Gestioné)
- Idioma: español
- Output SOLO los 4 bullets, uno por línea, empezando con "- "
SIN explicaciones, SIN preámbulo, SIN markdown.""",
            ),
            (
                "user",
                "RESUME:\n{resume}\n\nPUESTO: {title}\nEMPRESA: {company}\nDESCRIPCIÓN:\n{description}",
            ),
        ]
    )
    chain = prompt | llm
    try:
        result = chain.invoke(
            {
                "resume": resume_text[:2500],
                "title": job_title,
                "company": company,
                "description": description[:1500],
            }
        )
        bullets = result.content.strip()
        cleaned = []
        for line in bullets.split("\n"):
            line = line.strip()
            if not line:
                continue
            if not line.startswith("-"):
                line = "- " + line.lstrip("-•* ").strip()
            cleaned.append(line)
        return "\n".join(cleaned[:4])
    except Exception as e:
        print(f"  LLM bullets failed for '{job_title}': {e}")
        return ""


def create_tailored_pdf(
    base_pdf_bytes: bytes, job_title: str, company: str, bullets: str
) -> bytes:
    """Crea PDF adaptado: página 1 bullets + CV base."""
    doc_new = fitz.open()
    page = doc_new.new_page()

    page.insert_text(
        (40, 55), "CV ADAPTADO PARA ESTA OFERTA",
        fontsize=13, fontname="helv", color=(0.08, 0.2, 0.4),
    )
    page.insert_text(
        (40, 75), f"Puesto: {job_title}",
        fontsize=10, fontname="helv", color=(0, 0, 0),
    )
    page.insert_text(
        (40, 90), f"Empresa: {company}",
        fontsize=10, fontname="helv", color=(0, 0, 0),
    )
    page.insert_text(
        (40, 105), f"Generado: {date.today().isoformat()}",
        fontsize=9, fontname="helv", color=(0.4, 0.4, 0.4),
    )
    shape = page.new_shape()
    shape.draw_line((40, 115), (555, 115))
    shape.finish(color=(0.7, 0.7, 0.7), width=0.5)
    shape.commit()

    page.insert_text(
        (40, 135), "Bullets personalizados:",
        fontsize=10, fontname="helv", color=(0.15, 0.15, 0.15),
    )
    rect = fitz.Rect(40, 150, 555, 700)
    page.insert_textbox(
        rect, bullets, fontsize=9.5, fontname="helv", lineheight=1.5, color=(0, 0, 0)
    )
    page.insert_text(
        (40, 750), "Usa estos bullets al enviar la aplicación. CV completo en páginas siguientes.",
        fontsize=8, fontname="helv", color=(0.5, 0.5, 0.5),
    )

    base_doc = fitz.open(stream=base_pdf_bytes, filetype="pdf")
    doc_new.insert_pdf(base_doc)
    base_doc.close()

    buf = io.BytesIO()
    doc_new.save(buf)
    doc_new.close()
    return buf.getvalue()


def tailor_pdf_for_job(job: dict, base_pdf_cache: dict) -> bytes:
    """Pipeline: bullets LLM + PDF adaptado. Retorna bytes o None."""
    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or "").strip()
    description = str(job.get("description") or "").strip()
    if not title or not company or not description:
        return None

    bullets = get_tailored_bullets(title, company, description, RESUME)
    if not bullets:
        return None

    if "pdf" not in base_pdf_cache:
        base_pdf_cache["pdf"] = generate_base_cv_pdf(RESUME)

    pdf_bytes = create_tailored_pdf(base_pdf_cache["pdf"], title, company, bullets)
    print(f"  Tailored PDF: {title} @ {company} ({len(pdf_bytes)} bytes)")
    return pdf_bytes


def sanitize_filename(value: str) -> str:
    value = re.sub(r"[^\w]+", "_", value.strip())
    return value.strip("_")[:50] or "Unknown"

