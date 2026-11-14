"""Punto de entrada del informe diario.

En GitHub Actions se ejecuta con `python main.py` (ver `.github/workflows/daily.yml`),
por eso este fichero se mantiene en la raíz y es deliberadamente corto:

    1. `bootstrap_settings()` → carga config (.env/Secrets), CV y valida todo.
    2. `run_daily_report()`   → scrape → evaluar → personalizar → Sheets → email.

Toda la lógica real vive en el paquete `job_sniper/`.
"""

from job_sniper.pipeline import ConfigurationError, bootstrap_settings, run_daily_report


def main() -> None:
    """Arranca el informe diario. Sale con código 1 si la configuración es incompleta."""
    try:
        settings = bootstrap_settings()
    except ConfigurationError as exc:
        # Todavía no hay logging configurado → impresión directa en stderr.
        print(f"ERROR: {exc}")
        raise SystemExit(1)

    run_daily_report(settings)


if __name__ == "__main__":
    main()
