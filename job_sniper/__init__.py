"""LinkedIn Job Sniper — paquete con toda la lógica del agente de búsqueda de empleo.

Estructura interna (cada módulo con una única responsabilidad):

    config       → carga y valida la configuración (.env / GitHub Secrets)
    models       → modelos de datos (oferta de empleo, evaluación del LLM)
    prompts      → plantillas de texto que se envían al LLM
    llm          → cliente del LLM con estrategia de fallback entre modelos
    scraping     → obtención de ofertas (jobspy + descripción manual por HTTP)
    evaluation   → puntuación de afinidad oferta ↔ CV (0-100)
    tailoring    → viñetas de CV personalizadas para el email (usa el LLM)
    pdf_generator→ generación de PDFs de CV personalizados (sin LLM, offline)
    sheets       → exportación a Google Sheets (y lectura del historial)
    emailer      → digesto diario por email (HTML)
    history      → historial de ofertas ya enviadas (local + Sheets)
    resume       → carga del CV (variable de entorno o Google Drive)
    applications → registro y límite diario de solicitudes (modo autónomo)
    pipeline     → orquestación de los flujos completos
    logging_utils→ logging uniforme (consola + fichero)

Los puntos de entrada (`main.py` y `autonomous_agent.py`) viven en la raíz
del proyecto para no romper el GitHub Actions que ejecuta `python main.py`.
"""

__version__ = "2.0.0"
