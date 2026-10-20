"""
salud.py — GET /api/salud
==========================

Estado del despliegue: qué modelo está configurado y si hay credenciales para
alcanzarlo. La interfaz lo usa para decir con qué motor va a responder antes
de que el usuario escriba nada.

No llama al modelo. Una comprobación de salud que gasta una invocación del LLM
en cada carga de página cuesta dinero y latencia para confirmar algo que el
primer turno confirmará de todas formas.
"""

import os

from _comun import BaseJSON  # noqa: E402

from src.services.llm_synthesis import DEFAULT_MODEL, NVIDIA_BASE_URL  # noqa: E402


class handler(BaseJSON):  # noqa: N801
    def do_GET(self) -> None:  # noqa: N802
        tiene_clave = bool(os.getenv("NVIDIA_API_KEY"))
        self.responder(
            {
                "estado": "ok",
                "modelo": DEFAULT_MODEL,
                "endpoint": NVIDIA_BASE_URL,
                "modelo_disponible": tiene_clave,
                "motor": "modelo" if tiene_clave else "determinista",
                "nota": (
                    "Credenciales presentes; el primer turno confirma que el modelo responde."
                    if tiene_clave
                    else "Sin NVIDIA_API_KEY: el agente corre con el motor determinista."
                ),
            }
        )
