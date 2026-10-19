import os, smtplib, json, io, time, random
from typing import List
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import pandas as pd
from dotenv import load_dotenv
from jobspy import scrape_jobs

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from pypdf import PdfReader

from pdf_tailor import tailor_pdf_for_job, sanitize_filename

load_dotenv()

# ── Config ────────────────────────────────────────────────
# Perfil: Jose Alexander Zelada Aviles - Backend .NET Developer, Madrid
# Estrategia dual: Línea A (Mid/Senior .NET) + Línea B (Junior/.NET/Java)
SEARCH_TERMS = [
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
]
LOCATIONS        = ["Madrid, Spain", "Madrid, España", "Remote"]
RESULT_LIMIT     = 8
HOURS_OLD        = 48
SCORE_THRESHOLD  = int(os.getenv("SCORE_THRESHOLD", "50"))
TAILOR_THRESHOLD = 75
PDF_TAILOR_THRESHOLD = 85  # Generar PDF adaptado para scores >= 85
PROXY_URL        = os.getenv("PROXY_URL") or None
RESUME           = os.getenv("RESUME_TEXT") or None
API_KEY          = os.getenv("API_KEY")
BASE_URL         = os.getenv("API_BASE")
_RAW_LLM_MODEL   = os.getenv("LLM_MODEL", "mistral")
# Map friendly aliases to actual Groq model IDs where needed
if _RAW_LLM_MODEL == "llama3-70b-8192":
    LLM_MODEL = "llama-3.3-70b-versatile"
else:
    LLM_MODEL = _RAW_LLM_MODEL
CRITERIA         = os.getenv("CRITERIA", "")
GOOGLE_SHEET_ID  = os.getenv("GOOGLE_SHEET_ID") or None

# ── Resume: Google Drive fallback ─────────────────────────
def load_resume_from_google_drive() -> str:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    creds_json_str = os.getenv("GCP_CREDENTIALS_JSON")
    file_id        = os.getenv("RESUME_FILE_ID")
    if not creds_json_str or not file_id:
        return None
    print("Loading resume from Google Drive...")
    try:
        creds_dict = json.loads(creds_json_str)
        creds      = service_account.Credentials.from_service_account_info(
                        creds_dict, scopes=["https://www.googleapis.com/auth/drive.readonly"])
        service    = build('drive', 'v3', credentials=creds)
        req        = service.files().get_media(fileId=file_id)
        file_io    = io.BytesIO()
        downloader = MediaIoBaseDownload(file_io, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        file_io.seek(0)
        return "".join(p.extract_text() + "\n" for p in PdfReader(file_io).pages)
    except Exception as e:
        print(f"Google Drive load failed: {e}")
        return None

if not RESUME:
    RESUME = load_resume_from_google_drive()

# ── Guards ────────────────────────────────────────────────
if not RESUME:
    raise SystemExit("ERROR: No resume. Set RESUME_TEXT in .env or configure Google Drive.")
if not API_KEY:
    raise SystemExit("ERROR: No API_KEY set in .env")

# ── Preflight: validar credenciales ANTES de scraping ─────
def validate_credentials() -> None:
    """
    Comprueba que todas las credenciales y servicios críticos funcionan
    ANTES de ejecutar cualquier búsqueda. Si algo falla, aborta aquí
    mismo sin gastar esfuerzo en scraping ni llamadas al LLM.
    """
    errors = []

    # 1. CV en memoria
    if RESUME:
        print(f"[OK] CV cargado ({len(RESUME)} caracteres)")
    else:
        errors.append("CV vacío: define RESUME_TEXT en .env o configura Google Drive.")

    # 2. API key Groq: presencia + validez real (models + 1-token probe)
    if not API_KEY:
        errors.append("API_KEY (Groq) no definida en .env.")
    else:
        try:
            r = requests.get(
                f"{BASE_URL}/models",
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=15,
            )
            if r.status_code == 401:
                errors.append(
                    "API_KEY de Groq INVÁLIDA (401). Rótala en https://console.groq.com/keys"
                )
            elif r.status_code == 429:
                errors.append("Groq rate-limit (429) al validar la key. Reintentar más tarde.")
            elif r.status_code != 200:
                errors.append(
                    f"Groq API respondió {r.status_code} al validar la key: {r.text[:200]}"
                )
            else:
                n_models = len(r.json().get("data", []))
                print(f"[OK] API key Groq válida ({n_models} modelos disponibles)")

                probe = requests.post(
                    f"{BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": LLM_MODEL,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                    timeout=30,
                )
                if probe.status_code == 200:
                    print(f"[OK] Modelo '{LLM_MODEL}' responde correctamente")
                elif probe.status_code == 401:
                    errors.append("Groq rechazó la llamada al modelo (401). Key inválida.")
                elif probe.status_code == 403:
                    errors.append(
                        f"Sin acceso al modelo '{LLM_MODEL}' (403). Revisar modelo en .env."
                    )
                elif probe.status_code == 429:
                    errors.append(
                        "Cuota/rate-limit de Groq agotado (429). Esperar o revisar saldo."
                    )
                else:
                    errors.append(
                        f"Modelo '{LLM_MODEL}' no responde ({probe.status_code}): "
                        f"{probe.text[:200]}"
                    )
        except requests.exceptions.RequestException as e:
            errors.append(f"No se pudo contactar con Groq API: {e}")

    # 3. Credenciales de email: presencia + login SMTP real
    sender   = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    receiver = os.getenv("EMAIL_RECEIVER")
    if not sender or not password or not receiver:
        errors.append(
            "Faltan EMAIL_SENDER / EMAIL_PASSWORD / EMAIL_RECEIVER en .env."
        )
    else:
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
                server.login(sender, password)
            print(f"[OK] SMTP Gmail autenticado ({sender} → {receiver})")
        except smtplib.SMTPAuthenticationError:
            errors.append(
                "SMTP: autenticación fallida. Revisar app password de Gmail "
                "(https://myaccount.google.com/apppasswords)."
            )
        except Exception as e:
            errors.append(f"SMTP no disponible: {e}")

    if errors:
        print("\n" + "=" * 60)
        print("VALIDACIÓN PREVIA FALLIDA — no se ejecuta ningún scraping")
        print("=" * 60)
        for err in errors:
            print(f"  [FAIL] {err}")
        raise SystemExit(1)

    print("\n[Todo OK] Credenciales validadas. Iniciando scraping...\n")

# ── LLM setup ─────────────────────────────────────────────
llm = ChatOpenAI(model=LLM_MODEL, api_key=API_KEY, base_url=BASE_URL, temperature=0)

class JobEvaluation(BaseModel):
    score:  int = Field(description="Relevance score 0-100 based on resume match.")
    reason: str = Field(description="One-sentence reason for the score.")
    yoe:    str = Field(description="Years of experience required, or 'Not Specified'.")

structured_llm = llm.with_structured_output(JobEvaluation, method="json_mode")

system_template = """You are an expert career coach for the Spanish job market. Evaluate how well a job description matches a candidate's resume.
Return a score (0-100), years of experience required, and a one-sentence reason.
If YoE is not mentioned, return "Not Specified".

CANDIDATE CONTEXT: Backend .NET Developer in Madrid, Spain. 10+ years total experience (2015-2024) in .NET/C#/SQL Server
with a recent ~2-year gap (2024-2026) dedicated to relocation to Spain + retraining (FP DAM degree in progress,
Java Spring Boot course). The candidate accepts BOTH lines:
- LINE A (preferred): Mid/Senior .NET roles (30-45k EUR)
- LINE B (acceptable): Junior/Practicas .NET or Java roles (22-30k EUR) for re-incorporation

Scoring criteria:
1. Skill Match (50%): Alignment of required skills with resume (.NET, C#, ASP.NET, SQL Server are core).
2. Seniority Fit (30%): Mid/Senior = LINE A. Junior/Becario = LINE B. Both are acceptable fits.
3. Domain Fit (20%): Industry match with banking, e-commerce, education, consulting background.

Location: Madrid, Spain or Remote. Penalise only if exclusively on-site outside Madrid.
Language: Candidate has English B1 - penalise only if C2/Fluent is mandatory.
Do NOT penalise the 2024-2026 gap - it is explained by retraining.

Respond in JSON format with keys: score, reason, yoe.
""" + CRITERIA

eval_prompt = ChatPromptTemplate.from_messages([
    ("system", system_template),
    ("user", "RESUME:\n{resume}\n\nJOB TITLE: {title}\nJOB DESCRIPTION:\n{description}\n\nScore strictly but fairly. Consider both LINE A (Mid/Senior) and LINE B (Junior) matches.")
])
evaluation_chain = eval_prompt | structured_llm

# — Tailoring model —
tailor_llm = ChatOpenAI(model=LLM_MODEL, api_key=API_KEY, base_url=BASE_URL, temperature=0.4)

tailor_prompt = ChatPromptTemplate.from_messages([
    ("system", """You are an expert CV writer. Given a resume and a job description, rewrite 3-4 of the
most relevant bullet points from the candidate's experience to better mirror the job's language and keywords.
Keep the same facts — do NOT invent achievements or numbers. Output ONLY the rewritten bullet points as a
short HTML unordered list (<ul><li>...</li></ul>). No preamble, no explanation."""),
    ("user", "RESUME:\n{resume}\n\nJOB TITLE: {title}\nJOB DESCRIPTION:\n{description}")
])
tailor_chain = tailor_prompt | tailor_llm

def tailor_resume_for_job(title: str, description: str) -> str:
    try:
        result = tailor_chain.invoke({
            "resume":      RESUME[:2000],
            "title":       title,
            "description": description[:1500]
        })
        return result.content.strip()
    except Exception as e:
        print(f"  Tailoring failed for '{title}': {e}")
        return ""

# ── Scraping ──────────────────────────────────────────────
def get_jobs_data(location: str, search_term: str) -> pd.DataFrame:
    proxies = [PROXY_URL] if PROXY_URL else None
    print(f"\nSearching: '{search_term}' in '{location}'")
    for attempt in range(1, 4):
        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed", "glassdoor"],
                search_term=search_term,
                location=location,
                results_wanted=RESULT_LIMIT,
                hours_old=HOURS_OLD,
                proxies=proxies
            )
            print(f"  → {len(jobs)} jobs scraped")
            return jobs
        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(random.uniform(3, 6))
    return pd.DataFrame()

def fetch_missing_description(url: str) -> str:
    try:
        time.sleep(random.uniform(2, 4))
        headers  = {"User-Agent": UserAgent().random, "Accept-Language": "en-US,en;q=0.9"}
        proxies  = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        response = requests.get(url, headers=headers, proxies=proxies, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            div  = (soup.find("div", {"class": "show-more-less-html__markup"}) or
                    soup.find("div", {"class": "description__text"}))
            return div.get_text(separator="\n").strip() if div else soup.get_text()[:5000]
    except Exception as e:
        print(f"  Manual fetch failed: {e}")
    return ""

# ── Evaluation ────────────────────────────────────────────
def evaluate_job(title: str, description: str) -> dict:
    if not description or len(str(description)) < 50:
        return {"score": 0, "reason": "No description", "yoe": "N/A"}
    try:
        result = evaluation_chain.invoke({
            "resume":      RESUME[:2000],
            "title":       title,
            "description": description[:1500]
        })
        return {"score": result.score, "reason": result.reason, "yoe": result.yoe}
    except Exception as e:
        print(f"  AI error for '{title}': {e}")
        return {"score": 0, "reason": "AI Error", "yoe": "N/A"}

# ── Google Sheets export ──────────────────────────────────
def get_sheets_service():
    """Initialise Google Sheets API client from service account JSON in GCP_CREDENTIALS_JSON."""
    creds_json_str = os.getenv("GCP_CREDENTIALS_JSON")
    if not creds_json_str:
        print("No GCP_CREDENTIALS_JSON set — skipping Sheets export.")
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_dict = json.loads(creds_json_str)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return build("sheets", "v4", credentials=creds)
    except Exception as e:
        print(f"Sheets service init failed: {e}")
        return None


def export_to_sheets(jobs: List[dict]):
    """Sheets export disabled by configuration."""
    return


# ── Email ─────────────────────────────────────────────────
def send_email(top_jobs: List[dict], pdf_attachments: List[tuple] = None):
    """
    Envía email HTML con ofertas.
    pdf_attachments: lista de (filename, pdf_bytes) para adjuntar.
    """
    if not top_jobs:
        print("No jobs met the score threshold. No email sent.")
        return
    sender   = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    receiver = os.getenv("EMAIL_RECEIVER")
    subject  = f"[Búsqueda Empleo] {len(top_jobs)} ofertas .NET Madrid - {datetime.now().strftime('%d/%m/%Y')}"

    rows = ""
    for job in top_jobs:
        score_color = "#27ae60" if job['score'] >= 75 else "#e67e22"

        tailor_html = ""
        if job.get("tailored_bullets"):
            pdf_note = ""
            if job.get("pdf_filename"):
                pdf_note = f'<div style="margin-top:6px;font-size:12px;color:#27ae60;">📄 PDF adaptado adjunto: <b>{job["pdf_filename"]}</b></div>'
            tailor_html = f"""
            <tr>
                <td colspan="6" style="padding:8px 10px 14px 10px;background:#f0fff4;border-bottom:2px solid #27ae60;">
                    <b style="color:#27ae60;">✨ Tailored CV Bullets for this role:</b><br>
                    <div style="font-size:13px;color:#2c3e50;margin-top:4px;">{job['tailored_bullets']}</div>
                    {pdf_note}
                </td>
            </tr>"""

        rows += f"""
        <tr>
            <td style="padding:10px;border-bottom:1px solid #eee;font-weight:bold;color:{score_color};">{job['score']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{job['title']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{job['company']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{job['yoe']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;font-size:13px;color:#555;">{job['reason']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">
                <a href="{job['job_url']}" style="background:#0a66c2;color:white;padding:6px 12px;text-decoration:none;border-radius:4px;font-size:12px;">Apply</a>
            </td>
        </tr>{tailor_html}"""

    sheets_note = ""
    if GOOGLE_SHEET_ID:
        sheets_note = f'<p style="font-size:12px;color:#555;">📊 All jobs exported to <a href="https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}">your tracking sheet</a> — mark Applied when done!</p>'

    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:900px;margin:auto;">
        <h2 style="color:#2c3e50;">🎯 Búsqueda de Empleo Diaria — Resultados</h2>
        <p><b>{len(top_jobs)}</b> ofertas puntuadas ≥{SCORE_THRESHOLD} hoy · Línea A (Mid/Senior .NET) + Línea B (Junior/Prácticas) · ✨ = bullets CV personalizados</p>
        {sheets_note}
        <table style="border-collapse:collapse;width:100%;">
            <tr style="background:#f8f9fa;">
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Score</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Title</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Company</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">YoE</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Why a Match</th>
                <th style="padding:10px;border-bottom:2px solid #ddd;text-align:left;">Link</th>
            </tr>{rows}
        </table>
        <p style="margin-top:20px;font-size:11px;color:#aaa;">Powered by LinkedIn Job Sniper · LangChain · Groq</p>
    </body></html>"""

    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From']    = sender
    msg['To']      = receiver
    msg.attach(MIMEText(html, 'html'))

    # Adjuntar PDFs adaptados
    if pdf_attachments:
        from email.mime.application import MIMEApplication
        for filename, pdf_bytes in pdf_attachments:
            try:
                part = MIMEApplication(pdf_bytes, Name=filename)
                part['Content-Disposition'] = f'attachment; filename="{filename}"'
                msg.attach(part)
                print(f"  Attached: {filename} ({len(pdf_bytes)} bytes)")
            except Exception as e:
                print(f"  Failed to attach {filename}: {e}")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.send_message(msg)
        attach_note = f" with {len(pdf_attachments)} PDFs" if pdf_attachments else ""
        print(f"Email sent to {receiver}{attach_note}")
    except Exception as e:
        print(f"Email failed: {e}")

# ── Main ──────────────────────────────────────────────────
def main():
    # 0. Preflight: validar API keys y servicios ANTES de raspar
    validate_credentials()

    # 1. Scraping (solo si el preflight pasó)
    df = pd.DataFrame()
    for location in LOCATIONS:
        for term in SEARCH_TERMS:
            df = pd.concat([df, get_jobs_data(location, term)], ignore_index=True, sort=False)

    if df.empty:
        print("No jobs found. Check proxy/network.")
        return

    df = df.drop_duplicates(subset=["job_url"]).reset_index(drop=True)
    print(f"\nTotal unique jobs to evaluate: {len(df)}")

    scored_jobs = []
    for _, row in df.iterrows():
        title       = row.get('title', 'Unknown')
        description = row.get('description', '')
        job_url     = row.get('job_url', '')

        if not description or len(str(description)) < 50:
            description = fetch_missing_description(job_url) if job_url else ""

        if len(str(description)) < 50:
            print(f"  Skipping '{title}' — no description")
            continue

        result = evaluate_job(title, description)
        print(f"  [{result['score']:3d}] {title} @ {row.get('company', '?')} | {result['reason'][:60]}")

        if result['score'] >= SCORE_THRESHOLD:
            scored_jobs.append({
                "title":            title,
                "company":          row.get('company', 'Unknown'),
                "job_url":          job_url,
                "score":            result['score'],
                "reason":           result['reason'],
                "yoe":              result['yoe'],
                "tailored_bullets": "",
                "description":      description,
            })

    # 3. Sort, tailor, export & send
    scored_jobs.sort(key=lambda x: x['score'], reverse=True)
    top_20 = scored_jobs[:20]

    # Tailoring: bullets (≥75) + PDFs adaptados (≥85)
    base_pdf_cache = {}
    pdf_attachments = []
    for job in top_20:
        if job['score'] >= TAILOR_THRESHOLD:
            print(f"  Tailoring bullets: {job['title']} @ {job['company']}")
            job['tailored_bullets'] = tailor_resume_for_job(job['title'], job['description'])

        if job['score'] >= PDF_TAILOR_THRESHOLD:
            print(f"  Generating tailored PDF: {job['title']} @ {job['company']}")
            try:
                pdf_bytes = tailor_pdf_for_job(job, base_pdf_cache)
                if pdf_bytes:
                    fname = f"CV_{sanitize_filename(job['company'])}_{sanitize_filename(job['title'])[:30]}.pdf"
                    pdf_attachments.append((fname, pdf_bytes))
                    job['pdf_filename'] = fname
            except Exception as e:
                print(f"  PDF generation failed for '{job['title']}': {e}")

    export_to_sheets(top_20)
    print(f"\n{len(top_20)} jobs above threshold, {len(pdf_attachments)} tailored PDFs. Sending email...")
    send_email(top_20, pdf_attachments)

if __name__ == "__main__":
    main()
