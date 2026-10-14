import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import pandas as pd
from dotenv import load_dotenv

from jobspy import scrape_jobs

from main import (
    SEARCH_TERMS,
    LOCATIONS,
    RESULT_LIMIT,
    HOURS_OLD,
    SCORE_THRESHOLD,
    TAILOR_THRESHOLD,
    fetch_missing_description,
    evaluate_job,
)
from pdf_tailor import tailor_cv_for_job


LOG_DIR = Path(os.getenv("LOG_DIR", "./logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
APPLICATIONS_LOG = LOG_DIR / "applications.log"
MAX_APPLICATIONS_PER_DAY = 20