#!/usr/bin/env python3
"""Recolector de ofertas de InfoJobs para tu PC — LinkedIn Job Sniper.

Por qué existe: InfoJobs no se puede consultar con la API (registro de apps
cerrado), su web es una SPA que exige JavaScript y muestra un captcha (Geetest)
en el primer acceso. Este script lo resuelve del único modo honesto y estable:

    1. Abre una ventana de Chromium VISIBLE en tu PC con la búsqueda de InfoJobs.
    2. Resuelve el captcha («Hacer clic para comprobar») manualmente una vez.
       El navegador guarda esa sesión en `infojobs_session.json` (cookies).
    3. Recorre los términos de búsqueda del agente (search_terms × locations de
       `job_sniper/config.py`), espera a que la SPA renderice las tarjetas y
       extrae: URL de oferta, título, empresa y (visitando el detalle) descripción.
    4. Guarda todo en `infojobs_ofertas.csv` con las mismas columnas que el
       pipeline ya entiende (title, company, job_url, description, location),
       listo para usar como fuente más del informe diario.

Cómo ejecutar (en tu PC):

    pip install "playwright>=1.40" beautifulsoup4 pandas python-dotenv
    python -m playwright install chromium
    python infojobs_harvest_pc.py              # primera vez: resuelve el captcha
    python infojobs_harvest_pc.py --headless   # con sesión guardada, sin ventana

Notas:
  * Las cookies de InfoJobs posiblemente caduquen en pocos días; si reaparece
    el captcha, borra `infojobs_session.json` y repite la primera ejecución.
  * Si la web cambió su DOM, el extractor es tolerante: solo necesita que las
    URLs de oferta contengan el fragmento `of-i`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd

# ── Configuración del agente: SE IMPORTA de job_sniper/config.py ─────────────
# Única fuente de verdad (36 términos): evita que esta copia local diverja.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from job_sniper.config import LOCATIONS, SEARCH_TERMS  # noqa: E402

BASE = "https://www.infojobs.net"
SEARCH_URL = BASE + "/jobsearch/search-results/list.xhtml"
MADRID_PROVINCE_ID = "28"  # ID de normalización de Madrid en InfoJobs

SESSION_FILE = Path("infojobs_session.json")
OUTPUT_CSV = Path("infojobs_ofertas.csv")

_OF_LINK = "a[href*='of-i']"
_CAPTCHA_TXT = "Hacer clic para comprobar"

WAIT_CARDS_MS = 20_000  # espera máxima a que la SPA pinte las tarjetas


def _log(mensaje: str) -> None:
    print(mensaje, flush=True)


def _pausa(base: tuple[float, float]) -> None:
    time.sleep(random.uniform(*base))


def hay_captcha(page) -> bool:
    """True si la página actual es el interstitial de Geetest."""
    return page.locator("body").inner_text().find("Eres humano") >= 0


def resolver_captcha_una_vez(page) -> None:
    """Deja abierta la ventana hasta que el usuario resuelve el captcha manualmente."""
    if hay_captcha(page):
        _log("  ⏳ Resuelve el captcha en la ventana abierta y espera…")
        page.wait_for_selector(_OF_LINK, timeout=180_000)  # 3 minutos generosos
        _log("  ✓ Captcha superado.")
    else:
        page.wait_for_selector(_OF_LINK, timeout=WAIT_CARDS_MS)


def extraer_tarjetas(page) -> list[dict]:
    """De la página ya renderizada extrae las tarjetas de oferta con JS puro."""
    return page.evaluate(
        """() => {
            const mapa = new Map();
            for (const a of document.querySelectorAll('a')) {
                const href = a.getAttribute('href') || '';
                if (!href.includes('of-i')) continue;
                if (mapa.has(a.href)) continue;
                let empresa = '';
                const tarjeta = a.closest('[data-cy], li, article, div');
                if (tarjeta) {
                    const ce = tarjeta.querySelector(
                        '[data-cy="company-name"], .H3-COMPANYNAME, small, span'
                    );
                    if (ce) empresa = (ce.textContent || '').trim();
                }
                const titulo = (a.getAttribute('title') || a.textContent || '').trim();
                mapa.set(a.href, { job_url: a.href, title: titulo, company: empresa });
            }
            return Array.from(mapa.values());
        }"""
    )


def extraer_descripcion(page, url: str) -> str:
    """Navega al detalle de la oferta y devuelve el texto principal (≤4000 chars)."""
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:
        _log(f"    ✗ detalle falló: {type(exc).__name__}")
        return ""
    time.sleep(2)
    if hay_captcha(page):
        return ""
    texto = page.evaluate(
        """() => (document.querySelector('main') || document.body).innerText.slice(0, 4000)"""
    ) or ""
    return texto.strip()


def construir_url(term: str, location: str) -> str:
    """URL de búsqueda: por provincia si aplica, España entera si es Remote."""
    proxy = {"keyword": quote(term)}
    if "remote" not in location.lower():
        proxy["normalizedProvinceIds"] = MADRID_PROVINCE_ID  # Madrid
    return SEARCH_URL + "?" + "&".join(f"{k}={v}" for k, v in proxy.items())


def main() -> None:
    parser = argparse.ArgumentParser(description="Recolector InfoJobs → CSV (ventana visible)")
    parser.add_argument("--headless", action="store_true", help="sin ventana (usa sesión guardada)")
    parser.add_argument("--results", type=int, default=8, help="ofertas por término (default 8)")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    state = str(SESSION_FILE) if SESSION_FILE.exists() else None
    if args.headless and not state:
        _log("Sin sesión guardada: ejecuta primero sin --headless para resolver el captcha.")
        sys.exit(2)

    filas: list[dict] = []
    vistos: set[str] = set()

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=args.headless)
        contexto = navegador.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
            viewport={"width": 1366, "height": 900},
            storage_state=state,
        )
        pag = contexto.new_page()

        # 1) Primera búsqueda: captcha manual si hace falta; guarda cookies.
        _log(f"Abriendo búsqueda de InfoJobs: {construir_url(SEARCH_TERMS[0], 'Madrid, Spain')}")
        pag.goto(construir_url(SEARCH_TERMS[0], "Madrid, Spain"), wait_until="domcontentloaded")
        pag.wait_for_timeout(4000)
        resolver_captcha_una_vez(pag)
        contexto.storage_state(path=str(SESSION_FILE))
        _log(f"  ✓ Sesión guardada en {SESSION_FILE}")

        # 2) Resto de combinaciones término × ubicación.
        for location in LOCATIONS:
            for term in SEARCH_TERMS:
                if term == SEARCH_TERMS[0] and location == "Madrid, Spain":
                    continue  # ya recolectada en el arranque
                _log(f"Buscando '{term}' en '{location}'…")
                pag.goto(construir_url(term, location), wait_until="domcontentloaded")
                try:
                    pag.wait_for_selector(_OF_LINK, timeout=WAIT_CARDS_MS)
                except Exception:
                    _log("  ✗ sin tarjetas (captcha o vacío): se salta la combinación")
                    continue

                tarjetas = extraer_tarjetas(pag)[: args.results]
                _log(f"  → {len(tarjetas)} tarjetas")
                _pausa((2.0, 4.0))

                for tarjeta in tarjetas:
                    url = tarjeta["job_url"]
                    if not url or url in vistos:
                        continue
                    vistos.add(url)
                    descripcion = extraer_descripcion(pag, url)
                    filas.append(
                        {
                            "title": tarjeta.get("title") or "Unknown",
                            "company": tarjeta.get("company") or "Unknown",
                            "job_url": url,
                            "description": descripcion,
                            "location": location,
                            "published": "",
                            "harvested_at": dt.datetime.utcnow().isoformat(),
                        }
                    )
                    if hay_captcha(pag):
                        _log("  ⏳ Captcha reaparecido: vuelve a lanzarlo con ventana visible")
                        navegador.close()
                        sys.exit(3)
                    _pausa((1.0, 2.5))

        navegador.close()

    if not filas:
        _log("Sin ofertas capturadas.")
        sys.exit(0)

    frame = pd.DataFrame(filas).drop_duplicates(subset=["job_url"]).reset_index(drop=True)
    frame.to_csv(OUTPUT_CSV, index=False)
    _log(f"Guardado {len(frame)} ofertas únicas en {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
