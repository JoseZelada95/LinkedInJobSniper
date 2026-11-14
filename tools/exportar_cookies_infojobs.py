#!/usr/bin/env python3
"""Exporta las cookies de InfoJobs tras resolver el captcha — ejecutar en tu PC.

Qu hace:
  1. Abre Chromium en ventana visible con la bsqueda de InfoJobs.
  2. Espera a que resuelvas el captcha manualmente (2 minutos de margen).
  3. Cuando la pgina muestra ofertas, imprime TODAS las cookies (incluidas
     las HttpOnly, que devtools no deja copiar fcilmente) en formato JSON.
  4. Copia ese JSON y pgalo en el chat.

Cmo ejecutar en tu PC (una vez):

    pip install "playwright>=1.40"
    python -m playwright install chromium
    python exportar_cookies_infojobs.py

El JSON incluye: name, value, domain, path, expires, httpOnly, secure/samesite.
"""

from __future__ import annotations

import json
import sys

SEARCH = "https://www.infojobs.net/jobsearch/search-results/list.xhtml?keyword=desarrollador%20.NET&normalizedProvinceIds=28"


def main() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=False)
        contexto = navegador.new_context(locale="es-ES", viewport={"width": 1366, "height": 900})
        page = contexto.new_page()
        page.goto(SEARCH, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        print()
        print(">>> Abre la ventana visible: resuelve el captcha (Hacer clic para comprobar).")
        print(">>> Cuando veas tarjetas de ofertas en la pgina, vuelve aqu al terminal.")
        try:
            page.wait_for_selector("a[href*='of-i']", timeout=180_000)
        except Exception:
            print("No aparecieron ofertas en 3 minutos. Vuelve a ejecutar el script.")
            navegador.close()
            sys.exit(2)
        print(">>> Ofertas visibles: exportando cookies…")

        estado = contexto.storage_state()
        navegador.close()

    print()
    print("=== PEGA ESTE JSON A TU ASISTENTE ===")
    print(json.dumps(estado, ensure_ascii=False))
    print("=== FIN DEL JSON ===")


if __name__ == "__main__":
    main()
