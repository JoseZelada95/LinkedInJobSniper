import os
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import fitz  # pymupdf
from dotenv import load_dotenv
from openai import OpenAI


# Configurable via env vars; defaults work locally and in GitHub Actions
BASE_CV_DIR = Path(os.getenv("CV_DIR", "./CVs"))
TAILORED_DIR = BASE_CV_DIR / "tailored"
LOG_DIR = Path(os.getenv("LOG_DIR", "./logs"))