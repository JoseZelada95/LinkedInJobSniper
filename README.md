# 🕵️‍♂️ LinkedIn Job Sniper

An automated AI agent that scrapes job boards daily, scores every offer against your resume,
tailors your CV for the top matches and emails you the results every morning — completely free.

> Fork adapted for a **Backend .NET Developer in Madrid** (dual strategy: Mid/Senior .NET + Junior/Prácticas).

## ✨ Features

- 🔍 **Multi-term scraping**: 36 search terms × 3 locations (Madrid / Remote) across LinkedIn, Indeed, Glassdoor, Google Jobs and **InfoJobs (official API)**
- 🧠 **AI scoring**: Groq (free) + LangChain score every job 0–100 against your resume, with **automatic fallback across models**
- ✨ **CV tailoring**: jobs scoring ≥75 get rewritten bullets that mirror the job's keywords
- 📄 **Tailored PDFs** (optional, autonomous agent): builds a personalised PDF per offer, 100 % offline
- 📊 **Google Sheets tracker**: appends every match so you can follow your pipeline
- 📧 **Daily email digest**: HTML email with scores, tailored bullets, YoE and Apply buttons
- ✅ **Tested**: 130+ unit tests run offline (no network, no credentials needed)
- 🔁 **No repeats**: jobs already emailed are skipped on later runs (history kept in `sent_jobs.json` and Google Sheets)
- ☁️ **Zero cost**: GitHub Actions (free) + Groq API (free tier)

## 🚀 How It Works

1. **Trigger**: runs daily at 10:00 UTC (11:00/12:00 Europe/Madrid) via GitHub Actions cron — outside DeepSeek's peak window
2. **Scrape**: fetches fresh offers for every search term × location
3. **Deduplicate**: removes the same offer appearing in several searches
4. **Skip repeats**: drops offers already sent in previous daily emails (local `sent_jobs.json` + URLs in your Google Sheet)
5. **Evaluate**: Groq AI reads your resume and scores every job (0–100)
6. **Tailor**: for jobs scoring ≥75, rewrites your CV bullets to mirror the job's language
7. **Export**: appends all top matches to your Google Sheet tracker
8. **Email**: sends you the top 20 jobs with tailored bullets and Apply buttons

## 🗂️ Project structure

```
main.py                  # Entry point of the daily report (what GitHub Actions runs)
autonomous_agent.py      # Entry point of the autonomous agent (continuous or --once)
job_sniper/              # All the logic, one responsibility per module
├── config.py            # Configuration: .env / Secrets → immutable Settings object
├── models.py            # Domain models (JobEvaluation, JobOffer)
├── prompts.py           # LLM prompt templates
├── llm.py               # LLM client + fallback strategy across models
├── scraping.py          # Offers retrieval (jobspy + manual description fetch)
├── infojobs.py          # InfoJobs source (official API; jobspy doesn't support it)
├── evaluation.py        # Scores offers against the resume (0–100)
├── tailoring.py         # Tailored CV bullets for the email
├── pdf_generator.py     # Personalised PDF generation (offline, no LLM)
├── sheets.py            # Google Sheets export
├── emailer.py           # Daily HTML email digest
├── resume.py            # Resume loading (env var or Google Drive)
├── applications.py      # Application log + daily limit (autonomous agent)
├── pipeline.py          # Orchestration of the complete flows
└── logging_utils.py     # Uniform logging (console + file)
tests/                   # pytest suite (offline)
```

## ⚙️ Setup

### 1. Fork this repo

Click **Fork** → **Create fork**.

### 2. Get a Groq API Key (Free)

- Go to [https://console.groq.com](https://console.groq.com)
- Sign up and create an API key
- No credit card required — generous free tier

### 3. Set up a Gmail App Password

- Go to [https://myaccount.google.com/security](https://myaccount.google.com/security)
- Enable 2-Step Verification
- Search "App passwords" → Generate one for "Mail"
- Copy the 16-character password

### 4. Set up Google Cloud (for Sheets + optional Drive resume)

- Go to [https://console.cloud.google.com](https://console.cloud.google.com)
- Create a new project
- Go to **APIs & Services → Enable APIs** and enable:
  - ✅ Google Sheets API
  - ✅ Google Drive API (only if using Drive for your resume)
- Go to **IAM & Admin → Service Accounts → Create Service Account**
- Give it any name, click **Create**
- Click the service account → **Keys → Add Key → JSON** → download it
- Create a Google Sheet and share it with the service account's email (Editor access)
- Copy the Sheet ID from the URL: `docs.google.com/spreadsheets/d/`**THIS_PART**`/edit`

### 5. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value | Required? |
|--------|-------|-----------|
| `API_KEY` | Your Groq API key (`gsk_...`) | ✅ |
| `API_BASE` | `https://api.groq.com/openai/v1` | ✅ |
| `LLM_MODEL` | `llama-3.3-70b-versatile` | ✅ |
| `EMAIL_SENDER` | Your Gmail address | ✅ |
| `EMAIL_PASSWORD` | Gmail App Password | ✅ |
| `EMAIL_RECEIVER` | Email to receive reports | ✅ |
| `RESUME_TEXT` | Your full resume as plain text | ✅ |
| `SCORE_THRESHOLD` | Minimum score to include (e.g. `50`) | ✅ |
| `GOOGLE_SHEET_ID` | ID from your Google Sheet URL | ✅ |
| `GCP_CREDENTIALS_JSON` | Paste entire contents of your service account JSON | ✅ |
| `CRITERIA` | Optional extra scoring instructions | Optional |
| `RESUME_FILE_ID` | File ID from your Google Drive resume URL | Optional |
| `TAILOR_THRESHOLD` | Minimum score to tailor the CV (default `75`) | Optional |
| `TOP_JOBS_LIMIT` | Max jobs in the daily report (default `20`) | Optional |
| `PROXY_URL` | Proxy for scraping (optional) | Optional |
| `INFOJOBS_CLIENT_ID` | Client ID of your app at developer.infojobs.net (enables the InfoJobs source) | Optional |
| `INFOJOBS_CLIENT_SECRET` | Client secret of your app | With client ID |
| `LOG_DIR` | Where to write logs (default `~/.linkedin_job_sniper/logs`) | Optional |
| `CV_BASE_DIR` | Folder with your base CVs (autonomous agent only) | Optional |

### 6. Enable Actions & Run

- Go to the **Actions** tab → enable workflows
- Click **Daily Career Scout** → **Run workflow** to test manually
- Check your inbox and Google Sheet after ~5 minutes ✅

## 🔧 Customisation

Everything lives in **`job_sniper/config.py`** — there is a single source of truth for search
terms, locations and thresholds:

```python
SEARCH_TERMS = ("Desarrollador .NET", "Junior .NET Developer", ...)
LOCATIONS    = ("Madrid, Spain", "Madrid, España", "Remote")

DEFAULT_RESULT_LIMIT     = 8    # offers per search term
DEFAULT_HOURS_OLD        = 48   # max offer age
DEFAULT_SCORE_THRESHOLD  = 50   # minimum score to appear in the email
DEFAULT_TAILOR_THRESHOLD = 75   # minimum score to tailor the CV
DEFAULT_TOP_JOBS_LIMIT   = 20   # jobs in the daily report
```

## 🧑‍💻 Local development

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements-dev.txt

pytest                            # run the whole suite (offline, no credentials)
python main.py                    # daily report (needs a .env file)
python autonomous_agent.py --once # autonomous cycle, one pass
```

The test suite never touches the network: scraping, the LLM API, SMTP and Google are
either monkeypatched or replaced by pure functions.

## 📊 Google Sheets Tracker

Every daily run appends matched jobs to your sheet with these columns:

| Fecha | Empresa | Puesto | Ubicación | Match % | Salario | URL Oferta | Estado | Notas | CV Usado |
|-------|---------|--------|-----------|---------|---------|------------|--------|-------|----------|

Manually update the **Estado** column as you go to track your pipeline.

## ⏰ Schedule

Runs daily at **10:00 UTC** (`cron: '0 10 * * *'` in `.github/workflows/daily.yml`),
which is **11:00 Europe/Madrid in winter (CET)** and **12:00 in summer (CEST)**.

This time is deliberately outside DeepSeek's peak window (01:00–04:00 and
06:00–10:00 UTC, Mon–Fri), so the paid fallback runs at half price (off-peak).

To change it, edit the `cron` line in `.github/workflows/daily.yml`. The history
dedupe (Google Sheets + cached `sent_jobs.json`) prevents duplicates even if
GitHub delays the cron.

## 🔒 Privacy

Your `RESUME_TEXT` and each job description are sent to the LLM provider (Groq
by default, DeepSeek as paid fallback) for scoring and bullet tailoring. They
are **not** committed to this repository — only the resulting scores/bullets
are emailed to you and appended to your private Google Sheet. Use a provider you
trust and keep all API keys in GitHub Secrets (never in the repo).

## ⚠️ Disclaimer

For educational purposes only. Web scraping may violate job boards' Terms of Service.
Use responsibly and at your own risk.

## 👨‍💻 Author

Fork maintained by JoseZelada95, built on top of the original
[LinkedInJobSniper](https://github.com/tao-991/LinkedInJobSniper) by Tao.
Powered by Python, LangChain, Groq & GitHub Actions ☕
